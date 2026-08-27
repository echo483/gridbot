# gridbot — system state baseline

Frozen 2026-08-26 ~00:15 PT, the night of the first live-small fire.
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

**NOT yet proven:** map-level aggregation calibration (H-MAPENTRY1 has zero
instances — tonight starts it); fill probability at fair−4pts (the single
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
   → order_id persisted → 150s TTL auto-cancel if unfilled → reconcile pass
   (ledger vs venue orders.list) every ~60s.

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
- **Venue reports cancel success on already-FILLED orders** (confirmed in
  production: all 3 first-night fills were FULL fills whose TTL cancels
  returned clean "cancelled"). Never read cancel status as fill truth —
  fills.sync / settle_truth.py are the truth path.
- **Short-price convention unpinned**: venue resolution PnL for the
  AUR-G2 short was +$2.60 (= 0.52×5) not (1−0.52)×5=+2.40 — shorts fill
  at 1−price cost. ledger.realized_pnl's maker formula overstates short
  losses/understates wins by that gap (conservative direction). Settle
  rows carry venue_pnl; use IT for truth reporting. Pin the convention
  in ledger.py before scaling stakes.

## Where verdicts come from

`gridbot.instruments.verdict` (oracle = first-observation round winners from
captured stream; cells match the registry). `scripts/quant_eval.py` is the
metrics layer (Brier/ECE/EV/MC/Brownian) — REPORTING only; the frozen bars
in the registry are the only PASS/FAIL rule. H-MAPENTRY1's bar: beat the
book's Brier, ECE ≤ 0.08, settled edge CI > 0 at n≥150/60; starvation
clause closes the lane if <5% of instances ever show a qualifying quote.

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
12. Knowledge-graph lane (user-led, queued 08-26 eve, task #29): the user
   wants to learn KG engineering and co-develop a gridbot knowledge graph.
   They research first; do NOT start building until they kick it off.
   Carry-in when it starts: this STATE.md, docs/experiments/registry.jsonl
   (hypotheses/verdicts = natural nodes+edges), the capture corpus
   relations (series↔maps↔markets↔orders↔fills↔settles), the gotchas
   section, and Claude memory. Slow-loop only (iron rail 1).
