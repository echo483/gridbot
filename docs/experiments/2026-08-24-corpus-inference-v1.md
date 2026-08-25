# Corpus inference v1 — verified findings (2026-08-24)

Provenance: multi-agent workflow `corpus-inference-v1` (run wf_3598fd40-786, 11 agents:
5 analyst lenses → cross-lens ranking → 5 adversarial verifiers → synthesis) over
`data/analysis/training_rounds.jsonl` (pull of Supabase `training_rounds`, 22,273 rows,
2026-08-24). 26 raw findings; top 5 verified; **5 confirmed, 0 refuted**. Every number
below was recomputed independently by a verifier agent instructed to refute it.

---

# Gridbot corpus inference report — training_rounds.jsonl

**Date:** 2026-08-24 · **File:** `/home/ops/gridbot/data/analysis/training_rounds.jsonl`
**Corpus:** 22,273 rows = 5,746 unique (series, map, round) rounds × up to 4 time-bins
(b0 5,746 / b1 5,528 / b2 5,570 / b3 5,429), 159 series, 239 source files. 0 duplicate
row_keys/ids; 42 economy-free historical-default rows; 67 rows missing 3 feature keys
(hp_alive_diff, awp_alive_diff, equip_value_diff); 125 rows where top-level
round_number != features.round_number. CT wins 52.9% of rounds (N=5,746).

| # | Finding | Key number | N | Status |
|---|---------|-----------|---|--------|
| F1 | 13% of rows are post-round snapshots encoding their own label (47% of b3) | union filter flags 2,898 rows (13.0%) | 22,273 | CONFIRMED — **blocker** |
| F2 | Score-trailing team with 2+ man advantage wins the round | 0.931 vs 0.39–0.46 score-implied | 606 rounds | CONFIRMED — top in-round pocket |
| F3 | Full-buy vs non-full-buy side wins regardless of scoreline | 0.832, invariant to score | 1,393 rounds | CONFIRMED — broadest state edge |
| F4 | Pistol winner converts round 2/14 | 0.859 (0.874 if loser stays eco) | 468 pairs | CONFIRMED — cleanest trigger |

## F1 — BLOCKER: post-round snapshots leak the label into ~13% of the file, 47% of bin-3

Two independent confirmed detections of the same capture artifact (final-bin snapshot
taken after the round resolved):

- **Score drift:** vs each round's b0 reference, `score_lead` moved in 2,722/22,273 rows
  (12.2%); by bin: b3 2,563/5,429 = **47.2%**, b2 147, b1 12, b0 0. The move equals
  `2*label-1` (the exact leak signature) in **99.4%** of drifted rows. `rounds_played`
  advances in 2,592 rounds; `is_match_point` flips on 142 rows; `momentum_3` varies in
  5 rounds; label itself never varies within a round.
- **Round-index offset:** `rounds_played + 1 != features.round_number` flags 2,796 rows
  (12.55%); checked against top-level round_number, 2,891; **union filter: 2,898 rows =
  13.0%** (b3: 2,581 = 47.5%). [Corrected 2026-08-25: originally misprinted as "2,564 =
  47.2%", a transcription slip of the score-drift detector's separate b3 count (2,563)
  above. Caught by the cs2agent session's independent row-exact reproduction; recount
  against data/analysis/training_rounds.jsonl confirms 2,581/5,429 = 47.54%. The union
  total 2,898 was always correct.] Among leaked non-b0 rows, sign(Δscore_lead) == label in
  99.8%; man_advantage sign predicts label 97.7%; median time_remaining 18s. The file's
  single physically impossible row (6 CT alive) is in this set.

**Consequence:** any q trained or backtested on unfiltered rows reads the finished
scoreboard in ~half of late-round snapshots — exactly the window where H-MODELENTRY1
entries fire. Backtest accuracy there is inflated (sign(score_lead) agreement with
label: 0.56 in b0–b2 vs 0.62 in b3) and live PnL will not match.

**Fix (before trusting any model number):** drop rows failing
`rounds_played + 1 == round_number` against **both** features.round_number and
top-level round_number (removes 2,898 rows → 19,375 honest rows), and rebuild
score-derived features (score_lead, rounds_played, is_match_point, momentum_3) from
each round's b0. Re-run every existing H-MODELENTRY1 validation after the fix.
**Applied in gridbot:** `gridbot.corpus.build` now refuses transition-window snapshots
at build time (commit-linked leak guard), so new rows are clean at the source; the
historical 22k rows must be filtered at training time.

## F2 — Man count vaporizes score: trailing-by-3+ team with a 2-man advantage wins 93%

Filters: leak-honest in-round rows only (`rounds_played == round_number - 1`), state
rows deduped to one per round (highest qualifying bin).
- CT trails score_lead ≤ −3 with man_advantage ≥ +2: P(CT wins) = **0.918** (N=354).
  Mirror (CT leads ≥ +3, down 2+ men): P(CT) = **0.052** (N=252). Pooled side-agnostic:
  **trailing team wins 0.931** (N=606, Wilson CI 0.908–0.948).
- Clean pre-round baseline (b0 rows): trailing CT wins 0.463 (N=1,395); trailing T wins
  0.394 (N=1,380). **Deviation ≈ +47 to +54 points** vs score-implied pricing.
- Robustness: time_remaining ≥ 20s → 0.928 (N=538); ≥ 45s → 0.908 (N=326). Context:
  in-round man_advantage ≥ +2 wins 0.950 at any score (N=1,680; 0.958 leading vs 0.935
  trailing) — score adds ~2pts once man count is known.
- Caveats: 68/606 pocket rounds (11%) only qualify with <20s left, so actionability
  depends on detection latency; alive_ratio ≡ man_advantage/5, so 2v0 blowouts can't be
  separated from live 5v3 (the tr ≥ 45 cut bounds this at 0.908).

## F3 — Full-buy vs non-full-buy is a state override: buyer wins 83%, scoreline-irrelevant

Filters: one rich row per round (max time_remaining; 42 historical rows excluded),
"clean open" = time_remaining ≥ 90, man_advantage == 0, alive_ratio == 0, round_number
in 2–12/14–24. N=3,678 clean opens, CT base 0.548.
- Exactly one side full-buy (loadout ≥ $13,400): that side wins **0.832** (N=1,393,
  CI 0.811–0.851). Occurs in **37.9%** of clean-open regulation non-pistol rounds.
  CT-framed cells: ct-only-full 0.842 (N=789), t-only-full → T wins 0.820 (N=604),
  both full 0.543 (N=2,281).
- **Score invariance:** buyer behind on map score wins 0.825 (N=417), tied 0.820
  (N=128), ahead 0.837 (N=848).
- **Dose-response caveat (trade by gap, not by flag):** buyer-framed equipment gap
  $4–7k → 0.636 (N=44); $7–10k → 0.739 (N=111); ≥$10k → **0.849** (N=1,234,
  CI 0.828–0.868). Force-buy opponents pooled ($4–10k): 0.710 (N=155). Fair value is
  ~0.85 vs true ecos but only ~0.71 vs force-buys.

## F4 — Post-pistol round 2/14 is near-deterministic: pistol winner converts 86%

Filters: one b0 row per round; winner = ct_team if label==1 else t_team; pistol round
r ∈ {1,13} paired with r+1 on same series+map by team name.
- Pooled conversion: **0.859** (402/468, CI 0.825–0.888); after round 1: 0.896
  (172/192); after round 13: 0.833 (230/276). Generic previous-round carryover in all
  other rounds is only 0.611 (N=4,880) — economy effect, not momentum. A pistol win is
  worth ~1.86 expected rounds.
- Conditional on the observable loser buy (clean-open b0 snapshots of rounds 2/14,
  N=435): loser stays non-full in 89.2% of cases → winner converts **0.874** (N=388,
  CI 0.837–0.903); loser force/full-buys (10.8%) → **0.702** (N=47, CI 0.560–0.813).
  Winner full-buys 92.2% of the time.
- Caveat: r2 and r14 pairs from the same map share teams, so the CI is mildly optimistic.

## Refuted this cycle

None — all findings submitted to verification survived. Standing registry closures are
unaffected: **confirmed-event-timing entry (taker and maker variants) remains falsified
and closed.** None of the registered drafts re-enter that lane; all three are persistent
state-mispricing or predictive in-round state mechanisms, not confirmed-flip races.

## Implications for H-MODELENTRY1

1. **Validation gate:** no backtest of q on this file is trustworthy until the F1 leak
   filter is applied; contamination concentrates in exactly the late-round
   threshold-crossing states H-MODELENTRY1 trades. First task before any calibration claim.
2. **State filters beat the scoreboard:** man count (F2) and freeze-end economy (F3/F4)
   are the dominant round-level signals. Any q that weights score/momentum heavily on
   unfiltered data has learned the leak, not the game.
3. **Fair-value anchors for entry thresholds:** trailing+2-men ≈ 0.93; full-buy vs eco
   ≈ 0.85 (vs force ≈ 0.71); post-pistol ≈ 0.86 (0.87/0.70 conditional on loser buy).
   Formalized as frozen hypotheses H-MANADV1, H-ECONBUY1, H-PISTOLCARRY1 in
   `registry.jsonl` (REGISTERED_NO_RUN — instruments do not exist yet; verdicts only
   from held-out capture with time-aligned Polymarket books).
