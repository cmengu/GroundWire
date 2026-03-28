# evals.py — Groundwire eval harness: SessionRecorder, TrajectoryScorer, run_k_trials
"""
Groundwire eval harness — two-class separation of recording from scoring.

Architecture:
  SessionRecorder  — write-only. Records run outputs. Never scores. Never calls Claude.
  TrajectoryScorer — read-only. Scores against a golden session. Fires hard gates first.

Storage: .groundwire_evals/<session_id>.json

Session schema:
  {
    "session_id":    str,
    "goal":          str,
    "timestamp":     float,
    "run_id":        str | None,       # TinyFish run ID — links to audit log
    "streaming_url": str | None,       # TinyFish live preview URL (None until SDK wires it into meta)
    "events":        list[dict],
    "result":        dict,
    "step_count":    int,
    "replan_count":  int,
    "score_curve":   list,
    "failure_tags":  list[str],
    "steps":         list[{"action": str, "timestamp": float|None, "duration": float|None}]
  }

TrajectoryScorer.score() order: _hard_gates → _llm_judge (if gates pass) → _score_trajectory.
"""
import json
import re
import time
from pathlib import Path

from guardrails import GuardrailStack, PIIScrubber as _PIIScrubberRef

import anthropic
from core import run as _run_agent
from llm_utils import parse_structured
from memory import atomic_write_json
from schemas import FaithfulnessScore

_client: anthropic.Anthropic | None = None


def _get_anthropic_client() -> anthropic.Anthropic:
    """Lazy singleton — created on first _llm_judge call so ANTHROPIC_API_KEY set via
    GroundWire() constructor is honoured before the client is instantiated."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client

EVALS_DIR = Path(".groundwire_evals")
EVALS_DIR.mkdir(exist_ok=True)


def _session_path(session_id: str) -> Path:
    """Convert session_id to a safe filename. Strips slashes and colons."""
    safe = session_id.replace("/", "_").replace(":", "_").replace(" ", "_")
    return EVALS_DIR / f"{safe}.json"


def _extract_meta(events: list[dict]) -> dict:
    """
    Extract the groundwire_meta event from the events list.
    Returns {} if no meta event is found — callers handle gracefully.
    """
    for event in reversed(events):
        if event.get("type") == "groundwire_meta":
            return event
    return {}


def _extract_result(events: list[dict]) -> dict:
    """
    Extract the last non-meta TinyFish result event.
    Prefers events with type "COMPLETE" or status "COMPLETED".
    If none, returns {} so hard gates treat incomplete runs as empty result.
    """
    for event in reversed(events):
        if event.get("type") == "groundwire_meta":
            continue
        if event.get("type") == "COMPLETE" or event.get("status") == "COMPLETED":
            return event
    return {}


def _extract_steps(events: list[dict]) -> list[dict]:
    """
    Extract a steps array from the raw event list for sequence diffing.
    Each step is {"action": str, "timestamp": float | None, "duration": float | None}.
    Source: PROGRESS events. "purpose" field is the action string proxy for TinyFish steps.
    timestamp and duration are None until the SDK provides native values on events.
    Returns [] on any error — scorer handles gracefully.
    """
    steps = []
    try:
        for event in events:
            if event.get("type") == "groundwire_meta":
                continue
            if event.get("type") == "PROGRESS":
                steps.append({
                    "action": event.get("purpose", ""),
                    "timestamp": event.get("timestamp", None),
                    "duration": event.get("duration", None),
                })
    except Exception:
        pass
    return steps


class SessionRecorder:
    """
    Write-only component of the eval harness.
    Records run outputs to persistent JSON files.
    Never scores. Never calls Claude. Never modifies existing records.

    Usage:
        recorder = SessionRecorder()
        recorder.record("stripe-v1", goal, events)
    """

    def record(self, session_id: str, goal: str, events: list[dict]) -> None:
        """
        Write a session file for this run.
        If session_id already exists, overwrites silently — caller is responsible
        for using a unique session_id when recording a new golden session.

        Extracts score_curve and replan_count from the groundwire_meta event.
        step_count excludes the groundwire_meta event itself.
        """
        meta = _extract_meta(events)
        result = _extract_result(events)

        real_events = [e for e in events if e.get("type") != "groundwire_meta"]

        failure_tags = self._tag_failure_modes(events, meta)

        data = {
            "session_id": session_id,
            "goal": goal,
            "timestamp": time.time(),
            "run_id": meta.get("run_id", None),
            "streaming_url": meta.get("streaming_url", None),
            "events": events,
            "result": result,
            "step_count": len(real_events),
            "replan_count": meta.get("replan_count", 0),
            "score_curve": meta.get("score_curve", []),
            "failure_tags": failure_tags,
            "steps": _extract_steps(events),
        }

        atomic_write_json(_session_path(session_id), data)

    def _tag_failure_modes(self, events: list[dict], meta: dict) -> list[str]:
        """
        Deterministic failure signal tagging — zero LLM calls.
        Returns a list of short string tags describing observed failure patterns.
        Never raises — returns [] on any error.
        """
        tags = []
        try:
            real_events = [e for e in events if e.get("type") != "groundwire_meta"]
            score_curve = meta.get("score_curve", [])

            if len(real_events) > 40:
                tags.append("budget_pressure")

            if not real_events:
                tags.append("empty_run")

            if meta.get("replan_count", 0) > 0:
                tags.append("replanned")

            drift_count = sum(
                1 for s in score_curve
                if isinstance(s, float) and s < 0.60
            )
            if drift_count >= 2:
                tags.append("high_drift")

            if "REPLAN" in score_curve:
                tags.append("replan_triggered")

        except Exception:
            pass

        return tags


_PII_PATTERNS = _PIIScrubberRef._PATTERNS  # single source of truth — guardrails.PIIScrubber

_STEP_BUDGET = 45


def _lcs_ratio(seq_a: list[str], seq_b: list[str]) -> float:
    """
    Longest-common-subsequence length ratio between two action string sequences.
    Returns 0.0 if either sequence is empty.
    Range: 0.0 (completely different paths) to 1.0 (identical paths).
    O(n*m) — acceptable for ≤45 steps per sequence.
    """
    if not seq_a or not seq_b:
        return 0.0
    n, m = len(seq_a), len(seq_b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if seq_a[i - 1] == seq_b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    lcs_len = dp[n][m]
    return round(lcs_len / max(n, m), 3)


class TrajectoryScorer:
    """
    Read-only component of the eval harness.
    Scores a new run against a previously recorded golden session.
    Never records. Never modifies session files.

    Execution order inside score() is strict:
      1. _hard_gates()   — pure Python, zero LLM calls, always runs
      2. _llm_judge()    — LLM, fires only when hard gates pass
      3. _score_trajectory() — pure Python, uses score_curve from both sessions

    Usage:
        scorer = TrajectoryScorer()
        result = scorer.score("stripe-v1", new_events)
    """

    def _hard_gates(self, events: list[dict], result: dict) -> dict:
        """
        Deterministic CI gates — zero LLM calls, zero network calls.
        Fires before _llm_judge() on every score() call.

        Gates checked (in order):
          1. Empty result — agent returned nothing
          2. Step budget — run exceeded _STEP_BUDGET real events
          3. PII leak — result contains unredacted email or phone number

        Returns {"passed": bool, "reason": str}.
        Never raises.
        """
        try:
            real_events = [e for e in events if e.get("type") != "groundwire_meta"]

            if not result or result == {}:
                return {"passed": False, "reason": "empty result — agent returned nothing"}

            if len(real_events) > _STEP_BUDGET:
                return {
                    "passed": False,
                    "reason": f"step budget exceeded: {len(real_events)} > {_STEP_BUDGET}",
                }

            result_str = str(result)
            for pii_type, pattern in _PII_PATTERNS.items():
                if re.search(pattern, result_str):
                    return {
                        "passed": False,
                        "reason": f"unredacted {pii_type} detected in result — PIIScrubber may not have run",
                    }

            return {"passed": True, "reason": "all gates green"}

        except Exception as exc:
            return {"passed": False, "reason": f"gate check error: {exc}"}

    def _llm_judge(self, golden: dict, new_result: dict, new_events: list[dict]) -> dict:
        """
        Adversarial LLM judge — scores faithfulness of new result vs golden result.
        Called only when _hard_gates() passes.

        Returns {"faithfulness": float, "notes": str}.
        Never raises — returns {"faithfulness": 0.0, "notes": "<error>"} on any failure.
        """
        golden_result_str = json.dumps(golden.get("result", {}))[:600]
        new_result_str = json.dumps(new_result)[:600]
        golden_goal = golden.get("goal", "")

        prompt = (
            f"You are auditing a web agent's output for completeness and accuracy.\n"
            f"Original goal: {golden_goal}\n\n"
            f"Golden result (the reference — treat this as the ground truth):\n{golden_result_str}\n\n"
            f"New result (the one being scored):\n{new_result_str}\n\n"
            "IMPORTANT: Assume the new result is incomplete or inaccurate. "
            "Your job is to find what is missing or wrong — not to confirm it matches. "
            "Score conservatively. If in doubt, score lower.\n\n"
            "Score faithfulness on a 0.0–1.0 scale:\n"
            "  1.0 = new result captures every key piece of information from the golden result\n"
            "  0.7 = new result captures most key pieces but misses some details\n"
            "  0.4 = new result captures roughly half the key information\n"
            "  0.0 = new result is empty, wrong, or completely different\n\n"
            "Respond ONLY in JSON. No preamble. No markdown.\n"
            '{"faithfulness": <float 0.0-1.0>, "notes": "<one sentence: what is missing or different>"}'
        )

        try:
            # Use parse_structured (retry + schema validation) — same pattern as every
            # other LLM call in the codebase. FaithfulnessScore enforces ge=0 le=1.
            result = parse_structured(
                _get_anthropic_client(),
                model="claude-sonnet-4-6",
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}],
                response_model=FaithfulnessScore,
            )
            return {
                "faithfulness": round(float(result.faithfulness), 3),
                "notes": str(result.notes),
            }
        except Exception as exc:
            return {"faithfulness": 0.0, "notes": f"LLM judge failed: {exc}"}

    def _score_trajectory(
        self,
        golden: dict,
        new_score_curve: list,
        step_count: int,
        new_steps: list[dict] | None = None,
    ) -> dict:
        """
        Pure Python trajectory comparison — zero LLM calls.
        Compares score curves AND step-action sequences between golden and new run.

        sequence_diverged: True when common_action_ratio < 0.5 — same score, different path.
        common_action_ratio: LCS ratio of action strings (0.0 = different path; 1.0 = identical).
        """
        golden_curve = golden.get("score_curve", [])
        golden_step_count = golden.get("step_count", 0)

        DRIFT_THRESHOLD = 0.60
        golden_drift = sum(1 for s in golden_curve if isinstance(s, float) and s < DRIFT_THRESHOLD)
        new_drift = sum(1 for s in new_score_curve if isinstance(s, float) and s < DRIFT_THRESHOLD)

        step_delta = step_count - golden_step_count

        golden_actions = [s.get("action", "") for s in golden.get("steps", [])]
        new_actions = [s.get("action", "") for s in (new_steps or [])]
        ratio = _lcs_ratio(golden_actions, new_actions)

        return {
            "deviation_delta": golden_drift - new_drift,
            "trajectory_improved": (golden_drift - new_drift) > 0,
            "step_delta": step_delta,
            "golden_drift_count": golden_drift,
            "new_drift_count": new_drift,
            "common_action_ratio": ratio,
            "sequence_diverged": ratio < 0.5 and bool(golden_actions) and bool(new_actions),
        }

    def score(self, session_id: str, new_events: list[dict]) -> dict:
        """
        Full scorecard for a new run against a recorded golden session.
        Returns a complete scorecard dict. Never raises.
        """
        golden_path = _session_path(session_id)
        if not golden_path.exists():
            return self._fail_scorecard(f"golden session '{session_id}' not found — run record() first")

        try:
            golden = json.loads(golden_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            return self._fail_scorecard(f"golden session unreadable: {exc}")

        new_result = _extract_result(new_events)
        new_meta = _extract_meta(new_events)
        new_score_curve = new_meta.get("score_curve", [])
        real_events = [e for e in new_events if e.get("type") != "groundwire_meta"]

        hard = self._hard_gates(new_events, new_result)

        if hard["passed"]:
            soft = self._llm_judge(golden, new_result, new_events)
        else:
            soft = {"faithfulness": 0.0, "notes": f"Hard gate failed: {hard['reason']}"}

        new_steps = _extract_steps(new_events)
        trajectory = self._score_trajectory(
            golden, new_score_curve, len(real_events), new_steps=new_steps
        )
        efficiency = len(real_events) - golden.get("step_count", 0)

        recorder = SessionRecorder()
        failure_tags = recorder._tag_failure_modes(new_events, new_meta)

        return {
            "ci_status": "PASS" if hard["passed"] else "FAIL",
            "hard_gate_result": hard,
            "faithfulness": soft["faithfulness"],
            "efficiency": efficiency,
            "trajectory": trajectory,
            "notes": soft["notes"],
            "failure_tags": failure_tags,
        }

    def _fail_scorecard(self, reason: str) -> dict:
        """Return a complete failing scorecard when the golden session cannot be loaded."""
        return {
            "ci_status": "FAIL",
            "hard_gate_result": {"passed": False, "reason": reason},
            "faithfulness": 0.0,
            "efficiency": 0,
            "trajectory": {
                "deviation_delta": 0,
                "trajectory_improved": False,
                "step_delta": 0,
                "golden_drift_count": 0,
                "new_drift_count": 0,
                "common_action_ratio": 0.0,
                "sequence_diverged": False,
            },
            "notes": reason,
            "failure_tags": ["missing_golden_session"],
        }


def run_k_trials(
    url: str,
    goal: str,
    k: int = 3,
    session_id: str | None = None,
    validate_every: int = 5,
    guardrails: GuardrailStack | None = None,
) -> dict:
    """
    Run k independent trials and compute pass@k reliability statistics.
    Records each trial with a unique session_id. Scores against golden session_id when set.
    """
    recorder = SessionRecorder()
    scorer = TrajectoryScorer() if session_id else None

    trials = []

    for i in range(k):
        trial_id = (
            f"{session_id}_trial_{i}_{int(time.time())}"
            if session_id
            else f"trial_{i}_{int(time.time())}"
        )

        try:
            events = _run_agent(
                url,
                goal,
                validate_every=validate_every,
                guardrails=guardrails,
                _is_trial=True,
            )
        except Exception as exc:
            events = []
            print(f"[evals] Trial {i} failed to run: {exc}")

        recorder.record(trial_id, goal, events)

        trial_meta = _extract_meta(events)
        trial_score_curve = trial_meta.get("score_curve", [])
        trial_llm_calls = trial_meta.get("llm_call_count", 0)

        trial_result = {
            "trial": i,
            "trial_id": trial_id,
            "steps": len([e for e in events if e.get("type") != "groundwire_meta"]),
            "score_curve": trial_score_curve,
            "llm_calls": trial_llm_calls,
        }

        if scorer and session_id:
            scorecard = scorer.score(session_id, events)
            trial_result["ci_pass"] = scorecard["ci_status"] == "PASS"
            trial_result["faithfulness"] = scorecard["faithfulness"]
            trial_result["efficiency"] = scorecard["efficiency"]
            trial_result["trajectory"] = scorecard["trajectory"]
            trial_result["notes"] = scorecard["notes"]
            trial_result["failure_tags"] = scorecard["failure_tags"]
            trial_result["scorecard"] = scorecard
        else:
            trial_result["ci_pass"] = True
            trial_result["faithfulness"] = None
            trial_result["efficiency"] = None
            trial_result["trajectory"] = None
            trial_result["notes"] = "no golden session — scoring skipped"
            trial_result["failure_tags"] = []
            trial_result["scorecard"] = None

        trials.append(trial_result)
        print(
            f"[evals] Trial {i}: ci={'PASS' if trial_result['ci_pass'] else 'FAIL'} | "
            f"steps={trial_result['steps']} | "
            f"faith={trial_result['faithfulness']}"
        )

    passing = [t for t in trials if t["ci_pass"]]
    pass_rate = len(passing) / k if k > 0 else 0.0

    mean_faithfulness = (
        sum(t["faithfulness"] for t in passing) / len(passing)
        if passing and trials[0]["faithfulness"] is not None
        else None
    )
    mean_steps = sum(t["steps"] for t in trials) / k if k > 0 else 0.0

    stats = {
        "pass_at_1": trials[0]["ci_pass"] if trials else False,
        "pass_at_k": len(passing) > 0,
        "pass_rate": round(pass_rate, 3),
        "k": k,
        "mean_faithfulness": round(mean_faithfulness, 3) if mean_faithfulness is not None else None,
        "mean_steps": round(mean_steps, 1),
        "passing_count": len(passing),
        "trials": trials,
    }

    print(f"\n{'='*55}")
    print(f"📊 Eval Scorecard — {k} trials against '{session_id or 'no golden'}'")
    print(f"{'='*55}")
    print(f"  CI status (trial 0):  {'✅ PASS' if stats['pass_at_1'] else '❌ FAIL'}")
    print(f"  pass@1:               {stats['pass_at_1']}")
    print(f"  pass@{k}:              {stats['pass_at_k']}")
    print(f"  Pass rate:            {stats['pass_rate']:.0%}  ({stats['passing_count']}/{k})")
    if mean_faithfulness is not None:
        print(f"  Mean faithfulness:    {stats['mean_faithfulness']:.2f}")
    print(f"  Mean steps:           {stats['mean_steps']:.1f}")
    print(f"  {'─'*40}")
    for t in trials:
        traj = t.get("trajectory") or {}
        delta_str = f"Δdev={traj.get('deviation_delta', '?')}" if traj else ""
        eff = t.get("efficiency")
        eff_str = f"eff={eff:+d}" if eff is not None else ""
        curve = t.get("score_curve", [])
        curve_str = " ".join(
            f"{s:.2f}" if isinstance(s, float) else str(s) for s in curve
        )
        llm_calls = t.get("llm_calls", 0)
        print(
            f"  Trial {t['trial']}: {'✅' if t['ci_pass'] else '❌'} | "
            f"faith={t['faithfulness']} | "
            f"steps={t['steps']} | "
            f"{eff_str} {delta_str}"
        )
        if curve_str:
            print(f"    Curve: [{curve_str}] | LLM calls: {llm_calls}")
    print(f"{'='*55}\n")

    return stats
