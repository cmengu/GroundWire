"""
shared_memory.py — Supabase-backed cross-agent memory layer for GroundWire.

Provides network-effect site intelligence: quirks discovered by any agent
on any machine are promoted to a shared store once they cross the confidence
and confirmation thresholds. All functions degrade to safe no-ops when
SUPABASE_URL / SUPABASE_KEY are absent or the supabase package is not installed.

Public API
----------
get_shared_briefing(domain)                              -> str
promote_if_ready(domain, quirk, confidence)              -> None
record_episode(domain, run_id, steps, success, quirks)   -> None
record_antibot_event(domain, run_id, block_type, note)   -> None
record_resolution(domain, block_type, resolved, config)  -> None
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# Local promotion threshold lives in core.py as _SHARED_PROMOTE_THRESHOLD.
# Cross-agent confirmed_count >= 2 is enforced in get_shared_briefing.


def _client():
    """
    Return a live Supabase client or None.
    Returns None when URL/key unset, supabase not installed, or constructor fails.
    """
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()
    if not url or not key:
        return None
    try:
        from supabase import create_client  # noqa: PLC0415

        return create_client(url, key)
    except ImportError:
        log.warning(
            "shared_memory: supabase package not installed; shared layer disabled. "
            "Run: pip install supabase>=2.0.0"
        )
        return None
    except Exception as exc:
        log.warning("shared_memory: could not create Supabase client: %s", exc)
        return None


def get_shared_briefing(domain: str) -> str:
    """
    Top-5 quirks with confirmed_count >= 2 for this domain, formatted for TinyFish goals.
    """
    client = _client()
    if client is None:
        return ""
    try:
        result = (
            client.table("domain_quirks")
            .select("quirk, confidence, confirmed_count")
            .eq("domain", domain)
            .gte("confidence", 0.5)
            .gte("confirmed_count", 2)
            .order("confirmed_count", desc=True)
            .order("confidence", desc=True)
            .limit(5)
            .execute()
        )
        rows = result.data
        if not rows:
            return ""
        lines = [
            f"SHARED SITE MEMORY ({r['confirmed_count']} agents, {r['confidence']:.1f}x confidence): {r['quirk']}"
            for r in rows
        ]
        return "\n".join(lines) + "\n\n"
    except Exception as exc:
        log.warning("shared_memory.get_shared_briefing failed for %s: %s", domain, exc)
        return ""


def promote_if_ready(
    domain: str,
    quirk: str,
    confidence: float,
) -> None:
    """Call upsert_quirk RPC; caller filters by local confidence threshold."""
    client = _client()
    if client is None:
        return
    try:
        client.rpc(
            "upsert_quirk",
            {
                "p_domain": domain,
                "p_quirk": quirk,
                "p_confidence": confidence,
                "p_source": "groundwire",
            },
        ).execute()
    except Exception as exc:
        log.warning(
            "shared_memory.promote_if_ready failed for %s / %r: %s", domain, quirk, exc
        )


def record_episode(
    domain: str,
    run_id: Optional[str],
    steps: Optional[int],
    success: bool,
    quirks: list[str],
) -> None:
    """Append one row to run_episodes for adversarial / episodic history."""
    client = _client()
    if client is None:
        return
    try:
        client.table("run_episodes").insert(
            {
                "domain": domain,
                "run_id": run_id,
                "steps": steps,
                "success": success,
                "observed_quirks": quirks,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
    except Exception as exc:
        log.warning("shared_memory.record_episode failed for %s: %s", domain, exc)


def record_antibot_event(
    domain: str,
    run_id: Optional[str],
    block_type: str,
    note: str,
) -> None:
    client = _client()
    if client is None:
        return
    try:
        client.table("antibot_events").insert(
            {
                "domain": domain,
                "run_id": run_id,
                "block_type": block_type,
                "note": note,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
    except Exception as exc:
        log.warning("shared_memory.record_antibot_event failed for %s: %s", domain, exc)


def record_resolution(
    domain: str,
    block_type: str,
    resolved: bool,
    config: dict,
) -> None:
    client = _client()
    if client is None:
        return
    try:
        client.table("antibot_events").insert(
            {
                "domain": domain,
                "block_type": block_type,
                "resolved": resolved,
                "resolution_config": config,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()
    except Exception as exc:
        log.warning("shared_memory.record_resolution failed for %s: %s", domain, exc)
