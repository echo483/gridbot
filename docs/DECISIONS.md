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
- Where H-MAPENTRY1 (the only lane that has traded) stands: `p_map` skill ≈ 0
  when series-clustered and 12–21pt overconfident where we trade (NOTE5);
  paper edge lives in orders that never fill (NOTE6); among orders that
  actually rested, 45% filled on 08-27 (shorts 29%) and most unfilled orders
  sat the full TTL (`scripts/timing_profile.py 20260827`); every short was
  mispriced on the armed nights (NOTE7, fixed). Thirteen settled trades is
  0.9% of the ~1,500 needed to separate +2pt from zero.
- What accrues without you: BOOKPIN1 holdout (40/40 series accrued 09-01,
  verdicts as maps resolve); the nightly timing chain.
- Options: (a) keep dry-run accrual and let the BOOKPIN1 holdout speak;
  (b) pre-register an execution-realistic qualifying instrument for MAPENTRY1
  (rested-only, depth-limited fills, quote-lifetime-aware, series-clustered
  PASS/FAIL bars) and let it run on the live dry-run stream; (c) close
  MAPENTRY1 as a live candidate and put the effort into gridbot-lab.
- Recommendation: (a) + (b). Nothing goes live without a PASS, and the PASS
  instrument must model fills or its number is not a number (NOTE8). If you
  choose (b), the registry row is written BEFORE the instrument runs, and I
  draft it for your review rather than registering it myself.

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
