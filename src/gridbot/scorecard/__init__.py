"""Scorecard: the statistical kernel every number in gridbot is measured with.

One implementation per primitive, one interval convention, one result contract.

Why this package exists
-----------------------
Before it, the repo's most load-bearing statistic — the series-clustered
bootstrap — was hand-rolled in ten files, and "ECE (quintile bins)" meant three
different things in three files, one of which gates a lane's verdict. The
numbers were reproducible only in the sense that each file reproduced itself.

The contract, from the research-layer memo: **every function returns value +
CI + n + n_series, and no number prints without its interval.**

Layers
------
* :mod:`gridbot.scorecard.result`      — the :class:`Score` contract
* :mod:`gridbot.scorecard.intervals`   — Wilson + series-clustered bootstrap
* :mod:`gridbot.scorecard.calibration` — Brier, ECE (both conventions), Murphy
* :mod:`gridbot.scorecard.edge`        — executable pricing, fill-conditional split
* :mod:`gridbot.scorecard.risk`        — Kelly, barriers, Monte Carlo bankroll

Status: kernel only. The registry bars remain the sole PASS/FAIL rule, and
nothing here reads the corpus, the network, or any secret. Rewiring
``scripts/`` onto this kernel is a separate, equivalence-tested change.
"""

from __future__ import annotations

from gridbot.scorecard.calibration import (
    brier,
    brier_decomposition,
    brier_skill,
    ece,
    ece_equal_width,
    ece_quantile,
    reliability_table,
)
from gridbot.scorecard.edge import (
    conservative_ev,
    edge_per_opportunity,
    ev_per_contract,
    fill_conditional_edge,
    side_cost,
    venue_wire_price,
)
from gridbot.scorecard.intervals import (
    DEFAULT_ALPHA,
    DEFAULT_REPS,
    DEFAULT_SEED,
    clustered_bootstrap,
    clustered_bootstrap_delta,
    clustered_bootstrap_mean,
    percentile,
    wilson_interval,
    wilson_lower,
)
from gridbot.scorecard.result import Score
from gridbot.scorecard.risk import (
    BANKROLL0,
    barrier_up_probability,
    expected_trades_to_barrier,
    flat_stake_stats,
    fractional_kelly,
    kelly_fraction,
    log_growth_stats,
    mc_bankroll,
)

__all__ = [
    "BANKROLL0",
    "DEFAULT_ALPHA",
    "DEFAULT_REPS",
    "DEFAULT_SEED",
    # contract
    "Score",
    "barrier_up_probability",
    # calibration
    "brier",
    "brier_decomposition",
    "brier_skill",
    "clustered_bootstrap",
    "clustered_bootstrap_delta",
    "clustered_bootstrap_mean",
    "conservative_ev",
    "ece",
    "ece_equal_width",
    "ece_quantile",
    "edge_per_opportunity",
    # edge
    "ev_per_contract",
    "expected_trades_to_barrier",
    "fill_conditional_edge",
    "flat_stake_stats",
    "fractional_kelly",
    # risk
    "kelly_fraction",
    "log_growth_stats",
    "mc_bankroll",
    "percentile",
    "reliability_table",
    "side_cost",
    "venue_wire_price",
    # intervals
    "wilson_interval",
    "wilson_lower",
]
