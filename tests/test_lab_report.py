"""Report tests: the HTML must carry the numbers and the caveats, or it lies."""

from __future__ import annotations

import duckdb

from gridbot.lab.registry import Hypothesis
from gridbot.lab.report import ReportMeta, render_report, write_report
from gridbot.lab.scan import Verdict, run_scan


def _scan(value_outcomes=(1.0, 0.0)):
    con = duckdb.connect()
    con.execute(
        "CREATE OR REPLACE TABLE scan_input(series_id VARCHAR, ts_ns BIGINT, "
        "side VARCHAR, price DOUBLE, outcome DOUBLE, filled BOOLEAN, "
        "p_hat DOUBLE, reference DOUBLE)"
    )
    rows = []
    for s in range(10):
        for i, outcome in enumerate(value_outcomes):
            rows.append((f"s{s}", i, "long", 0.5, outcome, True, 0.5, 0.0))
    if rows:
        con.executemany("INSERT INTO scan_input VALUES (?,?,?,?,?,?,?,?)", rows)
    hyp = Hypothesis(
        id="H-REPORT1", title="t", claim="c", mechanism="m",
        population="price <= 0.6", metric="mean_pnl", bar=0.01, direction=">",
        n_min=10, n_trials=7,
    )
    return run_scan(con, hyp, reps=200)


def test_report_carries_the_caveats_not_just_the_number():
    result = _scan()
    html = render_report([result], ReportMeta(title="T", universe="synthetic", n_trials=7))
    # the verdict, the intervals, the fill information and the search size all appear
    assert result.verdict.value in html
    assert "clustered by series" in html
    assert "search size: 7" in html
    assert "selection cost" in html
    assert "min detectable effect" in html
    assert "H-REPORT1" in html


def test_report_renders_an_empty_population_without_crashing():
    result = _scan(value_outcomes=())
    html = render_report([result])
    assert "no fills" in html or Verdict.UNDER_POWERED.value in html


def test_write_report_emits_html_and_json(tmp_path):
    result = _scan()
    path = write_report([result], tmp_path / "r.html", ReportMeta(n_trials=7))
    assert path.exists()
    assert path.with_suffix(".json").exists()
    assert "H-REPORT1" in path.read_text()
    assert '"hypothesis_id": "H-REPORT1"' in path.with_suffix(".json").read_text()


def test_report_escapes_a_hostile_hypothesis_id():
    con = duckdb.connect()
    con.execute(
        "CREATE OR REPLACE TABLE scan_input(series_id VARCHAR, ts_ns BIGINT, "
        "side VARCHAR, price DOUBLE, outcome DOUBLE, filled BOOLEAN)"
    )
    hyp = Hypothesis(
        id="<script>alert(1)</script>", title="t", claim="c", mechanism="m",
        population="price <= 0.6", metric="mean_pnl", bar=0.0, direction=">", n_min=1,
    )
    result = run_scan(con, hyp, reps=50)
    html = render_report([result])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
