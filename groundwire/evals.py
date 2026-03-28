import json
import time
from pathlib import Path

import anthropic

from llm_utils import parse_structured
from memory import atomic_write_json
from schemas import FaithfulnessScore

EVALS_DIR = Path(".groundwire_evals")
EVALS_DIR.mkdir(exist_ok=True)

ANTHROPIC_MODEL = "claude-sonnet-4-6"


def _strip_groundwire_meta(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("type") != "groundwire_meta"]


def _flatten_keys(obj: object, prefix: str = "") -> set[str]:
    """Dotted paths for dicts; bracket indices for list items (capped)."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            keys.add(p)
            keys |= _flatten_keys(v, p)
    elif isinstance(obj, list):
        for idx, item in enumerate(obj[:50]):
            bracket = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            keys |= _flatten_keys(item, bracket)
    return keys


def _indexed_flat_keys(events: list[dict]) -> set[str]:
    acc: set[str] = set()
    for i, e in enumerate(events):
        acc |= {f"e{i}.{k}" for k in _flatten_keys(e)}
    return acc


def _structural_diff(golden_events: list[dict], new_events: list[dict]) -> dict:
    """
    Compare key presence between golden and new events. No LLM calls.
    Top-level union (legacy) plus nested flattening and key_coverage.
    """
    golden_keys: set[str] = set()
    for e in golden_events:
        golden_keys.update(e.keys())
    new_keys: set[str] = set()
    for e in new_events:
        new_keys.update(e.keys())
    missing_keys = sorted(golden_keys - new_keys)
    extra_keys = sorted(new_keys - golden_keys)

    g_flat = _indexed_flat_keys(golden_events)
    n_flat = _indexed_flat_keys(new_events)
    inter = g_flat & n_flat
    key_coverage = len(inter) / max(len(g_flat), 1)
    missing_keys_flat = sorted(g_flat - n_flat)
    extra_keys_flat = sorted(n_flat - g_flat)

    return {
        "missing_keys": missing_keys,
        "extra_keys": extra_keys,
        "structurally_equivalent": len(missing_keys) == 0,
        "missing_keys_flat": missing_keys_flat,
        "extra_keys_flat": extra_keys_flat,
        "key_coverage": round(key_coverage, 4),
    }


def record(session_id: str, goal: str, events: list[dict]) -> None:
    """Save a golden run to disk."""
    safe_id = session_id.replace("/", "_").replace(" ", "_")
    data = {
        "session_id": safe_id,
        "goal": goal,
        "timestamp": time.time(),
        "step_count": len(events),
        "events": events,
    }
    path = EVALS_DIR / f"{safe_id}.json"
    atomic_write_json(path, data)
    print(f"[eval] ✅ Recorded session '{safe_id}' ({len(events)} steps)")


def score(session_id: str, new_events: list[dict]) -> dict:
    """Score a new run against the recorded golden session."""
    safe_id = session_id.replace("/", "_").replace(" ", "_")
    path = EVALS_DIR / f"{safe_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No recorded session found: {safe_id}")

    golden = json.loads(path.read_text(encoding="utf-8"))
    new_core = _strip_groundwire_meta(new_events)
    step_delta = len(new_core) - golden["step_count"]
    structural = _structural_diff(golden.get("events", []), new_core)

    client = anthropic.Anthropic()
    golden_summary = json.dumps(golden["events"][-3:])
    new_summary = json.dumps(new_core[-3:])

    try:
        faith_out = parse_structured(
            client,
            model=ANTHROPIC_MODEL,
            max_tokens=200,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Goal: {golden['goal']}\n\n"
                        f"Golden run final events: {golden_summary}\n"
                        f"New run final events: {new_summary}\n\n"
                        "Score how well the new run achieved the same goal as the golden run.\n"
                        "Respond ONLY with JSON: "
                        '{"faithfulness": <float 0.0-1.0>, "notes": "<one sentence>"}'
                    ),
                }
            ],
            response_model=FaithfulnessScore,
        )
        faithfulness = float(faith_out.faithfulness)
        notes = faith_out.notes
    except Exception:
        faithfulness = 0.5
        notes = "score parse or API failed"

    efficiency = f"{'+' if step_delta > 0 else ''}{step_delta} steps vs golden ({golden['step_count']} steps)"

    return {
        "session_id": safe_id,
        "faithfulness": round(faithfulness, 2),
        "step_delta": step_delta,
        "efficiency": efficiency,
        "structural_diff": structural,
        "notes": notes,
    }
