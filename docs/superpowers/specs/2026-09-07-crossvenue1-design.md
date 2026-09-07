# H-CROSSVENUE1 — design: a cross-venue mispricing engine on the PMUS match book

Status: DESIGN, pre-registration not yet filed. Nothing here is a licence to trade.
Written 2026-09-07, user-approved concept ("green light"), rail-2 registry row to
be drafted for the user's own approval — never self-registered.

Holdout boundary candidate, stamped before any measurement: **2026-09-07T06:39:27Z**.

---

## 1. What dies, and what precisely survives

This design exists because one specific claim was falsified, and it is important
to name exactly how far the falsification reaches.

**DEAD — `p_map` as a fair-value estimator.** H-MAPENTRY1 priced the map book
against the frozen race aggregation. The measurement (2026-09-01, qualifying rows,
first-eval dedup, n=2,013 / 115 series):

| discount tercile | avg discount | model says | realized | gap |
|---|---|---|---|---|
| small | 0.078 | 0.631 | 0.551 | +0.080 |
| mid | 0.191 | 0.659 | 0.490 | +0.169 |
| large | 0.370 | 0.688 | 0.351 | **+0.337** |

`gap ≈ 0.9 × discount` in every tercile. When our model disagreed with the book,
**the book was ~90% right in proportion to the disagreement**. A recalibrated
`p_cal` therefore converges on "trust the book" — it can prevent bad entries but
can never create edge. Rail C is harm reduction, permanently.

**ALSO DEAD**, and not to be revived without a new mechanism claim:
- the retracted `+0.198 skill` headline (row-pooled across ~80 correlated matches;
  series-clustered it is −0.0007, CI (−0.164, +0.139))
- the discount-as-edge thesis (H-PRICEDIN1)
- post-shock model momentum (NOTE8 — the "+19pt edge" was a dead-book artifact;
  never-repricing books won 95% of it, live books were negative)
- any taker rule keyed on a GRID round event (NOTE9 — map books cost −0.071 to
  −0.151 to cross; match books −0.004 to −0.014, every CI at or below zero)

**SURVIVES, and is load-bearing here — `q_round`.** Measured live on 1,984
resolved shadow instances: pooled Brier 0.1105, skill +0.063, ECE 0.031;
MODELENTRY cells win-vs-forecast .806/.807, .865/.866, .928/.920 at ECE .009–.029.
The round model is calibrated. What failed was the *map-level aggregation of it*
into a price. So `q_round` is retained, and given a different job: **not "what is
this worth" but "is this repricing informed?"**

**ALSO SURVIVES:** the GRID latency lead (13–27s median on round outcomes, ~40s on
threshold crossings) and the wire path (place 394ms, cancel 137ms, measured).

## 2. The mechanism claim

> The intl Polymarket CLOB and Polymarket US are **structurally segmented markets**
> settling the same CS2 events. Intl is deep and continuously traded; PMUS is thin.
> Because the participant sets are disjoint by regulation, no arbitrageur closes the
> gap. Therefore PMUS quotes go **stale** relative to intl after information arrives,
> and a PMUS order that takes intl's side of that staleness has positive expectancy.

The estimator is swapped, not improved: we stop betting our model against a market
and start betting **a large market against a small one**. This is the single
structural change that distinguishes this lane from every dead one above.

Supporting structure, all measured:

| fact | number | source |
|---|---|---|
| intl depth | ~1.19M book events/day, ~44 CS2 markets, real websocket | `data/pm/books-*.jsonl` |
| intl has a trade tape | `last_trade_price` events present | tape inspection 09-07 |
| PMUS match spread | p50 0.02, 78% ≤2 ticks, 5% dead | NOTE9 + recount 09-07 |
| PMUS map spread | p50 0.07–0.12, 22% dead/one-sided | NOTE9 + recount 09-07 |
| match vs map activity | 95 vs 23 change-events/slug (09-06) | recount 09-07 |
| PMUS match depth | bid size p25 31 / p50 138 / p75 795 | sized tape 09-06 |
| our clip at $3 stake | ~6 contracts | `GRIDBOT_MAX_STAKE_USD=3` |
| tradeable events | 790–1,645 covered round-ends/night | recount 09-07 |

The last row is why this lane is viable where H-MAPENTRY1 was not: the map lane
accrued **39** deduped qualifying entries across its entire 40-series holdout and
cannot reach its bar of 60. This lane sees more events in one night.

## 3. The three signals

```
GRID (ground truth, 13-27s lead)
  │  role A: trigger — round ends are when intl reprices and PMUS is stalest
  │  role B: pull/skew resting quotes before informed flow arrives
  ▼
intl CLOB ──── FAIR VALUE (deep, traded; NOT tradeable by this account)
  │
  │  basis = PMUS_price − intl_fair          ← the mispricing
  ▼
PMUS match book ──── EXECUTION (2-tick, ~138 depth, TRADEABLE)
```

**Role B is the edge, not a safety feature.** Posting maker on a thin book means
being filled precisely when wrong — the measured −6.5pt fill-selection cost
(NOTE6), the reason paper edge lived entirely in orders that never filled. GRID's
lead means we learn a round resolved *before the taker who would pick us off can
act*. NOTE9 named this the one remaining use of the latency asset: "skew or pull
resting match-book quotes before the public reprices."

## 4. The square-quote classifier (what "sniper" means operationally)

We do not trade a basis. We trade a **classified counterparty**. Each PMUS quote
observation is labelled:

- **SQUARE / STALE** — intl has repriced following a GRID-confirmed information
  event, and PMUS has not yet moved. This is the target. We take intl's side.
- **INFORMED** — PMUS moves *at or ahead of* intl, or moves without a GRID event
  behind it. Someone knows something. We do not trade; if resting, we pull.
- **NOISE** — basis inside execution cost, or no GRID event behind the move.
  No trade at any accuracy; trading it is how you pay the spread for nothing.

The classifier inputs, all available pre-trade:
`basis`, PMUS quote age (time since last non-heartbeat change), intl move size and
direction since the GRID event, `Δq_round` across the event, round leverage, and
book two-sidedness/depth.

`q_round` enters here and only here: it says whether the information that moved
intl is *real and large enough to matter*, which is what separates a value gap
from a flicker. It never sets a price. **Rail 1 holds: no LLM anywhere in this
path; `q_round` is a calibrated quant model.**

## 5. Execution model — adversarial by construction

| stage | rule | why |
|---|---|---|
| side convention | `intl` Yes → named team → PMUS Yes, asserted per market | NOTE7: 15/24 shorts venue-rejected from exactly this class of error while the ledger said "placed" |
| entry | **maker, post-only** (`participateDontInitiate`) inside the match spread | 2-tick spread; NOTE9 already showed taking this book loses |
| taker exception | only when basis exceeds full spread + margin | must be pre-registered, not discretionary |
| queue | ~6 contracts behind a 138 median queue | **queue position is the whole execution question, and a BBO tape cannot measure it** |
| defense | GRID event → pull or skew before PMUS reprices | this is role B; the edge |
| exit | settlement, or basis convergence | both venues settle the same event — convergence is anchored |
| risk | one side per `market_slug`; `MAX_STAKE=3`, `MAX_OPEN=2` | existing SECONDSIDE1 + caps |
| fees | 0 (venue showed fees null on all 13 real fills) | verify again before arming |

## 6. Falsifiers — frozen before we look

1. **Basis is noise.** If both venues see the same information and PMUS merely has
   wider spreads, basis oscillates around a common fair. *Test: does `intl_mid`
   beat `PMUS_mid` on Brier for the same settled outcome, series-clustered?*
2. **The gap never clears cost.** A 2-tick spread needs ~1 tick each way. If basis
   is typically sub-tick there is no trade at any accuracy. *NOTE8's rule: no
   number without the spread it must cross.*
3. **Queue starvation / adverse fills.** We may be right and never fill, or fill
   only when wrong. This is the failure that killed the last lane and a BBO tape
   **cannot detect it** — hence the trades-tape prerequisite in §8.
4. **The join is wrong.** A sign-inverted Yes convention produces a beautiful,
   stable, entirely fictional signal. See §7.
5. **Segmentation closes.** If PMUS deepens or makers bridge both venues, basis
   compresses. Monitored, not assumed static.

## 7. The join — highest-risk component

Both venues quote a "Yes" price, but *Yes of what*. Intl markets are
`outcomes:["Yes","No"]` with separate token books under a question string; PMUS
markets are team-winner books keyed by `market_slug`. **If intl's Yes resolves to
team A and PMUS's Yes to team B, every number is exactly backwards and looks
excellent.**

Required before any basis is computed:
- resolve both venues' Yes to a **named team**, per market, per day
- assert agreement; refuse the row on mismatch rather than guessing
- a test that deliberately feeds an inverted pair and asserts refusal
- coverage report: how many CS2 matches join per day, and what fraction refuse

## 8. Prerequisite: the trades tape

Falsifier 3 cannot be evaluated on BBO data. Queue position and fill realism need
prints. Two sources, in order of preference:
1. **PMUS authenticated WebSocket `subscribe_trades`** — the venue we actually
   trade. Read-only, keyring-authenticated. This is the correct source.
2. **intl `last_trade_price`** — already captured, but it is the *other* venue;
   usable to characterise intl's information flow, not our fill realism.

Per NOTE8, **no PnL number may be published from this lane before a depth-limited
fill model exists**. EXEC1's machinery (`gridbot.analysis.exec_fills` and friends)
is the pattern to reuse — with its calibration gate honoured, not bypassed.

## 9. Pre-registration structure (to be drafted for the user)

- **Unit:** one observation per (series, map/match market, GRID event), deduped to
  the first qualifying observation per market per event.
- **Holdout:** series first mapped at/after the frozen boundary. Everything before
  it is training/calibration and may be explored freely.
- **Detector bar (clause i):** `intl_mid` Brier < `PMUS_mid` Brier by a margin,
  series-clustered bootstrap, CI excluding zero.
- **Cost bar (clause ii):** basis at signal exceeds measured execution cost, with
  the spread it must cross stated alongside.
- **Execution bar (clause iii):** fill-modelled PnL per contract, depth-limited,
  quote-lifetime aware, series-clustered — with a calibration gate on real orders
  that must PASS before any held-out read (the gate EXEC1 failed at 0.46 vs 0.60).
- **Starvation clause:** minimum qualifying events, so a dead venue renders
  INCONCLUSIVE rather than a false PASS.
- Single terminal look. Interim renders are REFERENCE and never stop accrual.

## 10. BLOCKING FINDING (2026-09-07, join audit) — there is no training data

The join audit returned **0 of 50** PMUS-mapped CS2 matches joining to the intl
catalog, and the reason is not the join key. **Zero CS2 match-level markets have
ever been captured on the intl side.** Every CS2-tagged catalog row across the
whole tape is one season-long future ("Will FaZe win a Tier 1 event in 2026?"):
151 such rows on 09-03, 261 on 09-06, one distinct event.

Root cause, verified live: `pm_discovery.fetch_esports_catalog()`
(`src/gridbot/capture/pm_discovery.py:62-101`) sweeps `/events?closed=false`
with offset paging capped at `offset<2000` and filters esports client-side.
Gamma hard-caps offset paging — `offset=2500` returns
`{"type":"validation error","error":"offset too large, use /events/keyset..."}` —
and the unfiltered list is dominated by politics/sports (4,113 `sports`-tagged
and 3,109 `politics`-tagged rows in our own 09-06 catalog of 10,223). CS2 sorts
below the reachable ceiling forever.

This is the SAME bug class already fixed on the PMUS side and never ported;
`src/gridbot/executor/us_mapping.py:8-13` documents it verbatim ("CS2 is buried
under MLB/NFL... silently starved the first watch lane").

The markets exist. Verified live: `/events?tag_slug=esports&closed=false&limit=100`
returns 100 events of which **88 are Counter-Strike matches**, titled
`Counter-Strike: TeamA vs TeamB (BO3) - Tournament` — a parseable key structurally
analogous to the working GRID↔PMUS matcher.

**Consequence for this design: the calibration phase in §9 cannot run today.**
There is no historical intl CS2 data to calibrate on. Data begins accruing only
after the discovery fix ships. This is a timeline change, not a falsification —
but the spec must not pretend otherwise.

### Related capture gaps found in the same audit

- **Pre-match window uncaptured.** `LiveTracker.live_maps()`
  (`pmus_books.py:87-102`) gates on GRID `started`, so PMUS books are recorded
  only from kickoff. Measured across 50 matches on 09-06, PMUS lists the market a
  median **4.46h** before scheduled start (p25 1.7h, p75 16.5h). That is where a
  thin book is most mispriced, and we have none of it.
- **`subscribe_trades` exists** in the vendored SDK
  (`polymarket_us/websocket/markets.py:36-43`) but `BaseWebSocket.connect()`
  requires Ed25519 auth unconditionally (`websocket/base.py:48-53`, `auth.py:9-40`).
  Provisioning a credential for a capture lane is a USER policy decision.
- **Full book depth is already fetched and then discarded** —
  `Recorder.fetch()` (`pmus_books.py:292-308`) keeps only top-of-book from a
  payload that already contains the ladder. Free depth, zero extra API calls.
- **`GATEWAY_TIMEOUT_S=4.0` is per-phase, not total** (`polymarket_us/client.py:66`,
  `httpx.Client(timeout=float)`), so observed `latency_ms` reaches 7,229ms.
- **`MAX_SLUGS_PER_CYCLE=8`** (`pmus_books.py:52`) against up to 27 concurrent
  slugs means per-slug blindness is seconds to tens of seconds, not the ~2s a
  naive read of `CYCLE_S=2.0` suggests.

### Correction to the record

The "PMUS venue book activity collapsed 78.0 → 2.7 → 23.6 change-events/slug,
venue-side" conclusion in `docs/learnings/2026-09-06.md` is **contaminated**.
`gridbot-pmus-books` did not start until 17:58:48Z on 09-05 (host was down
04:57→17:58Z): that day's tape spans **6.0h**, against 23.7h on 09-06. A 6-hour
day was compared against full days. The map-vs-match comparison is unaffected
(same tape, same days) and still holds.

## 11. Build order (revised)

1. capture cutover — poller fixes, `enable-linger`, evidence-flow monitoring.
   NOT watchdogs: every unit is `Type=simple` with `NotifyAccess=none` and no
   `sd_notify` anywhere, so `WatchdogSec=` would kill capture on a timer.
2. **intl CS2 discovery fix** (`pm_discovery.py`, tag-scoped route) — this is now
   the gating item; without it the lane has no data at all
3. **join + orientation verifier** with the refusal test (§7) — go/no-go
4. pre-match coverage + trades tape (§8) — the second needs a user policy call
5. classifier + basis instrument, on training data only
6. registry row drafted → user registers → holdout accrues → single terminal look

Nothing in steps 2–5 may produce a PnL claim. Rail 3 is unchanged: `dry_run=True`
until a pre-registered PASS, and flipping it live is the user's own act.
