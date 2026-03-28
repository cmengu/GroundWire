import json
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from guardrails import GuardrailStack
from memory import extract_quirks, recall, write
from validator import check_trajectory

_here = Path(__file__).resolve().parent
load_dotenv(_here / ".env")
load_dotenv(_here.parent / ".env")

TINYFISH_URL = "https://agent.tinyfish.ai/v1/automation/run-sse"


def _stream_tinyfish(url: str, goal: str) -> list[dict]:
    """Raw SSE stream → list of event dicts."""
    resp = requests.post(
        TINYFISH_URL,
        headers={
            "X-API-Key": os.getenv("TINYFISH_API_KEY"),
            "Content-Type": "application/json",
        },
        json={"url": url, "goal": goal},
        stream=True,
        timeout=120,
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


def run(
    url: str,
    goal: str,
    validate_every: int = 5,
    guardrails: Optional[GuardrailStack] = None,
) -> list[dict]:
    """
    Main entry point with memory, trajectory validation, and optional guardrails.
    validate_every: check trajectory every N events. Set to 0 to disable.
    """
    if guardrails:
        guardrails.pre_run(url, goal)

    domain = urlparse(url).netloc

    briefing = recall(domain)
    enriched_goal = f"{briefing}\n\n{goal}" if briefing else goal
    if briefing:
        print(f"[memory] Briefing loaded for {domain}")

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

    quirks = extract_quirks(events, domain)
    if quirks:
        write(domain, quirks)
        print(f"[memory] Wrote {len(quirks)} quirks for {domain}")

    if guardrails:
        result_str = json.dumps(events) if events else ""
        scrubbed = guardrails.post_run(result_str, events)
        if scrubbed != result_str:
            print("[guardrail] Output scrubbed by post_run rules")

    return events


if __name__ == "__main__":
    import sys

    from rich import print as rprint

    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://news.ycombinator.com"
    test_goal = (
        sys.argv[2] if len(sys.argv) > 2 else "Get the title of the top post"
    )

    print(f"Running: {test_goal}")
    print(f"On: {test_url}\n")
    result = run(test_url, test_goal)
    rprint(f"[green]✓ Received {len(result)} events[/green]")
    rprint(result[-3:])
