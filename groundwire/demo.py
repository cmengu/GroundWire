"""
Groundwire demo — run this to show judges the value of the reliability layer.

What it does:
1. Runs a naked TinyFish call (no Groundwire) — records as golden baseline
2. Runs a Groundwire-wrapped call — shows memory, validation, guardrails
3. Prints a scored comparison

Usage: python demo.py
"""
from dotenv import load_dotenv
from pathlib import Path

_here = Path(__file__).resolve().parent
load_dotenv(_here / ".env")
load_dotenv(_here.parent / ".env")

from rich import print as rprint
from rich.panel import Panel
from rich.table import Table

import core
from evals import record, score
from guardrails import ActionBudget, DomainAllowlist, GuardrailStack, PIIScrubber

TARGET_URL = "https://stripe.com/pricing"
GOAL = "Extract all pricing tiers, their monthly cost, and the features included in each tier"
SESSION_ID = "stripe-pricing-demo"

GUARDRAILS = GuardrailStack(
    [
        DomainAllowlist(["stripe.com"]),
        PIIScrubber(),
        ActionBudget(max_steps=60),
    ]
)


def run_naked():
    rprint(Panel("[bold red]RUN 1: Naked TinyFish (no Groundwire)[/bold red]"))
    events = core._stream_tinyfish(TARGET_URL, GOAL)
    rprint(f"  Steps taken: [red]{len(events)}[/red]")
    record(SESSION_ID, GOAL, events)
    return events


def run_wrapped():
    rprint(Panel("[bold green]RUN 2: Groundwire-wrapped[/bold green]"))
    events = core.run(
        url=TARGET_URL,
        goal=GOAL,
        validate_every=5,
        guardrails=GUARDRAILS,
    )
    rprint(f"  Steps taken: [green]{len(events)}[/green]")
    return events


def print_scorecard(naked_events, wrapped_events):
    scorecard = score(SESSION_ID, wrapped_events)

    table = Table(title="📊 Groundwire Scorecard", show_header=True)
    table.add_column("Dimension", style="bold")
    table.add_column("Result")

    table.add_row("Faithfulness", f"{scorecard['faithfulness']:.0%}")
    table.add_row("Efficiency", scorecard["efficiency"])
    table.add_row("Notes", scorecard["notes"])
    table.add_row("Naked steps", str(len(naked_events)))
    table.add_row("Wrapped steps", str(len(wrapped_events)))

    rprint(table)
    rprint(
        "\n[bold]One line:[/bold] We didn't build another agent. "
        "We built the layer that makes every agent trustworthy."
    )


if __name__ == "__main__":
    naked = run_naked()
    wrapped = run_wrapped()
    print_scorecard(naked, wrapped)
