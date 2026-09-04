"""Tests for the p_map calibration instrument.

The outcome join is the load-bearing part and is exactly where a
hand-rolled version of this analysis went wrong first (2026-08-28): the
mapentry ``team`` field is the ENTRY SIDE's team and flips within a map
as sides swap at half, so deriving a winner by pooling scores per team
name inverts the result. These tests pin the join to the GRID oracle and
pin the "never guess" rule.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pmap_calibration as pc

WINNERS = {"S1:1": {"series_id": "S1", "map_number": 1,
                    "winner": "HAVU", "loser": "Saint Sinners"}}


def _row(**over):
    base = {"type": "mapentry", "series_id": "S1", "map_number": 1,
            "team": "HAVU", "opponent": "Saint Sinners", "p_map": 0.8,
            "side_ask": 0.70, "entry_price_max": 0.76, "qualifies": True}
    base.update(over)
    return base


class TestOutcome:
    def test_winner_and_loser_resolve(self) -> None:
        assert pc.outcome(_row(), WINNERS) == 1.0
        assert pc.outcome(_row(team="Saint Sinners", opponent="HAVU"),
                          WINNERS) == 0.0

    def test_side_swap_does_not_flip_the_verdict(self) -> None:
        """The same team on the other side is still the same team — the
        join is by NAME against the oracle, never by score bookkeeping."""
        assert pc.outcome(_row(side="t"), WINNERS) == 1.0
        assert pc.outcome(_row(side="ct"), WINNERS) == 1.0

    def test_unresolved_map_is_none(self) -> None:
        assert pc.outcome(_row(series_id="S9"), WINNERS) is None

    def test_name_matching_neither_side_is_excluded_not_guessed(self) -> None:
        """A team the oracle does not name must drop out. Guessing 0.0 here
        would silently label every unmatched row a loss."""
        assert pc.outcome(_row(team="Some Other Org"), WINNERS) is None


class TestPostedPrice:
    def test_takes_the_binding_constraint(self) -> None:
        assert pc.posted_price(_row()) == 0.69            # ask - tick binds
        assert pc.posted_price(_row(side_ask=0.90)) == 0.76  # entry cap binds
        assert pc.posted_price(_row(side_ask=0.99,
                                    entry_price_max=0.95)) == 0.90  # ceiling

    def test_missing_book_fields_price_to_none(self) -> None:
        assert pc.posted_price(_row(side_ask=None)) is None
        assert pc.posted_price(_row(entry_price_max="0.7")) is None


class TestCalibrate:
    def test_perfect_forecaster_has_skill_one(self) -> None:
        out = pc.calibrate([(1.0, 1.0)] * 5 + [(0.0, 0.0)] * 5)
        assert out["brier"] == 0.0
        assert out["skill_vs_base_rate"] == 1.0

    def test_overconfident_forecaster_shows_a_negative_gap(self) -> None:
        # predicts 0.95, realizes 0.50 — the shape of the real finding
        out = pc.calibrate([(0.95, 1.0)] * 5 + [(0.95, 0.0)] * 5)
        top = next(b for b in out["reliability"] if b["lo"] == 0.9)
        assert top["gap"] < 0
        assert out["ece_10bin"] > 0.4

    def test_empty_input_does_not_divide_by_zero(self) -> None:
        assert pc.calibrate([])["n"] == 0
