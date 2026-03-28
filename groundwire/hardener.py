# groundwire/hardener.py
"""
AdversarialHardener — post-stream block detection, classification, and auto-retry.

Flow (called from client.py:GroundWire.run() after SSE stream completes):

  1. is_blocked(events)              — zero-LLM keyword scan on full event list.
  2. classify_block(domain, events)  — Claude Haiku classifies the block type.
  3. record_antibot_event(...)       — log to Supabase via shared_memory.
  4. if escalate_to_human → return with action_required="human_review".
  5. _retry_run(url, goal, profile, proxy) — re-fire TinyFish with escalated config.
  6. record_resolution(...)          — log whether retry succeeded to Supabase.

All TinyFish calls use raw requests.post + SSE (no Python SDK — not installed).
_retry_run duplicates the SSE HTTP pattern from client.py to avoid a circular import
(client.py imports hardener.py; hardener.py cannot import back from client.py).

Integration: called from client.py:GroundWire.run() post-stream (Step 11).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

import anthropic
import requests
from dotenv import load_dotenv

from llm_utils import parse_structured
from schemas import BlockClassification
from shared_memory import record_antibot_event, record_resolution

_here = Path(__file__).resolve().parent
load_dotenv(_here / ".env")
load_dotenv(_here.parent / ".env")

_claude: anthropic.Anthropic | None = None


def _get_anthropic_client() -> anthropic.Anthropic:
    """Lazy singleton — created on first classify_block() call so ANTHROPIC_API_KEY
    set via GroundWire() constructor is honoured before the client is instantiated."""
    global _claude
    if _claude is None:
        _claude = anthropic.Anthropic()
    return _claude


TINYFISH_SSE_URL = "https://agent.tinyfish.ai/v1/automation/run-sse"
MODEL = "claude-haiku-4-5"

# Keywords scanned from event action strings and COMPLETE result payloads.
BLOCK_SIGNATURES = [
    "access denied",
    "403",
    "blocked",
    "cloudflare",
    "datadome",
    "checking your browser",
    "captcha",
    "could not find",
    "access_denied",
    "robot",
    "bot detected",
    "challenge",
]


def _event_action(e: dict) -> str:
    """Extract a lowercased action string from any TinyFish event shape."""
    return str(
        e.get("purpose") or e.get("action") or e.get("description") or e.get("type") or ""
    ).lower()


class AdversarialHardener:
    """
    Post-run block detection, Claude Haiku classification, and escalated auto-retry.
    Stateless — no instance variables modified between calls.
    """

    def __init__(self, anthropic_client: Optional[anthropic.Anthropic] = None):
        # Store injected client for testing; None means use lazy getter at call time.
        self._anthropic_client = anthropic_client

    def is_blocked(self, events: list[dict]) -> bool:
        """
        Detect block from the full event list. Zero LLM calls. Never raises.

        Returns True if:
          - The last COMPLETE event status is FAILED, OR
          - The last COMPLETE event result JSON contains a block keyword, OR
          - No COMPLETE event exists and last 3 actions contain block keywords.
        """
        if not events:
            return False
        try:
            for e in reversed(events):
                if e.get("type") == "COMPLETE":
                    result_str = json.dumps(e.get("result", {})).lower()
                    status = e.get("status", "")
                    if status == "FAILED" or any(sig in result_str for sig in BLOCK_SIGNATURES):
                        return True
                    return False  # COMPLETED with no block signals → clean run

            # No COMPLETE event: check last 3 actions for stall keywords
            last_actions = [_event_action(e) for e in events[-3:]]
            combined = " ".join(last_actions)
            return any(sig in combined for sig in BLOCK_SIGNATURES)
        except Exception:
            return False

    def classify_block(
        self, domain: str, events: list[dict]
    ) -> Optional[BlockClassification]:
        """
        Ask Claude Haiku to classify the block type and recommend a retry config.
        Returns None on failure — caller skips classification and uses 'unknown'.
        """
        last_actions = [_event_action(e) for e in events[-10:]]
        # Include COMPLETE event result if present
        complete_result = ""
        for e in reversed(events):
            if e.get("type") == "COMPLETE":
                complete_result = json.dumps(e.get("result", {}))[:500]
                break

        prompt = (
            f"A web agent was blocked while navigating {domain}.\n\n"
            f"Last 10 actions:\n{json.dumps(last_actions, indent=2)}\n\n"
            f"Final result payload:\n{complete_result or '(no COMPLETE event)'}\n\n"
            "Classify the block and recommend a retry strategy.\n\n"
            "Return JSON only:\n"
            "{\n"
            "  \"block_type\": \"<cloudflare|datadome|captcha|geo_block|rate_limit|login_wall|unknown>\",\n"
            "  \"note\": \"<one sentence: what you detected>\",\n"
            "  \"escalate_to_human\": <true if auto-recovery is not possible>,\n"
            "  \"recommended_profile\": \"<lite|stealth>\",\n"
            "  \"recommended_proxy\": <true if residential proxy advised>\n"
            "}"
        )
        try:
            return parse_structured(
                self._anthropic_client or _get_anthropic_client(),
                model=MODEL,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
                response_model=BlockClassification,
            )
        except Exception as e:
            logging.warning("[hardener] classify_block failed: %s", e)
            return None

    def auto_harden_and_retry(
        self,
        domain: str,
        url: str,
        goal: str,
        run_id: Optional[str],
        events: list[dict],
    ) -> dict:
        """
        Full hardening cycle: classify → log → retry (if auto-recoverable) → log outcome.

        Returns:
            {"hardening_triggered": False, "result": None}
                — is_blocked() returned False (should not normally be called in this case).
            {"hardening_triggered": True, "auto_recovered": False, "block_type": str, "action_required": "human_review", "result": None}
                — escalate_to_human=True; human must intervene.
            {"hardening_triggered": True, "auto_recovered": bool, "block_type": str, "result": dict|None, "retry_events": list}
                — auto-retry was attempted; auto_recovered=True if retry succeeded.
        """
        if not self.is_blocked(events):
            return {"hardening_triggered": False, "result": None}

        classification = self.classify_block(domain, events)
        block_type = classification.block_type if classification else "unknown"
        note = classification.note if classification else "classification failed"

        # Log to Supabase shared memory (no-op if unconfigured).
        record_antibot_event(
            domain=domain,
            run_id=run_id,
            block_type=block_type,
            note=note,
        )

        if not classification or classification.escalate_to_human:
            logging.info("[hardener] Block type '%s' requires human review", block_type)
            return {
                "hardening_triggered": True,
                "auto_recovered": False,
                "block_type": block_type,
                "action_required": "human_review",
                "result": None,
            }

        # Auto-retry with escalated browser profile + optional proxy.
        browser_profile = classification.recommended_profile
        proxy_country = "US" if classification.recommended_proxy else None

        logging.info(
            "[hardener] Auto-retry: profile=%s proxy=%s", browser_profile, proxy_country
        )

        retry_result_events = self._retry_run(url, goal, browser_profile, proxy_country)
        recovered = not self.is_blocked(retry_result_events)

        # Log retry outcome to Supabase.
        record_resolution(
            domain=domain,
            block_type=block_type,
            resolved=recovered,
            config={
                "profile": browser_profile,
                "proxy_country": proxy_country,
            },
        )

        # Extract result payload from the last COMPLETE event in retry events.
        retry_result = None
        for e in reversed(retry_result_events):
            if e.get("type") == "COMPLETE":
                retry_result = e.get("result")
                break

        return {
            "hardening_triggered": True,
            "auto_recovered": recovered,
            "block_type": block_type,
            "result": retry_result,
            "retry_events": retry_result_events,
        }

    def _retry_run(
        self,
        url: str,
        goal: str,
        browser_profile: str,
        proxy_country: Optional[str],
    ) -> list[dict]:
        """
        Fire a TinyFish SSE run with escalated config. Returns collected events.

        Duplicates the SSE HTTP pattern from client.py — cannot import client.py
        due to circular dependency (client.py imports hardener.py).
        Adds browser_profile and proxy_config params unavailable in the shared helper.
        """
        api_key = os.getenv("TINYFISH_API_KEY", "")
        if not api_key:
            logging.warning("[hardener] TINYFISH_API_KEY not set — retry skipped")
            return []

        payload: dict = {"url": url, "goal": goal, "browser_profile": browser_profile}
        if proxy_country:
            payload["proxy_config"] = {"country": proxy_country}

        events: list[dict] = []
        try:
            resp = requests.post(
                TINYFISH_SSE_URL,
                headers={
                    "X-API-Key": api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                stream=True,
                timeout=180,
            )
            resp.raise_for_status()

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
                # Skip STREAMING_URL events — informational only.
                if event.get("type") == "STREAMING_URL":
                    live_url = event.get("streaming_url") or event.get("url", "")
                    if live_url:
                        logging.info("[hardener] 🔴 Retry live preview: %s", live_url)
                    continue
                events.append(event)
                # Stop consuming once the stream signals completion or failure.
                if event.get("type") == "COMPLETE":
                    break

        except requests.exceptions.RequestException as e:
            logging.warning("[hardener] Retry request failed: %s", e)

        return events
