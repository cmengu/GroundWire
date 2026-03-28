# groundwire/client.py
"""
GroundWire public client — Facade over TinyFish HTTP + SSE.

Usage:
    gw = GroundWire(tinyfish_api_key="...", anthropic_api_key="...")
    # OR
    gw = GroundWire.from_env()

    result_events = gw.run(url="https://example.com", goal="Get the price")

Feature flags:
    gw.run(..., validate=False, memory=False)  # zero-overhead baseline (replaces run_naked)

All params TinyFish accepts (browser_profile etc.) will be accepted as **tinyfish_kwargs
once a TinyFish Python SDK is available. Currently ignored (raw HTTP only).

Returns list[dict] identical shape to core.run() — backward compatible with evals.py.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from guardrails import GuardrailStack, _noop_stack
from hardener import AdversarialHardener
from healer import SelfHealer
from memory import consolidate, extract_quirks, log_run, recall, write
from openai_validator import DUAL_VALIDATE_THRESHOLD, dual_validate
from shared_memory import get_shared_briefing, promote_if_ready, record_episode
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
MAX_REPLANS = 1

# Local confidence needed before Supabase promotion (≈2+ sightings on this machine).
_SHARED_PROMOTE_THRESHOLD = 1.5

# Module-level singletons — stateless; shared across all GroundWire.run() calls.
# Created once at import time to reuse the Anthropic HTTP connection pool.
_healer = SelfHealer()
_hardener = AdversarialHardener()


def _infer_run_success(events: list[dict]) -> bool:
    """True if TinyFish reported COMPLETED; best-effort True if no COMPLETE event."""
    for e in reversed(events):
        if e.get("type") == "COMPLETE":
            return e.get("status") == "COMPLETED"
    return True


def _read_local_confidence(domain: str) -> dict[str, float]:
    """
    Read per-quirk confidence from .groundwire_memory/<domain>.json (memory.py format).
    Returns {} on error — never raises. Does not import memory.py to avoid coupling.
    """
    safe = domain.replace(":", "_").replace("/", "_")
    path = Path(".groundwire_memory") / f"{safe}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            q["text"]: float(q.get("confidence", 1.0))
            for q in data.get("quirks", [])
            if isinstance(q, dict) and "text" in q
        }
    except Exception:
        return {}


def _sync_quirks_to_shared(
    domain: str,
    quirks: list[str],
    run_id: str | None,
    success: bool,
    events: list[dict],
) -> None:
    """Promote high-confidence quirks via Supabase RPC; log episode. No-op if unconfigured."""
    local_confidences = _read_local_confidence(domain)
    for quirk in quirks:
        local_conf = local_confidences.get(quirk, 1.0)
        if local_conf >= _SHARED_PROMOTE_THRESHOLD:
            promote_if_ready(
                domain=domain,
                quirk=quirk,
                confidence=local_conf,
            )
    record_episode(
        domain=domain,
        run_id=run_id,
        steps=len(events),
        success=success,
        quirks=quirks,
    )


@dataclass
class _RunState:
    """
    Mutable per-run state shared between gw.run() and _on_progress_hook().
    One instance per run() call — never stored on self, so concurrent calls are safe.
    """

    goal: str
    domain: str
    validate_every: int
    validate: bool
    memory: bool
    depth: int
    briefing: str = ""
    events: list = field(default_factory=list)
    drift_streak: int = 0
    visited_urls: dict = field(default_factory=dict)
    score_curve: list = field(default_factory=list)
    spans: list = field(default_factory=list)
    llm_call_count: int = 0
    should_replan: bool = False
    replan_goal: str = ""
    # Memento checkpoint: saved at every validate_every gate.
    # On replan, completed-step count is injected into compress_goal briefing
    # so the agent continues from where it left off rather than restarting from page 1.
    checkpoint: dict = field(default_factory=dict)
    captcha_detected: bool = False


class GroundWire:
    """
    Drop-in replacement for calling TinyFish directly.
    One import, one object, zero required config — all three features fire automatically.

    "TinyFish gives you the agent. GroundWire gives you the SLA."
    """

    def __init__(self, tinyfish_api_key: str, anthropic_api_key: str):
        self._tinyfish_api_key = tinyfish_api_key
        self._anthropic_api_key = anthropic_api_key
        # Push keys into env so all downstream modules (validator, memory, healer, hardener)
        # pick them up via os.getenv. setdefault means .env file values always win.
        if tinyfish_api_key:
            os.environ.setdefault("TINYFISH_API_KEY", tinyfish_api_key)
        if anthropic_api_key:
            os.environ.setdefault("ANTHROPIC_API_KEY", anthropic_api_key)

    @classmethod
    def from_env(cls) -> "GroundWire":
        """Construct from TINYFISH_API_KEY and ANTHROPIC_API_KEY environment variables."""
        return cls(
            tinyfish_api_key=os.getenv("TINYFISH_API_KEY", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        )

    def run(
        self,
        url: str,
        goal: str,
        validate: bool = True,
        memory: bool = True,
        validate_every: int = 5,
        guardrails: Optional[GuardrailStack] = None,
        _score_curve: Optional[list] = None,
        _depth: int = 0,
        _run_id: Optional[str] = None,
        _spans: Optional[list] = None,
        _llm_call_count: int = 0,
        _is_trial: bool = False,
        **tinyfish_kwargs,
    ) -> list[dict]:
        """
        Memory recall → TinyFish SSE stream with live validation → memory write → log → consolidate.
        Validation fires on every PROGRESS event via _on_progress_hook().
        Returns list[dict] with groundwire_meta appended — identical shape to core.run().

        Feature flags:
          validate=False  skips all validation (deterministic + LLM). Memory still active.
          memory=False    skips recall, write, log_run, consolidate. Prints [naked] prefix.
        Both False = run_naked() equivalent.
        """
        stack = guardrails if guardrails is not None else _noop_stack()
        stack.pre_run(url, goal)

        domain = urlparse(url).netloc
        run_id = _run_id or str(uuid.uuid4())
        score_curve = _score_curve if _score_curve is not None else []
        spans = _spans if _spans is not None else []

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

        enriched_briefing = ""
        if memory:
            briefing = recall(domain)
            shared_briefing = get_shared_briefing(domain)
            enriched_briefing = (briefing + shared_briefing).strip()
            if enriched_briefing:
                for line in enriched_briefing.splitlines():
                    print(f"[memory] {line}")
                enriched_goal = f"{enriched_briefing}\n\n{goal}"
            else:
                print(f"[memory] No prior memory for {domain} — cold start")
                enriched_goal = goal
        else:
            print("[naked] Cold start — no memory, no validator, no guardrails")
            print(f"[naked] Domain: {domain}")
            enriched_goal = goal

        state = _RunState(
            goal=goal,
            domain=domain,
            validate_every=validate_every,
            validate=validate,
            memory=memory,
            depth=_depth,
            briefing=enriched_briefing,
            score_curve=score_curve,
            spans=spans,
            llm_call_count=_llm_call_count,
        )

        # TinyFish SSE stream
        _stream_deadline = time.time() + 300
        try:
            resp = requests.post(
                TINYFISH_URL,
                headers={
                    "X-API-Key": self._tinyfish_api_key,
                    "Content-Type": "application/json",
                },
                json={"url": url, "goal": enriched_goal, **tinyfish_kwargs},
                stream=True,
                timeout=180,
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"TinyFish request failed: {e}") from e

        for raw_line in resp.iter_lines():
            if time.time() > _stream_deadline:
                logging.warning("[core] Stream deadline (300s) exceeded — stopping stream")
                break
            if not raw_line:
                continue
            line = raw_line.decode("utf-8")
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue

            # STREAMING_URL event — print live preview URL and skip to next event
            if event.get("type") == "STREAMING_URL":
                live_url = event.get("streaming_url") or event.get("url", "")
                if live_url:
                    print(f"[groundwire] 🔴 Live preview: {live_url}")
                continue

            state.events.append(event)
            self._on_progress_hook(event, state)

            # CAPTCHA flag set inside hook — break loop so run() can close resp
            # and return the human-review meta event.
            if state.captcha_detected:
                break

            if state.should_replan:
                break

        # CAPTCHA early return: write partial memory, close connection, return.
        if state.captcha_detected:
            try:
                resp.close()
            except Exception:
                pass
            if memory:
                quirks = extract_quirks(state.events, domain)
                if quirks:
                    write(domain, quirks)
                log_run(domain, goal, state.events, success=False, is_trial=_is_trial)
            state.events.append({
                "type": "groundwire_meta",
                "captcha_detected": True,
                "action_required": "human_review",
                "run_id": run_id,
                "step": len(state.events),
                "score_curve": state.score_curve,
                "replan_count": state.score_curve.count("REPLAN"),
                "llm_call_count": state.llm_call_count,
                "spans": spans,
            })
            result_str = json.dumps(state.events) if state.events else ""
            scrubbed = stack.post_run(result_str, state.events)
            if scrubbed != result_str:
                print("[guardrail] Output scrubbed by post_run rules")
            return state.events

        # Handle replan
        if state.should_replan and _depth < MAX_REPLANS:
            if memory:
                quirks = extract_quirks(state.events, domain)
                if quirks:
                    write(domain, quirks)
                log_run(domain, goal, state.events, success=False, is_trial=_is_trial)
                consolidate(domain)
                _sync_quirks_to_shared(
                    domain, quirks, run_id=None, success=False, events=state.events
                )

            state.score_curve.append("REPLAN")
            spans.append(
                {
                    "name": "replan",
                    "ts": time.time(),
                    "depth": _depth,
                    "step": len(state.events),
                    "progress_rate": None,
                }
            )
            print(f"[validator] Replanning (attempt {_depth + 1}/{MAX_REPLANS})\n")

            # SelfHealer fires before replan to generate a site-behaviour hypothesis.
            # If the sandbox run confirms it, the confirmed fix is prepended to
            # state.replan_goal so the next run starts with a verified context.
            print(f"[healer] Firing hypothesis sandbox for {domain}...")
            _trajectory_so_far = [
                str(e.get("purpose") or e.get("action") or e.get("type") or "")
                for e in state.events[-20:]
            ]
            _heal_result = _healer.on_deviation_detected(
                domain=domain,
                goal=goal,
                trajectory_so_far=_trajectory_so_far,
                deviation_step="drift confirmed",
                url=url,
            )
            if _heal_result.get("healed"):
                _confirmed = _heal_result["hypothesis"]
                _prefix = f"CONFIRMED FIX: {_confirmed.get('suggested_goal_prefix', '')}\n\n"
                print(f"[healer] ✓ Confirmed: {_confirmed.get('quirk', '')}")
                state.replan_goal = _prefix + state.replan_goal
            else:
                print("[healer] Hypothesis unconfirmed — replanning with existing context")

            return self.run(
                url,
                state.replan_goal,
                validate=validate,
                memory=memory,
                validate_every=validate_every,
                guardrails=guardrails,
                _score_curve=state.score_curve,
                _depth=_depth + 1,
                _run_id=run_id,
                _spans=spans,
                _llm_call_count=state.llm_call_count,
                _is_trial=_is_trial,
                **tinyfish_kwargs,
            )

        if state.should_replan and _depth >= MAX_REPLANS:
            print(
                f"[validator] ✗  Drift confirmed but MAX_REPLANS={MAX_REPLANS} "
                "reached — continuing without replan"
            )

        print(f"[core] Run complete — {len(state.events)} events received")

        # AdversarialHardener post-stream check — zero-LLM keyword scan.
        # Fires only when block signals are detected; no-op on clean runs.
        # If auto-recovered, state.events is replaced with retry events so the
        # memory write below records the successful trajectory.
        if _hardener.is_blocked(state.events):
            print(f"[hardener] 🛡  Block detected — attempting auto-harden and retry for {domain}")
            _harden_result = _hardener.auto_harden_and_retry(
                domain=domain,
                url=url,
                goal=goal,
                run_id=run_id,
                events=state.events,
            )
            if _harden_result.get("auto_recovered"):
                print(f"[hardener] ✓ Auto-recovered from {_harden_result.get('block_type')} block")
                retry_evts = _harden_result.get("retry_events", [])
                if retry_evts:
                    state.events = retry_evts
            elif _harden_result.get("action_required") == "human_review":
                print(f"[hardener] 🔒 Hard block ({_harden_result.get('block_type')}) — human review required")
            else:
                print("[hardener] ✗ Auto-hardening attempted but could not recover")

        if memory:
            quirks = extract_quirks(state.events, domain)
            if quirks:
                write(domain, quirks)
                print(f"[memory] Confidence updated for {len(quirks)} quirk(s)")
            else:
                print(f"[memory] No new quirks extracted for {domain}")

            log_run(
                domain,
                goal,
                state.events,
                success=_infer_run_success(state.events),
                is_trial=_is_trial,
            )
            print("[memory] Run logged")

            if consolidate(domain):
                state.llm_call_count += 1
                print(f"[memory] ✦ Semantic profile updated for {domain}")

            _sync_quirks_to_shared(
                domain, quirks, run_id=run_id, success=True, events=state.events
            )

        if not memory:
            real_count = len(
                [e for e in state.events if e.get("type") != "groundwire_meta"]
            )
            print(f"[naked] Run complete — {real_count} real events received")

        print(f"\n📊 Score curve: {state.score_curve}")

        spans.append(
            {
                "name": "run_complete",
                "ts": time.time(),
                "event_count": len(state.events),
                "replan_count": state.score_curve.count("REPLAN"),
            }
        )
        state.events.append(
            {
                "type": "groundwire_meta",
                "run_id": run_id,
                "score_curve": state.score_curve,
                "replan_count": state.score_curve.count("REPLAN"),
                "llm_call_count": state.llm_call_count,
                "spans": spans,
            }
        )

        result_str = json.dumps(state.events) if state.events else ""
        scrubbed = stack.post_run(result_str, state.events)
        if scrubbed != result_str:
            print("[guardrail] Output scrubbed by post_run rules")

        return state.events

    def _on_progress_hook(self, event: dict, state: _RunState) -> None:
        """
        Called from the SSE loop on every event appended to state.events.
        Handles semantic loop detection + validation checkpoints.
        Sets state.should_replan + state.replan_goal when drift is confirmed.
        No-op if state.should_replan is already True (replan already decided).
        """
        if state.should_replan:
            return

        # Semantic loop detection: flag URLs visited more than twice
        event_url = event.get("url")
        if event_url:
            state.visited_urls[event_url] = state.visited_urls.get(event_url, 0) + 1
            if state.visited_urls[event_url] > 2:
                print(
                    f"[validator] ⚠  Semantic loop: '{event_url}' visited "
                    f"{state.visited_urls[event_url]}x"
                )
                state.drift_streak += 1

        if not state.validate or state.validate_every <= 0:
            return
        if len(state.events) % state.validate_every != 0:
            return

        # Save checkpoint at every validate_every gate (Memento pattern).
        # On replan, completed-step count is injected into compress_goal briefing.
        state.checkpoint = {
            "events_so_far": list(state.events),
            "step": len(state.events),
            "briefing": state.briefing,
            "drift_streak": state.drift_streak,
        }

        # CAPTCHA escalation: check before loop handling so CAPTCHA stalls route
        # to human review instead of triggering a replan cycle.
        det = detect_deterministic_signals(state.events)
        if det.get("captcha_detected"):
            print(f"[validator] 🔒 CAPTCHA detected — human intervention required: {det.get('reason', '')}")
            print("[validator]    Open the TinyFish streaming URL to observe and intervene.")
            state.captcha_detected = True
            return

        if det["loop"]:
            print(f"[validator] ⚡ Loop detected (deterministic): {det['reason']}")
            state.drift_streak += 1
        if det["irreversible"]:
            print(f"[validator] ⚠  Irreversible action detected: {det['reason']}")

        # LLM gates
        state.llm_call_count += 1
        intent = infer_intent(state.events, state.domain)
        if intent:
            print(f"[validator] Intent: {intent}")

        state.llm_call_count += 1
        check = check_trajectory(state.goal, state.events, intent=intent)

        # Dual-model validation: GPT-4o second opinion when Claude score drops below threshold
        if check["progress_rate"] < DUAL_VALIDATE_THRESHOLD:
            check["progress_rate"] = dual_validate(
                state.goal, state.events, check["progress_rate"], intent=intent
            )

        state.score_curve.append(check["progress_rate"])
        print(
            f"[validator] step {len(state.events):>3} | "
            f"progress={check['progress_rate']:.2f} | "
            f"align={check['goal_alignment']:.2f} | "
            f"eff={check['action_efficiency']:.2f} | "
            f"risk={check['risk_signal']:.2f}"
        )

        if check["progress_rate"] < DRIFT_THRESHOLD or det["loop"]:
            if check["progress_rate"] < DRIFT_THRESHOLD and not det["loop"]:
                state.drift_streak += 1
            print(
                f"[validator] ⚠  Drift signal ({state.drift_streak}/{DRIFT_STREAK_REQUIRED}): "
                f"{check['reason']}"
            )

            if state.drift_streak >= DRIFT_STREAK_REQUIRED and state.depth < MAX_REPLANS:
                print("[validator] ✗  Drift confirmed — generating Reflexion critique")
                state.llm_call_count += 2
                critique = generate_critique(state.goal, state.events, check, state.domain)
                print(f"[validator] Critique: {critique}")

                state.llm_call_count += 1
                # Inject completed-steps context so agent resumes from checkpoint,
                # not from the beginning of the page navigation.
                completed_summary = ""
                if state.checkpoint.get("events_so_far"):
                    n = len(state.checkpoint["events_so_far"])
                    completed_summary = (
                        f"\nCOMPLETED STEPS (do not repeat): {n} steps already taken. "
                        "Continue from where the previous attempt left off — "
                        "do not re-navigate to the start page."
                    )
                replanned = compress_goal(state.goal, state.briefing + completed_summary, critique)
                print(f"[validator] Compressed replanned goal: {replanned[:120]}...")
                state.replan_goal = replanned
                state.should_replan = True
                return  # SSE loop checks this flag after hook returns

            if state.drift_streak >= DRIFT_STREAK_REQUIRED and state.depth >= MAX_REPLANS:
                print(
                    f"[validator] ✗  Drift confirmed but MAX_REPLANS={MAX_REPLANS} "
                    "reached — continuing without replan"
                )
        else:
            if state.drift_streak > 0:
                print(f"[validator] ✓  Drift streak cleared (was {state.drift_streak})")
            state.drift_streak = 0
