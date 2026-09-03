# gridbot — system state baseline

Frozen 2026-08-26 ~00:15 PT, the night of the first live-small fire.
**Updated 2026-08-28** with the first calibration of the traded quantity and
the fill-selection measurement — see PROVEN below; both change how the
running PnL should be read.
**Updated 2026-09-03** — integrity pass (docs/analysis/2026-09-03-integrity-pass.md):
the short side was posted at the WRONG venue price for every armed night
(registry H-MAPENTRY1-NOTE7 — 20 of 48 venue-verified orders never rested;
see gotchas), the fail-open ledger-read window (1e-M3) is closed, nightly
analysis notes no longer truncate silently, the hermes builder profile runs
the doctrine model, and the timing-chain instrument is specced/in build.
**Fresh agent? Read in this order:** `CLAUDE.md` (iron rails — merge-blocking),
this file, `docs/experiments/registry.jsonl` (every frozen hypothesis),
`docs/LIVE_FIRE.md` (arming + runbook). The memory directory
(`~/.claude/projects/-home-ops-gridbot/memory/`) carries the cross-session
narrative; this file is the repo-side snapshot.

## What this is

An experiment factory trading esports prediction markets (Polymarket US)
on GRID Open Access data. The edge claim: GRID reports CS2 round outcomes
**13–27s before books reprice** (median; threshold-crossings lead by ~40s).
Everything else is discipline around that: calibrated quant model (q — never
an LLM, rail 1), pre-registered hypotheses with frozen falsification bars
(rail 2), dry_run until a PASS with live-small clips as execution evidence
(rail 3), capture continuity as the moat (rail 4). LLMs live in the slow
loop only: analysis, reviews, reports, hypothesis drafts.

## What is PROVEN (as of this baseline)

| claim | evidence |
|---|---|
| Wire path works | Real order C3KCSKP1PFSG placed+cancelled 2026-08-25: preview 2246ms (one-time auth), **place 394ms, cancel 137ms** — 531ms round trip = 2–4% of the latency lead. `data/executor/wire-baseline.json` |
| Round model is calibrated LIVE | 1,984 resolved shadow instances: pooled Brier 0.1105 (skill +0.063), ECE 0.031. MODELENTRY cells win-vs-forecast: .806/.807, .865/.866, .928/.920 (ECE .009–.029, n=1,613) |
| Event lanes track their bars | MANADV 91/80 win 0.956 (read it clock-stratified per H-MANADV1-CLOCKSTRAT before any verdict); ECONBUY ≥10k 199/120 win 0.83 (in corpus CI); PISTOL 66/100 win 0.83 |
| EV positive at conservative bound | Wilson-lo EV/contract at disciplined entry: MANADV +.042, thr0.85 +.022, thr0.75 +.008, thr0.65 +.004 (`scripts/quant_eval.py`, data/analysis/quant_eval-*.json) |
| GRID↔PMUS visibility | Mapping snapshot every 15 min: tier-1 matches join at coverage 1.0 (AURORA-G2, Spirit, NAVI-M80...). `data/pmus/mappings-*.jsonl` |
| **The traded number OVER-FORECASTS in the armed band** (the +0.198 skill claim is RETRACTED) | 2026-08-31, 434 resolved maps, **series-clustered** (2,000-rep bootstrap resampling 80 series, not 2,668 rows): armed-band gap **−0.2129, CI (−0.309, −0.114) — excludes zero**, so p_map runs 21pts above truth inside 0.50–0.80 and the engine posts at p_map−0.05, i.e. **~16pts above fair**. Skill vs base rate is **−0.0007, CI (−0.164, +0.139) — includes zero**. The 08-28 "+0.198 skill" headline was pooled at ROW level, treating 11,016 correlated rows from ~80 matches as independent; it was never a real edge and it did not decay — it evaporated under a correct test. The MODEL IS FROZEN and unchanged between the two runs. `docs/analysis/2026-08-30-pmap-slate-variance.md` |
| **The running loss is not evidence** | ~1,500 settled trades separate a +2pt edge from zero at 95% (σ=√(p(1−p))≈0.40); we have 13. The 13 give mean −$0.615, SE $0.44, CI95 (−1.48, +0.25) — indistinguishable from zero AND from a real edge. Four independent models derived this number in parallel. |

**Where the money actually goes (2026-08-28, H-MAPENTRY1-NOTE5/NOTE6).**
Paper edge on the entry signal is **+2.8pt/contract** deduped to one entry
per map book (n=117). Realized is −61pt/trade. The gap is **fill
selection**, not signal: of 114 opportunities, the 41% that would have
filled run win 0.553 / EV −3.5pt while the 59% that never fill run win
0.672 / EV +7.5pt, at the *same* price — a **−6.5pt selection cost**.
Nothing there is significant (every CI spans zero; z=1.29, p≈0.20;
resolving it needs ~500 filled orders vs 47 held) — direction only.
Corroborated analytically: qualification forces `side_ask ≤ p_map − 0.04`,
so `side_ask − 0.01 ≤ p_map − 0.05` and **`entry_price_max` never binds**.
The posted price is always `side_ask − 0.01`: the model gates *whether* to
enter, never *at what price*. Entry pricing is book-anchored, not
fair-anchored. `scripts/fill_conditional_edge.py`.
**Two amendment candidates recorded, NEITHER applied** (both need their own
pre-registration + held-out window): trade a calibrated `p_cal` instead of
raw `p_map`; or widen `ENTRY_MARGIN` to cover the measured bias. An
execution-side change is the third, and the mechanism now points there.

**Priced-in finding (2026-08-28, H-PRICEDIN1):** the "discount" is NOT edge.
Fill outcomes by discount bucket (n=13 fills, 2,844 book evals) run 0.20–0.67
win vs corpus anchors 0.83–0.85, and the postquote drift test (n≈7,900)
shows ~+0.5–1pt oriented drift per 2min — an order of magnitude short of
repaying 20pt+ discounts (docs/analysis/2026-08-28-priced-in-test.md).
Licensed responses, both landed: US band gate REVERTED to (0.50, 0.80)
(99520cc — the widened 0.40–0.90 experiment admitted more of these
lottery-ticket states), and a drift-confirmation gate now blocks mapentry
placement unless the postquote diagnostic shows the market already moving
our way — fail closed (a713593). The drift gate is a registered diagnostic
with a frozen bar, not a tuned parameter.

**NOT yet proven:** map-level aggregation calibration (H-MAPENTRY1 has zero
instances — tonight starts it; full-slate coverage now records one eval per
coherent round of every live series, src_hypothesis_id H-MAPENTRY1-SLATE);
fill probability at fair−4pts (the single
biggest unknown); any strategy PASS. No strategy is validated for scale.
**External diagnostic (cs2agent ADR-0038, our pinned 32-series holdout,
NOT a verdict input):** q at crossings transfers calibrated (+3pts
UNDER-confident at τ=0.85, safe direction), but the race aggregation's iid
p_fut over-forecasts above P_map≈0.8 (ECE 0.137-0.145 vs 0.08 bar; entry
at −4pts measured −8..−9.5pts settled edge up there, by-series CIs
excluding zero). Armed entries are band-gated to 0.50 ≤ P_map ≤ 0.80 —
frozen pre-first-fire as H-MAPENTRY1-BANDGATE1. The gate is HARM
REDUCTION only: H-BAND1 showed the kept band has NO demonstrated positive
edge (holdout CIs all include zero; the +0.026 was post-hoc selection).
Pre-registered first-night read: thin in-band ledger = consistent with
mechanism; in-band fills arriving READILY = adverse-selection warning
(judge fill rate per crossing, not count). 0.50-0.65 is the pre-declared
candidate for the next gate amendment (positive 6/6 cells, but only
in-sample CIs exclude zero) — do NOT re-cut on same-night data.

## What is RUNNING (systemd --user)

| unit | role |
|---|---|
| gridbot-capture.service | GRID SS/CD + PM books capture, sleep-inhibited (corpus moat) |
| gridbot-shadow.service | detectors for 4 registered lanes → data/shadow/instances-*.jsonl |
| gridbot-mapentry.service | H-MAPENTRY1 recorder: joins instances to PMUS map books, frozen race fair value, qualification — data/shadow/mapentry-*.jsonl |
| gridbot-executor.service | dry-run engine: instances+mapentry → capped intents ledger (NEVER arms itself) |
| gridbot-postquote.service | adverse-selection diagnostic: samples book mid +30/+60/+120s after every QUALIFYING mapentry row, ORIENTED to taken side — data/shadow/postquote-*.jsonl (analysis rule: never pool unoriented across sides) |
| gridbot-pmus-books.service | PMUS BBO tape (timing-chain Part A, 2026-09-03): polls the live map + match books of every live mapped series on the public gateway every 2s, change-only rows + 30s heartbeat — data/pmus/books-*.jsonl; read-only, no keys. Joined by scripts/timing_chain.py |
| gridbot-pmus-map.timer (15m) | GRID↔PMUS mapping snapshots |
| gridbot-ingest (1h) / gridbot-backup (05:10) | corpus → Supabase; raw offsite |
| gridbot-analysis (22:30) / gridbot-morning (08:00) | slow-loop Claude analyst; morning digest (hermes→Telegram) |
| gridbot-health (5m) + hermes crons | self-heal + escalation webhook + capture QA + spend guard |

## Signal path (the whole trade, one line each)

1. GRID poll → capture day files (`data/grid/`, `data/pm/`).
2. Shadow detector fires → instance row (hypothesis, q/fair_value, state, ts).
3. mapentry recorder: instance → frozen `mapwin-race-v1` P(map) → live PMUS
   map-book top (public gateway) → qualification `side_ask ≤ P(map) − 0.04`
   → mapentry row (ALL evaluations recorded — starvation denominator).
4. Executor engine: QUALIFYING mapentry row → post-only maker intent one
   tick inside, clamped (frozen cap ∧ ceiling 0.90), integer contracts,
   dedup one-entry-per-map-book → ledger row (`data/executor/intents-*.jsonl`).
5. dry_run (default): row says dry_run. ARMED (user's act only): pmus.place
   (our-side cost → venue Yes price via `wire_price`; an all-REJECTED create
   ack ledgers `rejected:venue:*`, never placed) → order_id persisted → 150s
   TTL auto-cancel if unfilled → reconcile pass (ledger vs venue
   orders.list) every ~60s; a failed ledger read blocks placement.

## Arming (rail 3 — the user's own act, never an agent's)

Runbook in `docs/LIVE_FIRE.md`. Gate: GRIDBOT_LIVE=1 + both US keys in
keyring + compliance affirmation line. Caps for the $26 bankroll era:
`GRIDBOT_MAX_STAKE_USD=3 GRIDBOT_MAX_OPEN=2` (≈ quarter-Kelly; ~$6 max
resting). KNOWN GAP: settles are not auto-written yet → daily-loss cap
doesn't bind on realized PnL; risk is bounded by stake×max_open instead.
**Next build: fills→settles writer from venue data.**

## Hard-won gotchas (do not relearn these)

- **PMUS discovery**: `events.list` pagination buries esports; `seriesSlug`
  param is silently IGNORED server-side. Use `search.query` + time-boxed
  `events.list(startTimeMin/Max)`. BBO = `marketData.bestBid/.bestAsk.value`
  (zero = dead book). Market slug field is `slug` upstream, stored as
  `market_slug` in mapping rows.
- **PMUS venue**: match/map winner books ONLY — no round books. Shorts price
  as 1 − long bid. `participateDontInitiate` = exchange-enforced post-only.
  Thursday 2–6am ET maintenance cancels all orders. 20 req/s.
- **Never install `dev-polymarket/clob-client-v2`** — typosquat. Official:
  `polymarket-us` (PyPI), Polymarket org repos. Intl CLOB is close-only for
  US IPs; py-clob-client v1 died 2026-04-28.
- **Claude 5 API**: thinking is on by default and counts against max_tokens —
  a long prompt with max_tokens 1500 can return 200 OK with ZERO text
  (observed; fixed in analysis.daily with 3000 + retry + loud failure).
- **systemd**: daemons can't block OS sleep — capture wraps in
  `systemd-inhibit --what=sleep`. KDE idle-suspend once cost 10.8h of
  capture. Keyring-needing units anchor to graphical-session.target.
- **Corpus**: CORPUS-F1 leak filter is binding (13% of rows were post-round
  transition snapshots; every pre-filter calibration number is suspect).
- **Creates are NEVER retried** (double-order risk); cancels ARE (×3).
  Ambiguous 2xx = "error:ambiguous_ack", never "rejected".
- **Hermes**: kanban create needs explicit `assign` + `set-model` after;
  a hook blocks env-dump-looking command bodies (rephrase); telegram sends
  work standalone via `hermes send -t telegram`. Judge dispatched agents
  by ARTIFACTS, not transcripts — the `-z` transcript channel renders
  mojibake for several models while the files they write are clean.
- **Secret Service is a single point of failure that fails BOTH ways**
  (2026-08-26): gnome-keyring crashed (killed the armed engine), came
  back LOCKED (reads BLOCK forever; wedged daemon, tests, gh/git-push,
  and capture — which reads the key per poll). Recovery: user runs
  `gnome-keyring-daemon --replace --unlock` (gcr prompter can be dead —
  no dialogs ever appear). Capture data gap 11:46–14:59 PT on 08-26
  (GRID + PM books; analyses over that window are missing data).
  **RECURRED 2026-08-31**: keyring restarted LOCKED at 10:14, the 600s
  last-good cache expired at 10:24, GRID capture failed closed for 100+ min
  (~4,200 snapshots, 7 live series). PM capture was UNAFFECTED, so that
  window holds book prices with NO game state to join them to — the join
  died, not just the volume, and orphaned books can never produce a mapentry
  row. Second capture loss in six days from workstation session lifecycle
  (08-30: powered off 6h31m), ZERO from code. The always-on-host question is
  the top open ops decision. `docs/analysis/2026-08-31-keyring-capture-outage.md`
- **Wedged keyring read, third occurrence (2026-09-03, 09:00–14:50 PDT,
  ~5h50m GRID-dark; PM books unaffected)**: the capture daemon's in-process
  keyring worker thread never returned, every later `_secret()` short-
  circuited to "read still blocked", the 600s cache expired, and the unit
  stayed ACTIVE (heartbeats every minute) — so the health check only
  alerted "GRID data stale" and never restarted it, while a fresh process
  could read the key in 0.1s. Fixed both ends: `config._secret` abandons a
  read blocked >120s and issues a fresh one (≤8 abandoned threads, then
  fail closed), and `health_check` restarts an ACTIVE capture unit whose
  GRID tape is stale >25min (once/hour, graphical session only; a truly
  locked keyring then fails the unit fast and the DOWN branch escalates).
  The running capture daemon keeps the old code until its next natural
  restart (rail 4); the health check picks up the new rule on its next
  5-min run. No mapentry rows exist for that window.
- **Venue reports cancel success on already-FILLED orders** (confirmed in
  production: all 3 first-night fills were FULL fills whose TTL cancels
  returned clean "cancelled"). Never read cancel status as fill truth —
  fills.sync / settle_truth.py are the truth path.
- **Venue price convention (PINNED 2026-09-03, registry NOTE7)**: the
  Polymarket US `price` field is ALWAYS the Yes/long price; `BUY_SHORT` at
  P is stored as `SELL @ P` and costs 1−P per contract (venue records:
  order C4KWZ5EV8GNE, cost 2.45 = 5×0.49). gridbot prices are OUR side's
  cost (short `side_ask = 1 − long_bid`); the wire converts ONLY in
  `pmus.wire_price` and fills convert back in `fills.fill_row` (`venue_px`
  keeps the raw number). Before the fix every short was sent at Yes price =
  cost: shorts with cost < 0.50 crossed the bid and were post-only REJECTED
  (15/24 armed-night shorts), the rest rested too far from the touch. The
  old "shorts fill at 1−price cost, pin the ledger formula" reading was
  this wire bug — `realized_pnl` was already right; `venue_pnl` still
  outranks it. The three pre-fix short fill rows carry Yes prices (true
  costs 0.48/0.48/0.49).
- **The venue hands back an order id for orders it then REJECTS** (post-only
  would-cross, stale book between the BBO read and the wire): 20 of the 48
  venue-verified armed-night orders (15 shorts, 5 longs) never rested but
  were ledgered `placed`. Real-order fill rate among orders that rested is
  13/28 = .46, not .27 — re-cut any placed/filled statistic on rested orders
  (fill_check / corrective-cancel rows now carry `venue_state`; synchronous
  rejections ledger as `rejected:venue:<reason>` and free the dedup key).

## Where verdicts come from

`gridbot.instruments.verdict` (oracle = first-observation round winners from
captured stream; cells match the registry). `scripts/quant_eval.py` is the
metrics layer (Brier/ECE/EV/MC/Brownian) — REPORTING only; the frozen bars
in the registry are the only PASS/FAIL rule. H-MAPENTRY1's bar: beat the
book's Brier, ECE ≤ 0.08, settled edge CI > 0 at n≥150/60; starvation
clause closes the lane if <5% of instances ever show a qualifying quote.

**H-MAPENTRY1 now HAS a verdict path (2026-08-31, commit 256d3a0).** It had
none for five nights: verdict.py read only instance rows, so the one armable
lane appeared in no verdict row and its bar was uncomputable by any code in
the tree. `src/gridbot/instruments/mapentry_verdict.py` scores all three
clauses + starvation, series-clustered, SLATE rows split from DETECTOR, and
`--book-reading {mid,side_ask}` because the registry's "book price as
forecast" is ambiguous AND THE READING FLIPS CLAUSE (i). First output on
captured data: **all three clauses read breached** (status
FALSIFIED_ON_CAPTURED_DATA). That is **NOT a registry verdict** — the bar
requires ≥150 HELD-OUT instances and no holdout has been declared; the result
carries `holdout_declared=false`. Two user decisions gate a real verdict:
pin the book-price reading, and declare the holdout window.
NOTE the bar says ECE "(quintile bins)" = FIVE bins (0.1144), which is a
DIFFERENT number from the 10-bin ECE every earlier analysis reported (0.1301).
Only the quintile figure is the bar.

## Open work queue (post-first-fire)

1. DONE 08-26 afternoon (executor/fills.py, awaiting independent review
   before the commit lands): venue orders.retrieve + activities
   (POSITION_RESOLUTION) → ledger fill/settle rows on the reconcile
   cadence; exposure now keeps counting a partially-filled-then-cancelled
   book until settle (first night: every real fill hid behind a clean
   "cancelled" — post-only partial fills, then TTL cancel succeeds on the
   remainder, ledger looks clean while contracts are held). Fill-tick
   anchoring for PnL analysis still applies (cs2agent H-MAKER-ANCHOR1).
1b. DONE 08-26 afternoon (commit 403f62a): open_ledger_orders now
   subtracts CLEAN cancel rows (root cause of the 15-order warning spam,
   558 rows); gone_before_cancel stays flagged until settle; failed venue
   fetch returns None and SKIPS the diff; unchanged anomaly sets log
   DEBUG and are not re-appended.
1c. Secret Service hardening (commit 403f62a) after gnome-keyring crashed
   BOTH ways on 08-26 (11:43 D-Bus death killed the armed engine; the
   restarted daemon came back LOCKED and reads blocked forever behind a
   GUI prompt, wedging the dry-run daemon + suite + GRID capture, which
   reads the key per poll): config._secret runs keyring reads in a 5s-
   timeout thread with an in-process last-good cache; deletion still
   disarms; engine main loop survives any cycle exception; tests are
   keyring-hermetic (conftest). Secrets live in gnome-keyring "login"
   collection (NOT KWallet) — locked keyring needs the user's login
   password, nothing code can do.
1d. Deferred review findings (08-26 four-lane round; each deliberate,
   none blocking at $3 stakes): (a) both-sides-held-on-one-slug settle
   NETTING — resolutions on such slugs are skip-and-warn (fail closed on
   exposure; loss cap blind for that book only); (b) daily-loss cap is
   UTC-day bucketed → resets 17:00 PT mid-evening (rolling-24h window is
   the fix); (c) no two-engine pidfile lock (runbook + systemd prevent);
   (d) ambiguous-ack orders consume a REENTRY slot but zero max-open
   capacity until adoption cancels them (~60s window); (e) armed-crash →
   dry-run-daemon takeover cannot cancel prior orders (no client by
   design) — re-arm briefly or hand-cancel; scripts for cancel-all
   emergency queued; (f) reconcile partial_fills "None"-id oddity
   (pre-existing, simplifier flagged).
2. First-night analysis: qualification rate, fill rate, realized edge vs
   the 4pt design margin; rerun quant_eval; feed H-MAPENTRY1 bar.
3. BO1 detection (GRID format field) to unlock match-winner books.
4. Record book tick in mapentry rows (currently constant 0.01 assumption).
5. Kalshi read-only lane (task #18) + in-repo read-only MCP (task #19).
6. 24/7 armed operation is a POST-PASS, user-deliberate step — the dry-run
   stack already runs 24/7; only the armed session is manual by design.
7. H-MAPENTRY2 scoring script (registered pre-fire 08-26 ~08:20Z): score
   aggregation candidates A/B0/B10/B20 + the 0.50-0.65 vs 0.65-0.80 band
   sub-hypothesis on live capture from 09:00Z onward, retrospectively from
   mapentry rows (a, b, q_round) + resolved map winners. Verdict at n>=60
   resolved maps; simplicity tiebreak frozen (B0>B10>B20 on |dECE|<=0.01).
   Slow-loop analysis only — armed lane unchanged until a pre-registered
   amendment cites the verdict.
8. Re-entry-after-clean-cancel amendment candidate (user-endorsed 08-26
   first armed night, observed cost: 3 later in-band chances at better
   prices blocked on the AURORA map1 book after a clean TTL cancel).
   Pre-register before adopting: does a CLEAN cancel release the book's
   dedup slot for one re-entry? Against: churn into a book that refuses
   to fill. For: more fill attempts per map at ≥4pt value prices.
9. Sell-side exit path (future build, user principle "cut losers"): held
   positions currently ride to settlement BOTH ways — no sell wire, no
   exit rule exists. Any exit logic must be its own pre-registered
   instrument; requires sell order support in pmus wire first.
10. Clock-stratified P_map calibration read.
10b. H-WAVE1 candidate (user-called live 08-26, G2 comeback map1):
   momentum/transit entry. Evidence: model tracked G2 0.04->1.0 but book
   led the model the whole climb (r11 model 0.573 vs book 0.68; r12
   0.744 vs 0.75) so static value never qualified — AND peer holdout
   shows v1 UNDER-forecasts 0.2-0.6 (realized 0.48-0.56+, unrepaired in
   all H-MAPAGG2 candidates). Thesis: during rapid upward transit the
   model lags truth; edge = timing into the move priced vs
   calibration-corrected fair, not discount vs a too-low fair.
   Pre-register with own trigger/pricing/bar before ANY run.
11. TIMING PROFILE (user priority 08-26: "get the timing of the predictive
   model down"): FIRST NUMBERS 08-26 (scripts/timing_profile.py, n=15
   placed): signal→ledger latency median 8.5s (min 5.5, max 38) — our own
   pipe consumes a third of the 13-27s intl-book lead; the 3 outliers
   (22-38s) need explanation (capacity retries?). Post-quote oriented
   drift (all qualifying rows, sides separate): long +0.85c@30s,
   +0.53c@60s, fading −0.57c@120s; short flat then +1.7c@120s — NOT the
   adverse-selection signature (immediate move against), directionally
   consistent with a real lead; small n, no CIs, describe only. TTL 150s
   spans the favorable window. Still open: per-fill absorption (needs
   fills), clock/progress trust windows, latency-outlier attribution,
   pipeline latency reduction (poll cadence 12s + file-follows + 2s
   engine cycle are the levers). Output feeds a pre-registered
   timing-aware entry v2. cs2agent (read-only cross-check,
   08-26) measured ~3x within-round Brier gradient on our 08-25 capture
   (0.22 earliest poll → 0.07 latest, n=232 rounds / 8 series). Our q is
   event-sampled at first threshold crossing (calibrated AT that event,
   n=1,613) — but drift-by-clock would hide in pooled cells. No schema
   change needed: join mapentry rows to instance rows on
   (src_hypothesis_id, series_id, map_number, round_number, dedup_extra);
   clock_seconds lives on the instance row.
11b. Queue 13+14 spec (docs/superpowers/specs/2026-08-27-entry-gate-loosening.md,
   drafted by the hermes gateway agent from user directives night 2, owner
   Claude): 13 = fill-acceptance widening — user observation elevated to
   primary: quote distance must consider the ACTIVE book, not just frozen
   fair (candidate: inside-spread quote at same fair−4 edge floor); backtest
   would-fill (a) vs (b) on tonight's rows BEFORE any PR. 14 = smart exits
   (user 08-27: "exit when the bid is at an acceptable place considering the
   trade" — night-2's winner was closed MANUALLY at 0.99, +$3.99); design
   sketch in spec; explicitly "comes later"; pre-register exit cells first.
11c. MANUAL-CLOSE RECONCILIATION — SHIPPED 08-27 dev window (7694553) after
   an opus review found 2 BLOCKERs + 5 MAJORs in the first cut (dict-shaped
   positions payload, availablePositions, resolution-precedence, order-scoped
   skip, newest-fill aging, revisable settle — all fixed). RESIDUAL RISK ON
   RECORD: the only positions() capture was taken FLAT, so the held-position
   field/shape is unverified — VALIDATION TASK: at the next armed fill,
   pull client.portfolio.positions() read-only WHILE HOLDING, confirm the
   shape, disable the pass if it mismatches. night_alerts count bug also
   fixed (cumulative across nights + rollover alert-loss, dedup v3).
11d. DEV-WINDOW 08-27 RESULTS (all committed): Queue 13 backtest first
   numbers (n=37 evals: rule a 20% fill/-0.20 edge-per-fill; inside-spread b
   37%/-0.13; edge-per-opportunity -0.040 vs -0.047 — ADVERSE-SELECTION
   signature: would-filled quotes lose on average; numbers only, no rule
   change licensed). Post-shock lag study (scripts/postshock_lag.py; 230
   shocks/52 priced: DOWN shocks leave the book +15.1c ABOVE model, ~29s
   median convergence, 44% converge — candidate SHORT-side mechanism;
   UP shocks +3.2c — book nearly caught up). H-WAVE1 accumulation pass 1
   (7 waves, bvm -14c, 2 priced both won). Rollover audit: 11 findings, all
   MAJORs fixed (24h-window readers, Follow eager read-through 60131f5);
   deferred MINORS: F8 analysis date-stamp one day ahead, F9 capture_qa
   fingerprint flips at rollover, F11 corpus [-days:] slices files not days.
1e. Deferred audit findings (08-27 coherence audit; each deliberate, none
   blocking at $3 stakes — BLOCKERs/MAJORs F1-F8 are fixed or in the
   confirmation-row relay; F6 awaits a user decision on cap semantics):
   (1) F9 orphan-fill blindness: a crash between wire ack and outcome row
   leaves an order no module can see fill (exposure counts only
   status==placed; reconcile skips no-oid rows; fill_candidates needs an
   order_id; manual-exit needs a fill row). Resting orphans heal via
   adoption; one that FILLS inside the ~60s pre-adoption window has zero
   ledger representation. Fix: on adoption, retrieve state and pair the
   slug with its dangling wire_pending to synthesize placed(+fill) rows.
   (2) M1 reconcile matches status=="placed" without type=="order_intent"
   (latent; mirror the ledger's both-field filter). (3) M2
   released_this_pass suppression is dead code (real suppression = the
   fetched_list guard; delete branch + comment). (4) M3 first-cycle
   ledger-read exception leaves caps open_count=0/_daily_loss=0 —
   fail-OPEN direction — DONE 2026-09-03: a failed read blocks placement
   (`skipped:ledger_unreadable`, identical in dry_run) until a read succeeds.
   (5) M4 disarmed RESTART stops truth ingestion (no pmus client → no
   reconcile/fills/adoption until re-arm; mid-run disarm is covered).
   (6) M5 _fill_totals marks an oid seen before validating the row
   (unreachable via current writers). (7) M6 one duplicate reconcile row
   per restart (report-only).
11e. NIGHT-3 RESULTS + INFERENCE (08-27 overnight armed, venue-verified,
   balance $26.49 flat): 29 placed / 9 fills / 8 resolutions — 4W-4L(+pair),
   overnight net -$0.76 after late riders lost (day net +$3.23 with the
   evening +$3.99). THE SPLITS (n=10 fills, describe-only, study running):
   (a) DISCOUNT: every winner entered >=11pts under fair (11/20/21/30);
   every loser sub-9pts (6.6/7.9/8.5) — a >=10pt floor separates them
   PERFECTLY at this n; (b) both losing shorts were mid-band (~0.59 fair)
   model-flip fades entered mid-map; (c) the XDM-NASE loss was entered
   within 10min of a >=25pt shock (post-shock turbulence); (d) early-map
   (r<=5) high-conviction longs 3-1, mid-map entries 1-3. IN BUILD:
   entry_quality.py study (full would-trade population, all 3 armed days)
   → evidence for pre-registering DISCOUNT-FLOOR + POST-SHOCK-FREEZE
   amendments — STUDY LANDED (2e78112): n=45 population CONTRADICTS the
   fills read (>=20pt discounts wr .33/-.08 edge WORST; 10-20pt .86/+.18
   best; early-phase .46 negative) so NO amendments registered. Study
   caveats (code-review 08-27): would-trade population diverges from the
   engine on band-gate/seen ordering + a stricter identity gate, and fills
   tagging is not 1:1 — treat splits as directional, not gospel. SHIPPED:
   second-side gate H-MAPENTRY1-SECONDSIDE1 (d041ede, registry #21 — NOT
   queue Q15, which is the separate analysis-only two-sidedness item) +
   post-review hardening: inflight vetoes terminal, trusted-settle-only
   slug release, same-cycle error-ack block, oid-resolved bad_cancel
   forgiveness. Known gate-state asymmetries (accepted): restart drops the
   in-process errack map (ledger inflight covers it next refresh); dry_run
   never wires so error-ack paths exercise armed-only. Also live-evidenced
   tonight: capacity overshoot via hidden-fill window (1d-d, 4 concurrent
   positions ~$8); venue outage arc (create endpoint down ~4.5h, 4
   triggers lost, fail-soft perfect); manual-exit E2E validated incl
   netPosition field (live held capture).
12. Knowledge-graph lane (user-led, task #29, APPROVED 08-27 PM —
   Approach A): purpose = AGENT CONTEXT ENGINE, interface = stdio MCP
   server mounted in hermes AND Claude (absorbs task #19), store = Kuzu,
   LangGraph confined to ingest pipelines (the user's learning surface).
   Locked decisions: pipelines write facts / agents only propose (staging
   + promotion); full rebuildability from repo sources (DB gitignored,
   schema+pipelines versioned). SPEC:
   docs/superpowers/specs/2026-08-27-knowledge-graph-design.md; kickoff
   prompt for the user's tutor session:
   2026-08-27-kg-kickoff-prompt.md (same dir). The USER drives the build
   in a separate session — ops sessions must NOT build ahead in this
   lane; coordinate via git. Carry-in: this STATE.md, docs/experiments/registry.jsonl
   (hypotheses/verdicts = natural nodes+edges), the capture corpus
   relations (series↔maps↔markets↔orders↔fills↔settles), the gotchas
   section, and Claude memory. Slow-loop only (iron rail 1).
   UPDATE 2026-09-03 (user): the KG moves to the NEXT repo (gridbot-lab) on a
   proper server — DuckDB for analytics/SQL learning + Kuzu (Vela-Engineering
   fork) for the graph; gridbot gets cleaned first, no KG build here.
13. TIMING CHAIN (user goal 2026-09-03: "a genuine timing comparison to show
   our latency and data edge over the books and find a way to beat it"):
   spec docs/superpowers/specs/2026-09-03-timing-chain.md — keyless PMUS BBO
   recorder (new unit gridbot-pmus-books) + scripts/timing_chain.py joining
   GRID round ends to the first book move, with our own pipe legs. Every
   lead number quoted above was measured against the INTL venue; this is
   the first measurement on the venue we trade and the number a rail-B
   instrument gets pre-registered against. LANDED 2026-09-03 (control build
   won the bake-off; hermes Part A/B builds kept as record). FIRST READ on
   the first six-hour tape (docs/analysis/2026-09-03-timing-chain-first-read.md):
   682 round ends, 293 with a book move; lead_vs_obs_s p50 +13.1s (lower
   bound; upper bound +27.5s), book moved FIRST in 29% (40% in late-map
   rounds); first-move direction agrees with the winner only 55% — the
   first move is mostly noise, a size/horizon read is the next amendment;
   our pipe ≈ 4.4s (GRID rtt 0.28 + detect 0.16 + recorder 1.19 + engine
   2.77) plus the 12s poll wait. Levers: 10s polls, one in-process
   pipeline, act on the GRID observation itself (rail B).
   AMENDED 2026-09-03 eve (two lane builds, Claude review): oriented
   30/60/120s moves, >=3-tick first move, next-round-bounded move, all
   with series-clustered bootstrap CIs and coverage-aware horizons
   (addenda in the first-read doc). Result: a round end is worth ~half a
   tick; bounded at the next round, direction agreement is 50.2%; the
   RAW move is +0.016 after EITHER side's round win — the book is not
   pricing rounds, it drifts toward the map STATE (rail-B mechanism from
   the venue side). Next: orient on the model (p_map - mid), in flight.
14. POST-SHOCK FADE reconciliation: the design brief's anchors (108/70, 32)
   were computed on a pool missing two of its own gates
   (docs/analysis/2026-09-01-postshock-anchor-trace.md); brief amended
   2026-09-03 to the spec-literal funnel (67/53 candidate, 37/34 control,
   pinned corpus). RECONCILED 2026-09-03 eve (reasoner lane, Claude review;
   commit b6a8c4d): the script reproduces the reference funnel to the
   digit (72/58 DOWN, 38/35 UP on the design pool) and prints it per
   gate. Read: BOTH arms positive in-sample (DOWN->short edge +0.19
   [+0.08, +0.30], UP->long +0.15 [-0.03, +0.33], series-clustered) — the
   fade's own control expectation fails; the book UNDER-reacts to the
   model after shocks. Momentum, not fade, and the same mechanism the
   timing chain found. Holdout starved (14/12 vs 40/15). Open caveats:
   staleness gate may select dead books; taker price has no size check;
   NOTE6 fill selection. H-MODELMOMENTUM1 brief + adversarial review
   dispatched; REGISTRATION IS THE USER'S CALL. No tests yet (queued).
15. Real-order fill statistics re-cut on RESTED orders only (NOTE7):
   timing_profile quote lifetimes, entry_quality fills tagging, nightly
   placed/filled counts. The NOTE6 proxy fill-conditional edge is unaffected.
