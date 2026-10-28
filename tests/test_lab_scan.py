"""Scan-engine tests: registered hypotheses scored on a synthetic book.

Each test states a population, the outcomes it implies, and the verdict it must
produce. The numbers are chosen so the expected PnL is arithmetic, not a vibe:
buying at 0.50 and winning 60% of the time is +0.10 per contract, exactly.
"""

from __future__ import annotations

import duckdb
import pytest

from gridbot.lab.metrics import cluster_returns
from gridbot.lab.registry import Hypothesis, append_hypothesis, import_legacy, load_registry
from gridbot.lab.scan import Verdict, check_scan_input, run_scan


def _input(con, rows) -> None:
    """rows: (series_id, ts_ns, side, price, outcome, filled, p_hat, reference)."""
    con.execute(
        "CREATE OR REPLACE TABLE scan_input("
        "series_id VARCHAR, ts_ns BIGINT, side VARCHAR, price DOUBLE, "
        "outcome DOUBLE, filled BOOLEAN, p_hat DOUBLE, reference DOUBLE)"
    )
    con.executemany("INSERT INTO scan_input VALUES (?,?,?,?,?,?,?,?)", rows)


def _book(n_series=10, per_series=10, win_rate=0.6, price=0.5, filled=True, side="long"):
    """A book whose win pattern shifts per series, so between-series variance is real.

    A constant pattern across series collapses the clustered interval to a point
    and the scan (correctly) refuses to call that evidence — see
    ``test_lab_metrics.test_degenerate_population_yields_no_sharpe_and_no_deflation``.
    """
    rows = []
    base_wins = round(win_rate * per_series)
    for s in range(n_series):
        # Each series gets a DIFFERENT win count, so the clustered interval has
        # between-series variance to resample. A constant win count per series
        # makes every series identical and the interval collapses to a point.
        k = max(0, min(per_series, base_wins + (s % 3) - 1))
        for i in range(per_series):
            wins = 1.0 if i < k else 0.0
            rows.append((f"s{s}", 1_000 + i, side, price, wins, filled, price, 0.0))
    return rows


def _hyp(**kw):
    base = {
        "id": "H-TEST1",
        "title": "t",
        "claim": "c",
        "mechanism": "m",
        "population": "price <= 0.60",
        "metric": "mean_pnl",
        "bar": 0.05,
        "direction": ">",
        "n_min": 40,
        "n_trials": 1,
    }
    base.update(kw)
    return Hypothesis(**base)


def test_scan_passes_a_well_powered_positive_edge():
    con = duckdb.connect()
    _input(con, _book(win_rate=0.6, price=0.5))  # +0.09/contract, 100 obs, 10 series
    result = run_scan(con, _hyp(), reps=300)
    assert result.verdict is Verdict.PASS, result.render()
    assert result.filled.mean_pnl.value == pytest.approx(0.09, abs=1e-9)
    assert result.n_opportunities == 100 and result.n_filled == 100
    assert result.n_series == 10
    assert result.bar_met and result.significant


def test_scan_under_powered_when_n_is_small():
    con = duckdb.connect()
    _input(con, _book(n_series=2, per_series=3, win_rate=0.9, price=0.5))
    result = run_scan(con, _hyp(), reps=200)
    assert result.verdict is Verdict.UNDER_POWERED
    assert "bar not evaluated" in result.verdict_reason


def test_scan_fails_when_the_bar_is_not_met():
    con = duckdb.connect()
    _input(con, _book(win_rate=0.5, price=0.5))  # no positive edge to find
    result = run_scan(con, _hyp(bar=0.05), reps=300)
    assert result.verdict is Verdict.FAIL
    assert not result.bar_met
    assert result.filled.mean_pnl.value < 0.02


def test_selection_cost_is_reported_and_can_sink_a_lane():
    """Unfilled quotes are the good ones — the NOTE6 signature, made visible."""
    con = duckdb.connect()
    good_unfilled = [(f"s{s}", 1_000 + i, "long", 0.5, 1.0, False, 0.5, 0.0)
                     for s in range(10) for i in range(5)]
    bad_filled = [(f"s{s}", 2_000 + i, "long", 0.5, 0.0, True, 0.5, 0.0)
                  for s in range(10) for i in range(5)]
    _input(con, good_unfilled + bad_filled)
    result = run_scan(con, _hyp(), reps=300)
    assert result.fill_rate == pytest.approx(0.5)
    assert result.selection_cost == pytest.approx(0.5)  # opportunity looks flat, fills lose
    assert result.filled.mean_pnl.value == pytest.approx(-0.5)
    assert result.opportunity.mean_pnl.value == pytest.approx(0.0)


def test_no_fills_is_reported_as_unfilled_not_as_zero_edge():
    con = duckdb.connect()
    _input(con, _book(win_rate=0.9, filled=False))
    result = run_scan(con, _hyp(), reps=200)
    assert result.n_filled == 0
    assert result.fill_rate == 0.0
    assert any("never tradeable" in n for n in result.notes)
    assert result.verdict is Verdict.UNDER_POWERED


def test_legacy_prose_row_is_not_runnable():
    con = duckdb.connect()
    _input(con, _book())
    legacy = Hypothesis(
        id="H-MANADV1",
        title="H-MANADV1",
        claim="prose only",
        mechanism="",
        population="",
        machine_checkable=False,
    )
    result = run_scan(con, legacy)
    assert result.verdict is Verdict.NOT_RUNNABLE
    assert "legacy" in result.verdict_reason


def test_empty_population_is_no_observations_not_a_pass():
    con = duckdb.connect()
    _input(con, _book())
    result = run_scan(con, _hyp(population="price > 0.99"))
    assert result.verdict is Verdict.NO_OBSERVATIONS
    assert result.n_opportunities == 0


def test_holdout_predicate_scopes_the_evaluation():
    con = duckdb.connect()
    rows = []
    for s in range(10):
        for i in range(10):
            rows.append((f"s{s}", i, "long", 0.5, 1.0, True, 0.5, 0.0))
    _input(con, rows)
    hyp = _hyp(holdout="ts_ns >= 5")
    result = run_scan(con, hyp, reps=200)
    assert result.evaluated_on == "holdout"
    assert result.n_opportunities == 50


def test_beta_alpha_runs_when_a_reference_is_present():
    con = duckdb.connect()
    rows = []
    for s in range(10):
        for i in range(10):
            outcome = 1.0 if i < 6 else 0.0
            reference = 0.4 * (outcome - 0.5)  # strategy is ~2.5x the reference
            rows.append((f"s{s}", i, "long", 0.5, outcome, True, 0.5, reference))
    _input(con, rows)
    result = run_scan(con, _hyp(), reps=300)
    assert result.beta_alpha is not None
    assert result.beta_alpha.beta.value == pytest.approx(2.5, rel=0.05)
    assert result.beta_alpha.alpha.value == pytest.approx(0.0, abs=1e-9)


def test_deflated_sharpe_reflects_the_declared_search_size():
    con = duckdb.connect()
    _input(con, _book(win_rate=0.6, price=0.5))
    small = run_scan(con, _hyp(n_trials=2), reps=300, variance_of_trial_sr=0.05)
    large = run_scan(con, _hyp(n_trials=500), reps=300, variance_of_trial_sr=0.05)
    assert large.deflated.value < small.deflated.value


def test_missing_required_columns_blocks_the_scan():
    con = duckdb.connect()
    con.execute("CREATE OR REPLACE TABLE scan_input(series_id VARCHAR, ts_ns BIGINT)")
    missing = check_scan_input(con)
    assert "price" in missing and "outcome" in missing


def test_registry_registration_refuses_a_barless_hypothesis(tmp_path):
    hyp = _hyp(id="H-NOBAR", population="", metric="mean_pnl")
    with pytest.raises(ValueError, match="refusing to register"):
        append_hypothesis(hyp, base=tmp_path)


def test_registry_round_trip_and_legacy_import(tmp_path):
    append_hypothesis(_hyp(id="H-A"), base=tmp_path)
    v1 = tmp_path / "registry.jsonl"
    v1.write_text(
        '{"hypothesis_id":"H-MANADV1","hypothesis_text":"prose","run_ts":"2026-08-25T04:10:00+00:00"}\n'
    )
    assert import_legacy(v1, base=tmp_path) == 1
    rows = {h.id: h for h in load_registry(tmp_path)}
    assert rows["H-A"].runnable
    assert not rows["H-MANADV1"].runnable
    assert rows["H-MANADV1"].claim == "prose"


def test_config_hash_changes_when_the_claim_changes(tmp_path):
    a = _hyp(id="H-X")
    b = _hyp(id="H-X", bar=0.06)
    assert a.config_hash != b.config_hash
    assert _hyp(id="H-X").config_hash == a.config_hash


def test_cluster_returns_groups_by_series():
    grouped = cluster_returns([("s1", 0.1), ("s1", 0.2), ("s2", -0.1)])
    assert grouped == {"s1": [0.1, 0.2], "s2": [-0.1]}
