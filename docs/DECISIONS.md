# Decisions pending — baseline 2026-09-04

This is the list the clean baseline hands you. Everything below is either a
decision only you can make (the rails say so) or a design gate that needs your
approval before anything is scaffolded. Nothing here is blocking the systems
that run on their own (capture, the book recorder, the nightly timing chain,
the dry-run executor, BOOKPIN1 holdout accrual).

Evidence pointers are to files in this repo; every number has a series-
clustered interval in its source doc. Recommendations are mine; the decision
is yours.

## D1 — H-MODELMOMENTUM1 draft brief: close, amend, or defer

- What it is: a drafted (never registered) hypothesis that the book converges
  toward `p_map` after a ≥25pt model shock and the gap can be captured.
  `docs/superpowers/specs/2026-09-03-modelmomentum-brief-DRAFT.md`.
- Evidence: the timing chain shows the book does not move toward `p_map`
  within a round (Addendum 3 of `docs/analysis/2026-09-03-timing-chain-first-
  read.md`), and the post-shock diagnostics show the only "edge" sat in books
  that never repriced and had no depth (2026-09-03 section of
  `docs/analysis/2026-09-01-postshock-anchor-trace.md`; registry
  `H-MAPENTRY1-NOTE8`).
- Options: (a) close; (b) amend to a settlement-horizon claim with a depth-
  limited fill gate and register it; (c) defer until ≥5 nights of the timing
  chain.
- Recommendation: (a). The reusable parts are the execution model (depth-
  limited fills only) and the artifact checks, both already carried into
  NOTE8 and the gridbot-lab constraints.

## D2 — What qualifies a strategy to go live (the rail-3 path)

- Rail 3: `dry_run=True` until a pre-registered PASS on held-out capture, and
  flipping live is your own act. As of this baseline NO lane has a PASS.
- **09-04 update — the holdout verdict is rendered and it is NOT a falsification.**
  On the BOOKPIN1 holdout (40 series, 1,084 instances) the model beats the book
  mid (Brier 0.121 vs 0.149) and is calibrated (ECE 0.054 vs the 0.08 bar);
  clause (iii) does not bind at 39/60 deduped entries and its reference reading
  is a negative paper edge (−5.9pt, CI −13.2..+0.3). Registry
  H-MAPENTRY1-HOLDOUT1; doc `docs/analysis/2026-09-04-venue-pricing-and-holdout.md`.
  The in-sample FALSIFIED reading (15,314 instances, all three clauses) does not
  transfer to new series.
- **09-04 update — no taker latency strategy exists on PMUS** (H-MAPENTRY1-NOTE9):
  map books are dead (8-tick median spread, 22% one-sided), match books are live
  (1-tick) but reprice ~1/3 tick per round; taking the stale quote after a round
  loses at every horizon and leverage bucket. "Move to the best strat" has no
  taker candidate; the maker side is the only latency use the data leaves open,
  and it needs a trades tape (authenticated WebSocket `subscribe_trades`).
- Standing facts: paper edge lives in orders that never fill (NOTE6); among
  rested orders 45% filled on 08-27 (shorts 29%); every short was mispriced on
  the armed nights (NOTE7, fixed). Thirteen settled trades is 0.9% of the
  ~1,500 needed to separate +2pt from zero.
- What accrues without you: the nightly timing chain; from tonight, covered map
  ends (recorder fix); BOOKPIN1 is frozen at 40 series and will not power
  clause (iii) further on its own.
- Options:
  (a) pre-register a clause-(iii) holdout extension (series first-mapped after
      the 40th, appended until ≥60 deduped qualifying entries) so MAPENTRY1 can
      reach PASS or FAIL on its own bar — I draft the row, you approve it;
  (b) pre-register the execution-realistic qualifying instrument (rested-only,
      depth-limited fills, quote-lifetime-aware, series-clustered bars) and run
      it on the live dry-run stream — the only thing that can turn a paper PASS
      into an arming case (NOTE8);
  (c) add a trades tape (WebSocket, read-only, keyring-authenticated) and put
      the maker-side latency question into the gridbot-lab research layer;
  (d) close MAPENTRY1 as a live candidate and move on.
- Recommendation: (a) + (b) now, (c) as the first gridbot-lab research task.
  Nothing goes live without a PASS on a fill-modelled instrument. Registry rows
  are drafted for your review, never self-registered.

## D3 — gridbot-lab research layer: approve the design

- Options memo: `docs/superpowers/specs/2026-09-03-gridbot-lab-research-layer-
  options.md` (option A recommended: a scorecard library where every score
  returns value + CI + n + n_series, a DuckDB/Parquet lake over the corpus,
  NautilusTrader custom Data types for the streams).
- The brainstorming gate is open: no scaffolding until you approve the
  sections. Tonight added three hard constraints for the backtester: depth-
  limited fills, quote-lifetime awareness, series-clustered evaluation.
- Your answer earlier: focus on the research layer and what actually computes
  real quant scores; learning the system and the data comes after. The memo
  is written to that answer.

## D4 — Knowledge graph: DuckDB + Kuzu, in gridbot-lab, before Nautilus

- Plan memory: KG lives in gridbot-lab on the server (DuckDB for analytics and
  SQL learning, the Vela-Engineering Kuzu fork for the graph). Carry-ins:
  STATE.md, the registry, corpus relations, the gotchas list.
- Decision needed: the sequencing you stated (baseline → KG → qualify the
  strategy → Nautilus integration) and whether the KG build starts from the
  research-layer scaffold (D3) or as its own first sub-project.

## D5 — NautilusTrader integration (after the KG)

- Lessons, not code (your rule). What tonight adds to the port list: the
  venue price convention (NOTE7), fills only against recorded depth, quote
  lifetimes measured on rested orders only, evaluation clustered by series,
  pre-registration as a first-class object in the runtime.
- Decision needed: none yet; it is sequenced after D3/D4.

## D6 — Ops and permissions (your hands only)

- Guard-hook v2 proposal is staged in your `~/.claude/guard-proposal`
  folder with its 41-case harness; installing it is your act.
- Permissions mode: you asked for bypass mode earlier; the switch is yours.
- The six leftover branches were deleted in this baseline pass (all were
  merged into main); the failed transient trial unit from the bake-off was
  cleared.

## D7 — Hermes routing doctrine (applied per your messages; confirm or revert)

- deepseek-v4-flash-0731 is the default builder (profile + ROUTES),
  glm-5.3-flash the alternate; gpt-5.6-luna and deepseek-v4-pro-0813 in the
  reasoning/review rotation; the reasoner profile runs on deepseek-pro.
  Grades by artifact for the night are in `agents/` mirrors and Claude memory.
- Decision needed: only if you want any of it reverted.

## What runs on its own while you decide

| runs | what it accrues |
|---|---|
| gridbot-capture + gridbot-pmus-books | the corpus and the book tape (the moat) |
| gridbot-executor (dry run) + shadow/mapentry/postquote | intents ledger, would-trade population, adverse-selection samples |
| gridbot-timing-chain.timer (17:30 local) | one timing-chain read per UTC day toward the 5-night threshold |
| gridbot-health (5 min) | self-heal, including the keyring-wedge restart rule |
| BOOKPIN1 holdout | verdicts as its 40 series resolve |

## D8 — Keyring durability for the capture key (added 2026-09-04 11:40 PDT)

- Third wedge of the desktop keyring in five days (08-31, 09-03, 09-04). Today's:
  two `gnome-keyring-daemon` processes alive, secret reads hang even from a
  fresh process, the 600s in-process cache expired, capture failed closed at
  ~11:18 PDT and every downstream instrument stalled. Recovery is your act
  (`gnome-keyring-daemon --replace --unlock`, runbook in
  `docs/analysis/2026-08-31-keyring-capture-outage.md`).
- Rail 6 says secrets live in the OS keyring only. The desktop (login) keyring
  is the wrong OS keyring for an always-on daemon: it locks with the session
  and wedges under D-Bus contention.
- Options: (a) keep the desktop keyring and rely on the 9d0543f self-heal plus
  the health check's stale-restart (already committed; capture still runs the
  pre-fix code until its next restart); (b) move the CAPTURE key (read-only
  GRID key, not the trading keys) to a systemd encrypted credential
  (`systemd-creds encrypt` + `LoadCredentialEncrypted=` on the capture unit),
  machine-bound, never on disk in clear, no D-Bus in the read path; (c) both,
  with the trading keys staying in the desktop keyring so arming still needs
  an unlocked session.
- Recommendation: (c). It keeps rail 6's intent (OS-managed secret store, no
  secrets in files) and removes the single point of failure from the capture
  path. Setting it up needs the key value, so it is your hands, not mine.
