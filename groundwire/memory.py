# memory.py  (Phase 1 — three-layer site memory per phrase1-actual)
"""
Groundwire site memory — three-layer knowledge system per domain.

Storage: .groundwire_memory/<domain>.json
Schema:
  {
    "quirks":           [{"text": str, "confidence": int, "last_seen": float}],
    "runs":             [{"id": str, "goal": str, "timestamp": float,
                          "step_count": int, "success": bool}],
    "semantic_profile": str,
    "run_count":        int,
    "last_consolidated": float
  }

Public interface:
    recall, write, extract_quirks, log_run, consolidate
"""
import json
import time
from pathlib import Path

import anthropic as _anthropic

MEMORY_DIR = Path(".groundwire_memory")
MEMORY_DIR.mkdir(exist_ok=True)

# Semantic consolidation every N runs (low threshold for demo visibility).
CONSOLIDATE_EVERY = 3

# Plan Step 2.2 / 2.3: exact model string from phrase1-actual.md
_ANTHROPIC_MODEL = "claude-sonnet-4-20250514"


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
        data = json.loads(path.read_text())
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
    Found → increment confidence + update last_seen.
    Not found → insert with confidence=1.
    Does NOT increment run_count — that is owned by log_run().
    """
    if not new_quirks:
        return

    path = _domain_path(domain)
    if path.exists():
        try:
            data = json.loads(path.read_text())
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

    for text in new_quirks:
        if text in existing:
            existing[text]["confidence"] = existing[text].get("confidence", 1) + 1
            existing[text]["last_seen"] = now
        else:
            existing[text] = {"text": text, "confidence": 1, "last_seen": now}

    data["quirks"] = list(existing.values())
    path.write_text(json.dumps(data, indent=2))


def extract_quirks(events: list[dict], domain: str) -> list[str]:
    """
    Ask Claude to extract site-specific navigation quirks from the first 20 events.
    Returns list[str]. Returns [] on any error — never raises.
    """
    if not events:
        return []

    client = _anthropic.Anthropic()
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
        "Return ONLY a JSON array of short strings. No preamble. No markdown. No explanation.\n"
        "If no quirks are detectable, return [].\n"
        'Example: ["Cookie consent modal on first load", '
        '"Job listings require scroll to trigger lazy load"]\n\n'
        f"Events:\n{event_sample}"
    )

    try:
        msg = client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        parsed = json.loads(raw)
        return [q for q in parsed if isinstance(q, str)]
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
            data = json.loads(path.read_text())
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

    path.write_text(json.dumps(data, indent=2))


def consolidate(domain: str) -> bool:
    """
    Every CONSOLIDATE_EVERY runs, synthesize episodic history into semantic_profile.
    Returns True if consolidation ran. Never raises.
    """
    path = _domain_path(domain)
    if not path.exists():
        return False

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    run_count = data.get("run_count", 0)
    if run_count == 0 or run_count % CONSOLIDATE_EVERY != 0:
        return False

    runs = data.get("runs", [])
    if not runs:
        return False

    client = _anthropic.Anthropic()
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
        "Return ONLY the sentence. No preamble. No markdown. No trailing punctuation beyond a period."
    )

    try:
        msg = client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        profile = msg.content[0].text.strip()
        data["semantic_profile"] = profile
        data["last_consolidated"] = time.time()
        path.write_text(json.dumps(data, indent=2))
        return True
    except Exception:
        return False
