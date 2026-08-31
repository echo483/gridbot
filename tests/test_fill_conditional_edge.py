"""Tests for the fill-conditional edge instrument.

The load-bearing piece is the SHORT-side fill condition: a short buys the
No leg, whose effective ask is 1 - bid, so a naive `ask <= price` test
reads the wrong side of the book and silently reports the long-side fill
rate for every short. Everything else in the report hangs off that.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fill_conditional_edge as fce


class TestSideAsk:
    def test_long_pays_the_ask(self) -> None:
        assert fce.side_ask_at("long", 0.40, 0.60) == 0.60

    def test_short_pays_one_minus_bid(self) -> None:
        assert fce.side_ask_at("short", 0.40, 0.60) == 0.60
        assert fce.side_ask_at("short", 0.30, 0.60) == 0.70

    def test_missing_leg_is_none_not_zero(self) -> None:
        """A missing quote must not read as a free fill at 0."""
        assert fce.side_ask_at("long", 0.4, None) is None
        assert fce.side_ask_at("short", None, 0.6) is None


class TestWouldFill:
    def _snaps(self, *pairs):
        return [{"bid": b, "ask": a} for b, a in pairs]

    def test_long_fills_when_the_ask_comes_down_to_us(self) -> None:
        row = {"us_side": "long"}
        assert fce.would_fill(row, 0.70, self._snaps((0.6, 0.75), (0.6, 0.70)))
        assert not fce.would_fill(row, 0.70, self._snaps((0.6, 0.75), (0.6, 0.72)))

    def test_short_fills_off_the_bid_side(self) -> None:
        """price 0.70 on a short fills when 1 - bid <= 0.70, i.e. bid >= 0.30.
        Reading `ask` here would give the opposite verdict on both cases."""
        row = {"us_side": "short"}
        assert fce.would_fill(row, 0.70, self._snaps((0.35, 0.90)))
        assert not fce.would_fill(row, 0.70, self._snaps((0.25, 0.40)))

    def test_any_snapshot_in_the_window_counts(self) -> None:
        row = {"us_side": "long"}
        assert fce.would_fill(row, 0.70, self._snaps((0.6, 0.9), (0.6, 0.65), (0.6, 0.9)))

    def test_no_snapshots_never_fills(self) -> None:
        assert not fce.would_fill({"us_side": "long"}, 0.70, [])


class TestSummarize:
    def test_reports_a_ci_that_spans_zero_on_thin_samples(self) -> None:
        """Two contradicting trades must NOT come back significant."""
        out = fce.summarize([(1.0, 0.5), (0.0, 0.5)])
        assert out["ev_per_contract"] == 0.0
        assert out["significant_at_95"] is False

    def test_flags_significance_only_when_the_ci_clears_zero(self) -> None:
        out = fce.summarize([(1.0, 0.5)] * 40)
        assert out["ev_per_contract"] == 0.5
        assert out["significant_at_95"] is True   # zero variance, real gap

    def test_empty_is_not_an_error(self) -> None:
        assert fce.summarize([])["n"] == 0
