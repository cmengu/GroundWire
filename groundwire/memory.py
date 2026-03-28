# memory.py  (Phase 1 — three-layer site memory per phrase1-actual)
"""
Groundwire site memory — three-layer knowledge system per domain.

Storage: .groundwire_memory/<domain>.json
Schema:
  {
    "quirks":           [{"text": str, "confidence": float, "last_seen": float}],
    "runs":             [{"id": str, "goal": str, "timestamp": float,
                          "step_count": int, "success": bool, "is_trial": bool}],
    "semantic_profile": str,
    "run_count":        int,
    "last_consolidated": float
  }

Public interface:
    recall, write, extract_quirks, log_run, consolidate, atomic_write_json,
    memory_report
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

import anthropic
from filelock import FileLock

from guardrails import PIIScrubber as _PIIScrubberGuard

from llm_utils import parse_structured
from schemas import QuirksList, SemanticProfile

MEMORY_DIR = Path(".groundwire_memory")
MEMORY_DIR.mkdir(exist_ok=True)

# Semantic consolidation every N runs (low threshold for demo visibility).
CONSOLIDATE_EVERY = 3

_ANTHROPIC_MODEL = "claude-sonnet-4-6"

# Confidence decay rate: 5% per day of not being seen.
_DECAY_RATE = 0.95
_SECONDS_PER_DAY = 86400.0

_PII_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b|"
    r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
)

# Module-level singleton — stateless, thread-safe, one connection pool per process.
_client = anthropic.Anthropic()


def atomic_write_json(path: Path, data: dict) -> None:
    """Persist JSON via temp file + os.replace (atomic on POSIX)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix=path.name + ".",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _domain_path(domain: str) -> Path:
    """Sanitise domain into a safe filename. Strips port numbers and slashes; keeps dots."""
    safe = domain.replace(":", "_").replace("/", "_")
    return MEMORY_DIR / f"{safe}.json"


def _domain_lock(path: Path) -> FileLock:
    """Per-domain advisory lock — serializes all read-modify-write on a given domain file."""
    return FileLock(str(path.with_suffix(".lock")), timeout=10)


def _empty_domain_data() -> dict:
    """
    Canonical empty schema. Single source of truth.
    Every function that creates a new domain record calls this — never inlines {}.
    """
    return {
        "quirks": [],
        "runs": [],
        "semantic_profile": "",
        "run_count": 0,
        "last_consolidated": 0.0,
    }


def _filter_pii_quirks(quirks: list[str]) -> list[str]:
    return [q for q in quirks if q and not _PII_RE.search(q)]


def recall(domain: str) -> str:
    """
    Return a stratified plain-English briefing for this domain.
    Layer 1 (always): run count + confidence headline.
    Layer 2 (if exists): semantic profile sentence.
    Layer 3 (if exists): top 10 quirks sorted by confidence descending.
    Returns "" if no memory exists. NEVER returns None.
    """
    path = _domain_path(domain)
    if not path.exists():
        return ""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    run_count = data.get("run_count", 0)
    quirks = data.get("quirks", [])
    semantic_profile = data.get("semantic_profile", "")

    if run_count == 0 and not quirks and not semantic_profile:
        return ""

    if run_count >= 10:
        confidence_label = "high"
    elif run_count >= 4:
        confidence_label = "medium"
    else:
        confidence_label = "low"

    lines = [f"Site memory for {domain} — {run_count} run(s), confidence: {confidence_label}"]

    if semantic_profile:
        lines.append(f"  Strategic profile: {semantic_profile}")

    if quirks:
        dict_quirks = [q for q in quirks if isinstance(q, dict)]
        sorted_quirks = sorted(dict_quirks, key=lambda q: q.get("confidence", 1), reverse=True)[:10]
        lines.append("  Known quirks:")
        for q in sorted_quirks:
            text = q.get("text", "") if isinstance(q, dict) else str(q)
            conf = q.get("confidence", 1) if isinstance(q, dict) else 1
            lines.append(f"    - {text} (confirmed {conf}x)")

    return "\n".join(lines)


def write(domain: str, new_quirks: list[str]) -> None:
    """
    Upsert new_quirks into the confidence map for this domain.
    Re-seen → time-decay prior confidence then +1; new → insert with confidence=1.
    Quirks matching email/phone patterns are dropped before persist.
    Does NOT increment run_count — that is owned by log_run().
    """
    # Defence in depth: strip PII before any quirk reaches disk.
    _pii_re = re.compile("|".join(_PIIScrubberGuard._PATTERNS.values()))
    new_quirks = [q for q in list(new_quirks) if q and not _pii_re.search(q)]
    if not new_quirks:
        return

    path = _domain_path(domain)
    with _domain_lock(path):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = _empty_domain_data()
        else:
            data = _empty_domain_data()

        # Migrate legacy flat string quirks if present
        raw_quirks = data.get("quirks", [])
        existing: dict[str, dict] = {}
        now = time.time()
        for q in raw_quirks:
            if isinstance(q, str):
                existing[q] = {"text": q, "confidence": 1, "last_seen": now}
            elif isinstance(q, dict) and "text" in q:
                existing[q["text"]] = q

        # Decay confidence of quirks not seen in this write batch.
        new_quirks_set = set(new_quirks)
        for text, q in existing.items():
            if text not in new_quirks_set:
                days = (now - q.get("last_seen", now)) / _SECONDS_PER_DAY
                if days > 0:
                    q["confidence"] = max(0.1, float(q.get("confidence", 1.0)) * (_DECAY_RATE**days))

        for text in new_quirks:
            if text in existing:
                prev = float(existing[text].get("confidence", 1.0))
                last_seen = float(existing[text].get("last_seen", now))
                days_since = (now - last_seen) / _SECONDS_PER_DAY
                decayed = prev * (_DECAY_RATE**days_since) + 1.0
                existing[text]["confidence"] = max(0.1, decayed)
                existing[text]["last_seen"] = now
            else:
                existing[text] = {"text": text, "confidence": 1.0, "last_seen": now}

        data["quirks"] = list(existing.values())
        atomic_write_json(path, data)


def extract_quirks(events: list[dict], domain: str) -> list[str]:
    """
    Ask Claude to extract site-specific navigation quirks from events.
    Uses head+tail sampling (first 10 + last 10) to capture both early and late patterns.
    Returns list[str]. Returns [] on any error — never raises.
    """
    if not events:
        return []

    # Head+tail: capture both start-of-run quirks (auth walls) and end-of-run patterns (CAPTCHAs)
    head = events[:10]
    tail = events[-10:] if len(events) > 10 else []
    event_sample = json.dumps(head + tail, indent=2)

    prompt = (
        f"These are events from a web agent navigating {domain}.\n"
        "Identify site-specific navigation quirks encountered or observable:\n"
        "- Cookie/consent modals\n"
        "- Authentication walls (note where they appear)\n"
        "- Lazy-loaded content (what scroll/click triggers it)\n"
        "- Anti-bot pauses or CAPTCHAs\n"
        "- Unusual pagination patterns\n"
        "- Redirect chains\n\n"
        "Return a JSON object with a single key \"quirks\" whose value is an array of short strings.\n"
        "No preamble. No markdown. If none, use {\"quirks\": []}.\n"
        'Example: {"quirks": ["Cookie consent modal on first load", '
        '"Job listings require scroll to trigger lazy load"]}\n\n'
        f"Events:\n{event_sample}"
    )

    try:
        out = parse_structured(
            _client,
            model=_ANTHROPIC_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
            response_model=QuirksList,
        )
        return [q for q in out.quirks if isinstance(q, str)]
    except Exception:
        return []


def log_run(
    domain: str,
    goal: str,
    events: list[dict],
    success: bool = True,
    is_trial: bool = False,
) -> None:
    """
    Append an episodic run entry. Increments run_count via len(runs).
    is_trial=True marks eval-mode runs so consolidate() excludes them from synthesis.
    This function owns run_count — write() does not touch it.
    """
    path = _domain_path(domain)
    with _domain_lock(path):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = _empty_domain_data()
        else:
            data = _empty_domain_data()

        run_entry = {
            "id": str(int(time.time())),
            "goal": goal,
            "timestamp": time.time(),
            "step_count": len(events),
            "success": success,
            "is_trial": is_trial,
        }

        runs = data.get("runs", [])
        runs.append(run_entry)
        data["runs"] = runs[-100:]  # cap at 100 most recent runs
        data["run_count"] = len(data["runs"])

        atomic_write_json(path, data)


def consolidate(domain: str) -> bool:
    """
    Every CONSOLIDATE_EVERY real (non-trial) runs, synthesize episodic history into semantic_profile.
    Trial runs are excluded from synthesis to prevent eval-goal bias.
    Returns True if consolidation ran. Never raises.
    """
    path = _domain_path(domain)
    if not path.exists():
        return False

    with _domain_lock(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False

        all_runs = data.get("runs", [])
        # Only real (non-trial) runs drive consolidation — trial runs reflect eval goals, not production diversity.
        real_runs = [r for r in all_runs if not r.get("is_trial", False)]
        real_run_count = len(real_runs)

        if real_run_count == 0 or real_run_count % CONSOLIDATE_EVERY != 0:
            return False

        if not real_runs:
            return False

        recent_runs = real_runs[-20:]
        runs_summary = json.dumps(
            [
                {"goal": r.get("goal"), "step_count": r.get("step_count"), "success": r.get("success")}
                for r in recent_runs
            ],
            indent=2,
        )

        top_quirks = sorted(
            data.get("quirks", []),
            key=lambda q: q.get("confidence", 0) if isinstance(q, dict) else 0,
            reverse=True,
        )[:10]
        quirks_summary = json.dumps(
            [{"text": q.get("text"), "confidence": q.get("confidence")} for q in top_quirks],
            indent=2,
        )

        prompt = (
            f"You are analysing the interaction history of a web agent with {domain}.\n"
            f"Recent runs ({len(recent_runs)}):\n{runs_summary}\n\n"
            f"Top known quirks (by confirmation count):\n{quirks_summary}\n\n"
            "Write ONE sentence (max 40 words) strategic profile of this site from a web agent's perspective.\n"
            "Focus on: reliability, common failure points, navigation patterns, which goal types succeed vs struggle.\n"
            "Return ONLY JSON: {\"profile\": \"<your sentence ending with a period>\"}"
        )

        try:
            out = parse_structured(
                _client,
                model=_ANTHROPIC_MODEL,
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}],
                response_model=SemanticProfile,
            )
            data["semantic_profile"] = out.profile.strip()
            data["last_consolidated"] = time.time()
            atomic_write_json(path, data)
            return True
        except Exception:
            return False


def patch_quirk(domain: str, quirk_text: str, delta: float) -> None:
    """
    Increment or decrement confidence on a single existing quirk by `delta`.

    Positive delta (e.g. +0.15) confirms the quirk after a healer sandbox pass.
    Negative delta (e.g. -0.10) weakens a quirk whose hypothesis was not confirmed.
    Confidence is clamped to [0.1, 10.0] to prevent runaway values.
    No-op if the quirk_text is not found — caller must ensure quirk already exists.
    Never raises.
    """
    path = _domain_path(domain)
    if not path.exists():
        return
    try:
        with _domain_lock(path):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return
            changed = False
            for q in data.get("quirks", []):
                if isinstance(q, dict) and q.get("text") == quirk_text:
                    current = float(q.get("confidence", 1.0))
                    q["confidence"] = round(max(0.1, min(10.0, current + delta)), 4)
                    changed = True
                    break
            if changed:
                atomic_write_json(path, data)
    except Exception:
        pass  # local memory patch is best-effort; never blocks healer flow


def record_antibot_event(domain: str, run_id: str, block_type: str, note: str) -> None:
    """
    Append a local antibot event record to the domain memory file.

    Provides offline tracking of block events in addition to the Supabase write
    in shared_memory.record_antibot_event. Useful for local debugging without
    Supabase credentials. Never raises.
    """
    path = _domain_path(domain)
    try:
        with _domain_lock(path):
            try:
                data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else _empty_domain_data()
            except (json.JSONDecodeError, OSError):
                data = _empty_domain_data()
            antibot_log = data.setdefault("antibot_events", [])
            antibot_log.append({
                "run_id": run_id,
                "block_type": block_type,
                "note": note,
                "resolved": None,  # filled in by record_antibot_resolution
                "ts": time.time(),
            })
            atomic_write_json(path, data)
    except Exception:
        pass


def record_antibot_resolution(domain: str, block_type: str, resolved: bool, config: dict) -> None:
    """
    Update the most recent unresolved antibot_event of `block_type` with its outcome.

    `resolved=True` means auto-retry succeeded; `resolved=False` means it failed.
    `config` contains the retry parameters used (e.g. {"profile": "stealth"}).
    Never raises.
    """
    path = _domain_path(domain)
    if not path.exists():
        return
    try:
        with _domain_lock(path):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return
            antibot_log = data.get("antibot_events", [])
            # Update the most recent entry matching block_type that has not been resolved yet.
            for entry in reversed(antibot_log):
                if entry.get("block_type") == block_type and entry.get("resolved") is None:
                    entry["resolved"] = resolved
                    entry["resolution_config"] = config
                    break
            atomic_write_json(path, data)
    except Exception:
        pass


def memory_report(domain: str) -> str:
    """
    Pretty-print accumulated domain knowledge for demo visibility.
    Shows quirk confidence bars, success rate, and semantic profile.
    Returns a formatted multi-line string. Never raises.
    """
    path = _domain_path(domain)
    if not path.exists():
        return f"No memory for {domain} — no runs recorded yet."

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return f"Memory file for {domain} could not be read."

    all_runs = data.get("runs", [])
    real_runs = [r for r in all_runs if not r.get("is_trial", False)]
    trial_runs = [r for r in all_runs if r.get("is_trial", False)]
    run_count = data.get("run_count", 0)
    semantic_profile = data.get("semantic_profile", "")
    quirks = [q for q in data.get("quirks", []) if isinstance(q, dict)]

    border = "═" * 52
    lines = [
        f"╔{border}╗",
        f"║  Groundwire Memory Report — {domain[:36]:<36}  ║",
        f"╠{border}╣",
        f"║  Runs: {run_count} total  ({len(real_runs)} real · {len(trial_runs)} eval trials){'':>14}║",
    ]

    if real_runs:
        success_count = sum(1 for r in real_runs if r.get("success", True))
        avg_steps = sum(r.get("step_count", 0) for r in real_runs) / len(real_runs)
        lines.append(
            f"║  Success: {success_count}/{len(real_runs)} real runs  ·  avg {avg_steps:.1f} steps/run{'':>12}║"
        )

    if semantic_profile:
        # Wrap to fit panel width (48 chars content)
        words = semantic_profile.split()
        line_buf, wrapped = [], []
        for word in words:
            if sum(len(w) + 1 for w in line_buf) + len(word) > 48:
                wrapped.append(" ".join(line_buf))
                line_buf = [word]
            else:
                line_buf.append(word)
        if line_buf:
            wrapped.append(" ".join(line_buf))
        lines.append(f"╠{border}╣")
        lines.append(f"║  Profile:{'':>42}║")
        for wline in wrapped:
            lines.append(f"║    {wline:<48}║")

    if quirks:
        sorted_quirks = sorted(quirks, key=lambda q: q.get("confidence", 0), reverse=True)[:5]
        lines.append(f"╠{border}╣")
        lines.append(f"║  Top quirks by confidence:{'':>25}║")
        for q in sorted_quirks:
            conf = q.get("confidence", 0)
            text = q.get("text", "")[:40]
            filled = min(5, int(conf))
            bar = "█" * filled + "░" * (5 - filled)
            lines.append(f"║  {bar} {conf:.1f}x  {text:<40}  ║")

    lines.append(f"╚{border}╝")
    return "\n".join(lines)
