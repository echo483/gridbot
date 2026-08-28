# Priced-in test — is the discount real edge or the market's true price?

Run: `uv run python scripts/priced_in_test.py` (2026-08-28, after night-4 −$9.23)

## The question (user's hypothesis)

"If it's discounted it's probably priced in" — are we the slow money? Night 4:
3W–10L cumulative on fills, losers concentrated in "discounted" LONGs.

## Evidence

### 1. Fill outcomes by discount bucket (n=13 fills, 2,844 real-book evals)

| discount bucket | evals | fills | win% | 95% CI |
|---|---|---|---|---|
| 0–4pt (below gate) | 2080 | 5 | 0.20 | — |
| 4–10pt | 181 | 1 | 0.00 | — |
| 10–20pt | 261 | 3 | 0.67 | 0.21–0.94 |
| 20–30pt | 225 | 6 | 0.50 | 0.19–0.81 |
| 30+pt | 917 | 0 (never quoted) | — | — |

Corpus anchor for these states was 0.83–0.85. Live fills run 0.20–0.67 —
**the anchor is not transferring.** All CIs include or sit far below anchor.

### 2. Post-quote drift test (does price move OUR way after a discount?)

Oriented mid drift after model-discounted states (n≈7,900 samples):

| bucket | +30s | +60s | +120s |
|---|---|---|---|
| 10–20pt | +0.43pt | +0.62pt | **+0.94pt** |
| 20–30pt | +0.54pt | +0.50pt | +0.63pt |
| 30+pt | +0.13pt | +0.24pt | +0.50pt |

Market-wide baseline (all postquotes, no discount filter): 08-27 +0.86pt,
08-28 −0.19pt at 120s.

**Interpretation:** discounted states drift up slightly MORE than baseline on
08-27 (+0.94 vs +0.86 — a wash) and on 08-28 the whole market drifted down
while discounted states still drifted up (+0.50 vs −0.19 — mild positive).
So the *direction* is weakly right, but the magnitude (~+0.5–1pt per 2min)
is nowhere near the 20pt+ discounts we pay against. **The drift cannot repay
the discount.** That is the signature of priced-in-with-noise, not edge.

### 3. Why the corpus lied

The 0.83–0.85 anchors came from states where the model was built on
score/econ features with 13–27s latency lead. Live, PMUS books reprice on
flow we don't see (kill-feed + stream watchers), and our p_map is stale or
over-confident exactly when books gap. The discount is mostly the model
being wrong late, not the market being slow. Corroborating: STATE.md's own
diagnostic (ADR-0038) showed over-forecast above P_map≈0.8 with settled edge
−8..−9.5pt in that region.

## Verdict

**User hypothesis confirmed at current margins:** at 10–30pt "discounts" the
realized edge is ~0 ± wide CI, and the drift test shows no mechanism to
recoup the gap. We are not harvesting mispricing; we are buying lottery
tickets on model error. The band-widening experiment (0.40–0.90) made this
worse by admitting more of these states.

## Licensed actions (need registry row + user sign-off)

1. **Revert band to (0.50, 0.80)** — the experiment answered: more entries ≠
   more edge.
2. **Raise entry margin**: fair − 4pts → fair − 10pts at minimum, OR gate on
   the postquote drift itself (require the market to START moving our way
   before paying).
3. **Fix the model's late-information deficit** — the real fix. The model
   sees GRID state 13–27s before books on round events, but is blind to
   accumulated flow pressure between rounds. Features from book-flow (not
   just game state) are the missing input.
4. **Stop judging fills in isolation** — n=13 is nothing. The drift test
   (n≈8k) is the real instrument; make it a registered diagnostic with a
   frozen bar (e.g. mean oriented drift at 120s must exceed +1pt to license
   entry at that bucket).

Registry row drafted as H-PRICEDIN1 (test amendment, no engine change).
