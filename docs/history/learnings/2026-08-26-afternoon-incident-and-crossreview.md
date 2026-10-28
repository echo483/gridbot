# 2026-08-26 afternoon — keyring incident, hidden fills, first cross-review round

## The incident (full causal chain, verified from journal)

1. gnome-keyring-daemon (PID 1755, PAM-unlocked at login 08-24) ran
   memory-stressed for a day (`couldn't lock 16384 bytes` 08-25; 808MB
   peak, 63MB swapped).
2. 11:43 PT: it stopped answering D-Bus. The armed engine's per-cycle
   arming gate took `SecretServiceNotAvailableException` uncaught →
   **armed session died**. Luck: all 15 orders were already TTL-cancelled;
   a crash with resting orders would have left live quotes unguarded.
3. 11:46:13: GLib assertion → SIGABRT core dump. systemd restarted it —
   **locked** (PAM only unlocks at login). A gcr password prompt spawned
   at 11:46:19 and sat unanswered; every Secret Service read then
   BLOCKED indefinitely behind it.
4. Cascade: restarted dry-run engine wedged on first `check_armed()`;
   pytest wedged (a test touched the real keyring); gridbot-pmus-map
   systemd job hung 2.5h "activating"; **GRID capture went blind at
   11:46** — it reads the API key per poll (`Failed to unlock the
   collection`), while PM-book capture (websocket, keyless) kept
   recording. Corrected a wrong belief: capture does NOT hold the key in
   memory.

**Meta-lesson**: one OS daemon was a single point of failure for the
armed engine, the dry-run pipe, the test suite, a systemd timer, and
half of capture — and it failed BOTH ways (raise, then block) within
three minutes. Anything that can fail must be assumed to also HANG.

## Fixes (commits 403f62a, 02c3fe2)

- `config._secret`: keyring reads in a daemon thread, 5s timeout;
  in-process last-good cache; **cache expires 600s after the last
  healthy confirmation** so a wedged keyring cannot keep an armed engine
  on a possibly-deleted credential (deletion stays a working kill
  switch, bounded); degradation logged on transition; module lock.
- Engine cycle: TTL sweep runs FIRST and whenever resting orders exist;
  arming-gate exception fails closed to dry_run; each follow isolated;
  main loop survives anything.
- Reconcile: clean cancels release ledger-open orders **only with no
  fill row**; failed venue fetch ≠ empty book; anomaly-keyed logging and
  ledger appends (first night wrote 558 spam rows for one order).
- Test suite keyring-hermetic via conftest fake.

## The hidden-fills discovery

First-night ledger: 15 placed / 15 clean cancels / 0 fills. Venue truth
(user report + partial-fill mechanics): fills DID happen — post-only
bids **partially fill, then the TTL cancel succeeds on the remainder and
reports "cancelled"**. A clean ledger can hide held contracts. Built
`executor/fills.py`: venue `orders.retrieve` + POSITION_RESOLUTION
activities → ledger `fill`/`settle` rows on the reconcile cadence;
`open_exposure` keeps counting filled-then-cancelled books until settle.
Never trust cancel status as fill truth.

## First cross-review round (user directive: agents review each other)

- hermes + glm-5.3 (`--provider openrouter`) adversarially reviewed
  403f62a: **9 findings, all verified real**, including a BLOCKER
  (unbounded cache broke the deletion kill switch exactly in the wedge
  state) and a MAJOR (blanket cycle catch could starve the TTL sweep it
  claimed to protect). Every finding applied + tested same afternoon.
- deepseek-v4-flash drafted the H-MAPENTRY2 scorer (691 lines + 50
  passing tests) from the frozen registry text. DP/ECE/bootstrap core
  reviewed sound; its winner oracle is unusable **because my dispatch
  prompt told it the wrong data source** (mapentry rows stop at the last
  crossing; winners must come from the GRID stream, which carries
  explicit finished/won flags). The defect was in the prompt, not the
  model. Marked DRAFT; oracle re-dispatched to deepseek-0731.
- Model quality is VARIANT-specific: `deepseek-v4-flash` and
  `deepseek-v4-pro-0813` produced mojibake through hermes;
  `deepseek-v4-flash-0731` (user: "#1s rn") and glm-5.3 are solid.
  Routing table now lives in `scripts/dispatch.py` (programming/
  classification → deepseek-0731; drafts → glm-5.3-flash (then stealth 'ox-alpha') free tier; review →
  opus-5 via OpenRouter; review2 → glm-5.3). hermes `-z` transcripts
  mirror to the user's Telegram — batch dispatches, don't spam.

## Timing profile (first real numbers, n=15 placed)

Signal→ledger median 8.5s (min 5.5, max 38) — our own pipe consumes a
third of the 13–27s intl lead; 3 outliers unexplained (queued). Post-
quote oriented drift (sides separate, all qualifying rows): long
+0.85c@30s / +0.53c@60s / −0.57c@120s; short ~0 then +1.7c@120s. No
adverse-selection signature. TTL 150s spans the favorable window.

## Policy movement

H-MAPENTRY1-REENTRY1 frozen + user-approved (explicit yes): clean cancel
releases the key for ONE re-entry; fill-bearing keys never release; 2
lifetime entries per key, ledger-derived. Tightened vs the morning draft
after the hidden-fills discovery. Implemented + tested (353 pass),
committed only after an independent Opus 5 review of the diff.
