# Book-flow feature study — do flow signals predict moves p_map misses?

Run: `uv run python scripts/flow_features_study.py` (2026-08-28). Follows
licensed action 3 of the priced-in test: "fix the model's late-information
deficit — features from book-flow are the missing input."

## Method

Offline, deterministic, analysis-only (no engine/registry changes):

- **Target:** `oriented_move` at 30/60/120s from `data/shadow/postquote-*.jsonl`
  (08-27 + 08-28, n≈3.3k per horizon joined with p_map).
- **Baseline:** p_map fair value joined from `data/shadow/mapentry-*.jsonl`
  via (series_id, map_number, dedup_extra), latest row ≤ sample time.
- **Flow features at sample time**, computed only from information already on
  disk before the sample: `data/pmus/books-*.jsonl` does not exist — PMUS book
  snapshots live in `data/pmus/watch-*.jsonl` (`pmus_bbo`, sparse: 333 records
  on 08-27, none on 08-28). The dense quote series is the postquote stream
  itself (each row carries bid/ask/mid at eval time), so trailing momentum /
  spread-change / staleness come from the prior same-book quote ≤120s earlier;
  depth imbalance (`imb`) comes from pmus_bbo `bidDepth`/`askDepth` where a
  snapshot exists (08-27 pre-market only, n≈330 — treat as indicative).

## Results (combined 08-27+08-28; corr = Pearson with oriented move,
partial = controlling for p_map fair; full tables in script output)

| feature | 120s corr | 120s partial | 120s AUC (p_map AUC 0.415) | top-tercile drift |
|---|---|---|---|---|
| mom120 (mid change vs ≤120s ago) | **+0.060** | +0.060 | 0.578 | +0.77pt |
| mom60 | +0.049 | +0.049 | 0.552 | +0.93pt |
| vel (mom/s) | +0.058 | +0.058 | 0.579 | +0.77pt |
| spread_ch | −0.042 | −0.042 | 0.510 | +0.58pt |
| spread | +0.023 | +0.024 | 0.538 | +1.22pt |
| gap_s (staleness) | −0.060 | −0.061 | 0.473 | (bottom terc +1.00pt) |
| imb (depth imbalance) | +0.073 | +0.202 | 0.515 | +3.74pt (n=267) |

p_map itself: corr ≈ 0.00 (−0.006..+0.018), AUC 0.415–0.425 across horizons —
**the game-state baseline has essentially zero short-horizon directional
signal**, so the flow features' raw and partial correlations coincide: their
signal is fully incremental over p_map.

Robustness (per-day): mom120@120s corr +0.037 (t≈1.7) on 08-27, **+0.158
(t≈3.6) on 08-28**; vel and mom60 same pattern. Same sign both days, much
stronger on 08-28 (the day the whole market drifted against us — flow
momentum was the signal that still worked).

## H-PRICEDIN1 licensing view (bar: mean oriented drift > +1.0pt at 120s)

Unconditioned 120s drift: +0.65pt (n=3315).

| conditioning | 120s drift | verdict |
|---|---|---|
| mom120 top tercile | +0.77pt | below bar |
| mom60 top tercile | +0.93pt | below bar |
| spread top tercile | +1.22pt | clears, but spread is a state, not flow — confounded with unquoted/wide markets |
| gap_s bottom tercile (fresh prior quote) | +1.00pt | borderline, n=828 |
| imb top tercile | +3.74pt | clears, n=267, 08-27 pre-market only — not licensable at this n |

**No flow feature, combined with the existing drift test, clears the +1.0pt
licensing bar on licensable evidence.** The features that clear numerically
are exactly the ones with broken provenance (spread = market state; imb =
sparse pre-market depth).

## Conclusion / recommendation

1. **Book-flow momentum (mom120/vel) is real, incremental, and cheap:**
   corr ≈ +0.06 with the 120s oriented move (AUC 0.58 vs p_map's 0.42),
   identical raw and partial correlation because p_map carries ~zero
   short-horizon signal. Worth adding as a *model input* (a flow-pressure
   term in q), NOT as an entry gate.
2. **It is not an entry-licensing signal:** best honest flow-conditioned
   drift at 120s is +0.93pt (mom60 top tercile) — below the H-PRICEDIN1
   +1.0pt bar. Flow momentum shifts the distribution by ~+0.1–0.3pt, an
   order of magnitude below the 10–30pt discounts that motivated the
   priced-in test.
3. **imb (depth imbalance) is the most intriguing signal (partial corr
   +0.20, +3.3–3.7pt drift spread) but n≈330 from pre-market snapshots on
   one day — do not act on it.** Action item: record `pmus_bbo` snapshots
   at mapentry eval time (the recorder already fetches the BBO; persist it)
   so this can be tested at real n.
4. Consistent with the priced-in verdict: the flow we can already see does
   not close the late-information gap enough to re-widen entries. The
   actionable engine change is capture-side (persist BBO at eval time),
   not a new gating rule.
