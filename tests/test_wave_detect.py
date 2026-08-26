"""Tests for scripts.wave_detect (H-WAVE1 retrospective detector).

Analysis only (slow loop, iron rail 1): proactively asserts the trigger semantics
so the detector's behaviour is locked in before any instrument consumes it.
No network, no keyring, no data mutation — pure functions over synthetic rows.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the project root is on sys.path so `scripts` is importable (pytest
# only adds tests/ to sys.path; mirror the existing test_grid_winners.py).
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts.wave_detect import detect


def mk(ts_ns: int, p_map: object, round_number: int, **kw) -> dict:
    """Build a minimal mapentry row for a single sequence."""
    row = {
        "series_id": "s1",
        "map_number": 1,
        "market_slug": "my-market",
        "us_side": "US",
        "ts_ns": ts_ns,
        "round_number": round_number,
        "p_map": p_map,
    }
    row.update(kw)
    return row


# ── synthetic comeback fires exactly once, at the right row ───────────────

def test_comeback_fires_exactly_once_at_right_row():
    rows = [
        mk(1, 0.20, 1),
        mk(2, 0.25, 2),
        mk(3, 0.70, 3),  # enters [0.50, 0.80] from below with rise >= 0.15
    ]
    res = detect(rows)  # defaults: min_rise=0.15, trail=3
    assert res["n_sequences"] == 1
    assert res["n_waves"] == 1
    assert res["wave_rate"] == pytest.approx(1.0)
    assert res["params"] == {"min_rise": 0.15, "trail": 3}

    w = res["waves"][0]
    assert len(res["waves"]) == 1
    # identity of the sequence
    assert w["series_id"] == "s1"
    assert w["map_number"] == 1
    assert w["market_slug"] == "my-market"
    assert w["us_side"] == "US"
    # the firing row
    assert w["round_number"] == 3
    assert w["ts_ns"] == 3
    assert w["p_map"] == pytest.approx(0.70)
    assert w["prev_p_map"] == pytest.approx(0.25)
    assert w["rise"] == pytest.approx(0.50)  # 0.70 - min(0.20, 0.25) = 0.50
    # no book data on the row -> null fill
    assert w["book_ask"] is None
    assert w["book_vs_model"] is None


# ── entering the band from ABOVE is not a wave ─────────────────────────────

def test_enter_band_from_above_no_wave():
    rows = [
        mk(1, 0.85, 1),  # above the band
        mk(2, 0.70, 2),  # slides down INTO the band from above
    ]
    res = detect(rows)
    assert res["n_sequences"] == 1
    assert res["n_waves"] == 0
    assert res["waves"] == []
    assert res["wave_rate"] == pytest.approx(0.0)


# ── insufficient rise is consumed, with no second chance ──────────────────

def test_rise_below_min_rise_consumed_no_second_chance():
    # First crossing at row2 rises only 0.05 (< 0.15) -> consumed. Later in-band
    # rows rise far more but prev_p is already >= 0.50, so condition (b) blocks
    # any re-fire. Even row3 at 0.80 (rise 0.35 over the trough) is ignored.
    rows = [
        mk(1, 0.45, 1),
        mk(2, 0.50, 2),  # crosses into band; rise = 0.50 - 0.45 = 0.05
        mk(3, 0.80, 3),  # big rise, but second chance is gone
        mk(4, 0.55, 4),
    ]
    res = detect(rows, min_rise=0.15, trail=3)
    assert res["n_waves"] == 0
    assert res["waves"] == []


# ── missing / None / bad p_map rows are skipped ────────────────────────────

def test_missing_or_none_p_map_rows_skipped():
    rows = [
        mk(1, 0.20, 1),
        mk(2, None, 2),                  # explicit None p_map
        mk(3, 0.40, 3),
        _without_p_map(mk(4, 0.30, 4)),  # p_map key absent entirely
        mk(5, "not-a-number", 5),        # non-numeric -> coerced to None
        mk(6, 0.70, 6),                  # fires; prev valid row is #3 (0.40)
    ]
    res = detect(rows)
    assert res["n_sequences"] == 1
    assert res["n_waves"] == 1
    w = res["waves"][0]
    assert w["round_number"] == 6
    assert w["p_map"] == pytest.approx(0.70)
    assert w["prev_p_map"] == pytest.approx(0.40)  # skipped rows don't set prev
    # tmin over distinct rounds {1:0.20, 3:0.40} is 0.20
    assert w["rise"] == pytest.approx(0.50)


def _without_p_map(row: dict) -> dict:
    row.pop("p_map", None)
    return row


# ── at most one wave per sequence ──────────────────────────────────────────

def test_one_wave_max_per_sequence():
    rows = [
        mk(1, 0.20, 1),
        mk(2, 0.70, 2),  # fires here
        mk(3, 0.30, 3),  # leaves the band ...
        mk(4, 0.70, 4),  # ... and re-enters from below (would re-fire if allowed)
    ]
    res = detect(rows)
    assert res["n_waves"] == 1
    assert len(res["waves"]) == 1
    assert res["waves"][0]["round_number"] == 2


# ── trailing min over DISTINCT round_numbers ──────────────────────────────

def test_same_round_two_rows_uses_lower_p_map():
    # Two rows share round_number 1 (p 0.70 then 0.35). The distilled per-round
    # minimum must be 0.35; if 0.70 were used the rise would be negative and no
    # wave would fire. trail=1 measures over the single preceding distinct round.
    rows = [
        mk(1, 0.70, 1),
        mk(2, 0.35, 1),
        mk(3, 0.55, 2),  # enters band; rise = 0.55 - 0.35 = 0.20 >= 0.15
    ]
    res = detect(rows, min_rise=0.15, trail=1)
    assert res["n_waves"] == 1
    w = res["waves"][0]
    assert w["round_number"] == 2
    assert w["p_map"] == pytest.approx(0.55)
    assert w["rise"] == pytest.approx(0.20)


def test_trailing_min_windows_by_distinct_rounds_not_rows():
    # trail=2 means the window covers the LAST two distinct round_numbers of the
    # prior valid rows (rounds 2 and 3), NOT every prior round. Round 1 (0.05) is
    # excluded: rise = 0.55 - min(0.35, 0.30) = 0.25 < 0.30 -> no wave. Had round
    # 1's 0.05 leaked in, rise 0.50 would have fired. Asserting no-wave proves the
    # oldest distinct round is correctly dropped.
    rows = [
        mk(1, 0.05, 1),  # lowest reading, but outside the trailing window
        mk(2, 0.35, 2),
        mk(3, 0.30, 3),
        mk(4, 0.55, 4),  # enters band from below
    ]
    res = detect(rows, min_rise=0.30, trail=2)
    assert res["n_waves"] == 0
    assert res["waves"] == []


# ── detect() contract guards ───────────────────────────────────────────────

def test_trail_below_one_rejected():
    with pytest.raises(ValueError, match="trail"):
        detect([], trail=0)