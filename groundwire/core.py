# core.py
"""
Groundwire core — Facade over TinyFish.

Public interface:
    run(url, goal, validate_every=..., guardrails=...) -> list[dict]

Internal (frozen after Phase 1 Step 1.3 — do not change without review):
    _stream_tinyfish(url, goal) -> list[dict]
"""
import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from guardrails import GuardrailStack, _noop_stack
from memory import consolidate, extract_quirks, log_run, recall, write
from validator import (
    DRIFT_STREAK_REQUIRED,
    DRIFT_THRESHOLD,
    check_trajectory,
    compress_goal,
    detect_deterministic_signals,
    generate_critique,
    infer_intent,
)

_here = Path(__file__).resolve().parent
load_dotenv(_here / ".env")
load_dotenv(_here.parent / ".env")

TINYFISH_URL = "https://agent.tinyfish.ai/v1/automation/run-sse"

# Hard cap on replan attempts (Phase 2). Visible orchestration policy.
MAX_REPLANS = 1


def _stream_tinyfish(url: str, goal: str) -> list[dict]:
    """
    Pure HTTP layer. POSTs to TinyFish, collects all SSE events, returns them as a list.
    FROZEN after Step 1.3 — only timeout/SSE handling; always call through run() for live validation.
    """
    resp = requests.post(
        TINYFISH_URL,
        headers={
            "X-API-Key": os.getenv("TINYFISH_API_KEY"),
            "Content-Type": "application/json",
        },
        json={"url": url, "goal": goal},
        stream=True,
        timeout=180,
    )
    resp.raise_for_status()

    events = []
    for raw_line in resp.iter_lines():
        if raw_line:
            line = raw_line.decode("utf-8")
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
    return events


def _infer_run_success(events: list[dict]) -> bool:
    """True if TinyFish reported COMPLETED; else True when no COMPLETE event (best-effort)."""
    for e in reversed(events):
        if e.get("type") == "COMPLETE":
            return e.get("status") == "COMPLETED"
    return True


def run(
    url: str,
    goal: str,
    validate_every: int = 5,
    guardrails: Optional[GuardrailStack] = None,
    _score_curve: Optional[list] = None,
    _depth: int = 0,
    _run_id: Optional[str] = None,
    _spans: Optional[list] = None,
) -> list[dict]:
    """
    Memory recall → TinyFish stream with live validation (Phase 2) → memory write → log → consolidate.
    Validation order each checkpoint: deterministic guard → intent phrase → rubric LLM.
    """
    stack = guardrails if guardrails is not None else _noop_stack()
    stack.pre_run(url, goal)

    domain = urlparse(url).netloc
    score_curve: list = _score_curve if _score_curve is not None else []
    run_id = _run_id or str(uuid.uuid4())
    spans: list = _spans if _spans is not None else []
    if _depth == 0:
        spans.append(
            {
                "name": "run_begin",
                "ts": time.time(),
                "run_id": run_id,
                "domain": domain,
                "url": url,
            }
        )
    drift_streak = 0
    visited_urls: dict[str, int] = {}

    briefing = recall(domain)
    if briefing:
        for line in briefing.splitlines():
            print(f"[memory] {line}")
        enriched_goal = f"{briefing}\n\n{goal}"
    else:
        print(f"[memory] No prior memory for {domain} — cold start")
        enriched_goal = goal

    # Live stream (duplicated from _stream_tinyfish pattern — _stream_tinyfish body unchanged)
    resp = requests.post(
        TINYFISH_URL,
        headers={
            "X-API-Key": os.getenv("TINYFISH_API_KEY"),
            "Content-Type": "application/json",
        },
        json={"url": url, "goal": enriched_goal},
        stream=True,
        timeout=180,
    )
    resp.raise_for_status()

    events: list[dict] = []

    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8")
        if not line.startswith("data: "):
            continue
        try:
            event = json.loads(line[6:])
        except json.JSONDecodeError:
            continue

        events.append(event)

        # Semantic loop detection: flag URLs visited more than twice.
        event_url = event.get("url")
        if event_url:
            visited_urls[event_url] = visited_urls.get(event_url, 0) + 1
            if visited_urls[event_url] > 2:
                print(f"[validator] ⚠  Semantic loop: '{event_url}' visited {visited_urls[event_url]}x")
                drift_streak += 1

        if validate_every > 0 and len(events) % validate_every == 0:
            det = detect_deterministic_signals(events)
            if det["loop"]:
                print(f"[validator] ⚡ Loop detected (deterministic): {det['reason']}")
                drift_streak += 1
            if det["irreversible"]:
                print(f"[validator] ⚠  Irreversible action detected: {det['reason']}")

            intent = infer_intent(events, domain)
            if intent:
                print(f"[validator] Intent: {intent}")

            check = check_trajectory(goal, events)
            score_curve.append(check["progress_rate"])
            print(
                f"[validator] step {len(events):>3} | "
                f"progress={check['progress_rate']:.2f} | "
                f"align={check['goal_alignment']:.2f} | "
                f"eff={check['action_efficiency']:.2f} | "
                f"risk={check['risk_signal']:.2f}"
            )

            if check["progress_rate"] < DRIFT_THRESHOLD or det["loop"]:
                if check["progress_rate"] < DRIFT_THRESHOLD and not det["loop"]:
                    drift_streak += 1
                print(f"[validator] ⚠  Drift signal ({drift_streak}/{DRIFT_STREAK_REQUIRED}): {check['reason']}")

                if drift_streak >= DRIFT_STREAK_REQUIRED and _depth < MAX_REPLANS:
                    print("[validator] ✗  Drift confirmed — generating Reflexion critique")
                    critique = generate_critique(goal, events, check, domain)
                    print(f"[validator] Critique: {critique}")
                    score_curve.append("REPLAN")
                    spans.append(
                        {
                            "name": "replan",
                            "ts": time.time(),
                            "depth": _depth,
                            "step": len(events),
                            "progress_rate": check.get("progress_rate"),
                        }
                    )

                    quirks = extract_quirks(events, domain)
                    if quirks:
                        write(domain, quirks)
                    log_run(domain, goal, events, success=False)
                    consolidate(domain)

                    replanned_goal = compress_goal(goal, briefing or "", critique)
                    print(f"[validator] Compressed replanned goal: {replanned_goal[:120]}...")
                    print(f"[validator] Replanning (attempt {_depth + 1}/{MAX_REPLANS})\n")
                    return run(
                        url,
                        replanned_goal,
                        validate_every,
                        guardrails,
                        _score_curve=score_curve,
                        _depth=_depth + 1,
                        _run_id=run_id,
                        _spans=spans,
                    )

                if drift_streak >= DRIFT_STREAK_REQUIRED and _depth >= MAX_REPLANS:
                    print(
                        f"[validator] ✗  Drift confirmed but MAX_REPLANS={MAX_REPLANS} "
                        "reached — continuing without replan"
                    )
            else:
                if drift_streak > 0:
                    print(f"[validator] ✓  Drift streak cleared (was {drift_streak})")
                drift_streak = 0

    print(f"[core] Run complete — {len(events)} events received")

    quirks = extract_quirks(events, domain)
    if quirks:
        write(domain, quirks)
        print(f"[memory] Confidence updated for {len(quirks)} quirk(s)")
    else:
        print(f"[memory] No new quirks extracted for {domain}")

    log_run(domain, goal, events, success=_infer_run_success(events))
    print("[memory] Run logged")

    if consolidate(domain):
        print(f"[memory] ✦ Semantic profile updated for {domain}")

    print(f"\n📊 Score curve: {score_curve}")

    spans.append(
        {
            "name": "run_complete",
            "ts": time.time(),
            "event_count": len(events),
            "replan_count": score_curve.count("REPLAN"),
        }
    )
    events.append(
        {
            "type": "groundwire_meta",
            "run_id": run_id,
            "score_curve": score_curve,
            "replan_count": score_curve.count("REPLAN"),
            "spans": spans,
        }
    )

    result_str = json.dumps(events) if events else ""
    scrubbed = stack.post_run(result_str, events)
    if scrubbed != result_str:
        print("[guardrail] Output scrubbed by post_run rules")

    return events


if __name__ == "__main__":
    import sys

    from rich import print as rprint
    from rich.panel import Panel

    target_url = sys.argv[1] if len(sys.argv) > 1 else "https://news.ycombinator.com"
    target_goal = (
        sys.argv[2] if len(sys.argv) > 2 else "Get the title of the top post"
    )

    rprint(Panel(f"[bold]URL:[/bold] {target_url}\n[bold]Goal:[/bold] {target_goal}"))
    out = run(target_url, target_goal)
    rprint(f"[green]✓ Received {len(out)} events[/green]")
    rprint("[dim]Last 3 events:[/dim]")
    for e in out[-3:]:
        rprint(f"  {e}")
