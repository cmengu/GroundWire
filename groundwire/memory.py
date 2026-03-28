import json
from pathlib import Path

import anthropic

MEMORY_DIR = Path(".groundwire_memory")
MEMORY_DIR.mkdir(exist_ok=True)

ANTHROPIC_MODEL = "claude-sonnet-4-6"


def _domain_path(domain: str) -> Path:
    safe = domain.replace(":", "_").replace("/", "_")
    return MEMORY_DIR / f"{safe}.json"


def recall(domain: str) -> str:
    """
    Returns a plain-English briefing string about known site quirks.
    Returns empty string if no memory exists for this domain.
    """
    path = _domain_path(domain)
    if not path.exists():
        return ""
    data = json.loads(path.read_text())
    quirks = data.get("quirks", [])
    if not quirks:
        return ""
    lines = [f"Known site behaviour for {domain}:"]
    lines += [f"  - {q}" for q in quirks]
    return "\n".join(lines)


def write(domain: str, new_quirks: list[str]) -> None:
    """Merge new quirks into existing memory for this domain (set union — no duplicates)."""
    path = _domain_path(domain)
    data = json.loads(path.read_text()) if path.exists() else {"quirks": []}
    existing = set(data["quirks"])
    merged = list(existing | set(new_quirks))
    data["quirks"] = merged
    path.write_text(json.dumps(data, indent=2))


def extract_quirks(events: list[dict], domain: str) -> list[str]:
    """
    Ask Claude to extract site-specific quirks from the first 20 agent events.
    Returns a list of short plain-English strings.
    """
    if not events:
        return []

    client = anthropic.Anthropic()
    event_summary = json.dumps(events[:20], indent=2)

    msg = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=400,
        messages=[
            {
                "role": "user",
                "content": (
                    f"These are the first events from a web agent navigating {domain}.\n"
                    "Extract a list of site-specific quirks discovered "
                    "(cookie modals, auth walls, lazy loads, anti-bot behaviour, pagination quirks).\n"
                    "Return ONLY a JSON array of short strings, no preamble, no markdown.\n"
                    f'Example: ["Cookie consent modal on load", "Auth wall after page 2"]\n\n'
                    f"Events:\n{event_summary}"
                ),
            }
        ],
    )

    raw = msg.content[0].text.strip()
    try:
        quirks = json.loads(raw)
        return [q for q in quirks if isinstance(q, str)]
    except json.JSONDecodeError:
        return []
