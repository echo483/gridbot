---
name: dispatch-relay
description: Use when delegating gridbot work to the hermes agent team (scripts/dispatch.py) — lane selection, spec craft, and the failure modes that produced rejected or unusable builds. Six relays of evidence behind every rule.
---

# Dispatching the agent team (gridbot)

One entry point: `uv run python scripts/dispatch.py <kind> <prompt-file>`.
Lane doctrine lives in dispatch.py ROUTES; behavior contracts live in
agents/<lane>.md (version-controlled preambles, prepended automatically).

## Lane selection (evidence-backed)

- **programming** (glm-flash): default builder. Fast and spec-faithful —
  which means specs must contain REAL payloads: it trusts the spec over
  reality and its tests mirror its own assumptions.
- **programming2** (deepseek): alternate builder; rotate real builds.
  Succeeded on the second-side gate v2 after glm submitted unchanged
  files. NEVER give deepseek hermes agent/tool-driving jobs — its
  weakness is the harness, not intelligence.
- **review** (opus xhigh): trade-path and registration-deciding code
  only (cost!). Stopped 2 evidence-corrupting bugs. Can stall >25min —
  pair() auto-falls-back to review2.
- **analysis** (deepseek): bulk data reads. Delivered the 08-27 funnel
  census that reconciled 379→33 exactly. Paste ALL data in; it has no
  repo access.
- **pair mode**: builder→reviewer→revise relay. `--builder`/`--reviewer`
  flags choose lanes.

## Spec craft (the difference between success and rejection)

- Name every trap explicitly (the D1–D5 pattern): real row shapes, what
  fields DON'T exist, which joins are mandatory. A gate reading a field
  that isn't there fails OPEN and hand-written fixtures still pass.
- Require verification the agent can run: exact commands, expected
  baseline numbers, "confirm the count GREW".
- Judge by ARTIFACTS on disk, never transcripts (mojibake channel).
  Verify files changed before believing "done".
- Prompts ride argv (visible in /proc/<pid>/cmdline): NEVER paste
  secrets or key material into a dispatch prompt.
- Claude line-by-line review is the LAST gate regardless of relay
  outcome. Reviewers stalling ≠ code is fine.

## Batching

hermes -z mirrors to the user's Telegram — batch dispatches, don't spam.
Run long dispatches in background and continue own work in parallel.
