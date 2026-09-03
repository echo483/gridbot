# Integrity pass — 2026-09-03 (ops session, user-directed)

Scope: "fix inconsistencies and logic errors, debug the trading engine, fix
model/routing errors, then drive alpha." Everything below is from primary
data (venue records, the ledger, code) re-run this session; nothing is
recalled from earlier notes.

## 1. The venue price convention was wrong on the wire (fixed; registry H-MAPENTRY1-NOTE7)

`data/executor/settle_truth-20260827.json`, order C4KWZ5EV8GNE: we sent
`ORDER_INTENT_BUY_SHORT` at price 0.51. The venue stored it as
`ORDER_SIDE_SELL @ 0.51`; the position cost 2.45 = 5 × (1 − 0.51), avgPx 0.49,
realized −2.45. Polymarket US's `price` is ALWAYS the Yes/long price; a short
costs `1 − price`. gridbot's prices are OUR side's cost (`side_ask = 1 −
long_bid` for shorts; entry rule `side_ask ≤ p_map − 0.04`), and `pmus.place`
sent that cost verbatim. Consequences, counted on the 48 venue-verified
armed-night orders (08-26/27):

| side | FILLED | CANCELED | REJECTED |
|---|---|---|---|
| long | 10 | 9 | 5 |
| short | 3 | 6 | 15 |

Every rejected short had cost < 0.50 (Yes bid ≥ 0.50 → the sent Yes price
crossed the bid → post-only rejection); every rested short had cost ≥ 0.51 —
a perfect split at 0.50, which is the mechanism's signature. The 5 rejected
longs crossed because the book moved between the BBO read and the wire
(stale-book cross). All 20 were ledgered `placed`.

**Fill rate among orders that actually rested: 13/28 = 0.46**, not the
13/48 = 0.27 a ledger read gives. Every reading that treats ledger
placements as resting orders (timing_profile quote lifetimes, entry_quality
fills tagging, the nightly placed/filled counts, STATE.md's "fills arriving
readily = adverse-selection warning") counted 20 never-rested orders as
resting-unfilled and must be re-cut. NOT affected: the fill-conditional edge
(NOTE6, 0.412) — it is a would-fill proxy over mapentry + postquote
snapshots and never reads ledger orders.

Fixes (all tested, 763+ suite green, three armed-day replays hold):
- `pmus.wire_price`: long sends cost, short sends 1 − cost; rounding never
  raises our cost (floor on the 0.001 grid; short's Yes price rounds up).
- `pmus._venue_rejection`: a create response whose executions are ALL
  `EXECUTION_TYPE_REJECTED` is ledgered `rejected:venue:<reason>` with the id
  kept; anything mixed stays `placed` (reasoner review F4a — a partial fill
  next to a rejected remainder may be live).
- `fills.fill_row`: a short's venue avgPx converts back to cost; raw value
  kept as `venue_px`. `fill_check` / corrective-cancel rows carry
  `venue_state`; source `venue_rejected_no_fill` for REJECTED.
- `engine.handle`: a `rejected:*` outcome frees the map book's dedup key
  (consistent with REENTRY1's ledger-derived cap, which never counted
  rejections); `error:*` still consumes.
- `engine.cycle`: a failed ledger read blocks placement
  (`skipped:ledger_unreadable`, identical in dry_run) until a read succeeds —
  audit 1e-M3 closed (it was fail-OPEN: every cap at zero).
- Ledger price convention pinned in `ledger.py` (our-side cost, both sides;
  the STATE.md "shorts fill at 1−price cost, pin the formula" gotcha was this
  wire bug — the formula was already right). The three pre-fix short fill
  rows still carry Yes prices (true costs 0.48/0.48/0.49); their settles carry
  `venue_pnl`, so PnL is unaffected.

## 2. Slow loop: analysis notes were truncating silently (fixed)

Four nightly notes (08-29, 08-31, 09-01, 09-02) end mid-sentence. Root cause
in `analysis/daily.py`: 805ec73 retried only when the text was EMPTY at
`stop_reason == max_tokens`; a partial text was written as-is. Now the retry
keys on the stop reason, and a second truncation appends
`TRUNCATION_MARKER` so the note reads as partial. Tests:
`tests/test_analysis_daily.py`.

## 3. Routing / hermes team

- Builder profile: SOUL claimed deepseek-v4-flash-0731, config ran
  glm-5.3-flash. Aligned to the doctrine (glm-5.3-flash default builder,
  deepseek = alternate lane) and routed builder/reviewer through `:floor`
  like the cron pins. Mirrors in `agents/profiles/` re-synced.
- Every ROUTES slug and the hermes fallback chain resolve on OpenRouter's
  live model list (the dispatch.py comment claiming qwen3.8-max was missing
  was stale). No banned model on any of the four drift surfaces.
- Session grades (artifacts, not transcripts):
  - reasoner (qwen3.8-max) adversarial review of the engine diff: **A** —
    re-derived the convention from the raw venue JSON, brute-forced the
    rounding, found one real MAJOR (mixed executions) that the tests missed.
  - reviewer (glm-5.3:floor) six-document drift audit: **F** — 25 min,
    178k input tokens, killed by the timeout with zero output. The task was
    too broad for one lane; split per document next time.
  - builder (glm-5.3-flash:floor) five-file timing-chain build: **F** —
    34 min reading, 490k input / 50k reasoning tokens, zero files written,
    killed.
  - **Re-dispatch as split tasks with a reading budget — both PASSED.**
    builder on Part A (recorder, 3 files): **B+** — delivered a
    spec-conformant recorder + 8 tests, full suite green, real gateway
    smoke, found and fixed two bugs in its own code (error-path counter,
    EOF-tail `--once`), and flagged the trial daemon it discovered. reasoner
    (qwen3.8-max) on Part B (analysis, 2 files): **A−** — delivered the
    script + 8 tests, ran it on the live day and found two REAL join bugs
    (dedup keys reused across rounds → −847s "engine lag"; slate vs trigger
    rows → ghost lag), fixed both. ~45–50 min each.
  - Control build (Claude general-purpose agent, isolated worktree, same
    spec, all 5 files): **A** — 16 min, 50 tests, ruff clean, 12 spec
    ambiguities resolved and documented (startup prime for `--once`,
    day-matched mappings, round-and-window intent join). **Landed** after a
    line-by-line review plus one hardening (winner lookup by segment
    sequence number, so an unresolved round cannot shift the label); the
    hermes builds are kept in the session record for comparison.
  - Lesson (now in memory): one lane, one over-broad task, high reasoning →
    the model deliberates for the whole budget. Split to ≤3 files, state a
    reading budget, size the timeout to the task, and watch artifact
    activity — with that, the open-source lanes deliver.

## 4. Cleanup

Five fully-merged stale worktrees (0 unmerged commits; 408 MB) and their
branches removed; `laneA.log` (blank) removed; the Q15 scratch scripts moved
next to their evidence (`data/analysis/q15_scratch/`), the keyring drill
driver to `data/ops/`. A review/simplify/census workflow (4 review lenses
→ 2-skeptic verification → per-file simplifier → census) ran over the
diff: the simplifier's edits to pmus.py, fills.py, daily.py, dispatch.py
and the executor tests were reviewed and kept (suite green at 827); its
three census agents stalled on permission prompts while the user was away
and were stopped. Census decisions taken by hand: `scripts/eval_harness.py`
+ `tasks.jsonl` removed (an unwired stub needing a `ground_truth/` dir that
never existed and env-var API keys the rails forbid); the AWS-migration
and routing-layer planning docs moved to `docs/archive/` (superseded by the
homelab / gridbot-lab direction; history, not guidance).

## 5. Alpha: what decides the next rail

- Timing chain (spec `docs/superpowers/specs/2026-09-03-timing-chain.md`):
  a keyless PMUS BBO recorder + a round-end → first-book-move join. LANDED
  and RUNNING; first read on a six-hour tape in
  `docs/analysis/2026-09-03-timing-chain-first-read.md`: median lead +13.1s
  (lower bound; +27.5s upper), book first 29%, first-move direction only
  55% aligned (noise — a size/horizon read is next), our pipe ≈ 4.4s.
- Capture hardening from the same night: GRID capture was dark 09:00–14:50
  PDT behind a wedged in-process keyring read while the unit stayed active;
  `config._secret` now abandons a read blocked >120s and re-asks, and the
  health check restarts an active-but-stale capture unit (once/hour,
  graphical session only).
- Post-shock fade: the design brief's anchors (108/70, 32) were void —
  amended 2026-09-03 to the spec-literal funnel (67/53 candidate, 37/34
  control on the pinned corpus). `scripts/postshock_fade.py` must be
  reconciled to those numbers before its output is read.
- Real-order fill statistics (timing_profile quote lifetimes, entry_quality
  fills tagging, nightly placed/filled counts) re-cut on rested orders only,
  using `venue_state` going forward and the settle_truth files for 08-26/27.
  The proxy-based "−6.5pt selection cost" (NOTE6) stands as measured.
