"""The research control plane.

Nothing in here places an order, reads a secret, or calls the network. It reads
the lake, runs strategies and scans, measures the result honestly, and writes
reports. The execution plane is downstream of this and the two meet at exactly
one artefact: a frozen hypothesis row plus the config hash it points at.
"""

from __future__ import annotations

from gridbot.lab.metrics import (
    BetaAlpha,
    PnlReport,
    beta_alpha,
    cluster_returns,
    deflated_sharpe,
    expected_max_sharpe,
    hit_rate_score,
    max_drawdown,
    minimum_detectable_effect,
    pnl_report,
    required_n,
    sharpe_ratio,
    sharpe_score,
)

__all__ = [
    "BetaAlpha",
    "PnlReport",
    "beta_alpha",
    "cluster_returns",
    "deflated_sharpe",
    "expected_max_sharpe",
    "hit_rate_score",
    "max_drawdown",
    "minimum_detectable_effect",
    "pnl_report",
    "required_n",
    "sharpe_ratio",
    "sharpe_score",
]
