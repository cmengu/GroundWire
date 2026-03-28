# GroundWire

**The SLA layer for web agents. One import. Zero new infrastructure.**

> "TinyFish gives you the agent. GroundWire gives you the SLA."

---

## The problem

Spinning up a web agent takes minutes. Getting it to behave consistently across 100 runs is a different problem entirely.

Every production deployment hits the same failure modes:
- The agent stalls on a Cloudflare challenge and you have no idea
- It drifts toward checkout when you asked for pricing data — no one catches it
- A cookie modal on the first visit blocks every run after
- Another team's agent already solved this site — but yours starts cold every time

These aren't agent problems. They're gaps in the layer *around* the agent. GroundWire closes them.

---

## One import. All seven features fire automatically.

```python
# Before GroundWire
result = requests.post("https://agent.tinyfish.ai/v1/automation/run-sse", ...)

# After GroundWire — zero other changes
from groundwire import GroundWire
gw = GroundWire.from_env()
result = gw.run(url="https://amazon.com", goal="Get price of AirPods Pro")
```

---

## What fires on every `gw.run()`

```
Your Code
  └─► GroundWire.run()
        ├── [1] Cross-agent memory recall        — briefing injected into goal
        ├── [2] Guardrails pre-check             — domain allowlist, action budget
        │
        │   TinyFish SSE stream (live)
        │     └── [3] Trajectory validation      — every 5 events, Claude scores progress
        │           ├── [4] CAPTCHA detection    — human escalation, not replan
        │           └── [5] Self-Healing         — hypothesis → sandbox → confirmed fix
        │
        ├── [6] Adversarial hardening            — block scan → Claude classify → stealth retry
        ├── [7] Memory write + cross-agent sync  — promotes confirmed quirks to Supabase
        └── Evals / faithfulness scoring
```

---

## Feature deep-dives

### [1] Cross-Agent Memory Sharing
*Network effect site intelligence — shared across every GroundWire user.*

After each run, confirmed site quirks (cookie modals, lazy-load timing, auth walls, anti-bot patterns) are promoted to a shared Supabase store when confidence crosses a threshold. Any agent on any machine tackling the same domain gets a pre-briefing:

```
[memory] SHARED SITE MEMORY (3 agents, 4.2x confidence):
         policy page uses lazy-loaded section headers — scroll required
         before section text is accessible
```

The first agent on a site pays the cold-start tax. Every agent after gets a head start.

---

### [2] Guardrails
*Hard stops that run before the agent touches the page.*

Composable rules checked before every run and post-processed on every output:

| Rule | What it does |
|---|---|
| `DomainAllowlist` | Blocks off-target requests before they fire |
| `PIIScrubber` | Catches emails, phone numbers before results leave the system |
| `ActionBudget(n)` | Hard-stops runaway agents at step n |

```
[guardrail] Domain not in allowlist — run blocked.
[guardrail] Output scrubbed: j.doe@example.com → [EMAIL_REDACTED]
```

---

### [3] Trajectory Validation
*Live mid-run scoring on the TinyFish SSE stream.*

Every 5 PROGRESS events, GroundWire scores the trajectory on four axes: `goal_alignment`, `action_efficiency`, `risk_signal`, `progress_rate`. A dual-model gate (Claude + GPT-4o second opinion) fires when Claude's score drops below threshold.

```
[validator] step  10 | progress=0.64 | align=0.71 | eff=0.55 | risk=0.22
[validator] ⚠  Drift signal (1/2): agent scrolling without producing output
[validator] ✗  Drift confirmed — generating Reflexion critique
```

Replan carries forward a checkpoint context: the compressed goal tells the new run exactly how many steps completed and where to resume, not restart.

---

### [4] CAPTCHA Escalation
*Route to human-in-loop, not into an infinite replan cycle.*

When `detect_deterministic_signals()` sees 3 identical consecutive actions containing CAPTCHA keywords (`cloudflare`, `challenge`, `verify`, `datadome`...), it sets `captcha_detected: True` instead of `loop: True`. The SSE connection closes immediately, partial memory is written, and a `groundwire_meta` event with `action_required: "human_review"` is returned.

CAPTCHA stalls no longer trigger replans that hit the same challenge page again.

---

### [5] Self-Healing
*Hypothesis → Sandbox → Commit. Confirmed fixes go into the replanned goal.*

When drift is confirmed, before replanning, GroundWire runs a full hypothesis cycle:

1. **Hypothesise** — Claude Haiku generates a site-behaviour explanation for the stall (e.g. *"cookie modal blocks the pricing section on first visit"*)
2. **Sandbox** — fires a real TinyFish sync run with the hypothesis prefix injected into the goal
3. **Commit** — if the sandbox succeeds, bumps local confidence on the quirk and prepends `CONFIRMED FIX: Accept the cookie modal first.` to the replanned goal

The replanned run starts with verified site knowledge, not just a compressed hope.

---

### [6] Adversarial Hardening
*Post-stream block detection, Claude classification, and escalated auto-retry.*

After the SSE stream completes, a zero-LLM keyword scan checks for block signals in the event list and COMPLETE payload. If blocked:

1. **Classify** — Claude Haiku identifies the block type (`cloudflare`, `datadome`, `captcha`, `geo_block`, `rate_limit`, `login_wall`)
2. **Decide** — `escalate_to_human: true` for hard blocks; auto-retry for soft ones
3. **Retry** — re-fires TinyFish with `browser_profile: "stealth"` and optional country-routed residential proxy
4. **Log** — outcome recorded to Supabase `antibot_events` for cross-agent pattern sharing

```
[hardener] 🛡  Block detected — attempting auto-harden and retry for stripe.com
[hardener] ✓ Auto-recovered from cloudflare block (stealth profile, US proxy)
```

---

### [7] Memory Write + Eval Harness
*Every run teaches the system. Every replay is graded.*

Post-run: Claude extracts site quirks → confidence-weighted write → Supabase promotion if threshold met → `run_episodes` logged.

Record a golden run once. Replay any time. Get back:

```
Faithfulness: 0.95 | Steps: 6 vs naked 11 | pass@3: True
```

---

## Architecture

```
groundwire/
├── client.py         # GroundWire class — public API, SSE orchestration, _on_progress_hook
├── core.py           # Thin backward-compat shim → client.py
├── validator.py      # Trajectory rubric + CAPTCHA detection (zero-LLM + Claude)
├── healer.py         # SelfHealer — Hypothesis → TinyFish sandbox → memory commit
├── hardener.py       # AdversarialHardener — block scan, Claude classify, stealth retry
├── memory.py         # Per-domain JSON: confidence quirks, episodic runs, semantic profile
├── shared_memory.py  # Supabase sync: get_shared_briefing, promote_if_ready, record_episode
├── schemas.py        # Pydantic DTOs: TrajectoryRubric, HypothesisResult, BlockClassification
├── guardrails.py     # Composable rules: DomainAllowlist, PIIScrubber, ActionBudget
├── evals.py          # Golden-run recorder + faithfulness scorer
├── openai_validator.py # GPT-4o dual-validation gate
└── demo.py           # End-to-end demo: naked vs GroundWire vs scored trials
```

**Stack:** Python · TinyFish Agent API (SSE + sync) · Anthropic Claude (Haiku + Sonnet) · OpenAI GPT-4o · Supabase (pgvector-ready) · Pydantic

---

## Quickstart

```bash
git clone https://github.com/cmengu/GroundWire.git
cd GroundWire/groundwire
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

**Required — `.env`:**
```
TINYFISH_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here
```

**Optional — enable cross-agent shared memory (Supabase):**
```
NEXT_PUBLIC_SUPABASE_URL=https://<your-project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_DEFAULT_KEY=your_supabase_anon_key_here
```
> Run `groundwire/supabase_schema.sql` once in the Supabase SQL editor to create the `domain_quirks`, `run_episodes`, and `antibot_events` tables.

**Run the demo (dry-run — no TinyFish credits used):**
```bash
python demo.py --dry-run
```

**Run live:**
```bash
python demo.py
```

---

## Benchmark

| | Raw TinyFish | With GroundWire |
|---|---|---|
| Steps taken (cold start) | 11 | 10 |
| Steps taken (warm — memory active) | 11 | **6** |
| Trajectory deviations | 2 (undetected) | 1 (caught at step 9, replanned) |
| CAPTCHA handling | stalls silently | escalates to human immediately |
| Block recovery | fails | auto-retries with stealth profile |
| PII in output | leaked | redacted |
| Cross-agent knowledge | none | shared via Supabase |
| Eval faithfulness | not measured | **0.95** |
| pass@3 | — | **100%** |

---

## What judges are actually looking at

**Technical Complexity** — CAPTCHA detection that's mutually exclusive with loop detection (not conflated). Healer runs a real TinyFish sandbox to confirm a hypothesis before committing anything to memory. Adversarial hardening classifies, retries with escalated browser profile + proxy, and logs the outcome to shared Supabase for every future agent.

**Tool Integration** — TinyFish SSE stream is the interception surface for all validation and healing. Sync endpoint powers the healer sandbox. Stealth browser profile + proxy routing power the hardener retry. Claude Haiku handles hypothesis generation and block classification. Claude Sonnet scores trajectory rubrics. GPT-4o provides a second opinion when Claude's confidence is low.

**Utility & Impact** — Every agent deployment is currently a fresh roll of the dice. GroundWire turns site knowledge into a compounding asset that gets shared across teams. A cookie modal discovered by one agent is never discovered again by any agent.

**Innovation** — Self-healing that tests its own hypothesis before committing. Cross-agent memory that creates a network effect from individual agent runs. CAPTCHA escalation that knows the difference between a stall and a loop. All behind a single method call.

---

## Built at TinyFish × Anthropic Hackathon

Powered by [TinyFish](https://www.tinyfish.ai) web agents API and [Anthropic Claude](https://www.anthropic.com).
