# Groundwire

**Middleware that makes web agents actually reliable in production.**

---

## Why we built this

Spinning up a web agent takes minutes. Getting it to behave consistently across 100 runs is a different problem entirely.

We kept running into the same four failure modes. The agent hits a cookie modal it's never seen and stalls out. It drifts toward the checkout flow when you asked for pricing data, and nothing catches it. It hands back results with email addresses baked in. You run the same task a week later and have no idea if it got better or worse because you never recorded a baseline.

None of these are agent problems exactly. They're gaps in the layer around the agent. That's what Groundwire fills.

---

## How it works

Groundwire wraps any web agent API (we built on [TinyFish](https://www.tinyfish.ai)) with four things that should have shipped with the agent in the first place:

```
Your Code  ->  [ Guardrails -> Memory -> Agent -> Validator -> Evals ]  ->  Result you can trust
```

**Site Memory**

After each run, Groundwire asks Claude to pull out whatever site-specific quirks the agent encountered: cookie modals, auth walls, lazy-load timing, anti-bot patterns. These get saved per domain. On the next run, that briefing gets prepended to the goal before the agent ever touches the page.

Run 1: 34 steps, two wrong turns.
Run 2: 21 steps. It already knew about the modal.

**Trajectory Validation**

Groundwire taps into the live SSE event stream and checks every few steps whether the agent is still heading toward the actual goal. When confidence drops below threshold, it replans automatically, carrying forward context about what already happened.

`Deviation detected at step 9: agent navigated to login page instead of pricing. Replanning.`

**Eval Harness**

Record a golden run. Replay it any time. Get a scorecard back from Claude grading faithfulness, efficiency delta, and a short explanation of what changed.

`Faithfulness: 0.96 | Efficiency: -13 steps | "Same data extracted, fewer navigation errors."`

**Guardrail Middleware**

Composable rules that run before and after execution. Domain allowlists block off-target requests before they fire. A PII scrubber catches emails and phone numbers before results leave the system. Action budget limits hard-stop runaway agents.

`Redacted email: j.doe@example.com -> [EMAIL_REDACTED]`
`Domain not in allowlist. Run blocked.`

---

## Architecture

```
groundwire/
├── core.py          # Orchestration: runs TinyFish with memory-enriched goals
├── memory.py        # Per-domain JSON store: quirks extracted after each run
├── validator.py     # Claude-powered trajectory checker on live SSE stream
├── evals.py         # Record golden runs, score replays with faithfulness metric
├── guardrails.py    # Pluggable rules: allowlist, PII scrubber, action budget
└── demo.py          # End-to-end demo: two runs, scorecard printed
```

**Stack:** Python, TinyFish Agent API, Anthropic Claude (claude-sonnet-4), SSE streaming, flat-file memory store

---

## Quickstart

```bash
git clone https://github.com/cmengu/GroundWire.git
cd GroundWire/groundwire
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env`:
```
TINYFISH_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here
```

Run the demo:
```bash
python demo.py
```

---

## Side by side

| | Raw Agent | With Groundwire |
|---|---|---|
| Steps taken | 34 | 21 |
| Trajectory deviations | 2 (undetected) | 1 (caught at step 9, replanned) |
| PII in output | leaked email | redacted |
| Site memory used | no | modal skipped on load |
| Eval faithfulness | not measured | 0.96 |

---

## The production gap

Most agent demos work. Most agent deployments don't, at least not consistently. Without something tracking whether the agent is on goal, learning from past runs, and enforcing hard rules about what it can touch, every run is a fresh roll of the dice.

Groundwire is the wrapper that closes that gap. You still pick your agent. We just make sure it behaves.

---

## Built At

Powered by [TinyFish](https://www.tinyfish.ai) web agents API and [Anthropic Claude](https://www.anthropic.com).
