# groundwire/core.py
"""
Groundwire core — backward-compat shim over GroundWire (client.py).

All orchestration logic lives in client.py.
This file exists for backward compatibility with:
  evals.py:  from core import run as _run_agent
  demo.py:   from core import run as _run, run_naked
  __main__:  python core.py <url> <goal>

Do not add logic here. If a caller needs new behaviour, add it to client.py.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from client import GroundWire
from guardrails import GuardrailStack

_here = Path(__file__).resolve().parent
load_dotenv(_here / ".env")
load_dotenv(_here.parent / ".env")


def run(
    url: str,
    goal: str,
    validate_every: int = 5,
    guardrails: Optional[GuardrailStack] = None,
    _score_curve: Optional[list] = None,
    _depth: int = 0,
    _run_id: Optional[str] = None,
    _spans: Optional[list] = None,
    _llm_call_count: int = 0,
    _is_trial: bool = False,
) -> list[dict]:
    """Thin shim — delegates to GroundWire.run(). Preserves all existing call sites."""
    return GroundWire.from_env().run(
        url,
        goal,
        validate=True,
        memory=True,
        validate_every=validate_every,
        guardrails=guardrails,
        _score_curve=_score_curve,
        _depth=_depth,
        _run_id=_run_id,
        _spans=_spans,
        _llm_call_count=_llm_call_count,
        _is_trial=_is_trial,
    )


def run_naked(url: str, goal: str) -> list[dict]:
    """Thin shim — delegates to GroundWire.run(validate=False, memory=False)."""
    return GroundWire.from_env().run(url, goal, validate=False, memory=False)


if __name__ == "__main__":
    from rich import print as rprint
    from rich.panel import Panel

    target_url = sys.argv[1] if len(sys.argv) > 1 else "https://news.ycombinator.com"
    target_goal = sys.argv[2] if len(sys.argv) > 2 else "Get the title of the top post"

    rprint(Panel(f"[bold]URL:[/bold] {target_url}\n[bold]Goal:[/bold] {target_goal}"))
    out = run(target_url, target_goal)
    rprint(f"[green]✓ Received {len(out)} events[/green]")
    for e in out[-3:]:
        rprint(f"  {e}")
