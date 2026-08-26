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
  work standalone via `hermes send -t telegram`.

## Where verdicts come from

`gridbot.instruments.verdict` (oracle = first-observation round winners from
captured stream; cells match the registry). `scripts/quant_eval.py` is the
metrics layer (Brier/ECE/EV/MC/Brownian) — REPORTING only; the frozen bars
in the registry are the only PASS/FAIL rule. H-MAPENTRY1's bar: beat the
book's Brier, ECE ≤ 0.08, settled edge CI > 0 at n≥150/60; starvation
clause closes the lane if <5% of instances ever show a qualifying quote.

## Open work queue (post-first-fire)

1. Fills→settles writer (venue settlement → ledger settle rows → daily-loss
   cap binds; then raise caps discussion). Anchor PnL accounting at the FILL
   tick, not the post tick — fills are a selected sample (cs2agent
   H-MAKER-ANCHOR1: fill-anchored ~0.38/$100 worse, uniformly).
1b. Reconcile false-alarm fix (OBSERVED first armed night, order
   C3RBPBKHTFSK): fetch_venue_orders returns [] on any fetch error and
   diff() reads [] as "venue empty" → phantom gone_from_venue (disproven
   by the subsequent CLEAN cancel). Fix: return None on error and SKIP
   the diff; optionally require 2 consecutive gone sightings. Fail
   direction was safe (false alarm, TTL cancel is the real guard) — do
   NOT hot-patch mid-armed-session.
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
8. Clock-stratified P_map calibration read. cs2agent (read-only cross-check,
   08-26) measured ~3x within-round Brier gradient on our 08-25 capture
   (0.22 earliest poll → 0.07 latest, n=232 rounds / 8 series). Our q is
   event-sampled at first threshold crossing (calibrated AT that event,
   n=1,613) — but drift-by-clock would hide in pooled cells. No schema
   change needed: join mapentry rows to instance rows on
   (src_hypothesis_id, series_id, map_number, round_number, dedup_extra);
   clock_seconds lives on the instance row.
