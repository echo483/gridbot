"""Tests for scripts/latency_report.py — synthetic records only, no real files."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from latency_report import (
    detect_lags,
    inter_poll_gaps,
    is_test_series,
    percentile,
    poll_latency_by_title,
    ss_errors_per_day,
)


class TestPercentile:
    def test_single_value(self) -> None:
        assert percentile([5.0], 50) == 5.0
        assert percentile([5.0], 99) == 5.0

    def test_interpolated(self) -> None:
        # rank = 1 * 0.5 -> exactly the midpoint of [10, 20].
        assert percentile(sorted([20.0, 10.0]), 50) == pytest.approx(15.0)

    def test_min_max_bounds(self) -> None:
        values = sorted([1.0, 2.0, 3.0, 4.0])
        assert percentile(values, 0) == 1.0
        assert percentile(values, 100) == 4.0

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            percentile([], 50)


class TestPollLatencyByTitle:
    def test_groups_ss_records_only(self) -> None:
        recs = [
            {"type": "ss", "title": "cs2", "latency_ms": 100},
            {"type": "ss", "title": "cs2", "latency_ms": 200},
            {"type": "ss_error", "title": "cs2", "latency_ms": 9999},
            {"type": "instance"},
        ]
        out = poll_latency_by_title(recs)
        assert out == {"cs2": [100.0, 200.0]}

    def test_skips_non_numeric_latency(self) -> None:
        recs = [{"type": "ss", "title": "cs2", "latency_ms": None}]
        assert poll_latency_by_title(recs) == {}


def _live_ss(sid: str, req_ns: int, live: bool = True) -> dict:
    state = {"started": True, "finished": not live}
    return {"type": "ss", "series_id": sid, "req_ns": req_ns,
            "data": {"seriesState": state}}


class TestInterPollGaps:
    def test_per_series_isolation(self) -> None:
        # Series A polled live at t=0 and t=12s; series B polled once.
        recs = [
            _live_ss("A", 0),
            _live_ss("B", 5_000_000_000),
            _live_ss("A", 12_000_000_000),
        ]
        assert inter_poll_gaps(recs) == [12.0]

    def test_out_of_order_input_sorted(self) -> None:
        recs = [_live_ss("A", 12_000_000_000), _live_ss("A", 0)]
        assert inter_poll_gaps(recs) == [12.0]

    def test_no_gap_for_single_poll(self) -> None:
        assert inter_poll_gaps([_live_ss("A", 1)]) == []

    def test_ignores_non_ss(self) -> None:
        recs = [
            {"type": "ss_error", "series_id": "A", "req_ns": 0},
            _live_ss("A", 1_000_000_000),
        ]
        assert inter_poll_gaps(recs) == []

    def test_idle_polls_do_not_count(self) -> None:
        # A gap following an IDLE observation (60s cadence by design) is not
        # a live-cadence breach and must be excluded.
        recs = [
            _live_ss("A", 0, live=False),
            _live_ss("A", 60_000_000_000, live=False),
            _live_ss("A", 72_000_000_000),
            _live_ss("A", 84_000_000_000),
        ]
        assert inter_poll_gaps(recs) == [12.0]


class TestTestSeriesExclusion:
    @pytest.mark.parametrize("sid", ["2", "28", "99"])
    def test_excluded_by_default(self, sid: str) -> None:
        assert is_test_series({"series_id": sid}, include_test=False)

    @pytest.mark.parametrize("sid", ["2998616", "12345"])
    def test_real_series_kept(self, sid: str) -> None:
        assert not is_test_series({"series_id": sid}, include_test=False)

    def test_include_test_overrides(self) -> None:
        assert not is_test_series({"series_id": "28"}, include_test=True)

    def test_int_ids_handled(self) -> None:
        assert is_test_series({"series_id": 28}, include_test=False)


class TestSsErrorsPerDay:
    def test_counts_per_day_file(self) -> None:
        day_files = {
            Path("series_state-20260825.jsonl"): [
                {"type": "ss_error"},
                {"type": "ss"},
                {"type": "ss_error"},
            ],
            Path("series_state-20260826.jsonl"): [{"type": "ss"}],
        }
        out = ss_errors_per_day(day_files)
        assert out == {"20260825": 2, "20260826": 0}

    def test_sorted_output(self) -> None:
        day_files = {
            Path("series_state-20260827.jsonl"): [],
            Path("series_state-20260825.jsonl"): [],
        }
        assert list(ss_errors_per_day(day_files)) == ["20260825", "20260827"]


class TestDetectLags:
    def test_ms_conversion(self) -> None:
        insts = [{"grid_ts_ns": 1_000_000_000, "ts_ns": 1_250_000_000}]
        assert detect_lags(insts) == [pytest.approx(250.0)]

    def test_skips_incomplete(self) -> None:
        insts = [{"grid_ts_ns": 1}, {"ts_ns": 2}, {}]
        assert detect_lags(insts) == []
