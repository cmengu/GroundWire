# openai_validator.py
"""
Dual-model trajectory validation using GPT-4o as a second opinion.
Called from core.py when Claude's trajectory score drops below DUAL_VALIDATE_THRESHOLD.
Rubric criterion 02: TinyFish + OpenAI integration.
"""
import json
import logging
import os

DUAL_VALIDATE_THRESHOLD = 0.70  # trigger GPT-4o check when Claude score < this
_GPT_MODEL = "gpt-4o"


def dual_validate(goal: str, events: list[dict], claude_score: float, intent: str = "") -> float:
    """
    Call GPT-4o with the same trajectory rubric when Claude's progress_rate < threshold.
    Returns the more conservative (lower) of the two scores.
    Prints a visible comparison line. Never raises — returns claude_score on any failure.
    """
    if claude_score >= DUAL_VALIDATE_THRESHOLD:
        return claude_score

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logging.warning("[openai] OPENAI_API_KEY not set — skipping dual validation")
        return claude_score

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
    except ImportError:
        logging.warning("[openai] openai package not installed — skipping dual validation")
        return claude_score

    recent = events[-10:]
    steps_summary = json.dumps(
        [str(e.get("purpose") or e.get("action") or e.get("type") or "") for e in recent],
        indent=2,
    )
    intent_line = f"Agent's current inferred intent: {intent}\n\n" if intent else ""

    prompt = (
        f"You are evaluating a web agent's trajectory.\n"
        f"Goal: {goal}\n"
        f"Last {len(recent)} actions:\n{steps_summary}\n\n"
        f"{intent_line}"
        "Score PROGRESS_RATE: a single float 0.0–1.0 where 1.0 = perfectly on track "
        "and 0.0 = completely off track. Be conservative — score low if in doubt.\n"
        'Respond with JSON only: {"progress_rate": <float>}'
    )

    try:
        response = client.chat.completions.create(
            model=_GPT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=60,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content.strip()
        gpt_score = float(json.loads(raw).get("progress_rate", claude_score))
        conservative = round(min(claude_score, gpt_score), 3)
        print(
            f"[openai] GPT-4o trajectory check: {gpt_score:.2f} | "
            f"Claude: {claude_score:.2f} | "
            f"Using conservative: {conservative:.2f}"
        )
        return conservative
    except Exception as e:
        logging.warning("[openai] dual_validate failed: %s", e)
        return claude_score
