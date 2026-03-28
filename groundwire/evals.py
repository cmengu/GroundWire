import json
import time
from pathlib import Path

import anthropic

EVALS_DIR = Path(".groundwire_evals")
EVALS_DIR.mkdir(exist_ok=True)

ANTHROPIC_MODEL = "claude-sonnet-4-6"


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
    path.write_text(json.dumps(data, indent=2))
    print(f"[eval] ✅ Recorded session '{safe_id}' ({len(events)} steps)")


def score(session_id: str, new_events: list[dict]) -> dict:
    """Score a new run against the recorded golden session."""
    safe_id = session_id.replace("/", "_").replace(" ", "_")
    path = EVALS_DIR / f"{safe_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No recorded session found: {safe_id}")

    golden = json.loads(path.read_text())
    step_delta = len(new_events) - golden["step_count"]

    client = anthropic.Anthropic()
    golden_summary = json.dumps(golden["events"][-3:])
    new_summary = json.dumps(new_events[-3:])

    msg = client.messages.create(
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
                    "Respond ONLY with JSON, no preamble:\n"
                    '{"faithfulness": 0.0-1.0, "notes": "one sentence"}'
                ),
            }
        ],
    )

    raw = msg.content[0].text.strip()
    try:
        faith = json.loads(raw)
    except json.JSONDecodeError:
        faith = {"faithfulness": 0.5, "notes": "score parse failed"}

    efficiency = f"{'+' if step_delta > 0 else ''}{step_delta} steps vs golden ({golden['step_count']} steps)"

    return {
        "session_id": safe_id,
        "faithfulness": round(float(faith.get("faithfulness", 0.5)), 2),
        "efficiency": efficiency,
        "notes": faith.get("notes", ""),
    }
