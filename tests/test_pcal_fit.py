"""Pinned unit tests for scripts/pcal_fit.py (H-PCAL1).

Pure-function tests; no subprocess, no network, no real oracle — the oracle is
injected as a plain dict and the shadow/mapping data is constructed inline.
The only contact with disk is tmp_path for the artifact-roundtrip case.
"""

import itertools
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import pcal_fit

CUTOFF = pcal_fit.HOLDOUT_CUTOFF_NS


def _obs(pairs):
    """Turn [(p_map, y), ...] into observation dicts."""
    return [{"p_map": p, "y": y} for p, y in pairs]


# ---------------------------------------------------------------------------
# 1. PAV correctness: a hand-computed violator sequence pools correctly;
#    monotone non-decreasing; ties averaged.
# ---------------------------------------------------------------------------

def test_pav_pools_violator_sequence():
    ys = [0.3, 0.5, 0.4, 0.9]
    xs = [0.2, 0.4, 0.6, 0.8]
    ws = [1, 1, 1, 1]
    bx, by = pcal_fit.pav_increasing(xs, ys, ws)
    # The 0.5 / 0.4 pair at x=0.4,0.6 violated monotonicity and pools to 0.45;
    # the pooled block keeps its leftmost x (0.4); x=0.6 is no longer a break.
    assert bx == [0.2, 0.4, 0.8]
    assert by == pytest.approx([0.3, 0.45, 0.9])
    assert all(a <= b for a, b in itertools.pairwise(by)), "must be monotone non-decreasing"


def test_pav_handles_fully_decreasing_input():
    # Strictly decreasing input must collapse to a single pooled block.
    bx, by = pcal_fit.pav_increasing([0.1, 0.2, 0.3], [0.9, 0.5, 0.1], [1, 1, 1])
    assert bx == [0.1]
    assert by == pytest.approx([0.5])  # (0.9+0.5+0.1)/3


def test_ties_averaged_per_distinct_pmap():
    obs = [
        {"p_map": 0.6, "y": 1},
        {"p_map": 0.6, "y": 1},
        {"p_map": 0.6, "y": 0},
        {"p_map": 0.3, "y": 0},
    ]
    xs, ys, ws = pcal_fit.preaverage(obs)
    assert xs == [0.3, 0.6]
    assert ys == pytest.approx([0.0, 2.0 / 3.0])  # ties averaged (2 wins / 3 rows)
    assert ws == [1, 3]


# ---------------------------------------------------------------------------
# 2. Clipping: calibrated output never leaves [0.001, 0.999].
# ---------------------------------------------------------------------------

def test_clip_low_tail():
    obs = _obs([(0.05, 0), (0.10, 0), (0.15, 0)])
    bx, by = pcal_fit.fit_isotonic(obs)
    assert min(by) == pytest.approx(0.001)
    assert max(by) == pytest.approx(0.001)
    g = pcal_fit.make_g(bx, by)
    assert g(0.0) == pytest.approx(0.001)
    assert g(0.9) == pytest.approx(0.001)


def test_clip_high_tail():
    obs = _obs([(0.85, 1), (0.90, 1), (0.99, 1)])
    bx, by = pcal_fit.fit_isotonic(obs)
    assert max(by) == pytest.approx(0.999)
    g = pcal_fit.make_g(bx, by)
    assert g(1.0) == pytest.approx(0.999)
    assert g(0.5) == pytest.approx(0.999)


def test_clip_never_outside_interval():
    obs = _obs([(0.05, 0), (0.2, 0), (0.5, 0.5), (0.8, 1), (0.95, 1)])
    g = pcal_fit.make_g(*pcal_fit.fit_isotonic(obs))
    for x in [i / 100.0 for i in range(101)]:
        assert 0.001 - 1e-12 <= g(x) <= 0.999 + 1e-12


# ---------------------------------------------------------------------------
# 3. Dedup: two evaluations of the same (series, map, round) keep earliest ts.
# ---------------------------------------------------------------------------

def test_first_evaluation_wins_by_earliest_ts():
    winners = {"s1:1": {"winner": "A", "loser": "B"}}
    rows = [
        {"series_id": "s1", "map_number": 1, "round_number": 2,
         "ts_ns": 200, "team": "A", "p_map": 0.6},
        {"series_id": "s1", "map_number": 1, "round_number": 2,
         "ts_ns": 100, "team": "A", "p_map": 0.7},  # earlier -> must be kept
    ]
    obs, _ = pcal_fit.build_observations(rows, winners)
    assert len(obs) == 1
    assert obs[0]["ts_ns"] == 100
    assert obs[0]["p_map"] == 0.7


def test_same_series_different_round_kept_separately():
    winners = {"s1:1": {"winner": "A", "loser": "B"}}
    rows = [
        {"series_id": "s1", "map_number": 1, "round_number": 1,
         "ts_ns": 100, "team": "A", "p_map": 0.4},
        {"series_id": "s1", "map_number": 1, "round_number": 2,
         "ts_ns": 150, "team": "A", "p_map": 0.5},
    ]
    obs, _ = pcal_fit.build_observations(rows, winners)
    assert len(obs) == 2


# ---------------------------------------------------------------------------
# 4. Unresolved maps are excluded and counted.
# ---------------------------------------------------------------------------

def test_unresolved_maps_excluded_and_counted():
    winners = {"s1:1": {"winner": "A", "loser": "B"}}
    rows = [
        {"series_id": "s1", "map_number": 1, "round_number": 1,
         "ts_ns": 10, "team": "A", "p_map": 0.5},          # resolved
        {"series_id": "s2", "map_number": 1, "round_number": 1,
         "ts_ns": 10, "team": "A", "p_map": 0.5},          # map absent -> unresolved
        {"series_id": "s1", "map_number": 1, "round_number": 1,
         "ts_ns": 10, "team": "C", "p_map": 0.5},          # matches neither -> unresolved
        {"series_id": "s1", "map_number": 1, "round_number": 1,
         "ts_ns": 10, "team": "A", "p_map": "not-a-num"},  # non-numeric p -> skipped, uncounted
        {"series_id": "s1", "map_number": 1, "round_number": 1,
         "team": "A", "p_map": 0.5},                        # no ts_ns -> skipped, uncounted
    ]
    obs, n_unresolved = pcal_fit.build_observations(rows, winners)
    assert len(obs) == 1
    assert obs[0]["series_id"] == "s1"
    assert n_unresolved == 2


# ---------------------------------------------------------------------------
# 5. Holdout split (H-MAPENTRY1-BOOKPIN1 predicate).
# ---------------------------------------------------------------------------

def test_holdout_split_by_first_mapping_ts():
    mapping_ts = {"pre_s": CUTOFF - 1, "hold_s": CUTOFF + 1}
    pre, hold, absent = pcal_fit.classify_holdout(
        {"pre_s", "hold_s", "no_mapping_s"}, mapping_ts)
    assert "pre_s" in pre and "pre_s" not in hold
    assert "hold_s" in hold and "hold_s" not in pre
    assert "no_mapping_s" in absent  # absent series -> pre-holdout, counted separately
    assert absent == {"no_mapping_s"}


def test_cutoff_is_inclusive_for_holdout():
    # ts_ns == CUTOFF is at/after the pinned instant => holdout.
    pre, hold, _ = pcal_fit.classify_holdout({"s"}, {"s": CUTOFF})
    assert "s" in hold
    assert "s" not in pre


# ---------------------------------------------------------------------------
# 6. Quintile ECE: hand-checkable 10-observation case.
# ---------------------------------------------------------------------------

def test_quintile_ece_hand_case():
    fs = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
    ys = [0, 0, 0, 1, 1, 1, 1, 1, 1, 1]
    m = pcal_fit.metrics(fs, ys)
    # 5 equal-count bins of 2: contributions 0.15,0.15,0.45,0.25,0.075,
    # each weighted 0.2 => 0.2 * 1.075 = 0.215
    assert m["n"] == 10
    assert m["quintile_ece"] == pytest.approx(0.215)


# ---------------------------------------------------------------------------
# 7. Interpolation/eval: g() between breakpoints = pinned step value.
# ---------------------------------------------------------------------------

def test_g_between_breakpoints_is_step_value():
    g = pcal_fit.make_g([0.2, 0.4, 0.8], [0.3, 0.45, 0.9])
    assert g(0.7) == pytest.approx(0.45)   # largest breakpoint <= 0.7 is 0.4
    assert g(0.4) == pytest.approx(0.45)   # bin edge inclusive
    assert g(0.1) == pytest.approx(0.3)    # left of first breakpoint
    assert g(0.95) == pytest.approx(0.9)   # right of last breakpoint


def test_g_is_monotone_non_decreasing():
    bx, by = pcal_fit.fit_isotonic(
        _obs([(0.1, 0), (0.3, 0.4), (0.6, 0.7), (0.9, 1)]))
    g = pcal_fit.make_g(bx, by)
    seq = [g(x) for x in [0.0, 0.2, 0.4, 0.8, 1.0]]
    assert all(a <= b + 1e-12 for a, b in itertools.pairwise(seq))


# ---------------------------------------------------------------------------
# 8. Artifact roundtrip: written JSON reloads and reproduces g() exactly.
# ---------------------------------------------------------------------------

def test_artifact_roundtrip_reproduces_g(tmp_path):
    obs = _obs([(0.1, 0), (0.3, 0.2), (0.5, 0.6), (0.7, 0.8), (0.9, 1)])
    bx, by = pcal_fit.fit_isotonic(obs)
    art = {"version": "pcal-v1", "fitted_utc": "2026-08-31T00:00:00Z",
           "train_cutoff_ns": CUTOFF, "n_obs": len(obs), "n_series": 1,
           "source_files": [], "breakpoints_x": bx, "breakpoints_y": by}
    path = tmp_path / "pcal-v1.json"
    path.write_text(json.dumps(art))
    reloaded = json.loads(path.read_text())
    g_orig = pcal_fit.make_g(bx, by)
    g_rel = pcal_fit.make_g(reloaded["breakpoints_x"], reloaded["breakpoints_y"])
    for x in [0.0, 0.05, 0.15, 0.33, 0.5, 0.62, 0.9, 1.0]:
        assert g_rel(x) == g_orig(x)  # exact -- JSON round-trips floats
