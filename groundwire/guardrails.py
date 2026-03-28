# guardrails.py
"""
Groundwire guardrail middleware — composable pre/post execution rules.

Public interface:
    DomainAllowlist(allowed: list[str])   — pre_run: raises if domain not in allowlist
    PIIScrubber()                         — post_run: redacts email and phone from result string
    ActionBudget(max_steps: int)          — post_run: raises if real event count exceeds budget
    GuardrailStack(rules: list)           — orchestrates pre_run() and post_run() across all rules

PIIScrubber._PATTERNS is a class-level constant imported by memory.py and evals.py.
"""
import logging
import re
from urllib.parse import urlparse


class DomainAllowlist:
    """
    Pre-run guard. Raises ValueError if the target URL's domain is not in the allowlist.
    Fail-fast: fires before TinyFish is called — zero API cost on blocked requests.

    Usage:
        DomainAllowlist(["stripe.com", "linkedin.com"])
    """

    def __init__(self, allowed: list[str]):
        self.allowed = allowed

    def pre_run(self, url: str, goal: str) -> None:
        domain = urlparse(url).netloc
        if not any(a in domain for a in self.allowed):
            raise ValueError(
                f"Domain '{domain}' is not in the allowlist: {self.allowed}. "
                "Add it to DomainAllowlist() or change the target URL."
            )


class PIIScrubber:
    """
    Post-run guard. Redacts PII from a result string before it reaches the caller.
    _PATTERNS is a class-level constant — importable by memory.py and evals.py
    without instantiating this class.

    Usage:
        scrubbed = PIIScrubber().post_run(result_str, events)
    """

    _PATTERNS: dict[str, str] = {
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "phone": r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
    }

    def post_run(self, result: str, events: list[dict]) -> str:
        """
        Redact all PII matches from the result string.
        Returns the cleaned string. Prints a line for each redaction — visible in demo.
        Never raises.
        """
        text = str(result)
        try:
            for pii_type, pattern in self._PATTERNS.items():
                matches = re.findall(pattern, text)
                for match in matches:
                    text = text.replace(match, f"[{pii_type.upper()}_REDACTED]")
                    logging.debug("[guardrails] Redacted %s", pii_type)
        except Exception:
            pass
        return text


class ActionBudget:
    """
    Post-run guard. Raises RuntimeError if the number of real events exceeds max_steps.
    'Real events' excludes groundwire_meta and HEARTBEAT events.

    Usage:
        ActionBudget(max_steps=50)
    """

    def __init__(self, max_steps: int = 50):
        self.max_steps = max_steps

    def post_run(self, result: str, events: list[dict]) -> str:
        real = [
            e for e in events
            if e.get("type") not in ("groundwire_meta", "HEARTBEAT")
        ]
        if len(real) > self.max_steps:
            raise RuntimeError(
                f"[guardrails] Action budget exceeded: {len(real)} steps > {self.max_steps} max. "
                "Increase ActionBudget(max_steps=N) or investigate agent loop."
            )
        return result


class GuardrailStack:
    """
    Composite orchestrator. Holds a list of rule objects and delegates
    pre_run() and post_run() to each rule that implements the method.

    pre_run()  — fires before TinyFish. Raises on first rule failure.
    post_run() — fires after TinyFish. Passes result through each rule in order.
                 Returns the (possibly modified) result string.

    Usage:
        stack = GuardrailStack([
            DomainAllowlist(["stripe.com"]),
            PIIScrubber(),
            ActionBudget(max_steps=50),
        ])
        stack.pre_run(url, goal)
        result = stack.post_run(result_str, events)
    """

    def __init__(self, rules: list):
        self.rules = rules

    def pre_run(self, url: str, goal: str) -> None:
        """Run all pre_run checks. Raises on first failure."""
        for rule in self.rules:
            if hasattr(rule, "pre_run"):
                rule.pre_run(url, goal)

    def post_run(self, result: str, events: list[dict]) -> str:
        """
        Run all post_run checks in order.
        Each rule receives the (possibly modified) result from the previous rule.
        Returns the final cleaned result string.
        """
        for rule in self.rules:
            if hasattr(rule, "post_run"):
                result = rule.post_run(result, events)
        return result


def _noop_stack() -> "GuardrailStack":
    """Return an empty GuardrailStack that passes everything through. Used as default in run()."""
    return GuardrailStack([])
