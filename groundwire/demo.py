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

Usage:
  python demo.py            # live (API keys required)
  python demo.py --dry-run  # mock golden + trials, no network
"""
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich import print as rprint
from rich.panel import Panel
from rich.rule import Rule

_here = Path(__file__).resolve().parent
load_dotenv(_here / ".env")
load_dotenv(_here.parent / ".env")

from evals import SessionRecorder, run_k_trials
from guardrails import ActionBudget, DomainAllowlist, GuardrailStack, PIIScrubber

DRY_RUN = "--dry-run" in sys.argv

# ── Config ────────────────────────────────────────────────────────────────────
TARGET_URL = "https://stripe.com/pricing"
GOAL = "Get all pricing tiers, monthly prices, and top 3 features for each plan"
GOLDEN_SESSION = "stripe-pricing-v1"
K_TRIALS = 3

GUARDRAILS = GuardrailStack(
    [
        DomainAllowlist(["stripe.com"]),
        PIIScrubber(),
        ActionBudget(max_steps=50),
    ]
)


def main() -> None:
    from core import run as _run

    # ── Run 0 — Record the golden session ─────────────────────────────────────
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

    recorder = SessionRecorder()
    if DRY_RUN:
        rprint("  [dim]--dry-run: synthetic golden session (no TinyFish)[/dim]\n")
        golden_events = [
            {"type": "PROGRESS", "purpose": "Navigate pricing"},
            {
                "type": "COMPLETE",
                "status": "COMPLETED",
                "result": {"plans": ["Starter", "Pro"]},
            },
            {
                "type": "groundwire_meta",
                "score_curve": [0.82, 0.88],
                "replan_count": 0,
            },
        ]
    else:
        golden_events = _run(
            TARGET_URL, GOAL, validate_every=5, guardrails=GUARDRAILS
        )
    recorder.record(GOLDEN_SESSION, GOAL, golden_events)

    golden_meta = [e for e in golden_events if e.get("type") == "groundwire_meta"]
    golden_steps = len([e for e in golden_events if e.get("type") != "groundwire_meta"])
    golden_curve = golden_meta[0].get("score_curve", []) if golden_meta else []

    rprint("\n[green]✓ Golden session recorded[/green]")
    rprint(f"  Steps:       {golden_steps}")
    rprint(f"  Score curve: {golden_curve}")
    rprint(f"  Session ID:  {GOLDEN_SESSION}\n")

    # ── Run 1–3 — Scored trials against golden ────────────────────────────────
    rprint(Rule())
    rprint(f"\n[dim]Step 2 of 2 — Running {K_TRIALS} trials against golden...[/dim]")
    rprint("[dim]Memory now active. Briefing prepended. Validator fires every 5 events.[/dim]\n")

    if DRY_RUN:
        import evals as _evals_mod

        _fake = [
            {"type": "PROGRESS"},
            {
                "type": "COMPLETE",
                "status": "COMPLETED",
                "result": {"plans": ["Starter", "Pro"]},
            },
            {"type": "groundwire_meta", "score_curve": [0.85, 0.90], "replan_count": 0},
        ]
        _evals_mod._run_agent = (
            lambda url, goal, validate_every=5, guardrails=None, **_: list(_fake)
        )

        def _dry_score(_self, _session_id: str, new_events: list) -> dict:
            real = [e for e in new_events if e.get("type") != "groundwire_meta"]
            g_steps = 2  # matches synthetic golden in dry-run
            return {
                "ci_status": "PASS",
                "hard_gate_result": {"passed": True, "reason": "all gates green"},
                "faithfulness": 0.95,
                "efficiency": len(real) - g_steps,
                "trajectory": {
                    "deviation_delta": 0,
                    "trajectory_improved": False,
                    "step_delta": len(real) - g_steps,
                    "golden_drift_count": 0,
                    "new_drift_count": 0,
                },
                "notes": "[dry-run] mock faithfulness (no LLM)",
                "failure_tags": [],
            }

        _evals_mod.TrajectoryScorer.score = _dry_score  # type: ignore[method-assign]

    stats = run_k_trials(
        url=TARGET_URL,
        goal=GOAL,
        k=K_TRIALS,
        session_id=GOLDEN_SESSION,
        validate_every=5,
    )

    # ── Final judge summary ───────────────────────────────────────────────────
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


if __name__ == "__main__":
    main()
