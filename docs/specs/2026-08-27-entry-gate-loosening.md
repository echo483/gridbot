# Queue 13 — entry-gate loosening review (fill-acceptance widening)

User directive (2026-08-27, night 2): "shouldn't chase price but be more
accepting to trades and fills." Confirmed by user after night-2 first order
(clean cancel): the signal is provably tradable; if skipped trades resolve
profitable majority-of-time, loosen skip params.

## The question to answer with data (no code until answered)

For EVERY mapentry row that reached a real book but failed an entry gate
(band_gate, price gap, TTL-expired placements), measure:

1. **Resolution rate of the would-have-traded side** at each gate's margin:
   - band edges: win rate for p_map 0.80–0.85 and 0.45–0.50 (outside band by
     ≤5pts) vs inside-band baseline. If out-by-≤5 resolves >= in-band rate,
     widening the band is free EV.
   - price gap: settled edge when entry price was fair−2pts or fair−3pts
     (below current −4) vs fair−4+. Dose-response curve of edge vs discount.
     If −2/−3 entries still clear positive EV net of spread+fees, the gate
     admits more fills at smaller edge.
2. **TTL/fill interaction**: of clean-cancelled placements, what did fair do
   afterwards? If our quotes were *right* (book later traveled to us),
   longer TTL or mid-quote (bid+tick → mid) converts cancels to fills.
3. **Keeper constraint**: any widened gate must keep the H-BAND1 lesson —
   no positive-edge claim from post-hoc selection; register parameter
   amendments as pre-committed cells BEFORE reading their live outcomes.

## Candidate levers (ordered by evidence needed)

| lever | change | data needed before flipping |
|---|---|---|
| L1 band width | 0.50–0.80 → widen ±0.05 | stratified resolution table (1) |
| L2 discount depth | fair−4pts → fair−2 or −3 | dose-response curve (2), must clear fees |
| L3 quote level | bid+tick → mid when spread ≤0.04 | spread distribution from books capture |
| L4 TTL | 150s → 240–300s on slow maps | cancel-audit: did book reach our price <300s? |

## User night-2 fill-rate observation (2026-08-27, overrides lever ordering)

Watching placements live: **fills, not edge, are the binding constraint.**
A 27c maker quote on a book trading 7–9% will NEVER fill in any TTL — the
entry logic priced off model fair but ignored *where the active market
actually sits*. User: "tailor to be closer to the active book so trades get
filled a good amt of the time. shouldn't chase price."

**Implication for L2/L3 (elevated to primary):** quote distance must be
measured against the ACTIVE book side, not just frozen fair:
- if ask < fair−4pts we already qualify — fine;
- if our bid+tick lands far below best bid (quote 27c vs market ~9c for the
  other side's perception), the resting order is dead inventory.
Candidate rule: quote = max(bid + tick, min(fair−4pts, ask − tick)) — stay
inside the spread whenever the spread is inside our discount envelope. Edge
floor stays (never pay above fair−4), but fills convert because the quote
sits where flow actually crosses.

**Owner action:** backtest against tonight's mapentry rows + books capture:
for each gated row, compute would-fill rate under (a) current bid+tick rule,
(b) inside-spread rule at same edge floor. Report both WR/RR before any PR.

## Queue 14 — smart exits (user directive, 2026-08-27 night 2)

Night 2's winning trade was closed MANUALLY by the user at 0.99 (bought 0.42,
market never resolved — exit captured the move without waiting for settle).
User: "we should be able to have smart exits when the bid is at an acceptable
place considering the trade." Later item; model continues freely until then.

**Design sketch (to spec when prioritized):**
- Exit rule family for long positions: sell (maker or taker) when bid >= f(p_map):
  e.g. bid >= q_eff + margin (edge realized), or bid >= 0.95 (near-certain),
  or EV-neutral vs holding: exit when bid - entry >= expected holdout value of
  riding to settlement under q — exit only beats hold when offer exceeds the
  probability-weighted payoff minus fill-risk.
- Symmetric short-exit logic once shorts carry real inventory.
- Must interact with TTL/settle writer: exits need their own ledger rows
  (`exit` type) and reconcile pairing so PnL accounting stays venue-true.
- KEEP maker-preference: post the ask-tick, fall back to taker only on a
  threshold that already banked most of the edge.
- Pre-register exit cells before enabling on armed sessions.

Priority below Queue 13 (fill acceptance) per user: "comes later."

## Queue 15 — market-selection signal: book two-sidedness as a tradability gate (user observation, night 2)

User noticed the only games with PMUS mapping tonight were ones where "the
other games are one sided and somewhat untradable" — possibly deliberate
listing behavior by the venue. Observation to test, not assume:

- Measure per-mapped-market: pre-match and in-play quote depth/two-sidedness,
  spread stability, and whether untradeable (one-sided, no bids) books
  correlate with certain match classes (tier, region, heavily-favored
  favorites). The Voca-FV map2 post-resolve dead book is an instance.
- If predictive of fill futility, add a **tradability pre-gate** before
  placement: skip markets whose recent book history shows persistent
  one-sidedness (saves TTL slots + ledger noise), while keeping genuinely
  thin-but-two-sided books tradeable.
- Data source: books capture already on disk (pm/ws snapshots) — analysis
  lane, no engine change until measured.

Priority: below Queues 13/14.

## Queue 16 — band-skip near-cross measurement (failure sweep H4, 08-27)

67% of raw intents die at the band gate. Measure, per band-skip, whether
the market crosses INTO the accepted band within 60–120s of the skip.
A non-trivial cross-in rate means the price cadence read lags the edge
(timing problem); near-zero means the 67% is honest no-edge. Data: shadow
mapentry rows + captured books, analysis lane only.

## Queue 17 — rail-blocked counterfactual (failure sweep H3, 08-27)

72.8% of band survivors are killed by capacity/reentry caps, not price.
Before ANY cap change: replay the 91 rail-blocked intents against
captured books — would-fill, then settled edge (same method as Queue 13).
If blocked intents would have been cost-positive, capital scaling has a
measured payoff; if not, the caps are free. No cap change without a
pre-registered amendment.

## Queue 16 — full-slate coverage audit (night-3 gap, user directive: "watching all live games and finding the right trades")

Night-3 audit (02:55Z): 19 series polled live by capture, but only 7 had
mapentry evaluations; 12 had ZERO (ex-RUBY-GenOne, OG-exZT, Metizport-Bushido,
WOPA-G2 Ares, Color-HYPERSPIRIT, Misa-ILLYRIANS, Lynx pairings, PuckChamp,
RE.Arise, Phantom-QuB, 1win-SPARTA — mostly 08:00–11:00Z starts). Pattern:
evaluated series are yesterday's continuing maps; newly-starting series
produce no mapentry rows.

**Requirement:** every live GRID series with a PMUS mapping gets mapentry
evaluation from its first polled round — no series falls through the
day-rollover or start-time seam. Audit points:
1. mapentry daemon's series-discovery trigger: does it re-sweep discovery
   files each cycle or only at startup / on known series?
2. day-rollover seam (UTC midnight + morning starts): new-day discovery
   file must feed the evaluator immediately (cf. the two-rollover bugs
   already fixed in loader/alerts/settle — this is the fourth instance of
   the same class).
3. If the engine only evaluates series it saw BEFORE arming, add a live
   re-sweep. Test: start a synthetic series mid-session, assert evals.

Priority: HIGH for tomorrow's dev session — this caps how many right trades
we can even see.

## Rails

- All four remain maker-only: we never cross the ask (no chasing).
- Any flip = registry amendment row (pre-commitment), engine PR, then arm.
- Shadow-first: run widened gates as shadow-cells for >=1 session, compare
  vs frozen current gates on same stream before touching armed params.

Priority for next dev session.
