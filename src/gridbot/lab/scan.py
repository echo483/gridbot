"""The scan engine: run a registered hypothesis over the tape, without new code.

A hypothesis is data (a :class:`~gridbot.lab.registry.Hypothesis` row), and this
module is the one implementation that evaluates it. The engine is deliberately
source-agnostic: it reads a DuckDB view called ``scan_input`` with a fixed,
documented schema, so the same scanner scores the map-winner book today and a
Dota market tomorrow with no change to this file.

Required ``scan_input`` columns
-------------------------------
``series_id``  the clustering unit — trades inside a series are not independent
``ts_ns``      observation time, for ordering and for the PnL path
``side``       ``'long'`` or ``'short'`` — the side WE would take
``price``      our cost per contract, in [0, 1], on the side we would take
``outcome``    1.0 if our side won, 0.0 if it lost
``filled``     TRUE only if an order at ``price`` would actually have filled

Optional: ``p_hat`` (our forecast, for Brier), ``reference`` (a reference return
realised over the same holding window, for beta/alpha).

Two populations, always reported separately
-------------------------------------------
The engine never reports a single "edge" number. It reports the **opportunity**
population (every observation the registered predicate matched) and the **filled**
population (only the ones an order would have got). The gap between them is the
selection cost that this project measured at -6.5pt per contract and that killed
the post-shock lane entirely. Reporting only the filled set hides it; reporting
only the opportunity set is the dead-book artifact. Both, or neither.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

import duckdb

from gridbot.lab.metrics import (
    BetaAlpha,
    PnlReport,
    beta_alpha,
    deflated_sharpe,
    minimum_detectable_effect,
    pnl_report,
)
from gridbot.lab.registry import Hypothesis
from gridbot.scorecard import Score

REQUIRED_COLUMNS = ("series_id", "ts_ns", "side", "price", "outcome", "filled")
OPTIONAL_COLUMNS = ("p_hat", "reference", "market_slug", "stake")

#: PnL per contract for the side we took, against a 1.0 payout on a win.
PAYOFF_SQL = (
    "CASE WHEN lower(side) = 'long' THEN outcome - price ELSE (1.0 - outcome) - price END"
)


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNDER_POWERED = "UNDER_POWERED"
    NO_OBSERVATIONS = "NO_OBSERVATIONS"
    NOT_RUNNABLE = "NOT_RUNNABLE"


@dataclass
class ScanResult:
    """Everything one scan produced, with every number carrying its n and CI."""

    hypothesis_id: str
    verdict: Verdict
    verdict_reason: str
    config_hash: str
    n_opportunities: int
    n_filled: int
    n_series: int
    fill_rate: float
    filled: PnlReport
    opportunity: PnlReport
    selection_cost: float
    bar_value: float
    bar_met: bool
    significant: bool
    beta_alpha: BetaAlpha | None
    deflated: Score
    mde: float
    n_trials: int
    evaluated_on: str = "opportunity+filled"
    equity: list[float] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        def score(s: Score) -> dict:
            return {
                "value": s.value,
                "ci": list(s.ci) if s.ci else None,
                "n": s.n,
                "n_series": s.n_series,
            }

        return {
            "hypothesis_id": self.hypothesis_id,
            "verdict": self.verdict.value,
            "verdict_reason": self.verdict_reason,
            "config_hash": self.config_hash,
            "n_opportunities": self.n_opportunities,
            "n_filled": self.n_filled,
            "n_series": self.n_series,
            "fill_rate": self.fill_rate,
            "selection_cost": self.selection_cost,
            "bar_value": self.bar_value,
            "bar_met": self.bar_met,
            "significant": self.significant,
            "mde": self.mde,
            "n_trials": self.n_trials,
            "filled": {
                "total_pnl": self.filled.total_pnl,
                "mean_pnl": score(self.filled.mean_pnl),
                "hit_rate": score(self.filled.hit_rate),
                "sharpe": score(self.filled.sharpe),
                "max_drawdown": self.filled.max_drawdown,
            },
            "opportunity": {
                "mean_pnl": score(self.opportunity.mean_pnl),
                "hit_rate": score(self.opportunity.hit_rate),
            },
            "beta_alpha": (
                {"beta": score(self.beta_alpha.beta), "alpha": score(self.beta_alpha.alpha),
                 "r_squared": self.beta_alpha.r_squared}
                if self.beta_alpha
                else None
            ),
            "deflated_sharpe": {
                **score(self.deflated),
                "expected_max_sharpe": self.deflated.meta.get("expected_max_sharpe"),
            },
            "notes": self.notes,
        }

    def render(self) -> str:
        opportunity_line = (
            f"  opportunity only: mean={_fmt(self.opportunity.mean_pnl.value)} "
            f"CI {_ci(self.opportunity.mean_pnl)}"
        )
        lines = [
            f"{self.hypothesis_id}  ->  {self.verdict.value}  ({self.verdict_reason})",
            f"  config_hash={self.config_hash}  n_trials={self.n_trials}",
            (
                f"  opportunities={self.n_opportunities}  filled={self.n_filled}  "
                f"series={self.n_series}  fill_rate={self.fill_rate:.1%}"
            ),
            f"  {self.filled.render()}",
            opportunity_line,
            f"  selection cost (opportunity - filled) = {self.selection_cost:+.4f} per contract",
            (
                f"  bar: {self.hypothesis_id} {self.filled.name} {self.bar_value} "
                f"({'met' if self.bar_met else 'NOT met'}), significant={self.significant}"
            ),
            f"  min detectable effect at this n = {self.mde:.4f}",
        ]
        if self.beta_alpha:
            lines.append("  " + self.beta_alpha.render().replace("\n", "\n  "))
        if self.deflated.defined:
            lines.append(
                f"  deflated sharpe = {self.deflated.value:.4f} "
                f"(search handed you {self.deflated.meta.get('expected_max_sharpe', 0):+.3f})"
            )
        lines.extend(f"  note: {n}" for n in self.notes)
        return "\n".join(lines)


def _fmt(v: float | None) -> str:
    return "undefined" if v is None or not math.isfinite(float(v)) else f"{v:+.4f}"


def _ci(s: Score) -> str:
    return "n/a" if s.ci is None else f"({s.ci[0]:+.4f}, {s.ci[1]:+.4f})"


def check_scan_input(con: duckdb.DuckDBPyConnection, view: str = "scan_input") -> list[str]:
    """Return the missing required columns of the scan input view, if any."""
    try:
        described = con.execute(f"SELECT * FROM {view} LIMIT 0").description
    except duckdb.Error as exc:
        return [f"{view} not queryable: {exc}"]
    have = {c[0] for c in described}
    return [c for c in REQUIRED_COLUMNS if c not in have]


def _rows_for(
    con: duckdb.DuckDBPyConnection,
    hyp: Hypothesis,
    predicate: str,
    only_filled: bool,
    cols: set[str],
) -> list[tuple]:
    filled_clause = " AND filled" if only_filled else ""
    p_hat = "p_hat" if "p_hat" in cols else "NULL"
    reference = "reference" if "reference" in cols else "NULL"
    sql = (
        f"SELECT series_id, ts_ns, side, price, outcome, filled, "
        f"({PAYOFF_SQL}) AS pnl, ({p_hat}) AS p_hat, ({reference}) AS reference "
        f"FROM scan_input WHERE ({predicate}){filled_clause} ORDER BY ts_ns"
    )
    return con.execute(sql).fetchall()


def _columns(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {c[0] for c in con.execute("SELECT * FROM scan_input LIMIT 0").description}


def _group(rows: list[tuple], cluster_index: int = 0, value_index: int = 6) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for row in rows:
        out.setdefault(str(row[cluster_index]), []).append(float(row[value_index]))
    return out


def _equity(pnl_path: Sequence[float]) -> list[float]:
    """Cumulative PnL after each fill, in time order. The equity curve."""
    total = 0.0
    out: list[float] = []
    for value in pnl_path:
        total += float(value)
        out.append(total)
    return out


def _metric_value(hyp: Hypothesis, rows: list[tuple]) -> float:
    if not rows:
        return float("nan")
    if hyp.metric == "hit_rate":
        return sum(1 for r in rows if float(r[6]) > 0) / len(rows)
    if hyp.metric == "brier":
        vals = [(float(r[7]), float(r[4])) for r in rows if r[7] is not None]
        if not vals:
            return float("nan")
        return sum((p - y) ** 2 for p, y in vals) / len(vals)
    if hyp.metric == "edge_per_obs":
        return sum(float(r[6]) for r in rows) / len(rows)
    return sum(float(r[6]) for r in rows) / len(rows)  # mean_pnl


def _bar_met(value: float, bar: float, direction: str) -> bool:
    if not math.isfinite(value):
        return False
    return {
        ">": value > bar,
        "<": value < bar,
        ">=": value >= bar,
        "<=": value <= bar,
    }[direction]


def run_scan(
    con: duckdb.DuckDBPyConnection,
    hyp: Hypothesis,
    *,
    view: str = "scan_input",
    reps: int = 2000,
    periods_per_year: float | None = None,
    variance_of_trial_sr: float = 0.0,
) -> ScanResult:
    """Evaluate one registered hypothesis. Never raises on a bad population —
    it returns a NOT_RUNNABLE / NO_OBSERVATIONS verdict, because a scan that
    cannot run must say so rather than report a zero."""
    problems = hyp.validate()
    if problems:
        return _blocked(hyp, Verdict.NOT_RUNNABLE, "; ".join(problems))

    missing = check_scan_input(con, view)
    if missing:
        return _blocked(
            hyp, Verdict.NOT_RUNNABLE, f"scan_input missing columns: {', '.join(missing)}"
        )

    predicate = hyp.population
    evaluated_on = "opportunity+filled"
    if hyp.holdout:
        predicate = f"({predicate}) AND ({hyp.holdout})"
        evaluated_on = "holdout"

    cols = _columns(con)
    filled_rows = _rows_for(con, hyp, predicate, only_filled=True, cols=cols)
    all_rows = _rows_for(con, hyp, predicate, only_filled=False, cols=cols)
    if not all_rows:
        return _blocked(hyp, Verdict.NO_OBSERVATIONS, f"population matched nothing: {predicate}")

    filled_by_series = _group(filled_rows)
    opp_by_series = _group(all_rows)
    pnl_path = [float(r[6]) for r in filled_rows]
    filled_report = pnl_report(
        filled_by_series,
        name=hyp.metric,
        pnl_path=pnl_path,
        periods_per_year=periods_per_year,
        reps=reps,
    )
    opp_report = pnl_report(opp_by_series, name=hyp.metric, reps=reps)

    n_series = filled_report.n_series
    value = _metric_value(hyp, filled_rows if hyp.metric != "edge_per_obs" else all_rows)
    bar_value = value
    bar_met = _bar_met(value, hyp.bar, hyp.direction)
    significant = _metric_significant(hyp, filled_report, filled_rows)

    ba = _beta_alpha(hyp, predicate, filled_rows, cols, reps)
    deflated = deflated_sharpe(
        filled_by_series if hyp.metric != "edge_per_obs" else opp_by_series,
        n_trials=hyp.n_trials,
        variance_of_trial_sr=variance_of_trial_sr,
    )
    sigma = filled_report.metrics.get("sigma", 0.0)
    mde = minimum_detectable_effect(sigma, filled_report.n_trades)

    selection_cost = (opp_report.mean_pnl.value or 0.0) - (filled_report.mean_pnl.value or 0.0)
    verdict, reason = _decide(hyp, filled_report, bar_met, significant)
    notes: list[str] = []
    if hyp.metric != "edge_per_obs" and filled_report.n_trades == 0:
        notes.append("no fills in population: the edge, if any, was never tradeable")
    ci = filled_report.mean_pnl.ci
    if ci is not None and ci[1] <= ci[0]:
        notes.append(
            "degenerate interval: every observation is identical, so resampling "
            "series produces no dispersion and the interval is not evidence"
        )
    if not math.isfinite(mde):
        notes.append("min detectable effect undefined (n < 2)")
    elif abs(filled_report.mean_pnl.value or 0.0) < mde:
        notes.append(
            f"point estimate is under the min detectable effect ({mde:.4f}): "
            "this n cannot resolve it either way"
        )

    return ScanResult(
        hypothesis_id=hyp.id,
        verdict=verdict,
        verdict_reason=reason,
        config_hash=hyp.config_hash,
        n_opportunities=len(all_rows),
        n_filled=len(filled_rows),
        n_series=n_series,
        fill_rate=(len(filled_rows) / len(all_rows)) if all_rows else 0.0,
        filled=filled_report,
        opportunity=opp_report,
        selection_cost=selection_cost,
        bar_value=bar_value,
        bar_met=bar_met,
        significant=significant,
        beta_alpha=ba,
        deflated=deflated,
        mde=mde,
        n_trials=hyp.n_trials,
        evaluated_on=evaluated_on,
        equity=_equity(pnl_path),
        notes=notes,
    )


def _excludes_zero(score: Score) -> bool:
    """True only for a zero-width-free interval that excludes zero.

    ``Score.significant`` is deliberately strict (an absent interval reads
    False), but a *degenerate* interval slips through: a population where every
    trade won resamples to itself, the clustered CI collapses to a point, and the
    point excludes zero. That is arithmetic, not evidence, so a zero-width
    interval is refused here.
    """
    ci = score.ci
    if ci is None:
        return False
    lo, hi = ci
    if hi <= lo:
        return False
    return (lo > 0 and hi > 0) or (lo < 0 and hi < 0)


def _metric_significant(hyp: Hypothesis, filled: PnlReport, filled_rows: list[tuple]) -> bool:
    if hyp.metric == "hit_rate":
        return _excludes_zero(filled.hit_rate)
    if hyp.metric == "edge_per_obs":
        return _excludes_zero(filled.mean_pnl)  # opportunity-level significance
    return _excludes_zero(filled.mean_pnl)


def _beta_alpha(hyp, predicate, filled_rows, cols: set[str], reps) -> BetaAlpha | None:
    if not filled_rows or "reference" not in cols:
        return None
    pairs_present = any(r[8] is not None for r in filled_rows)
    if not pairs_present:
        return None
    strat: dict[str, list[float]] = {}
    ref: dict[str, list[float]] = {}
    for r in filled_rows:
        if r[8] is None:
            continue
        strat.setdefault(str(r[0]), []).append(float(r[6]))
        ref.setdefault(str(r[0]), []).append(float(r[8]))
    if not strat:
        return None
    return beta_alpha(strat, ref, reps=reps)


def _decide(hyp: Hypothesis, filled: PnlReport, bar_met: bool, significant: bool) -> tuple[Verdict, str]:
    """PASS requires the registered bar AND a real interval AND enough series.

    The bar alone is a point-estimate rule and can be met by three lucky trades.
    The registry's job is the claim; the interval's job is whether the claim is
    resolved. Both, at the registered n, or the lane is not passed.
    """
    if filled.n_trades < hyp.n_min or filled.n_series < max(3, hyp.n_min // 20):
        return (
            Verdict.UNDER_POWERED,
            f"n={filled.n_trades} (min {hyp.n_min}), series={filled.n_series} — bar not evaluated",
        )
    if bar_met and significant:
        return Verdict.PASS, "bar met and interval excludes zero"
    if bar_met and not significant:
        return Verdict.FAIL, "bar met but interval includes zero — not resolved"
    return Verdict.FAIL, "bar not met"


def _blocked(hyp: Hypothesis, verdict: Verdict, reason: str) -> ScanResult:
    empty = pnl_report({}, name=hyp.metric)
    empty_s = Score(name="deflated_sharpe", value=None, ci=None, n=0, n_series=0)
    return ScanResult(
        hypothesis_id=hyp.id,
        verdict=verdict,
        verdict_reason=reason,
        config_hash=hyp.config_hash,
        n_opportunities=0,
        n_filled=0,
        n_series=0,
        fill_rate=0.0,
        filled=empty,
        opportunity=empty,
        selection_cost=0.0,
        bar_value=float("nan"),
        bar_met=False,
        significant=False,
        beta_alpha=None,
        deflated=empty_s,
        mde=float("inf"),
        n_trials=hyp.n_trials,
        notes=[reason],
    )


def run_scans(
    con: duckdb.DuckDBPyConnection,
    hypotheses: list[Hypothesis],
    **kwargs,
) -> list[ScanResult]:
    """Run many hypotheses over one connection. The breadth path."""
    return [run_scan(con, h, **kwargs) for h in hypotheses]
