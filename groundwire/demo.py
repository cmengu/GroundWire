# demo.py — Groundwire pitch script
# Run with: python demo.py
# Total runtime: ~90s (1 golden run + 3 trial runs + Claude API calls)
"""
THE 60-SECOND DEMO SCRIPT

Narrative arc:
  [0–10s]  Run 1 — naked TinyFish on Stripe. Cold start. No memory.
            Validator fires. Score curve prints. Memory learns.
  [10–40s] Run 2, 3, 4 — same goal, Groundwire fully active.
            Memory briefing prepended. Score curve improves.
            Pass@3 and faithfulness distribution print.
  [40–60s] Judge line delivered verbally over the scorecard:

  "We didn't build another agent. We built the layer that makes every agent
   trustworthy — memory that compounds, a validator that intercepts, and an eval
   harness that separates recording from scoring. Pass@3 is your reliability
   guarantee. A single score is a lucky guess."
"""
from pathlib import Path

from dotenv import load_dotenv
from rich import print as rprint
from rich.panel import Panel
from rich.rule import Rule

_here = Path(__file__).resolve().parent
load_dotenv(_here / ".env")
load_dotenv(_here.parent / ".env")

from evals import SessionRecorder, run_k_trials

# ── Config ────────────────────────────────────────────────────────────────────
TARGET_URL = "https://stripe.com/pricing"
GOAL = "Get all pricing tiers, monthly prices, and top 3 features for each plan"
GOLDEN_SESSION = "stripe-pricing-v1"
K_TRIALS = 3

# ── Run 0 — Record the golden session ─────────────────────────────────────────
rprint(Rule("[bold]Groundwire Demo[/bold]"))
rprint(
    Panel(
        f"[bold]URL:[/bold]  {TARGET_URL}\n"
        f"[bold]Goal:[/bold] {GOAL}\n"
        f"[bold]Session:[/bold] {GOLDEN_SESSION}",
        title="Config",
    )
)

rprint("\n[dim]Step 1 of 2 — Recording golden session (Run 0)...[/dim]")
rprint("[dim]This run primes memory. Cold start — no briefing prepended.[/dim]\n")

from core import run as _run

recorder = SessionRecorder()
golden_events = _run(TARGET_URL, GOAL, validate_every=5)
recorder.record(GOLDEN_SESSION, GOAL, golden_events)

golden_meta = [e for e in golden_events if e.get("type") == "groundwire_meta"]
golden_steps = len([e for e in golden_events if e.get("type") != "groundwire_meta"])
golden_curve = golden_meta[0].get("score_curve", []) if golden_meta else []

rprint("\n[green]✓ Golden session recorded[/green]")
rprint(f"  Steps:       {golden_steps}")
rprint(f"  Score curve: {golden_curve}")
rprint(f"  Session ID:  {GOLDEN_SESSION}\n")

# ── Run 1–3 — Scored trials against golden ────────────────────────────────────
rprint(Rule())
rprint(f"\n[dim]Step 2 of 2 — Running {K_TRIALS} trials against golden...[/dim]")
rprint("[dim]Memory now active. Briefing prepended. Validator fires every 5 events.[/dim]\n")

stats = run_k_trials(
    url=TARGET_URL,
    goal=GOAL,
    k=K_TRIALS,
    session_id=GOLDEN_SESSION,
    validate_every=5,
)

# ── Final judge summary ────────────────────────────────────────────────────────
rprint(Rule("[bold]Results[/bold]"))
mf = stats["mean_faithfulness"]
mf_str = f"{mf:.2f}" if mf is not None else "n/a"
rprint(
    Panel(
        f"[bold]pass@1:[/bold]  {stats['pass_at_1']}\n"
        f"[bold]pass@{K_TRIALS}:[/bold]  {stats['pass_at_k']}\n"
        f"[bold]Pass rate:[/bold]  {stats['pass_rate']:.0%}  ({stats['passing_count']}/{K_TRIALS})\n"
        f"[bold]Mean faithfulness:[/bold]  {mf_str}\n"
        f"[bold]Mean steps (trials):[/bold]  {stats['mean_steps']:.1f}  vs golden {golden_steps}",
        title="📊 Scorecard",
        border_style="green" if stats["pass_at_k"] else "red",
    )
)

rprint("\n[bold cyan]Judge line:[/bold cyan]")
rprint(
    '"We didn\'t build another agent. We built the layer that makes every agent trustworthy.\n'
    " Memory that compounds, a validator that intercepts mid-run, and an eval harness that\n"
    " separates recording from scoring. Pass@3 is your reliability guarantee — a single score\n"
    ' is a lucky guess."\n'
)
