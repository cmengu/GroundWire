# Groundwire — Phase 1 Integrated Plan
## Three-Layer Memory System + Core Facade

**Overall Progress:** `0%` — 0 of 5 steps complete

**Prerequisite:** Phase 0 complete. `groundwire/` exists, venv active, all packages installed, TinyFish SSE schema confirmed.

---

## TLDR

Phase 1 builds `core.py` (the Facade every module plugs into) and `memory.py` (a three-layer memory system: procedural quirks with confidence scoring, episodic run history, and semantic consolidation). After this phase, calling `run("https://stripe.com/pricing", "get pricing tiers")` a second time prints `"stripe.com — 3 runs, memory confidence: high"`, prepends a stratified briefing to the goal, logs what happened, and — every 3 runs — asks Claude to synthesize all episodic history into a one-sentence strategic profile of the domain. The compounding moat starts here. The judge line upgrades from "we save notes" to "we built the three-layer memory architecture from cognitive science — procedural, episodic, and semantic — in a single Python file."

---

## Architecture Overview

**The problem this plan solves:**
`core.py` is 0 bytes. `memory.py` is 0 bytes. Every TinyFish call starts from zero — the same cookie modal is rediscovered on every run, there is no history of what goals succeeded or failed, and there is no higher-level understanding of how a domain behaves. The agent has working memory only (current context window). It has no long-term memory of any kind.

**The patterns applied:**

| Pattern | What it is | Why chosen | What breaks if violated |
|---|---|---|---|
| Facade | `core.py` exposes one `run()` hiding HTTP, SSE, and all module calls | Callers never import `memory.py` directly | Orchestration order breaks; memory writes can be skipped |
| Write-through cache | Every run writes back automatically; caller does nothing extra | Compounding improvement with zero caller effort | If opt-in, most runs skip it and the moat never builds |
| Null Object | `recall()` always returns `str`, never `None` | Caller concatenates directly: `f"{recall()}\n\n{goal}"` | `f"{None}\n\n{goal}"` silently corrupts the goal string |
| Confidence map | Quirks stored as `{text, confidence, last_seen}` dicts, not flat strings | Each re-observation increments confidence; caller gets ranked list | Flat strings treat a quirk seen once the same as one seen 50 times |
| Episodic log | Each run appended to `runs[]` before any consolidation | Preserves raw history for consolidation; never destructive | Without raw history, consolidation has nothing to read |
| Semantic consolidation | Every N runs, Claude reads episodic summaries → writes `semantic_profile` | Abstracts patterns a flat quirk list cannot express | Without consolidation, memory grows as data, never as knowledge |

**What stays unchanged:**
- `validator.py`, `guardrails.py`, `evals.py`, `demo.py` — all 0 bytes. Untouched. They depend on `run(url, goal) -> list[dict]` which this plan defines and freezes.
- `_stream_tinyfish()` — frozen after Step 1.3. No subsequent step touches it.
- TinyFish API — Groundwire is a pure client-side wrapper.

**What this plan adds:**

| File | Responsibility |
|---|---|
| `core.py` | `_stream_tinyfish()`: raw HTTP SSE → list of dicts. `run()`: enriches goal with memory, calls TinyFish, writes all memory layers back. |
| `memory.py` | `_domain_path()`, `_empty_domain_data()`: storage helpers. `recall()`: returns stratified briefing string. `write()`: confidence-scored quirk upsert. `extract_quirks()`: LLM extraction from events. `log_run()`: episodic entry append + run_count increment. `consolidate()`: semantic profile synthesis every N runs. |

**JSON schema — designed once, used throughout:**
```json
{
  "quirks": [
    {"text": "Cookie consent modal on first load", "confidence": 4, "last_seen": 1718000000.0}
  ],
  "runs": [
    {"id": "1718000000", "goal": "Get pricing tiers", "timestamp": 1718000000.0, "step_count": 21, "success": true}
  ],
  "semantic_profile": "Stripe pricing page is reliable but requires dismissing a cookie modal; pricing data loads immediately with no auth wall.",
  "run_count": 4,
  "last_consolidated": 1718000050.0
}
```

**Critical decisions:**

| Decision | Alternative considered | Why alternative rejected |
|---|---|---|
| Quirks as list of dicts `{text, confidence, last_seen}` | Flat `list[str]` (original gameplan) | Flat strings treat every quirk equally; no confidence signal, no decay upgrade path |
| `_empty_domain_data()` helper | Inline `{}` literals in each function | Three functions read/write the same schema; one source of truth prevents drift |
| `log_run()` owns `run_count` increment | `write()` increments it | `write()` is called with 0 quirks on clean runs; run_count would be wrong |
| `consolidate()` triggers every 3 runs | On every run / manual call only | Every run is too expensive; manual call won't fire during demo |
| `consolidate()` reads last 20 episodic summaries | Reads all runs | Unbounded prompt growth; first 20 covers all demo scenarios |
| `recall()` sorts quirks by confidence, takes top 10 | Returns all quirks | Unbounded briefing grows to dominate goal token budget |
| Semantic profile = single sentence | Full paragraph | Sentence fits inside goal naturally; paragraph overwhelms it |

**Known limitations acknowledged:**

| Limitation | Why acceptable now | Upgrade path |
|---|---|---|
| No concurrent write safety | Single-process hackathon build | Swap `path.write_text()` for Postgres upsert behind same `write()` interface |
| No quirk TTL / decay | Stale quirks don't crash anything; they just appear in briefing | Add `last_seen` TTL check in `recall()`: skip quirks not seen in last N runs |
| `extract_quirks()` uses first 20 events only | Covers early navigation where quirks appear most | Tune N or use token-counted truncation post-hackathon |
| `consolidate()` fires every 3 runs | Low threshold is intentional for demo visibility | Raise `CONSOLIDATE_EVERY` to 10–20 for production |
| No episodic success detection | Agent always logged as `success=True` | Parse final TinyFish event for error/completion signal |

---

## Clarification Gate

| Unknown | Required | Source | Blocking | Resolved |
|---|---|---|---|---|
| Exact SSE event action key | Which key holds action description (e.g. `"action"`, `"type"`) | Phase 0 Baseline Snapshot | Steps 1.3, 2.2 | ✅ **`purpose`** on `type: "PROGRESS"` events (live curl 2026-03-28); fall back to `action`, `type`, `description` |
| SSE completion signal | Which event type/value marks the final event | Phase 0 Baseline Snapshot | Step 1.3 | ✅ **`type: "COMPLETE"`** and **`status: "COMPLETED"`**; structured answer in **`result`** |

**If either unknown is unresolved:** Output `[CLARIFICATION NEEDED: SSE event schema not confirmed — re-run Phase 0 curl and capture Baseline Snapshot]` and stop.

---

## Agent Failure Protocol

1. A verification command fails → read the full error output.
2. Cause is unambiguous → make ONE targeted fix → re-run the same verification command.
3. If still failing after one fix → **STOP**. Output full contents of every file modified in this step. Report: (a) command run, (b) full error verbatim, (c) fix attempted, (d) exact state of each modified file, (e) why you cannot proceed.
4. Never attempt a second fix without human instruction.
5. Never modify files not named in the current step.

---

## Pre-Flight — Run Before Any Code Changes

```bash
# Must be run from inside the groundwire/ directory with venv active

# 1. Confirm working directory
pwd  # must end in /groundwire

# 2. Confirm venv active
which python  # must point to groundwire/venv/bin/python

# 3. Confirm all stub files exist and are 0 bytes
wc -c core.py memory.py validator.py guardrails.py evals.py demo.py
# Expected: all 6 files show 0 bytes

# 4. Confirm packages installed
python -c "import requests, anthropic, dotenv, rich; print('OK')"

# 5. Confirm API keys loaded
python -c "
from dotenv import load_dotenv; import os; load_dotenv()
assert os.getenv('TINYFISH_API_KEY'), 'TINYFISH_API_KEY missing'
assert os.getenv('ANTHROPIC_API_KEY'), 'ANTHROPIC_API_KEY missing'
print('Keys OK')
"

# 6. Confirm no stale memory entries
ls .groundwire_memory/ 2>&1  # expected: empty or "No such file or directory"
```

**Baseline Snapshot (agent fills during pre-flight — do not pre-fill):**
```
Working directory confirmed:      (fill when running pre-flight locally)
venv active:                      (fill when running pre-flight locally)
core.py byte count:               (post-scaffold / post-Phase-1)
memory.py byte count:             (post-scaffold / post-Phase-1)
All packages importable:          (fill when running pre-flight locally)
TINYFISH_API_KEY present:         (fill when running pre-flight locally)
ANTHROPIC_API_KEY present:        (fill when running pre-flight locally)

——— Phase 0 Step 1.2 — live TinyFish SSE (curl 2026-03-28, HN goal) ———

SSE event action key (from Ph0):  "purpose"
  • Mid-run narrative lives on events with "type": "PROGRESS" and field "purpose"
    (e.g. "Visit Hacker News to find the top post.").
  • No "action" or "description" fields observed on this sample; "type" alone is the
    event class (STARTED, STREAMING_URL, PROGRESS, COMPLETE, HEARTBEAT), not a step label.
  • validator.py should use: purpose → action → type → description (see implementation).

SSE completion signal (from Ph0): {"type": "COMPLETE", "status": "COMPLETED"}
  • Terminal business outcome is in "result" (object with title/source/description, etc.).
  • Stream may emit "type": "HEARTBEAT" after COMPLETE; _stream_tinyfish keeps reading until close.

Sample lines (abbreviated):
  data: {"type":"STARTED",...}
  data: {"type":"STREAMING_URL",...}
  data: {"type":"PROGRESS","purpose":"Visit Hacker News to find the top post.",...}
  data: {"type":"PROGRESS","purpose":"Extract the title of the top post on Hacker News.",...}
  data: {"type":"COMPLETE","status":"COMPLETED","result":{...}}

Stale memory entries:             (clear before cold-start tests)
```

**Automated checks (all must pass before Step 1.3):**
- [ ] `pwd` ends in `/groundwire`
- [ ] `which python` points to venv
- [ ] `wc -c core.py` returns `0 core.py`
- [ ] `wc -c memory.py` returns `0 memory.py`
- [ ] All packages import without error
- [ ] Both API keys non-empty
- [ ] SSE event action key confirmed from Phase 0
- [ ] No stale `.groundwire_memory/` files

---

## STEPS ANALYSIS

```
Step 1.3 (core.py bare runner)       — Critical (Facade base; all modules wire into run()) — full review — Idempotent: Yes
Step 2.1 (memory.py schema + recall) — Critical (defines JSON schema used by every subsequent step; recall() type contract) — full review — Idempotent: Yes
Step 2.2 (memory.py write + extract) — Critical (confidence scoring schema write; extract_quirks LLM call) — full review — Idempotent: Yes
Step 2.3 (memory.py log + consolidate)— Critical (episodic + semantic layers; consolidate makes LLM call on N-run threshold) — full review — Idempotent: Yes
Step 2.4 (wire all memory into core) — Critical (changes the only public function; all future phases depend on run()) — full review — Idempotent: Yes
```

---

## Tasks

---

### Step 1.3 — Write `core.py` bare stream runner
*Critical: Facade base. `_stream_tinyfish()` is frozen after this step and never modified again.*

**Step Architecture Thinking:**

**Pattern applied:** Facade — `run(url, goal)` is the single public interface. `_stream_tinyfish()` is the pure HTTP layer, private and frozen. No external code ever calls `_stream_tinyfish()` directly.

**Why this step exists here in the sequence:**
All four memory functions wired in Step 2.4 call `_stream_tinyfish()` indirectly through `run()`. `run()` must exist and be verified before anything is wired into it.

**Why `core.py` is the right location:**
`core.py` is the contract between the caller and all Groundwire internals. Defining the Facade here means `from core import run` is the stable import path for every downstream file.

**Alternative approach considered and rejected:**
`Groundwire` class with `self.run()` — rejected because module-level functions are faster to demo and a class adds no value at hackathon scope.

**What breaks if this step deviates:**
If `_stream_tinyfish()` is not separated from `run()`, Step 2.4 cannot replace `run()`'s body without duplicating HTTP code. If `run()` does not return `list[dict]`, every downstream call to `extract_quirks(events, domain)` receives the wrong type and throws `TypeError`.

---

**Idempotent:** Yes — overwrites an empty stub. Running twice produces identical output.

**Context:** `core.py` is 0 bytes. TinyFish SSE schema confirmed from Phase 0.

**Pre-Read Gate:**
```bash
wc -c core.py    # must return: 0 core.py
wc -c memory.py  # must return: 0 memory.py (not touched in this step)
```
If `core.py` > 0 bytes: read full contents before overwriting.
If `memory.py` > 0 bytes: STOP — steps are out of order.

**Self-Contained Rule:** Code block below is complete and immediately runnable.

**No-Placeholder Rule:** No `<VALUE>` tokens. All values are literals.

```python
# core.py
"""
Groundwire core — Facade over TinyFish.

Public interface:
    run(url, goal) -> list[dict]

Internal (frozen after Step 1.3 — never modify):
    _stream_tinyfish(url, goal) -> list[dict]
"""
import json
import os
import requests
from dotenv import load_dotenv

load_dotenv()

TINYFISH_URL = "https://agent.tinyfish.ai/v1/automation/run-sse"


def _stream_tinyfish(url: str, goal: str) -> list[dict]:
    """
    Pure HTTP layer. POSTs to TinyFish, collects all SSE events, returns them as a list.
    FROZEN after Step 1.3. Never called by external code — always called through run().
    """
    resp = requests.post(
        TINYFISH_URL,
        headers={
            "X-API-Key": os.getenv("TINYFISH_API_KEY"),
            "Content-Type": "application/json",
        },
        json={"url": url, "goal": goal},
        stream=True,
        timeout=180,
    )
    resp.raise_for_status()

    events = []
    for raw_line in resp.iter_lines():
        if raw_line:
            line = raw_line.decode("utf-8")
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass  # skip malformed SSE lines silently
    return events


def run(url: str, goal: str) -> list[dict]:
    """
    Main public entry point.
    Phase 1 bare pass-through. Memory hooks added in Step 2.4.
    Returns: list of SSE event dicts from TinyFish.
    """
    return _stream_tinyfish(url, goal)


# ---------------------------------------------------------------------------
# Manual smoke test — run directly: python core.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from rich import print as rprint
    from rich.panel import Panel

    target_url = sys.argv[1] if len(sys.argv) > 1 else "https://news.ycombinator.com"
    target_goal = sys.argv[2] if len(sys.argv) > 2 else "Get the title of the top post"

    rprint(Panel(f"[bold]URL:[/bold] {target_url}\n[bold]Goal:[/bold] {target_goal}"))
    events = run(target_url, target_goal)
    rprint(f"[green]✓ Received {len(events)} events[/green]")
    rprint("[dim]Last 3 events:[/dim]")
    for e in events[-3:]:
        rprint(f"  {e}")
```

**What it does:** `_stream_tinyfish()` POSTs to TinyFish with `stream=True`, reads each `data:` SSE line, JSON-parses it, accumulates into a list. `run()` is a thin pass-through at this step — gains all memory hooks in Step 2.4 without `_stream_tinyfish()` ever changing.

**Why this approach:** `stream=True` with `iter_lines()` handles SSE natively without a dedicated library. Separating the HTTP layer is the key structural decision that keeps all future steps surgical.

**Assumptions:**
- SSE lines are prefixed with `data: ` (confirmed Phase 0)
- `TINYFISH_API_KEY` is in `.env` and non-empty (confirmed pre-flight)
- TinyFish returns HTTP 200 for valid requests (confirmed Phase 0)

**Risks:**
- `timeout=180` exceeded on slow runs → mitigation: increase to 300 if demo runs regularly hit it
- Zero events returned if SSE prefix differs → mitigation: add `print(raw_line)` inside loop temporarily to inspect

**Git Checkpoint:**
```bash
git add core.py
git commit -m "step 1.3: core.py Facade with bare TinyFish stream runner"
```

**Subtasks:**
- [ ] 🟥 `core.py` written with `_stream_tinyfish()` and bare `run()`
- [ ] 🟥 `python core.py` exits 0
- [ ] 🟥 At least 1 event returned
- [ ] 🟥 `memory.py` still 0 bytes (untouched)

**✓ Verification Test:**

**Type:** Integration

**Action:**
```bash
python core.py https://news.ycombinator.com "Get the title of the top post"
```

**Expected:**
- Prints panel with URL and goal
- Prints `✓ Received N events` where N >= 1
- Prints last 3 events as dicts, no empty dicts

**Pass:** `N >= 1` and script exits 0

**Fail:**
- `HTTPError: 401` → `TINYFISH_API_KEY` not loaded → confirm `load_dotenv()` runs before `os.getenv()`
- `N = 0 events` → SSE prefix mismatch → add `print(raw_line)` inside loop to inspect actual prefix
- `ConnectionError` / `Timeout` → network issue → confirm pre-flight network check passed

---

### Step 2.1 — Write `memory.py`: schema helpers + `recall()`
*Critical: Defines the JSON schema used by every subsequent step. `recall()` type contract (always `str`) must be established before `write()` or `log_run()` are written.*

**Step Architecture Thinking:**

**Pattern applied:** Null Object + Single Source of Truth. `recall()` always returns `str` — never `None`, never a list. `_empty_domain_data()` defines the canonical schema once; all other functions call it rather than inlining `{}` literals.

**Why this step exists here in the sequence:**
`write()` (Step 2.2) and `log_run()` (Step 2.3) both read the domain JSON and must produce output matching this schema. The schema must be defined and verified before either write function is written. `recall()` is read-only and can be unit-tested in total isolation — cold-start returning `""` is the most important invariant and must be verified before anything writes files that could mask a bug.

**Why `memory.py` is the right location:**
All memory I/O is co-located. `core.py` calls memory functions but never touches `.groundwire_memory/` files directly. If the storage backend changes, only `memory.py` changes.

**Alternative approach considered and rejected:**
Returning `None` from `recall()` on cold start — rejected because `f"{None}\n\n{goal}"` produces `"None\n\ngot pricing"` which silently corrupts the TinyFish goal string with no error.

**What breaks if this step deviates:**
If `_empty_domain_data()` is not defined here, Steps 2.2 and 2.3 each inline their own schema dict. When the schema changes, three files need updating. If `recall()` returns a list, Step 2.4's `f"{briefing}\n\n{goal}"` throws `TypeError: can only concatenate str (not "list") to str`.

---

**Idempotent:** Yes — `recall()` is read-only; `MEMORY_DIR.mkdir(exist_ok=True)` is safe to call twice.

**Context:** `memory.py` is 0 bytes. No `.groundwire_memory/` directory exists yet.

**Pre-Read Gate:**
```bash
wc -c memory.py    # must return: 0 memory.py
wc -c core.py      # must return: > 0 (Step 1.3 complete)
ls .groundwire_memory/ 2>&1  # expected: "No such file or directory"
```
If `memory.py` > 0: read full contents before overwriting.
If `core.py` == 0: STOP — Step 1.3 not complete.

**Self-Contained Rule:** Code block is complete. `write()`, `log_run()`, `consolidate()` are added in Steps 2.2–2.3 and are not referenced here.

**No-Placeholder Rule:** No `<VALUE>` tokens.

```python
# memory.py  (Step 2.1: schema helpers + recall — write/log/consolidate added in Steps 2.2–2.3)
"""
Groundwire site memory — three-layer knowledge system per domain.

Storage: .groundwire_memory/<domain>.json
Schema:
  {
    "quirks":           [{"text": str, "confidence": int, "last_seen": float}],
    "runs":             [{"id": str, "goal": str, "timestamp": float,
                          "step_count": int, "success": bool}],
    "semantic_profile": str,
    "run_count":        int,
    "last_consolidated": float
  }

Public interface (this step):
    recall(domain: str) -> str   # stratified briefing; always str; "" on cold start
"""
import json
from pathlib import Path

MEMORY_DIR = Path(".groundwire_memory")
MEMORY_DIR.mkdir(exist_ok=True)


def _domain_path(domain: str) -> Path:
    """Sanitise domain into a safe filename. Strips port numbers and slashes."""
    safe = domain.replace(":", "_").replace("/", "_").replace(".", "_")
    return MEMORY_DIR / f"{safe}.json"


def _empty_domain_data() -> dict:
    """
    Canonical empty schema. Single source of truth.
    Every function that creates a new domain record calls this — never inlines {}.
    """
    return {
        "quirks": [],
        "runs": [],
        "semantic_profile": "",
        "run_count": 0,
        "last_consolidated": 0.0,
    }


def recall(domain: str) -> str:
    """
    Return a stratified plain-English briefing for this domain.
    Layer 1 (always): run count + confidence headline.
    Layer 2 (if exists): semantic profile sentence.
    Layer 3 (if exists): top 10 quirks sorted by confidence descending.
    Returns "" if no memory exists. NEVER returns None.
    """
    path = _domain_path(domain)
    if not path.exists():
        return ""

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return ""

    run_count = data.get("run_count", 0)
    quirks = data.get("quirks", [])
    semantic_profile = data.get("semantic_profile", "")

    if run_count == 0 and not quirks and not semantic_profile:
        return ""

    # Confidence headline
    if run_count >= 10:
        confidence_label = "high"
    elif run_count >= 4:
        confidence_label = "medium"
    else:
        confidence_label = "low"

    lines = [f"Site memory for {domain} — {run_count} run(s), confidence: {confidence_label}"]

    # Semantic profile (Layer 2)
    if semantic_profile:
        lines.append(f"  Strategic profile: {semantic_profile}")

    # Procedural quirks sorted by confidence, top 10 (Layer 3)
    if quirks:
        sorted_quirks = sorted(quirks, key=lambda q: q.get("confidence", 1), reverse=True)[:10]
        lines.append("  Known quirks:")
        for q in sorted_quirks:
            lines.append(f"    - {q.get('text', '')} (confirmed {q.get('confidence', 1)}x)")

    return "\n".join(lines)
```

**What it does:** Defines the canonical JSON schema via `_empty_domain_data()`. `_domain_path()` converts domain strings to safe filenames. `recall()` reads the domain JSON and renders a three-layer briefing string — run count headline, semantic profile (if consolidated), and top 10 confidence-ranked quirks. Returns `""` on any failure.

**Why this approach:** Building the full schema in this step means Steps 2.2 and 2.3 write to known fields rather than inferring structure. The three-layer output of `recall()` is the core demo artefact — judges see it print before every run.

**Assumptions:**
- `MEMORY_DIR.mkdir(exist_ok=True)` fires on module import, before any function is called
- Domain strings are lowercase (standard from `urlparse().netloc`)

**Risks:**
- `_domain_path()` collision for `a.b.com` vs `a_b_com` → mitigation: astronomically unlikely across 3–5 demo domains
- Corrupt memory file crashes recall → mitigation: `try/except (json.JSONDecodeError, OSError)` returns `""` safely

**Git Checkpoint:**
```bash
git add memory.py
git commit -m "step 2.1: memory.py schema definition, _domain_path, _empty_domain_data, recall"
```

**Subtasks:**
- [ ] 🟥 `memory.py` written with `_domain_path()`, `_empty_domain_data()`, `recall()`
- [ ] 🟥 `MEMORY_DIR.mkdir(exist_ok=True)` fires on import
- [ ] 🟥 Cold-start returns `""` (verified below)
- [ ] 🟥 `core.py` byte count unchanged from Step 1.3

**✓ Verification Test:**

**Type:** Unit

**Action:**
```bash
python -c "
from memory import recall, _empty_domain_data
from pathlib import Path

# Test 1: cold start returns empty string
result = recall('linkedin.com')
assert result == '', f'Expected empty string on cold start, got: {result!r}'
print('PASS 1: cold start returns empty string')

# Test 2: return type is always str, never None
assert isinstance(result, str), f'Expected str, got {type(result)}'
print('PASS 2: return type is str')

# Test 3: MEMORY_DIR created on import
assert Path('.groundwire_memory').exists(), 'MEMORY_DIR not created'
print('PASS 3: .groundwire_memory/ directory exists')

# Test 4: _empty_domain_data has all required schema keys
schema = _empty_domain_data()
for key in ['quirks', 'runs', 'semantic_profile', 'run_count', 'last_consolidated']:
    assert key in schema, f'Missing schema key: {key}'
print('PASS 4: _empty_domain_data has all required keys')

print('ALL PASS')
"
```

**Expected:** Four `PASS` lines then `ALL PASS`.

**Pass:** `ALL PASS` printed, script exits 0.

**Fail:**
- `Expected empty string, got 'Site memory...'` → stale `.groundwire_memory/` file exists → delete and re-run
- `Expected str, got NoneType` → `recall()` has an early `return None` — check all return paths
- `MEMORY_DIR not created` → `MEMORY_DIR.mkdir` is inside a function, not module level → move it to module level
- `Missing schema key` → `_empty_domain_data()` dict is incomplete — add missing key

---

### Step 2.2 — Write `memory.py` write path + quirk extraction
*Critical: `write()` implements confidence scoring. Schema change from flat strings to dicts. `extract_quirks()` is the LLM call. Both are called every run.*

**Step Architecture Thinking:**

**Pattern applied:** Write-through cache with confidence map. `write()` upserts by text key — found → increment confidence + update `last_seen`; not found → insert with `confidence=1`. The caller (Step 2.4's `run()`) never manages deduplication or confidence arithmetic.

**Why this step exists here in the sequence:**
`recall()` exists and is verified (Step 2.1). `write()` can now be tested against the same domain file that `recall()` reads, verifying the round-trip. `extract_quirks()` depends on no prior state — it can be written and verified independently of `log_run()` (Step 2.3).

**Why `memory.py` is the right location:**
`write()` and `recall()` share the JSON schema. Co-locating them means one file owns all schema knowledge. `core.py` never sees the schema directly.

**Alternative approach considered and rejected:**
List append instead of confidence map — rejected because after 50 runs on `stripe.com`, the briefing string becomes thousands of tokens and dominates the goal, causing the agent to ignore the actual task.

**What breaks if this step deviates:**
If `write()` appends rather than upserts, the `quirks` list grows unbounded. If `extract_quirks()` returns `None` instead of `[]` on empty events, Step 2.4's `write(domain, quirks)` passes `None` to the confidence map loop and throws `TypeError`.

---

**Idempotent:** Yes — upsert with confidence increment is idempotent in structure (same key is never duplicated). Running `write("stripe.com", ["Cookie modal"])` ten times produces one entry with `confidence: 10`, not ten entries.

**Context:** `memory.py` currently contains `_domain_path()`, `_empty_domain_data()`, `recall()` from Step 2.1. This step APPENDS to the bottom of the file.

**Pre-Read Gate:**
```bash
grep -n "def recall" memory.py          # must return exactly 1 match
grep -n "def write" memory.py           # must return 0 matches
grep -n "def extract_quirks" memory.py  # must return 0 matches
grep -n "import anthropic" memory.py    # must return 0 matches
grep -n "import time" memory.py         # must return 0 matches
```
If `write` or `extract_quirks` already exist: read full `memory.py` before proceeding.

**Anchor Uniqueness Check:** Insertion point is the END of `memory.py`. Append only — do not touch any existing content.

**Self-Contained Rule:** Code block below is a complete append. `recall()` and `_domain_path()` from Step 2.1 are not repeated.

**No-Placeholder Rule:** `claude-sonnet-4-20250514` is the exact model string.

```python
# APPEND this block to the bottom of memory.py after the recall() function.
# Do NOT replace or modify any existing content.

import time
import anthropic as _anthropic  # prefixed to avoid shadowing if re-imported

CONSOLIDATE_EVERY = 3  # consolidate semantic profile every N runs (low for demo visibility)


def write(domain: str, new_quirks: list[str]) -> None:
    """
    Upsert new_quirks into the confidence map for this domain.
    Found → increment confidence + update last_seen.
    Not found → insert with confidence=1.
    Never appends duplicates. Safe to call multiple times with the same list.
    Does NOT increment run_count — that is owned by log_run().
    """
    if not new_quirks:
        return  # no-op on empty list

    path = _domain_path(domain)
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            data = _empty_domain_data()
    else:
        data = _empty_domain_data()

    # Build lookup by text for O(1) upsert
    existing: dict[str, dict] = {q["text"]: q for q in data.get("quirks", [])}
    now = time.time()

    for text in new_quirks:
        if text in existing:
            existing[text]["confidence"] += 1
            existing[text]["last_seen"] = now
        else:
            existing[text] = {"text": text, "confidence": 1, "last_seen": now}

    data["quirks"] = list(existing.values())
    path.write_text(json.dumps(data, indent=2))


def extract_quirks(events: list[dict], domain: str) -> list[str]:
    """
    Ask Claude to extract site-specific navigation quirks from the first 20 agent events.
    Returns list[str]. Returns [] on any error — never raises.
    Quirks are short plain-English strings suitable for storage and display.
    """
    if not events:
        return []

    client = _anthropic.Anthropic()
    event_sample = json.dumps(events[:20], indent=2)

    prompt = (
        f"These are the first events from a web agent navigating {domain}.\n"
        "Identify site-specific navigation quirks encountered or observable:\n"
        "- Cookie/consent modals\n"
        "- Authentication walls (note where they appear)\n"
        "- Lazy-loaded content (what scroll/click triggers it)\n"
        "- Anti-bot pauses or CAPTCHAs\n"
        "- Unusual pagination patterns\n"
        "- Redirect chains\n\n"
        "Return ONLY a JSON array of short strings. No preamble. No markdown. No explanation.\n"
        "If no quirks are detectable, return [].\n"
        'Example: ["Cookie consent modal on first load", '
        '"Job listings require scroll to trigger lazy load"]\n\n'
        f"Events:\n{event_sample}"
    )

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        parsed = json.loads(raw)
        return [q for q in parsed if isinstance(q, str)]
    except Exception:
        # Never crash the agent run over a memory extraction failure
        return []
```

**What it does:** `write()` reads the domain JSON, builds a text-keyed dict of existing quirks, upserts each new quirk (increment confidence if seen before, insert with confidence=1 if new), writes back. `extract_quirks()` sends first 20 events to Claude with a structured prompt targeting known web-agent quirk categories; returns `[]` on any failure.

**Why this approach:** The text-keyed dict upsert is O(n) and produces exactly the confidence-scored schema `recall()` expects. The broad `except Exception` in `extract_quirks()` is intentional — memory extraction is best-effort and must never crash a live agent run.

**Assumptions:**
- `json` is already imported at top of `memory.py` (it is — from Step 2.1)
- `ANTHROPIC_API_KEY` is set (confirmed pre-flight)
- `_empty_domain_data()` is already defined in the file (it is — from Step 2.1)

**Risks:**
- `extract_quirks()` adds ~2–3s per run → mitigation: acceptable for demo; add background thread post-hackathon
- Claude returns partial JSON → `json.loads` raises → caught by `except Exception`, returns `[]` — safe
- `write()` called concurrently by two processes → last-write-wins on JSON file → mitigation: single-process hackathon, acceptable

**Git Checkpoint:**
```bash
git add memory.py
git commit -m "step 2.2: memory.py write() confidence upsert and extract_quirks LLM extraction"
```

**Subtasks:**
- [ ] 🟥 `write()` appended to `memory.py` — confidence upsert verified
- [ ] 🟥 `extract_quirks()` appended to `memory.py`
- [ ] 🟥 `CONSOLIDATE_EVERY = 3` defined at module level
- [ ] 🟥 `recall()`, `_domain_path()`, `_empty_domain_data()` from Step 2.1 still present and unchanged
- [ ] 🟥 Deduplication and confidence increment verified (test below)

**✓ Verification Test:**

**Type:** Unit

**Action:**
```bash
python -c "
from memory import write, recall
import json
from pathlib import Path

# Test 1: write creates file with correct schema
write('test-domain.com', ['Cookie modal on load', 'Auth wall after page 2'])
path = Path('.groundwire_memory/test-domain_com.json')
assert path.exists(), 'Memory file not created'
data = json.loads(path.read_text())
assert isinstance(data['quirks'], list), 'quirks must be a list'
assert all('text' in q and 'confidence' in q for q in data['quirks']), 'quirk missing text or confidence'
print('PASS 1: file created with correct schema')

# Test 2: recall returns written quirks
briefing = recall('test-domain.com')
assert 'Cookie modal on load' in briefing
assert 'Auth wall after page 2' in briefing
print('PASS 2: recall returns written quirks')

# Test 3: second write with same quirk increments confidence
write('test-domain.com', ['Cookie modal on load', 'New quirk'])
data = json.loads(path.read_text())
by_text = {q['text']: q for q in data['quirks']}
assert by_text['Cookie modal on load']['confidence'] == 2, f\"Expected confidence 2, got {by_text['Cookie modal on load']['confidence']}\"
assert by_text['New quirk']['confidence'] == 1
assert len(data['quirks']) == 3  # 2 original + 1 new
print('PASS 3: confidence incremented, no duplicates, new quirk added')

# Test 4: empty list is a no-op
import time; orig_mtime = path.stat().st_mtime; time.sleep(0.01)
write('test-domain.com', [])
assert path.stat().st_mtime == orig_mtime, 'File written on empty input — should be no-op'
print('PASS 4: empty list is a no-op')

# Cleanup
path.unlink()
print('ALL PASS')
"
```

**Expected:** Four `PASS` lines then `ALL PASS`.

**Pass:** `ALL PASS` printed, test file cleaned up, script exits 0.

**Fail:**
- `FAIL 3: Expected confidence 2, got 1` → `write()` inserting new dict instead of incrementing → check text-key lookup
- `FAIL 4: File written on empty input` → `if not new_quirks: return` guard missing or after the file write
- `quirk missing text or confidence` → schema dict keys differ — confirm `{"text": ..., "confidence": ..., "last_seen": ...}`

---

### Step 2.3 — Write `memory.py` episodic log + semantic consolidation
*Critical: `log_run()` owns run_count increment. `consolidate()` makes the LLM call that produces the semantic profile judges will see.*

**Step Architecture Thinking:**

**Pattern applied:** Append-only episodic log + threshold-gated consolidation. `log_run()` is purely additive — it never modifies existing runs. `consolidate()` fires only when `run_count % CONSOLIDATE_EVERY == 0`, preventing an LLM call on every run while still firing during a demo.

**Why this step exists here in the sequence:**
`write()` and `extract_quirks()` exist and are verified (Step 2.2). `log_run()` reads the same domain JSON that `write()` just updated, adding the episodic entry on top of the updated quirks. The sequence `write → log_run → consolidate` in a single run produces a fully consistent domain file in one process.

**Why `memory.py` is the right location:**
All domain JSON read/write is co-located. `core.py` calls these functions in sequence but never touches the file directly. Adding new memory layers later means adding a function to `memory.py` and one call in `core.py`'s `run()` — not touching any other file.

**Alternative approach considered and rejected:**
Triggering consolidation inside `log_run()` rather than as a separate function — rejected because it makes `log_run()` non-deterministic in runtime (sometimes 0.1s, sometimes 3s for LLM call). Keeping them separate lets `core.py` log them independently in output and makes `consolidate()` independently testable.

**What breaks if this step deviates:**
If `log_run()` does not increment `run_count`, `consolidate()` never fires (division by zero safe, but threshold never reached). If `consolidate()` fires on every run instead of every N, the demo incurs a 3s LLM call on every single TinyFish invocation.

---

**Idempotent:** `log_run()`: No — appends a new run entry each call. Calling it twice for the same run creates two entries. `consolidate()`: Yes — re-running on the same `run_count` produces the same `semantic_profile` overwrite.

**Context:** `memory.py` currently has `_domain_path()`, `_empty_domain_data()`, `recall()`, `write()`, `extract_quirks()`, `CONSOLIDATE_EVERY`. This step APPENDS to the bottom of the file.

**Pre-Read Gate:**
```bash
grep -n "def write" memory.py            # must return exactly 1 match
grep -n "def extract_quirks" memory.py   # must return exactly 1 match
grep -n "def log_run" memory.py          # must return 0 matches
grep -n "def consolidate" memory.py      # must return 0 matches
grep -n "CONSOLIDATE_EVERY" memory.py    # must return exactly 1 match
```
If `log_run` or `consolidate` already exist: read full `memory.py` before proceeding.

**Anchor Uniqueness Check:** Insertion point is END of `memory.py`. Append only.

**Self-Contained Rule:** Complete append. No prior functions are repeated.

**No-Placeholder Rule:** `claude-sonnet-4-20250514` is the exact model string.

```python
# APPEND this block to the bottom of memory.py after extract_quirks().
# Do NOT replace or modify any existing content.


def log_run(domain: str, goal: str, events: list[dict], success: bool = True) -> None:
    """
    Append an episodic run entry to the domain memory file.
    Increments run_count. This function owns run_count — write() does not touch it.
    """
    path = _domain_path(domain)
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            data = _empty_domain_data()
    else:
        data = _empty_domain_data()

    run_entry = {
        "id": str(int(time.time())),
        "goal": goal,
        "timestamp": time.time(),
        "step_count": len(events),
        "success": success,
    }

    runs = data.get("runs", [])
    runs.append(run_entry)
    data["runs"] = runs
    data["run_count"] = len(runs)  # authoritative count from actual list length

    path.write_text(json.dumps(data, indent=2))


def consolidate(domain: str) -> bool:
    """
    Every CONSOLIDATE_EVERY runs, ask Claude to synthesize episodic history into a
    one-sentence semantic_profile. Returns True if consolidation ran, False otherwise.
    Never raises — any LLM failure leaves the existing semantic_profile unchanged.
    """
    path = _domain_path(domain)
    if not path.exists():
        return False

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    run_count = data.get("run_count", 0)
    if run_count == 0 or run_count % CONSOLIDATE_EVERY != 0:
        return False

    runs = data.get("runs", [])
    if not runs:
        return False

    client = _anthropic.Anthropic()

    # Summarise last 20 runs (covers all demo scenarios; avoids unbounded prompt)
    recent_runs = runs[-20:]
    runs_summary = json.dumps(
        [{"goal": r.get("goal"), "step_count": r.get("step_count"), "success": r.get("success")}
         for r in recent_runs],
        indent=2,
    )

    # Top 10 quirks by confidence for context
    top_quirks = sorted(
        data.get("quirks", []), key=lambda q: q.get("confidence", 0), reverse=True
    )[:10]
    quirks_summary = json.dumps(
        [{"text": q.get("text"), "confidence": q.get("confidence")} for q in top_quirks],
        indent=2,
    )

    prompt = (
        f"You are analysing the interaction history of a web agent with {domain}.\n"
        f"Recent runs ({len(recent_runs)}):\n{runs_summary}\n\n"
        f"Top known quirks (by confirmation count):\n{quirks_summary}\n\n"
        "Write ONE sentence (max 40 words) strategic profile of this site from a web agent's perspective.\n"
        "Focus on: reliability, common failure points, navigation patterns, which goal types succeed vs struggle.\n"
        "Return ONLY the sentence. No preamble. No markdown. No trailing punctuation beyond a period."
    )

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        profile = msg.content[0].text.strip()
        data["semantic_profile"] = profile
        data["last_consolidated"] = time.time()
        path.write_text(json.dumps(data, indent=2))
        return True
    except Exception:
        # Never crash the run over a consolidation failure; leave existing profile intact
        return False
```

**What it does:** `log_run()` appends a run entry dict to the `runs[]` array and sets `run_count` to the authoritative list length. `consolidate()` checks `run_count % CONSOLIDATE_EVERY == 0`, and if so, sends the last 20 episodic summaries + top 10 quirks to Claude for a one-sentence synthesis, then writes it to `semantic_profile`.

**Why this approach:** Deriving `run_count` from `len(runs)` (not incrementing a counter) means the count is always self-consistent even if a write failed mid-run. The threshold check using modulo ensures consolidation fires predictably on runs 3, 6, 9 — visible in a demo without hammering the API.

**Assumptions:**
- `time` and `_anthropic` are already imported (added in Step 2.2)
- `json` and `_domain_path()`, `_empty_domain_data()` already exist in `memory.py`
- `CONSOLIDATE_EVERY` is defined (added in Step 2.2)

**Risks:**
- `consolidate()` fires at run 3 even if events were trivial → mitigation: acceptable; profile sentence acknowledges limited data
- `log_run()` called twice for same run (agent bug) → two entries appended → mitigation: idempotency note in docstring; callers must ensure single call

**Git Checkpoint:**
```bash
git add memory.py
git commit -m "step 2.3: memory.py log_run() episodic append and consolidate() semantic synthesis"
```

**Subtasks:**
- [ ] 🟥 `log_run()` appended to `memory.py` — run entry written and `run_count` incremented
- [ ] 🟥 `consolidate()` appended to `memory.py` — fires at correct threshold
- [ ] 🟥 All prior functions (`recall`, `write`, `extract_quirks`) still present and unchanged
- [ ] 🟥 Round-trip test passes (write → log_run × 3 → consolidate fires)

**✓ Verification Test:**

**Type:** Unit (no network — consolidate will be called but will fail gracefully without a live API key connection; the threshold trigger is what we verify)

**Action:**
```bash
python -c "
from memory import write, log_run, consolidate, recall
import json
from pathlib import Path

domain = 'test-consolidate.com'
path = Path('.groundwire_memory/test-consolidate_com.json')

# Write some quirks
write(domain, ['Cookie modal on load'])

# Run 1 — log_run, consolidate should NOT fire
log_run(domain, 'Get pricing', [{'type': 'click'}] * 5, success=True)
data = json.loads(path.read_text())
assert data['run_count'] == 1, f'Expected run_count=1, got {data[\"run_count\"]}'
fired = consolidate(domain)
assert not fired, 'consolidate should not fire at run_count=1'
print('PASS 1: run_count=1, consolidate did not fire')

# Run 2
log_run(domain, 'Get features', [{'type': 'scroll'}] * 8, success=True)
data = json.loads(path.read_text())
assert data['run_count'] == 2
fired = consolidate(domain)
assert not fired, 'consolidate should not fire at run_count=2'
print('PASS 2: run_count=2, consolidate did not fire')

# Run 3 — consolidate SHOULD fire (will fail gracefully without API key env in this test)
log_run(domain, 'Get testimonials', [{'type': 'navigate'}] * 12, success=False)
data = json.loads(path.read_text())
assert data['run_count'] == 3, f'Expected run_count=3, got {data[\"run_count\"]}'
assert len(data['runs']) == 3, f'Expected 3 run entries, got {len(data[\"runs\"])}'
print('PASS 3: run_count=3, 3 episodic entries stored')

# Confirm recall shows correct run count
briefing = recall(domain)
assert '3 run(s)' in briefing, f'run count not in briefing: {briefing!r}'
print('PASS 4: recall briefing shows 3 runs')

# Cleanup
path.unlink()
print('ALL PASS')
"
```

**Expected:** Four `PASS` lines then `ALL PASS`. Consolidate may or may not produce a semantic profile depending on API availability — both outcomes are valid since this test validates structure, not LLM output.

**Pass:** `ALL PASS` printed, script exits 0.

**Fail:**
- `Expected run_count=1, got 0` → `log_run()` not writing `run_count` → check `data["run_count"] = len(runs)` line
- `consolidate should not fire at run_count=1` → modulo check is wrong → confirm `run_count % CONSOLIDATE_EVERY != 0`
- `Expected 3 run entries, got 1` → `runs.append()` followed by overwrite rather than append → confirm `runs = data.get("runs", [])` reads before append

---

### Step 2.4 — Wire all memory layers into `core.py` `run()`
*Critical: Replaces the only public function. All future phases (validator, guardrails, evals) call `run()`. `_stream_tinyfish()` is NEVER touched.*

**Step Architecture Thinking:**

**Pattern applied:** Facade orchestration — `run()` sequences all memory calls in the correct order. The sequence is: recall → enrich → stream → extract + write (confidence) → log (episodic) → consolidate (semantic). No step can be reordered without breaking a dependency.

**Why this step exists here in the sequence:**
All five memory functions are complete and individually verified. Only now is it safe to compose them — each piece has been tested in isolation. If Step 2.3 had not been verified before this step, a bug in `consolidate()` would appear to be a `run()` bug.

**Why `core.py` is the right location:**
`core.py` is the Facade. Orchestration (what order calls happen, what to do with their outputs) belongs here. `memory.py` functions are stateless — they know nothing about each other's existence.

**Alternative approach considered and rejected:**
Having `memory.py` import `core.py` and wrap `run()` itself — rejected because it creates a circular import (`core` imports `memory`, `memory` imports `core`) and inverts the dependency direction.

**What breaks if this step deviates:**
- If `log_run()` is called before `write()`, the domain file may not exist yet and `log_run()` creates it without quirks — then `write()` creates a second conflicting file. Actually both handle file-not-exists with `_empty_domain_data()`, so no crash, but quirks would be in a separately-written state.
- If `consolidate()` is called before `log_run()`, `run_count` has not been incremented yet and the threshold check fires one run late.
- If `_stream_tinyfish()` body is accidentally modified, all future phases that depend on its frozen interface break.

---

**Idempotent:** Yes — replacing `run()` with a new implementation is idempotent.

**Context:** `core.py` currently has `_stream_tinyfish()` (frozen) and a bare `run()` returning `_stream_tinyfish(url, goal)`. We replace `run()` only.

**Pre-Read Gate:**
```bash
# All must pass before any edit:
grep -n "def run" core.py                     # must return exactly 1 match
grep -n "def _stream_tinyfish" core.py        # must return exactly 1 match
grep -n "from memory import" core.py          # must return 0 matches
grep -n "from urllib.parse import" core.py    # must return 0 matches
grep -n "def recall" memory.py                # must return exactly 1 match
grep -n "def write" memory.py                 # must return exactly 1 match
grep -n "def extract_quirks" memory.py        # must return exactly 1 match
grep -n "def log_run" memory.py               # must return exactly 1 match
grep -n "def consolidate" memory.py           # must return exactly 1 match
```
If any grep returns unexpected count: STOP and report before touching any file.

**Anchor Uniqueness Check:**
The string `return _stream_tinyfish(url, goal)` must appear exactly once in `core.py`.
```bash
grep -n "return _stream_tinyfish" core.py   # must return exactly 1 match inside run()
```

**Two surgical edits to `core.py`:**

**Edit A — Add imports** (insert after `from dotenv import load_dotenv`, before `load_dotenv()`):
```python
from urllib.parse import urlparse
from memory import recall, write, extract_quirks, log_run, consolidate
```

**Edit B — Replace the entire `run()` function body** (replace from `def run` through its closing line):
```python
def run(url: str, goal: str) -> list[dict]:
    """
    Main public entry point.
    Orchestrates: memory recall → TinyFish stream → confidence write
                  → episodic log → semantic consolidation.
    Signature is frozen: run(url, goal) -> list[dict]. Do not change.
    """
    domain = urlparse(url).netloc

    # ── 1. Recall and display stratified memory briefing ──────────────────
    briefing = recall(domain)
    if briefing:
        for line in briefing.splitlines():
            print(f"[memory] {line}")
        enriched_goal = f"{briefing}\n\n{goal}"
    else:
        print(f"[memory] No prior memory for {domain} — cold start")
        enriched_goal = goal

    # ── 2. Stream TinyFish with enriched goal ─────────────────────────────
    events = _stream_tinyfish(url, enriched_goal)
    print(f"[core] Run complete — {len(events)} events received")

    # ── 3. Extract quirks and update confidence map ────────────────────────
    quirks = extract_quirks(events, domain)
    if quirks:
        write(domain, quirks)
        print(f"[memory] Confidence updated for {len(quirks)} quirk(s): {quirks}")
    else:
        print(f"[memory] No new quirks extracted for {domain}")

    # ── 4. Log episodic run entry ──────────────────────────────────────────
    log_run(domain, goal, events, success=True)
    print(f"[memory] Run logged — episodic history updated")

    # ── 5. Consolidate into semantic profile if threshold reached ──────────
    consolidated = consolidate(domain)
    if consolidated:
        print(f"[memory] ✦ Semantic profile updated for {domain}")

    return events
```

**What it does:** Five sequential phases in `run()`: recall and prepend briefing → stream TinyFish with enriched goal → confidence-upsert extracted quirks → append episodic run entry → trigger semantic consolidation if threshold reached. `_stream_tinyfish()` is completely unchanged. Print statements are intentional demo artefacts — judges see the memory system operating in real time.

**Why this approach:** The five phases must execute in this exact order. Recall before stream (agent must be briefed before navigating). Write before log (quirks must be in the file before `log_run` reads it for consistency). Log before consolidate (run_count must be incremented before the threshold check).

**Assumptions:**
- `urlparse(url).netloc` correctly extracts `"stripe.com"` from `"https://stripe.com/pricing"` — stdlib behaviour
- All five memory functions return their documented types (verified in prior steps)
- `_stream_tinyfish()` body is unchanged from Step 1.3 (Pre-Read Gate confirms)

**Risks:**
- `briefing` grows large (many runs, many quirks) and pushes goal over TinyFish token limit → mitigation: `recall()` already caps at top 10 quirks; semantic profile is max 40 words
- `extract_quirks()` and `consolidate()` each add ~2–3s latency → mitigation: acceptable for demo; total overhead is at most ~6s per run
- Editing `run()` accidentally trims `_stream_tinyfish()` → mitigation: Anchor Uniqueness Check + post-edit grep confirms `_stream_tinyfish()` body unchanged

**Git Checkpoint:**
```bash
git add core.py
git commit -m "step 2.4: wire three-layer memory into core.py run() — recall, write, log_run, consolidate"
```

**Subtasks:**
- [ ] 🟥 `from memory import recall, write, extract_quirks, log_run, consolidate` added to `core.py`
- [ ] 🟥 `run()` replaced with five-phase memory-wired version
- [ ] 🟥 `_stream_tinyfish()` body UNCHANGED — confirm with `grep -n "_stream_tinyfish" core.py`
- [ ] 🟥 Run 1 prints `cold start`; Run 2 prints memory briefing with run count

**✓ Verification Test:**

**Type:** Integration (requires live network — 2 TinyFish calls + 2 Claude API calls)

**Action:**
```bash
# RUN 1 — cold start
echo "=== RUN 1 ===" && python core.py https://news.ycombinator.com "Get the title of the top post"

# RUN 2 — should show memory briefing
echo "=== RUN 2 ===" && python core.py https://news.ycombinator.com "Get the title of the top post"

# RUN 3 — should trigger consolidation (CONSOLIDATE_EVERY=3)
echo "=== RUN 3 ===" && python core.py https://news.ycombinator.com "Get the top 3 post titles"
```

**Expected:**
- RUN 1: prints `[memory] No prior memory for news.ycombinator.com — cold start`
- RUN 1: prints `[memory] Run logged — episodic history updated`
- RUN 2: prints `[memory] Site memory for news.ycombinator.com — 1 run(s), confidence: low`
- RUN 3: prints `[memory] ✦ Semantic profile updated for news.ycombinator.com`
- All runs: print `[core] Run complete — N events received` where N >= 1
- After RUN 3: `cat .groundwire_memory/news_ycombinator_com.json` shows `run_count: 3`, `semantic_profile` non-empty

**Pass:** RUN 3 prints the semantic profile update line. `cat` confirms all three layers populated.

**Fail:**
- `ImportError: cannot import name 'log_run' from 'memory'` → import line not added or typo → re-check Edit A
- RUN 2 shows cold start again AND RUN 1 showed `No new quirks extracted` → `extract_quirks()` returned `[]` (Claude API issue) → check `ANTHROPIC_API_KEY`; quirks being empty is non-fatal — run_count still increments
- RUN 3 does not print semantic profile update → `consolidate()` returning `False` → add `print(consolidate(domain))` temporarily to inspect return value; check `run_count % 3 == 0`
- `_stream_tinyfish()` body changed → `grep -n "def _stream_tinyfish" core.py` — confirm line number matches Step 1.3 output

---

## Phase 1 Complete — State Manifest

```
STATE MANIFEST — Phase 1 Complete

Files created / modified:
  core.py:   _stream_tinyfish() [frozen], run() [five-phase memory facade]
  memory.py: _domain_path(), _empty_domain_data(), recall(), write(),
             extract_quirks(), log_run(), consolidate(), CONSOLIDATE_EVERY

Files confirmed unchanged:
  validator.py:  0 bytes
  guardrails.py: 0 bytes
  evals.py:      0 bytes
  demo.py:       0 bytes

JSON schema:
  .groundwire_memory/<domain>.json:
    quirks[]         → {text, confidence, last_seen}
    runs[]           → {id, goal, timestamp, step_count, success}
    semantic_profile → str (populated every CONSOLIDATE_EVERY runs)
    run_count        → int (authoritative: len(runs))
    last_consolidated→ float

Verifications passed:
  Step 1.3: ✅ core.py streams TinyFish, returns list[dict], _stream_tinyfish frozen
  Step 2.1: ✅ recall() returns "" on cold start, always str, schema defined
  Step 2.2: ✅ write() confidence upsert (no duplicates), extract_quirks() returns list[str]
  Step 2.3: ✅ log_run() appends + increments run_count, consolidate() fires at threshold
  Step 2.4: ✅ run() orchestrates all five phases, briefing visible in terminal

Next phase:
  Phase 2 — Trajectory Validator
  Requires: core.py run() exists (✅), _stream_tinyfish() importable (✅)
  First step: write validator.py check_trajectory() in isolation
```

---

## Regression Guard

**Systems at risk:** None — Phase 1 is additive. `run(url, goal) -> list[dict]` signature unchanged.

```bash
# Confirm _stream_tinyfish still works independently
python -c "
from core import _stream_tinyfish
events = _stream_tinyfish('https://news.ycombinator.com', 'Get top post title')
assert isinstance(events, list), 'Must return list'
assert len(events) >= 1, 'Must return at least 1 event'
print(f'PASS: _stream_tinyfish returns {len(events)} events')
"
```

---

## Risk Heatmap

| Step | Risk Level | What Could Go Wrong | Early Detection | Idempotent |
|---|---|---|---|---|
| 1.3 | 🟡 Medium | SSE prefix differs → 0 events returned | Verification shows N=0 | Yes |
| 2.1 | 🟢 Low | `recall()` returns None on cold start | Unit test catches immediately | Yes |
| 2.2 | 🟡 Medium | `extract_quirks()` Claude call fails silently | `[]` returned; memory stays empty — acceptable | Yes |
| 2.3 | 🟡 Medium | `consolidate()` fires on every run if modulo wrong | Run 1 triggers consolidation | Yes |
| 2.4 | 🔴 High | `run()` replacement corrupts `_stream_tinyfish()` | Pre-Read Gate grep + post-edit grep | Yes |

---

## Success Criteria

| Feature | Target | Verification |
|---|---|---|
| TinyFish stream | Returns >= 1 event | `python core.py` prints `N events`, N >= 1 |
| Cold start | Prints cold start message | RUN 1 terminal shows `cold start` |
| Confidence scoring | Second run increments confidence | Domain JSON shows `"confidence": 2` after RUN 2 |
| Episodic log | Run entries accumulate | Domain JSON `runs[]` has 3 entries after RUN 3 |
| Semantic consolidation | Profile written at run 3 | RUN 3 prints `✦ Semantic profile updated`; JSON `semantic_profile` non-empty |
| Recall briefing | Run 2 shows memory header | RUN 2 terminal shows `run(s), confidence:` line |
| Regression: _stream_tinyfish | Unchanged from Step 1.3 | grep line number matches before and after Step 2.4 |

---

⚠️ **Do not mark a step 🟩 Done until its verification test passes.**
⚠️ **Do not proceed to Phase 2 until State Manifest is filled and all 5 verifications show ✅.**
⚠️ **`_stream_tinyfish()` must never be modified — it is frozen after Step 1.3.**
⚠️ **Do not batch Steps 1.3, 2.1, 2.2, 2.3, 2.4 into one git commit — each step is a separate commit.**
⚠️ **`log_run()` must be called exactly once per `run()` invocation — it is not idempotent.**
⚠️ **Architecture Overview must be complete before Pre-Flight begins.**