# core.py
"""
Groundwire core — Facade over TinyFish.

Public interface:
    run(url, goal, validate_every=..., guardrails=...) -> list[dict]

Internal (frozen after Phase 1 Step 1.3 — body must not change without explicit review):
    _stream_tinyfish(url, goal) -> list[dict]
"""
import json
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from guardrails import GuardrailStack
from memory import consolidate, extract_quirks, log_run, recall, write
from validator import check_trajectory

_here = Path(__file__).resolve().parent
load_dotenv(_here / ".env")
load_dotenv(_here.parent / ".env")

TINYFISH_URL = "https://agent.tinyfish.ai/v1/automation/run-sse"


def _stream_tinyfish(url: str, goal: str) -> list[dict]:
    """
    Pure HTTP layer. POSTs to TinyFish, collects all SSE events, returns them as a list.
    FROZEN after Step 1.3 — only timeout/SSE handling; always call through run().
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
) -> list[dict]:
    """
    Memory: recall → stream → extract/write → log_run → consolidate.
    Optional: trajectory validation (every N events) and guardrail stack.
    """
    if guardrails:
        guardrails.pre_run(url, goal)

    domain = urlparse(url).netloc

    briefing = recall(domain)
    if briefing:
        for line in briefing.splitlines():
            print(f"[memory] {line}")
        enriched_goal = f"{briefing}\n\n{goal}"
    else:
        print(f"[memory] No prior memory for {domain} — cold start")
        enriched_goal = goal

    raw_events = _stream_tinyfish(url, enriched_goal)

    events = []
    replanned = False
    for event in raw_events:
        events.append(event)

        if validate_every > 0 and len(events) % validate_every == 0 and not replanned:
            check = check_trajectory(goal, events)
            if not check["on_track"] and check["confidence"] < 0.65:
                print(f"[validator] ⚠️  Deviation at step {len(events)}: {check['reason']}")
                print(f"[validator] Replanning: {check['suggestion']}")
                replanned = True
                corrected = (
                    f"Original goal: {goal}\n\n"
                    f"Correction: {check['suggestion']}\n\n"
                    f"Context: agent ran {len(events)} steps before deviation was detected."
                )
                return run(url, corrected, validate_every=0, guardrails=guardrails)
            print(f"[validator] ✓ Step {len(events)} on track (confidence: {check['confidence']:.2f})")

    print(f"[core] Run complete — {len(events)} events received")

    quirks = extract_quirks(events, domain)
    if quirks:
        write(domain, quirks)
        print(f"[memory] Confidence updated for {len(quirks)} quirk(s): {quirks}")
    else:
        print(f"[memory] No new quirks extracted for {domain}")

    log_run(domain, goal, events, success=_infer_run_success(events))
    print("[memory] Run logged — episodic history updated")

    if consolidate(domain):
        print(f"[memory] ✦ Semantic profile updated for {domain}")

    if guardrails:
        result_str = json.dumps(events) if events else ""
        scrubbed = guardrails.post_run(result_str, events)
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
    events = run(target_url, target_goal)
    rprint(f"[green]✓ Received {len(events)} events[/green]")
    rprint("[dim]Last 3 events:[/dim]")
    for e in events[-3:]:
        rprint(f"  {e}")
