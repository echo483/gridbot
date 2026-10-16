# Spec: Probability-Scaled Position Sizing Layer (H-SIZING0)

Status: PROPOSED — for the dev queue. Not registered in the experiment
registry until instruments produce evidence; nothing here touches the trade path.

## Goal

Size every (instrument, state) entry from the model's calibrated probability q,
not from intuition. "Exploit the squares" = the market anchors on scoreboard
flow; our edge is state-conditional probability. Sizing must convert that
probability delta into stake with explicit, adjustable risk controls.

## Core formula (v0)

fractional Kelly on the mispricing, floored by estimation uncertainty:

    edge   = q_win - price            # price = ask + fee-adjusted spread
    kelly  = edge / (1 - price)       # binary market Kelly
    f      = kelly / K_divisor        # K_divisor default 8 (1/8-Kelly), env-tunable
    ci_lo  = Wilson lower bound of the state's historical resolution rate
              (registered corpus numbers, e.g. H-MANADV1: 0.908)
    q_eff  = min(q, ci_lo - margin)   # margin default 0.02; never bet above
                                      # what the WORST defensible estimate supports
    stake  = bankroll * f(q_eff)      # capped (below)

Hard caps (all env-tunable at runtime, no code changes):
  - max_stake_frac:  0.02  (2% of bankroll per entry)
  - max_open_per_series: 1  (no averaging into a losing state)
  - max_daily_loss_frac: 0.05 (circuit breaker, halts entries for the day)
  - min_edge: 0.05 (skip quotes inside 5 points of fair)
  - max_price: 0.90 (never pay above 90¢ regardless of q)

## Evidence inputs (why shadow capture must run first)

Each sizing parameter maps to a measurable from the shadow corpus:
  - q calibration per state cell → recalibrate q_eff floors weekly
  - quote-availability rate (falsification criterion ii) → whether min_edge
    and max_price ever co-occur with the state in practice
  - time-to-convergence distribution from timestamped books → expected
    capital lock-up duration → informs K_divisor (longer lock = smaller f)
  - realized slippage paper vs settled → adjust `price` definition

## Self-improvement loop

Nightly EOD digest already reports instrument evidence. Extend it with a
sizing table: per hypothesis, N instances, paper edge mean/σ, Wilson floor
drift, and a recommendation line when a parameter should move (only a
recommendation — changes require explicit user sign-off, logged in the
registry as SIZING-PARAM deltas with before/after).

## Explicitly out of scope / rails

- No LLM in the trade path; this layer consumes the served model artifact only.
- dry_run=True until a pre-registered instrument PASS; live flip is the
  user's act alone.
- Single PM account; scaling axes are titles × venues × capital, not leverage.
- All parameters serialized into a versioned sizing_config JSON committed
  with each change, so every historical stake is exactly reproducible.

## Build order (small PRs)

1. sizing/params.py — config load + env overrides + validation tests
2. sizing/kelly.py — pure function (q_eff, price, bankroll) → stake; unit
   tests incl. caps, zero-edge, q_eff > price guards
3. sizing shadow logging — record would-be stakes alongside instrument
   instances (no orders)
4. EOD sizing table (extend scripts/eod_report.py)
5. After first instrument PASS: paper-trade replay of the sizing layer over
   the accrued shadow corpus → expected PnL vs actual instrument evidence
