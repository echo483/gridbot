# Amendment drafts for the second armed night — 2026-08-27

Drafted ~03:35 PT during the first armed session, BEFORE its data is
complete. Nothing here is frozen; each item states who freezes it and on
what evidence. Registry text is copy-paste ready. User decisions needed
on A1 and A2 only.

## A1 — Re-entry after clean cancel (armed policy; NEEDS USER YES)

**Change**: a TTL cancel with status `cancelled` (venue confirmed the
order was never filled) releases the map book's dedup slot for AT MOST
ONE re-entry, at a fresh qualifying crossing, re-priced at the current
book. `gone_before_cancel` (likely fill) never releases. Max 2 entries
per (book, side) per map, ever.

**Tonight's evidence**: the only in-band book (AURORA map1 long) had 4
qualifying crossings; the frozen one-shot rule spent the slot at 09:07
(P_map 0.787, bid 0.53, unfilled) and blocked 3 later chances at better
model prices (P_map 0.542-0.579, asks 0.47-0.49). Counterfactual under
A1: +1 placement tonight (the first re-qualifying crossing after the
09:09 clean cancel).

**Risk**: doubles worst-case attempts per book; churn into a book that
refuses to fill. Bounded by max_open and stake caps regardless.

**Registry text (append on user yes, BEFORE tomorrow's first match)**:
H-MAPENTRY1-REENTRY1 | EXECUTOR_POLICY_FROZEN | "A cancel row with
status=cancelled releases that (hypothesis, series, map, us_side, slug)
dedup key for exactly one further armed entry at a later qualifying
crossing (fresh price computation; band gate and all caps unchanged).
gone_before_cancel and error cancels never release. Hard limit 2
lifetime entries per key. Motivation: first-night observation of 3
blocked in-band chances after a clean cancel on the sole in-band book;
user-endorsed 08-26. Recording population unchanged."

## A2 — Band upper bound 0.80 → 0.65 (armed policy; DO NOT ADOPT YET)

**Recommendation: NO for tomorrow.** The 0.65 boundary is twice-
corroborated but only in-sample CIs exclude zero (H-BAND1 §3,
H-MAPAGG2 §3), and H-MAPENTRY2-AMEND1 pinned the license condition:
the 0.50-0.65 settled-edge CI under v1 must EXCLUDE ZERO on capture
data not seen at registration. Tonight begins that data; n will not be
close for days. Adopting early would repeat the exact selected-band
error the peer retracted. Keep 0.50-0.80; revisit when H-MAPENTRY2's
band arm reports.

## A3 — Aggregation (λ-shrinkage) adoption path (no user action)

H-MAPENTRY2 (+AMEND1/2) already governs this: candidates A/B0/B10/B20
score retrospectively on live capture; verdict at n>=60 resolved maps
(OT-aware oracle); simplicity tiebreak frozen. A winning challenger is
adopted only via a NEW whole instrument (aggregation + boundary + entry
rule together). Nothing to decide until the verdict; the scoring script
is the build item (STATE queue 7).

## A4 — H-WAVE1 registration draft (new lane; register before ANY
recorder runs; runs DRY-RUN regardless per rail 3)

**Mechanism claim**: during rapid upward P_map transit, mapwin-race-v1
UNDER-forecasts the rising side (peer holdout: forecasts 0.2-0.6
realized at 0.48-0.56+, unrepaired in every H-MAPAGG2 candidate), so
the frozen "book ≤ fair − 4pts" test never sees value on comebacks —
observed live on G2 map1 (model 0.573 vs book 0.68 at r11; book led the
model the entire 0.05→0.99 climb).

**Draft frozen text (fill brackets from tonight's full data before
registering)**:
Trigger: first crossing where P_map enters [0.50, 0.80] from below
within a map, with P_map risen ≥ 0.15 over the trailing [3] rounds.
Entry: post-only at min(book ask − tick, CAL(P_map) − 0.02), where CAL
is the frozen empirical calibration map from the peer's holdout
reliability table (documented constants, not fit to our capture).
TTL [150]s, one entry per (book, side), band gate inherited.
Bar: ≥ [40] settled wave entries; mean settled edge > 0 with by-series
CI excluding zero; Brier(CAL(P_map)) < Brier(book) on wave population.
Starvation: <[3]% of maps ever produce a wave trigger → lane closes.
Falsified if the CI excludes zero NEGATIVE or Brier test fails at n.

**Build**: wave detector is analysis of existing mapentry rows (P_map
sequence per book) — recorder-side only, no engine change until a PASS.

## Morning-report checklist (auto-inputs)

Fill-rate truth (placed vs filled per in-band crossing — the
pre-registered read), timing profile (queue 11), in/out-band ledger mix
vs peer's 27% prediction, postquote drift curves, overtime_domain rate
vs 9.0%, reconcile-bug fix plan (queue 1b), H-MAPENTRY2 scorer status.
