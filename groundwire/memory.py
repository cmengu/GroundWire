# memory.py  (Phase 1 — three-layer site memory per phrase1-actual)
"""
Groundwire site memory — three-layer knowledge system per domain.

Storage: .groundwire_memory/<domain>.json
Schema:
  {
    "quirks":           [{"text": str, "confidence": float, "last_seen": float}],
    "runs":             [{"id": str, "goal": str, "timestamp": float,
                          "step_count": int, "success": bool}],
    "semantic_profile": str,
    "run_count":        int,
    "last_consolidated": float
  }

Public interface:
    recall, write, extract_quirks, log_run, consolidate, atomic_write_json
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

import anthropic

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
    """Sanitise domain into a safe filename. Strips port numbers and slashes."""
    safe = domain.replace(":", "_").replace("/", "_").replace(".", "_")
    return MEMORY_DIR / f"{safe}.json"


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
        sorted_quirks = sorted(quirks, key=lambda q: q.get("confidence", 1), reverse=True)[:10]
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
    new_quirks = _filter_pii_quirks(list(new_quirks))
    if not new_quirks:
        return

    path = _domain_path(domain)
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
    Ask Claude to extract site-specific navigation quirks from the first 20 events.
    Returns list[str]. Returns [] on any error — never raises.
    """
    if not events:
        return []

    client = anthropic.Anthropic()
    event_sample = json.dumps(events[:20], indent=2)

    prompt = (
        f"These are the first events from a web agent navigating {domain}.\n"
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
            client,
            model=_ANTHROPIC_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
            response_model=QuirksList,
        )
        return [q for q in out.quirks if isinstance(q, str)]
    except Exception:
        return []


def log_run(domain: str, goal: str, events: list[dict], success: bool = True) -> None:
    """
    Append an episodic run entry. Increments run_count via len(runs).
    This function owns run_count — write() does not touch it.
    """
    path = _domain_path(domain)
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
    }

    runs = data.get("runs", [])
    runs.append(run_entry)
    data["runs"] = runs
    data["run_count"] = len(runs)

    atomic_write_json(path, data)


def consolidate(domain: str) -> bool:
    """
    Every CONSOLIDATE_EVERY runs, synthesize episodic history into semantic_profile.
    Returns True if consolidation ran. Never raises.
    """
    path = _domain_path(domain)
    if not path.exists():
        return False

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    run_count = data.get("run_count", 0)
    if run_count == 0 or run_count % CONSOLIDATE_EVERY != 0:
        return False

    runs = data.get("runs", [])
    if not runs:
        return False

    client = anthropic.Anthropic()
    recent_runs = runs[-20:]
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
            client,
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
