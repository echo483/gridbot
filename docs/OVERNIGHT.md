# Overnight protocol

How gridbot runs unattended (user asleep / away), and what the morning looks
like. The design goal: the night produces **evidence and reviewed code**, and
the morning starts with **one digest**, not a scroll of pings.

## Standing overnight loop (Claude session, self-paced ~25–30 min ticks)

1. **Captures first** (iron rail 4): capture + shadow daemon health, bad-record
   counters, timer states. Deterministic watchdog self-heals every 5 min;
   webhook escalation wakes a Hermes triage agent only after self-heal fails,
   once per outage.
2. **Evidence watch**: new shadow instances → verdict progress per cell
   (resolution rates, quote availability, distance to frozen bars). Ingest
   picks up newly finished series hourly; spot-check counts.
3. **Dual-agent development**: work that doesn't need the user is split —
   Hermes kanban workers (glm-5.3 / ox-alpha) author well-specified facets in
   isolated worktrees; Claude reviews substance (not just tests), corrects,
   merges with credit, and comments the verdict on the card. Claude-side
   builds get adversarial multi-agent review before they count as done.
4. **Registry discipline holds at night**: findings become DRAFTs or
   pre-committed reading amendments only while their evidence set is empty;
   nothing falsified reopens; dry_run is untouchable.

## Fixed overnight schedule (all times PT)

| time | what | owner |
|---|---|---|
| hourly :20 | corpus ingest (finished series → training_rounds) | systemd timer |
| every 5 min | health watchdog (self-heal + transition alerts) | systemd timer |
| every 30 min | capture-QA triage (fires only on fingerprint change) | hermes cron |
| 05:00 | hermes state backup | hermes cron |
| 05:10 | raw capture day files → Supabase Storage (offsite moat) | systemd timer |
| 07:00 | spend-guard tripwire ($2/24h) | hermes cron |
| 07:30 Mon | MCP auth canary | hermes cron |
| 08:00 | **morning report** — consolidated overnight digest | systemd timer |
| 22:00 | EOD report | hermes cron |
| 22:15 | hermes verdict summary (agent) | hermes cron |
| 22:30 | Claude daily analysis (slow loop) | systemd timer |
| Sun 21:00 | managed-agent deep analysis (hermetic sandbox) | systemd timer |

## Telegram at night

Transition-gated only: an incident opens with one 🔴 and closes with one ✅;
milestones (first instances, verdicts moving) are reported once. Everything
else waits for the 08:00 morning report.

## What the user wakes up to

One 08:00 message: overnight instances by cell with resolution rates, corpus
growth, series captured, worker/cron outcomes, incidents (if any), disk, and
the one next decision if one exists.
