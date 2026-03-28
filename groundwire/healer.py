# groundwire/healer.py
"""
SelfHealer — Hypothesis → Sandbox → Commit flow for GroundWire.

When the trajectory validator confirms drift (DRIFT_STREAK_REQUIRED consecutive
low-score checkpoints), the healer fires before the replan:

  1. Generate a site-behaviour hypothesis explaining the stall (Claude Haiku).
  2. Test the hypothesis in a real TinyFish sync run (the "sandbox").
  3. If the sandbox succeeds: commit the quirk to local memory + bump confidence.
  4. Return {"healed": True, "hypothesis": {...}} so client.py can prefix the
     replanned goal with the confirmed fix.

The healer is stateless between calls — all mutable state is local to
on_deviation_detected(). Module-level _client is reused for connection pooling.

Integration: called from client.py:GroundWire.run() in the replan block (Step 10).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import anthropic
import requests
from dotenv import load_dotenv

from llm_utils import parse_structured
from memory import patch_quirk, write
from schemas import HypothesisResult

_here = Path(__file__).resolve().parent
load_dotenv(_here / ".env")
load_dotenv(_here.parent / ".env")

_claude = anthropic.Anthropic()

# TinyFish sync endpoint — returns a single JSON response (no SSE streaming).
TINYFISH_SYNC_URL = "https://agent.tinyfish.ai/v1/automation/run"
MODEL = "claude-haiku-4-5"
SANDBOX_TIMEOUT_S = 90
MAX_HYPOTHESIS_ATTEMPTS = 2


class SelfHealer:
    """
    Hypothesis → Sandbox (real TinyFish run) → Commit flow.

    Stateless: no instance variables modified between calls.
    One shared anthropic.Anthropic client is reused via module-level _claude.
    """

    def on_deviation_detected(
        self,
        domain: str,
        goal: str,
        trajectory_so_far: list[str],
        deviation_step: str,
        url: str = "",
    ) -> dict:
        """
        Entry point called by client.py when drift is confirmed.

        Args:
            domain:             Netloc of the target site (e.g. "stripe.com").
            goal:               Original task goal passed to GroundWire.run().
            trajectory_so_far:  Last N action strings from state.events.
            deviation_step:     Human-readable reason for the deviation (from check["reason"]).

        Returns:
            {"healed": False}  — no confirmed fix found.
            {"healed": True, "hypothesis": HypothesisResult-like dict}
                               — confirmed fix; caller should prefix replanned goal.
        """
        api_key = os.getenv("TINYFISH_API_KEY", "")
        if not api_key:
            logging.warning("[healer] TINYFISH_API_KEY not set — healer disabled")
            return {"healed": False}

        for attempt in range(MAX_HYPOTHESIS_ATTEMPTS):
            logging.info("[healer] Hypothesis attempt %d/%d for %s", attempt + 1, MAX_HYPOTHESIS_ATTEMPTS, domain)

            hypothesis = self._generate_hypothesis(domain, goal, trajectory_so_far, deviation_step)
            if hypothesis is None:
                continue

            logging.info("[healer] Hypothesis: %s", hypothesis.quirk)
            confirmed = self._sandbox_test(api_key, domain, goal, hypothesis, url=url)

            if confirmed:
                # Commit: bump confidence on the matching local quirk (if it exists).
                # write() upserts the quirk text; patch_quirk boosts if already present.
                try:
                    write(domain, [hypothesis.quirk])
                    patch_quirk(domain, hypothesis.quirk, +0.15)
                except Exception as e:
                    logging.warning("[healer] Memory commit failed (non-fatal): %s", e)

                return {
                    "healed": True,
                    "hypothesis": {
                        "quirk": hypothesis.quirk,
                        "suggested_goal_prefix": hypothesis.suggested_goal_prefix,
                        "confidence": hypothesis.confidence,
                    },
                }

        return {"healed": False}

    def _generate_hypothesis(
        self,
        domain: str,
        goal: str,
        trajectory: list[str],
        deviation_step: str,
    ) -> Optional[HypothesisResult]:
        """
        Ask Claude Haiku for a site-behaviour hypothesis explaining the stall.
        Returns None on any failure — caller retries up to MAX_HYPOTHESIS_ATTEMPTS.
        """
        steps_summary = json.dumps(trajectory[-15:], indent=2)
        prompt = (
            f"A web agent was navigating {domain} to achieve:\n\"{goal}\"\n\n"
            f"It stalled at step: \"{deviation_step}\"\n\n"
            f"Last actions taken:\n{steps_summary}\n\n"
            "Hypothesise ONE site-specific behaviour that could explain the stall "
            "(e.g. a cookie modal, a lazy-loaded section, a login wall, a rate limit).\n\n"
            "Return JSON only:\n"
            "{\n"
            "  \"quirk\": \"<one sentence: what site behaviour is causing the stall>\",\n"
            "  \"suggested_goal_prefix\": \"<short imperative instruction to resolve it, e.g. 'Accept the cookie modal first.'>\",\n"
            "  \"confidence\": <float 0.0-1.0>\n"
            "}"
        )
        try:
            result = parse_structured(
                _claude,
                model=MODEL,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
                response_model=HypothesisResult,
            )
            return result
        except Exception as e:
            logging.warning("[healer] _generate_hypothesis failed: %s", e)
            return None

    def _sandbox_test(
        self,
        api_key: str,
        domain: str,
        original_goal: str,
        hypothesis: HypothesisResult,
        url: str = "",
    ) -> bool:
        """
        Fire a TinyFish SYNC run with the hypothesis prefix prepended to the goal.
        Returns True if the run completes without error (status != FAILED).

        Uses the sync endpoint (/v1/automation/run) — no SSE polling required.
        Times out after SANDBOX_TIMEOUT_S seconds; returns False on timeout.
        """
        # Build a minimal sandbox goal: hypothesis prefix + original goal.
        sandbox_goal = f"{hypothesis.suggested_goal_prefix}\n\n{original_goal}"
        # Use the original URL so the sandbox tests the actual page, not just the root domain.
        # Fallback to root domain only when no URL was provided.
        sandbox_url = url if url else f"https://{domain}"

        try:
            resp = requests.post(
                TINYFISH_SYNC_URL,
                headers={
                    "X-API-Key": api_key,
                    "Content-Type": "application/json",
                },
                json={"url": sandbox_url, "goal": sandbox_goal},
                timeout=SANDBOX_TIMEOUT_S,
            )
            # Non-2xx = TinyFish rejected the request outright.
            if not resp.ok:
                logging.warning("[healer] Sandbox request failed: HTTP %d", resp.status_code)
                return False

            data = resp.json()
            status = data.get("status", "")
            # COMPLETED → hypothesis likely valid. FAILED or missing → not confirmed.
            confirmed = status == "COMPLETED"
            logging.info("[healer] Sandbox result: status=%s → confirmed=%s", status, confirmed)
            return confirmed
        except requests.exceptions.Timeout:
            logging.warning("[healer] Sandbox timed out after %ds", SANDBOX_TIMEOUT_S)
            return False
        except Exception as e:
            logging.warning("[healer] Sandbox test exception: %s", e)
            return False
