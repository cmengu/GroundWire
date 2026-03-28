import re
from urllib.parse import urlparse


class DomainAllowlist:
    """Blocks runs against domains not in the allowed list."""

    def __init__(self, allowed: list[str]):
        self.allowed = allowed

    def pre_run(self, url: str, goal: str) -> None:
        domain = urlparse(url).netloc
        if not any(a in domain for a in self.allowed):
            raise ValueError(
                f"[guardrail] 🚫 Domain '{domain}' not in allowlist: {self.allowed}"
            )


class PIIScrubber:
    """Redacts PII patterns from string representation of results."""

    PATTERNS = {
        "EMAIL": r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
        "PHONE": r"\b\d{3}[\s.\-]?\d{3}[\s.\-]?\d{4}\b",
        "SSN": r"\b\d{3}-\d{2}-\d{4}\b",
    }

    def post_run(self, result: str) -> str:
        for label, pattern in self.PATTERNS.items():
            matches = re.findall(pattern, result)
            for match in matches:
                result = result.replace(match, f"[{label}_REDACTED]")
                print(f"[guardrail] 🛡️  Redacted {label}: {match[:6]}...")
        return result


class ActionBudget:
    """Raises if the agent used more than max_steps events."""

    def __init__(self, max_steps: int = 50):
        self.max_steps = max_steps

    def post_run(self, events: list[dict]) -> None:
        if len(events) > self.max_steps:
            raise RuntimeError(
                f"[guardrail] 🚫 Action budget exceeded: {len(events)} steps > limit {self.max_steps}"
            )


class GuardrailStack:
    """Composes multiple guardrail rules. Call pre_run() before and post_run() after TinyFish."""

    def __init__(self, rules: list):
        self.rules = rules

    def pre_run(self, url: str, goal: str) -> None:
        for rule in self.rules:
            if hasattr(rule, "pre_run"):
                rule.pre_run(url, goal)

    def post_run(self, result_str: str, events: list[dict]) -> str:
        for rule in self.rules:
            if hasattr(rule, "post_run"):
                ret = (
                    rule.post_run(result_str)
                    if isinstance(rule, PIIScrubber)
                    else rule.post_run(events)
                )
                if ret is not None:
                    result_str = ret
        return result_str
