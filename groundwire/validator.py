# validator.py — Phase 2 trajectory validator (phrase2.md)
"""
Groundwire trajectory validator — live mid-run evaluation.

Key functions:
    check_trajectory(goal, events_so_far) -> rubric dict + progress_rate
    detect_deterministic_signals(events) -> loop / irreversibility (no LLM)
    infer_intent(events, domain) -> short phrase
    generate_critique(goal, events, check_result) -> Reflexion string
    compress_goal(original_goal, briefing, critique) -> 2-sentence replan goal
"""
import json
import logging

import anthropic

from llm_utils import parse_structured
from schemas import (
    CompressedGoal,
    CritiqueText,
    IntentPhrase,
    TrajectoryRubric,
)

# One client per process — stateless, reuses connection pool across validator calls.
_client = anthropic.Anthropic()

MODEL = "claude-sonnet-4-6"

PROGRESS_WEIGHTS = {
    "goal_alignment": 0.50,
    "action_efficiency": 0.30,
    "risk_signal": 0.20,
}

DRIFT_THRESHOLD = 0.60
DRIFT_STREAK_REQUIRED = 2

IRREVERSIBLE_KEYWORDS = {
    "confirm",
    "submit",
    "checkout",
    "purchase",
    "delete",
    "pay",
    "place order",
}


def _event_step_str(e: dict) -> str:
    """Human-readable step label for TinyFish SSE (purpose on PROGRESS) and fallbacks."""
    return str(
        e.get("purpose")
        or e.get("action")
        or e.get("description")
        or e.get("type")
        or ""
    )


def _safe_pass_result(reason: str) -> dict:
    """Neutral passing rubric — used on cold start or any validator failure."""
    return {
        "goal_alignment": 0.8,
        "action_efficiency": 0.8,
        "risk_signal": 0.0,
        "progress_rate": 0.8,
        "reason": reason,
        "suggestion": "",
    }


def check_trajectory(goal: str, events_so_far: list[dict], intent: str = "") -> dict:
    """
    Rubric scoring with adversarial framing. Computes progress_rate locally.
    Never raises — returns safe pass dict on failure.

    progress_rate = 0.5*ga + 0.3*ae + 0.2*(1 - risk_signal)
    """
    if not events_so_far:
        return _safe_pass_result("No events to evaluate")

    recent = events_so_far[-10:]
    steps_summary = json.dumps([_event_step_str(e) for e in recent], indent=2)
    intent_line = f"Agent's current inferred intent: {intent}\n\n" if intent else ""

    prompt = (
        f"You are evaluating a web agent's trajectory.\n"
        f"Goal: {goal}\n"
        f"Last {len(recent)} actions:\n{steps_summary}\n\n"
        f"{intent_line}"
        "IMPORTANT: Assume the agent has made at least one mistake. "
        "Your job is to find evidence of drift or failure — not to confirm things are going well. "
        "Score conservatively. If in doubt, score lower.\n\n"
        "Score the trajectory on three dimensions. Each score is 0.0 to 1.0.\n\n"
        "GOAL_ALIGNMENT (0.0–1.0):\n"
        "  1.0 = every action directly serves the goal\n"
        "  0.5 = agent is partially on track but drifting\n"
        "  0.0 = agent is clearly pursuing the wrong objective\n\n"
        "ACTION_EFFICIENCY (0.0–1.0):\n"
        "  1.0 = direct, minimal steps toward goal\n"
        "  0.5 = some redundant or exploratory actions\n"
        "  0.0 = stuck in loops, backtracking repeatedly\n\n"
        "RISK_SIGNAL (0.0–1.0):\n"
        "  0.0 = clean navigation, no blockers\n"
        "  0.5 = possible auth wall or redirect detected\n"
        "  1.0 = confirmed blocker: login wall, CAPTCHA, dead end\n\n"
        "Respond with JSON only (no markdown) matching this shape:\n"
        '{"goal_alignment": float, "action_efficiency": float, "risk_signal": float, '
        '"reason": "<one sentence>", "suggestion": "<one sentence>"}'
    )

    try:
        rubric = parse_structured(
            _client,
            model=MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
            response_model=TrajectoryRubric,
        )
        ga = float(rubric.goal_alignment)
        ae = float(rubric.action_efficiency)
        rs = float(rubric.risk_signal)
        progress_rate = (
            PROGRESS_WEIGHTS["goal_alignment"] * ga
            + PROGRESS_WEIGHTS["action_efficiency"] * ae
            + PROGRESS_WEIGHTS["risk_signal"] * (1.0 - rs)
        )
        return {
            "goal_alignment": round(ga, 3),
            "action_efficiency": round(ae, 3),
            "risk_signal": round(rs, 3),
            "progress_rate": round(progress_rate, 3),
            "reason": rubric.reason,
            "suggestion": rubric.suggestion,
        }
    except Exception as e:
        logging.warning("[validator] check_trajectory failed: %s", e)
        return _safe_pass_result("Validator call failed — defaulting to pass")


def detect_deterministic_signals(events: list[dict]) -> dict:
    """
    Loop + irreversibility detection, zero LLM calls. Never raises.
    """
    _safe = {"loop": False, "irreversible": False, "reason": ""}
    if not events:
        return _safe
    try:
        recent_actions = [_event_step_str(e) for e in events[-3:]]
        loop_detected = (
            len(recent_actions) == 3
            and len(set(recent_actions)) == 1
            and recent_actions[0] != ""
        )
        irreversible_detected = False
        irreversible_reason = ""
        for event in events[-3:]:
            action_str = _event_step_str(event).lower()
            matched = IRREVERSIBLE_KEYWORDS & set(action_str.split())
            if matched:
                irreversible_detected = True
                irreversible_reason = f"Irreversible keyword detected: {', '.join(matched)}"
                break
        reasons = []
        if loop_detected:
            reasons.append(f"3 identical consecutive actions: '{recent_actions[0]}'")
        if irreversible_reason:
            reasons.append(irreversible_reason)
        return {
            "loop": loop_detected,
            "irreversible": irreversible_detected,
            "reason": "; ".join(reasons),
        }
    except Exception:
        return _safe


def infer_intent(events: list[dict], domain: str) -> str:
    """Rolling 5-event intent phrase. Returns \"\" on failure — never raises."""
    if not events:
        return ""
    recent = events[-5:]
    steps_summary = json.dumps([_event_step_str(e) for e in recent], indent=2)
    prompt = (
        f"A web agent is navigating {domain}.\n"
        f"Last 5 actions:\n{steps_summary}\n\n"
        "In 3–7 words, what is the agent currently trying to do?\n"
        "Examples: 'navigating to pricing section', 'dismissing cookie modal', "
        "'stuck in authentication loop', 'extracting plan feature list'\n"
        "Return JSON only: {\"phrase\": \"<short phrase, no trailing punctuation>\"}"
    )
    try:
        out = parse_structured(
            _client,
            model=MODEL,
            max_tokens=60,
            messages=[{"role": "user", "content": prompt}],
            response_model=IntentPhrase,
        )
        return out.phrase.strip()
    except Exception as e:
        logging.warning("[validator] infer_intent failed: %s", e)
        return ""


def generate_critique(goal: str, events: list[dict], check_result: dict, domain: str = "") -> str:
    """
    Reflexion-style critique for replan. Never raises; always non-empty str.
    Optionally accepts domain to enrich diagnosis with inferred intent.
    """
    if not events:
        return f"Previous attempt had no recorded actions. Retry goal: {goal}"

    # Enrich diagnosis with rolling intent if domain is provided.
    current_intent = infer_intent(events, domain) if domain else ""

    early, late = events[:5], events[-5:]
    trajectory_summary = json.dumps(
        {
            "first_5_actions": [_event_step_str(e) for e in early],
            "last_5_actions": [_event_step_str(e) for e in late],
            "total_steps": len(events),
        },
        indent=2,
    )
    diagnosis = (
        f"goal_alignment={check_result.get('goal_alignment', '?')}, "
        f"action_efficiency={check_result.get('action_efficiency', '?')}, "
        f"risk_signal={check_result.get('risk_signal', '?')}, "
        f"progress_rate={check_result.get('progress_rate', '?')}"
    )
    intent_line = f"Agent's last inferred intent: {current_intent}\n" if current_intent else ""
    prompt = (
        f"A web agent failed to complete this goal: {goal}\n\n"
        f"Trajectory summary:\n{trajectory_summary}\n\n"
        f"Evaluation scores: {diagnosis}\n"
        f"Validator diagnosis: {check_result.get('reason', 'unknown')}\n"
        f"{intent_line}\n"
        "Write a short Reflexion critique (2–3 sentences) for the replanned attempt:\n"
        "1. What went wrong (specific — name the page, action, or pattern)\n"
        "2. What the next attempt should explicitly avoid\n"
        "3. One concrete alternative strategy to try\n\n"
        "Format: Start with 'Previous attempt failed because:' and write in plain English.\n"
        "Do not use bullet points. Do not use markdown. Max 60 words.\n"
        'Return JSON only: {"critique": "<your critique text>"}'
    )
    fallback_critique = (
        f"Previous attempt failed because: {check_result.get('reason', 'trajectory deviated from goal')}. "
        f"Avoid: {check_result.get('suggestion', 'repeating the same navigation path')}."
    )
    try:
        out = parse_structured(
            _client,
            model=MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
            response_model=CritiqueText,
        )
        text = out.critique.strip()
        return text if text else fallback_critique
    except Exception as e:
        logging.warning("[validator] generate_critique failed: %s", e)
        return fallback_critique


def compress_goal(original_goal: str, briefing: str, critique: str) -> str:
    """
    Two-sentence structured handoff for replan. Never raises.
    """
    fallback = f"{critique} Original goal: {original_goal}".strip()
    if not original_goal.strip():
        return fallback if fallback else "Complete the user's web task directly and efficiently."

    briefing_section = (
        f"Memory briefing from prior runs:\n{briefing}\n\n" if briefing.strip() else ""
    )
    prompt = (
        f"You are preparing a structured goal for a web agent's retry attempt.\n\n"
        f"Original goal:\n{original_goal}\n\n"
        f"{briefing_section}"
        f"Reflexion critique (what went wrong and what to avoid):\n{critique}\n\n"
        "Write a structured goal using exactly these three labeled lines:\n"
        "OBJECTIVE: [the specific task to accomplish, max 20 words]\n"
        "AVOID: [what the previous attempt did wrong, max 15 words]\n"
        "APPROACH: [concrete alternative strategy to try, max 15 words]\n\n"
        "Rules:\n"
        "- Use exactly these three labels in this order\n"
        "- No preamble, no markdown, no extra lines\n"
        "- Be specific: name the page, section, or pattern\n"
        "Return JSON only: {\"goal\": \"OBJECTIVE: ...\\nAVOID: ...\\nAPPROACH: ...\"}"
    )
    try:
        out = parse_structured(
            _client,
            model=MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
            response_model=CompressedGoal,
        )
        text = out.goal.strip()
        return text if text else fallback
    except Exception as e:
        logging.warning("[validator] compress_goal failed: %s", e)
        return fallback
