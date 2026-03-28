# groundwire/__init__.py
"""
Groundwire — reliability middleware for web agents.

Drop-in wrapper around any TinyFish call:

    import groundwire
    events = groundwire.run("https://example.com", "your goal")

The validator fires every 5 events on the live SSE stream, memory writes
per-domain quirks after each run, and guardrails enforce domain, PII, and
action budget policy.

Requires TINYFISH_API_KEY and ANTHROPIC_API_KEY in env or .env file.
"""
import sys
from pathlib import Path

# Support both `import groundwire` (installed) and running scripts directly
# from the groundwire/ directory (e.g. python demo.py).
sys.path.insert(0, str(Path(__file__).parent))

from core import run, run_naked  # noqa: E402
from guardrails import (  # noqa: E402
    ActionBudget,
    DomainAllowlist,
    GuardrailStack,
    PIIScrubber,
    _noop_stack,
)

__all__ = [
    "run",
    "run_naked",
    "GuardrailStack",
    "DomainAllowlist",
    "PIIScrubber",
    "ActionBudget",
    "_noop_stack",
]
