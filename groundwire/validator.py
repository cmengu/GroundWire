import json

import anthropic

client = anthropic.Anthropic()

ANTHROPIC_MODEL = "claude-sonnet-4-6"

VALIDATOR_PROMPT = """You are a trajectory auditor for a web agent.

Goal: {goal}

Last {n} agent actions (most recent last):
{steps}

Is the agent still on track to complete the goal?
Respond ONLY with valid JSON, no preamble, no markdown:
{{"on_track": true, "confidence": 0.95, "reason": "...", "suggestion": "..."}}

- confidence: 0.0 (completely off track) to 1.0 (perfectly on track)
- suggestion: what the agent should do next if off track (empty string if on track)
"""


def check_trajectory(goal: str, events: list[dict]) -> dict:
    """
    Returns: {"on_track": bool, "confidence": float, "reason": str, "suggestion": str}
    """
    steps = []
    for e in events[-10:]:
        # TinyFish PROGRESS events carry human-readable intent in "purpose" (Phase 0 curl).
        step_str = (
            e.get("purpose")
            or e.get("action")
            or e.get("description")
            or e.get("type")
            or str(e)[:120]
        )
        steps.append(step_str)

    prompt = VALIDATOR_PROMPT.format(
        goal=goal,
        n=len(steps),
        steps=json.dumps(steps, indent=2),
    )

    msg = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = msg.content[0].text.strip()
    try:
        result = json.loads(raw)
        return {
            "on_track": bool(result.get("on_track", True)),
            "confidence": float(result.get("confidence", 1.0)),
            "reason": result.get("reason", ""),
            "suggestion": result.get("suggestion", ""),
        }
    except (json.JSONDecodeError, ValueError):
        return {
            "on_track": True,
            "confidence": 1.0,
            "reason": "validator parse error",
            "suggestion": "",
        }
