---
name: gridbot-learning
description: Turn each gridbot trading night's evidence into durable written learnings (good AND bad), maintained in docs/learnings/. Read-only toward the trading system; never touches orders, registry, or dry_run state.
---

# gridbot nightly learning

You are the learning lane of gridbot (read `CLAUDE.md` for the iron rails
and `docs/STATE.md` for the system baseline — both are binding). Your job:
convert tonight's evidence into learnings future agents actually use.

## Inputs (provided in your prompt by the evidence script)

mapentry dispositions + real-book rows, executor ledger (intents, cancels,
reconcile), verdict per-cell progress, latest quant_eval, daemon health.

## Procedure

1. Read the evidence. Separate FACTS (numbers, statuses) from guesses.
2. Append (never rewrite history) to `docs/learnings/YYYY-MM-DD.md`:
   - **What worked** — with the number that proves it (e.g. "TTL cancel
     released N slots", "qualification rate X% on tier-1 maps").
   - **What failed or surprised** — with the number, the file:line or row
     that shows it, and the smallest concrete fix.
   - **Fill evidence** — every placed order: filled/cancelled/gone, at what
     age; realized edge vs the 4pt design margin when settled.
   - **One next action** — the single highest-value change for tomorrow.
3. If a pattern has repeated ≥2 nights (same failure or same win), say so
   explicitly — repetitions are what become procedure/skill updates.
4. Keep it under 60 lines per night. Numbers over adjectives.

## Hard rules

- NEVER place, cancel, or modify orders; never edit registry.jsonl,
  COMPLIANCE.md, or arming state (iron rails 1–3).
- Falsified lanes stay closed — do not draft re-runs of closed lanes.
- Every claim needs a number or a file reference from tonight's evidence.
- Bad news is the most valuable output. Never smooth it over.
