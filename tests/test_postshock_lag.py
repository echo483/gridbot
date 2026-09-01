"""Tests for scripts.postshock_lag (post-shock book-vs-model lag measurement).

Analysis only (slow loop, iron rail 1): locks in the shock/restart/window
semantics before any instrument consumes them.  No network, no keyring, no
data mutation — pure functions over synthetic rows, mirroring
test_wave_detect.py.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import pytest

# Ensure the project root is on sys.path so `scripts` is importable (pytest
# only adds tests/ to sys.path; mirror the existing test_wave_detect.py).
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts.postshock_lag import (
    NS_PER_S,
    SOURCE_FILE_TAG,
    detect,
    load_data,
)

S = NS_PER_S


def mk(ts_ns, p_map, side_ask=None, **kw) -> dict:
    """Build a minimal mapentry row for a single default sequence."""
    row = {
        "series_id": "s1",
        "map_number": 1,
        "team": "AAA",
        "ts_ns": ts_ns,
        "round_number": kw.pop("round_number", ts_ns),
        "p_map": p_map,
        "side_ask": side_ask,
    }
    row.update(kw)
    return row


# ── (a) shock fires exactly at the threshold boundary ─────────────────────


def test_threshold_boundary_delta_equals_shock_fires():
    # delta == shock fires (>= semantics).  Reviewer F9: the comparison
    # cases are built from quarter/binary-fraction values whose float diff
    # is BIT-EXACT (0.75-0.5 == 0.25 double, and 0.625-0.5 = 0.125 <
    # 0.25), so this test exercises the boundary itself — not a decimal
    # value that happens to land within one ulp of it.  Do not template
    # arbitrary decimal diffs here; they may straddle the threshold in
    # either direction.
    rows = [
        mk(0 * S, 0.50, 0.50),
        mk(10 * S, 0.75, 0.75),  # delta = +0.25 == --shock
    ]
    assert 0.75 - 0.50 == 0.25  # bit-exactness precondition for this test
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    assert res["summary"]["n_shocks"] == 1
    rec = res["shocks"][0]
    assert rec["prev_p_map"] == pytest.approx(0.50)
    assert rec["p_map"] == pytest.approx(0.75)
    assert rec["delta"] == pytest.approx(0.25)

    # strictly below threshold -> no fire
    rows[1]["p_map"] = 0.625  # 0.625 - 0.50 == 0.125 < 0.25 (bit-exact)
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    assert res["summary"]["n_shocks"] == 0

    # tiny step below any threshold never fires
    res = detect([mk(0, 0.50), mk(S, 0.55)], shock=0.25)
    assert res["summary"]["n_shocks"] == 0


# ── (b) convergence time from the FIRST |gap| <= converge row ─────────────


def test_time_to_converge_uses_first_within_band():
    rows = [
        mk(0 * S, 0.90, 0.95),       # pre-shock baseline row (book present)
        mk(5 * S, 0.30, 0.85),       # SHOCK down; gap_at_shock = 0.55
        mk(20 * S, 0.32, 0.80),      # gap 0.48
        mk(60 * S, 0.31, 0.34),      # gap 0.03 <= 0.05 -> converges here
        mk(90 * S, 0.33, 0.40),      # gap 0.07 again; must NOT change answer
    ]
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    assert res["summary"]["n_shocks"] == 1
    rec = res["shocks"][0]
    # Shock fires at t=5s; first converged row is at t=60s -> 55s later.
    assert rec["time_to_converge"] == pytest.approx(55.0)
    assert res["summary"]["median_time_to_converge_s"] == pytest.approx(55.0)
    assert rec["gap_at_shock"] == pytest.approx(0.55)
    assert rec["gap_at_end"] == pytest.approx(0.07)
    assert rec["n_book_obs"] == 4
    assert rec["direction"] == "down"


def test_book_before_window_or_after_end_not_measured():
    rows = [
        mk(0 * S, 0.50, 0.52),
        mk(1 * S, 0.00, 0.51),   # shock up at t=1s; window covers 1s..301s
        mk(2 * S, 0.02, 0.03),   # inside window -> measured (|gap|=0.01)
        mk(310 * S, 0.04, 0.05), # past window end -> excluded
    ]
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    assert res["summary"]["n_shocks"] == 1
    rec = res["shocks"][0]
    # Shock row's own quote is the first at/after-shock observation.
    assert rec["n_book_obs"] == 2          # t=1s (shock row) + t=2s
    assert rec["gap_at_shock"] == pytest.approx(0.51)
    assert rec["gap_at_end"] == pytest.approx(0.01)
    assert rec["time_to_converge"] == pytest.approx(1.0)


def test_window_boundary_is_inclusive():
    # Row lands exactly on window_end_ns == anchor + window_s: included.
    rows = [
        mk(0, 0.10, 0.12),
        mk(1 * S, 0.60, 0.95),               # shock up; window [1s, 301s]
        mk(301 * S, 0.62, 0.66),             # gap 0.04 <= 0.05, ts == end
    ]
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    rec = res["shocks"][0]
    assert rec["n_book_obs"] == 2  # shock row's own quote + the boundary row
    assert rec["gap_at_shock"] == pytest.approx(0.35)  # not yet converged
    assert rec["time_to_converge"] == pytest.approx(300.0)


# ── (c) never converges -> null and converge_rate reflects it ──────────────


def test_never_converges_null_ttc_and_rate_zero():
    rows = [
        mk(0 * S, 0.90, 0.95),
        mk(5 * S, 0.30, 0.85),   # shock down; book stays far away
        mk(60 * S, 0.35, 0.75),  # gap 0.40, still converged nowhere
    ]
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    rec = res["shocks"][0]
    assert rec["time_to_converge"] is None
    s = res["summary"]
    assert s["median_time_to_converge_s"] is None
    # Book observations exist, so a genuine measured failure: rate 0.0.
    assert s["converge_rate"] == pytest.approx(0.0)


def test_empty_input_all_nulls_no_crash():
    res = detect([], shock=0.25)
    assert res["summary"]["n_shocks"] == 0
    assert res["summary"]["n_with_book"] == 0
    # Reviewer F5: with zero book observations convergence is undecidable,
    # not failed -- null like every other empty aggregate.
    assert res["summary"]["converge_rate"] is None
    assert res["summary"]["median_time_to_converge_s"] is None
    assert res["summary"]["median_gap_at_shock"] is None
    for d in ("up", "down"):
        assert res["summary"]["by_direction"][d]["n_shocks"] == 0
        assert res["summary"]["by_direction"][d]["converge_rate"] is None
        assert res["summary"]["by_direction"][d]["median_gap_at_shock"] is None


def test_shocks_without_any_book_rows_excluded_from_gaps_but_counted():
    rows = [
        mk(0 * S, 0.50, None),      # no book ever
        mk(1 * S, 0.80, None),      # shock up; no observation possible
    ]
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    s = res["summary"]
    assert s["n_shocks"] == 1
    assert s["n_with_book"] == 0
    # Reviewer F5: undecidable (no book) -> null, NOT a fake 0.0.
    assert s["converge_rate"] is None
    assert s["median_gap_at_shock"] is None
    assert res["shocks"][0]["n_book_obs"] == 0
    assert res["shocks"][0]["time_to_converge"] is None


# ── (d) restart of an open window counts as a restart ──────────────────────


def test_second_shock_restarts_open_window_and_counts():
    rows = [
        mk(0 * S, 0.50, 0.55),
        mk(5 * S, 0.20, 0.15),    # shock 1 (down); window [5s, 305s]
        mk(50 * S, 0.18, 0.14),   # in-window observation for shock 1
        mk(60 * S, 0.70, 0.68),   # shock 2 RESTARTS window 1 ...
        mk(100 * S, 0.72, 0.71),  # ... observed by window 2 only
        mk(400 * S, 0.73, 0.74),  # outside both windows
    ]
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    s = res["summary"]
    assert s["n_shocks"] == 2
    assert s["n_window_restarts"] == 1

    first, second = res["shocks"][0], res["shocks"][1]
    assert first["ended_by_restart"] is True
    assert second["ended_by_restart"] is False
    # Window 1 was cut short: the shock row's own quote plus the t=50s row.
    assert first["n_book_obs"] == 2
    assert first["gap_at_shock"] == pytest.approx(0.15 - 0.20)
    # Window 2 owns the later rows and inherits a fresh 300s tail even though
    # less than 300s remains before some nominal deadline.
    assert second["window_start_ns"] == 60 * S
    assert second["window_end_ns"] == 360 * S
    # Shock row's own quote + the t=100s row.
    assert second["n_book_obs"] == 2


def test_late_shock_does_not_restart_expired_window():
    # Second shock arrives after the first window expired: it opens its own
    # window but must NOT count as a restart.
    rows = [
        mk(0 * S, 0.50, 0.55),
        mk(1 * S, 0.20, 0.19),     # shock 1; window expires at 301s
        mk(500 * S, 0.80, 0.78),   # shock 2 after expiry -> normal new window
    ]
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    s = res["summary"]
    assert s["n_shocks"] == 2
    assert s["n_window_restarts"] == 0
    assert all(rec["ended_by_restart"] is False for rec in res["shocks"])


# ── (e) up vs down direction split ─────────────────────────────────────────


def test_up_vs_down_direction_split():
    rows = [
        # up-shock sequence
        mk(0 * S, 0.30, 0.28, series_id="sA", team="UPTEAM"),
        mk(5 * S, 0.60, 0.59, series_id="sA", team="UPTEAM"),  # +0.30 up
        # down-shock sequence (book ABOVE the model post-flip: NuTorious-
        # style lag-down regime, gap > 0)
        mk(0 * S, 0.80, 0.83, series_id="sB", team="DOWNTEAM"),
        mk(7 * S, 0.45, 0.47, series_id="sB", team="DOWNTEAM"),  # -0.35 down
    ]
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    s = res["summary"]
    assert s["n_shocks"] == 2
    assert s["by_direction"]["up"]["n_shocks"] == 1
    assert s["by_direction"]["down"]["n_shocks"] == 1
    assert res["shocks"][0]["direction"] == "up"
    assert res["shocks"][0]["delta"] == pytest.approx(0.30)
    assert res["shocks"][1]["direction"] == "down"
    assert res["shocks"][1]["delta"] == pytest.approx(-0.35)
    # per-direction aggregates carry their own medians
    up = s["by_direction"]["up"]
    down = s["by_direction"]["down"]
    # up case: ask 0.59 vs p_map 0.60 -> book BELOW model (-0.01)
    assert up["median_gap_at_shock"] == pytest.approx(0.59 - 0.60)
    # down case: ask 0.47 vs p_map 0.45 -> book ABOVE model (+0.02)
    assert down["median_gap_at_shock"] == pytest.approx(0.47 - 0.45)
    # Reviewer F2: pooled sign split separates book-above vs book-below.
    split = s["gap_sign_split_at_shock"]
    assert split["n_book_above_model"] == 1   # +0.02 (down/NuTorious case)
    assert split["n_book_below_model"] == 1   # -0.01 (up/wave-start case)
    assert split["median_gap_when_above"] == pytest.approx(0.02)
    assert split["median_gap_when_below"] == pytest.approx(-0.01)


# ── (f) no-book rows keep the sequence alive without entering gaps ─────────


def test_rows_without_book_tracked_but_excluded_from_gaps():
    rows = [
        mk(0 * S, 0.40, 0.42),
        mk(1 * S, 0.70, None),     # shock up, NO book on the shock row itself
        mk(2 * S, 0.71, None),     # valid p_map keeps sequence alive, no gap
        mk(3 * S, 0.72, 0.745),    # first book obs; gap 0.025 -> convergence
        mk(4 * S, 0.73, None),     # book drops out again
    ]
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    s = res["summary"]
    assert s["n_shocks"] == 1
    assert s["n_with_book"] == 1
    rec = res["shocks"][0]
    assert rec["n_book_obs"] == 1              # only the t=3s row produced a gap
    assert rec["gap_at_shock"] == pytest.approx(0.025)
    assert rec["gap_at_end"] == pytest.approx(0.025)
    assert rec["time_to_converge"] == pytest.approx(2.0)
    assert res["counts"]["n_sequences"] == 1


def test_invalid_p_map_rows_do_not_break_sequencing_or_windows():
    rows = [
        mk(0 * S, 0.40, 0.41),
        mk(1 * S, None, 0.42),            # unusable p_map; sequence continues
        mk(2 * S, "bad", 0.43),           # unusable p_map (string)
        mk(3 * S, math.inf, 0.44),        # non-finite -> invalid
        mk(4 * S, True, 0.45),            # bool p_map -> invalid guard
        mk(5 * S, 0.75, 0.76),            # shock fires vs prev VALID row (0.40)
        mk(6 * S, 0.76, 0.82),            # gap 0.06
    ]
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    s = res["summary"]
    assert s["n_shocks"] == 1
    rec = res["shocks"][0]
    assert rec["prev_p_map"] == pytest.approx(0.40)
    assert rec["p_map"] == pytest.approx(0.75)
    assert rec["n_book_obs"] == 2  # shock-row ask plus the t=6s row
    assert rec["gap_at_shock"] == pytest.approx(0.01)


# ── (g) malformed rows are counted and skipped ─────────────────────────────


def test_load_data_counts_malformed_lines(tmp_path):
    good = json.dumps(mk(0, 0.5))
    f = tmp_path / "day.jsonl"
    f.write_text(
        "\n"                       # blank line
        + "{not json}\n"           # malformed JSON
        + "[1, 2, 3]\n"            # valid JSON but not an object
        + 'null\n'                 # JSON null literal
        + good + "\n",
    )
    rows, stats = load_data([str(f)])
    assert len(rows) == 1
    assert stats["lines_read"] == 5
    assert stats["lines_blank"] == 1
    assert stats["lines_malformed_json"] == 1
    assert stats["lines_non_object"] == 2
    with pytest.raises(OSError):
        load_data(["/nonexistent/nope.jsonl"])


def test_detect_guards_bad_values():
    # Non-int / float / bool / missing ts_ns rows are skipped wholesale.
    bad_ts = [
        {"series_id": "x", "p_map": 0.5},
        mk("1712345678.5", 0.5),   # string ts
        mk(1712345678.5, 0.5),     # float ts -> forbidden float-ts path
        mk(True, 0.5),             # bool ts
    ]
    res = detect(bad_ts)
    assert res["counts"]["n_rows_without_valid_ts"] == 4
    assert res["counts"]["n_sequences"] == 0
    assert res["summary"]["n_shocks"] == 0

    # Unhashable sequence-key parts don't crash grouping.
    weird_key = [
        {"series_id": ["a"], "map_number": {}, "team": None,
         "ts_ns": 0, "p_map": 0.10},
        {"series_id": ["a"], "map_number": {}, "team": None,
         "ts_ns": S, "p_map": 0.60},
    ]
    res = detect(weird_key)
    assert res["counts"]["n_sequences"] == 1
    assert res["summary"]["n_shocks"] == 1

    # Parameter guards.
    with pytest.raises(ValueError):
        detect([], shock=0.0)
    with pytest.raises(ValueError):
        detect([], window_s=0)
    with pytest.raises(ValueError):
        detect([], converge=-0.1)

    # Reviewer F4: NaN parameters must be rejected, not silently accepted
    # (every comparison with NaN is False, so shock=nan would detect
    # nothing and converge=nan would make convergence impossible).
    with pytest.raises(ValueError):
        detect([], shock=float("nan"))
    with pytest.raises(ValueError):
        detect([], converge=float("nan"))
    # inf is likewise non-finite -> rejected for float params.
    with pytest.raises(ValueError):
        detect([], shock=float("inf"))

    # Reviewer F7 (partial application of finding): a genuine NaN key part
    # can never equal itself, so it is pinned to one stand-in instead of
    # fragmenting/merging sequences depending on loader identity caching.
    nan_rows = [
        {"series_id": "x", "map_number": 1, "team": float("nan"),
         "ts_ns": 0, "p_map": 0.10},
        {"series_id": "x", "map_number": 1, "team": float("nan"),
         "ts_ns": S, "p_map": 0.60},
    ]
    res = detect(nan_rows)
    assert res["counts"]["n_sequences"] == 1
    assert res["summary"]["n_shocks"] == 1


# ── aggregate structure sanity ──────────────────────────────────────────────


def test_summary_shape_and_params_echo():
    rows = [
        mk(0 * S, 0.30, 0.29),
        mk(5 * S, 0.60, 0.58),
    ]
    res = detect(rows, shock=0.25, window_s=120, converge=0.02)
    assert res["params"] == {"shock": 0.25, "window_s": 120, "converge": 0.02}
    s = res["summary"]
    assert set(s) == {
        "n_shocks", "n_window_restarts", "n_with_book", "n_converged",
        "converge_rate", "median_time_to_converge_s", "median_gap_at_shock",
        "gap_sign_split_at_shock", "by_direction",
    }
    assert set(s["gap_sign_split_at_shock"]) == {
        "n_book_above_model", "n_book_below_model",
        "median_gap_when_above", "median_gap_when_below",
    }
    rec = res["shocks"][0]
    assert rec["series_id"] == "s1" and rec["team"] == "AAA"
    assert rec["shock_ts_ns"] == 5 * S
    assert rec["window_start_ns"] == 5 * S
    assert rec["window_end_ns"] == 125 * S


# ── reviewer F1: per-file sequence partitioning ───────────────────────────


def _load_two_files(rows_a: list[dict], rows_b: list[dict], tmp_path,
                   stem: str = "day") -> tuple[list[dict], dict]:
    """Write two day files; return load_data output for both."""
    fa = tmp_path / f"{stem}-a.jsonl"
    fb = tmp_path / f"{stem}-b.jsonl"
    fa.write_text("".join(json.dumps(r) + "\n" for r in rows_a))
    fb.write_text("".join(json.dumps(r) + "\n" for r in rows_b))
    return load_data([str(fa), str(fb)])


def test_sequences_do_not_span_input_file_boundary(tmp_path):
    # Same sequence key in two files: a +0.30 repricing split across the
    # boundary (+0.15 in each) must NOT fire as one bridged shock.  Inside
    # each file it is below threshold, so no shocks at all are expected.
    rows_a = [mk(100 * S, 0.30, 0.31)]
    rows_b = [mk(9_000_000 * S, 0.45, 0.46)]  # hours later: capture hole
    rows, stats = _load_two_files(rows_a, rows_b, tmp_path)
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    assert res["summary"]["n_shocks"] == 0            # no bridged shock
    assert res["counts"]["n_source_files"] == 2       # both files sequenced
    assert stats["n_input_files"] == 2


def test_per_file_shocks_are_independent_across_files(tmp_path):
    # A genuine shock inside each file is detected once per file even
    # though the sequence keys and prices are identical.
    rows_single = [
        mk(100 * S, 0.50, 0.52),
        mk(105 * S, 0.20, 0.21),   # -0.30 down shock
    ]
    rows, _stats = _load_two_files(rows_single, rows_single, tmp_path)
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    assert res["summary"]["n_shocks"] == 2            # one per file copy
    assert res["counts"]["n_sequences"] == 2          # NOT merged into one
    ts_list = sorted(rec["shock_ts_ns"] for rec in res["shocks"])
    assert ts_list == [105 * S, 105 * S]              # same local stamps


def test_untagged_rows_share_one_universe_backcompat():
    # Direct detect() callers that bypass load_data keep the historical
    # cross-row behavior (one universe); counted via n_untagged_rows.
    rows = [
        mk(0 * S, 0.40),
        mk(1 * S, 0.70),           # fires as before for direct callers
    ]
    assert all(SOURCE_FILE_TAG not in r for r in rows)
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    assert res["summary"]["n_shocks"] == 1
    assert res["counts"]["n_untagged_rows"] == 2
    assert res["counts"]["n_source_files"] == 0


def test_duplicate_input_paths_warn_and_count(tmp_path, capsys):
    # Reviewer F1: passing the same file twice silently double-counted
    # rows before; now it warns on stderr and tallies in stats.
    rows_single = [
        mk(100 * S, 0.50, 0.52),
        mk(105 * S, 0.20, 0.21),
    ]
    fa = tmp_path / "dup-a.jsonl"
    fb = tmp_path / "sub" / "dup-b.jsonl"
    fb.parent.mkdir()
    fb.write_text("".join(json.dumps(r) + "\n" for r in rows_single))
    fa.write_text("".join(json.dumps(r) + "\n" for r in rows_single))
    # Same file reachable via two spellings -> normalized to one path.
    rows, stats = load_data(
        [str(fa), str(fb), str(fb),
         str(tmp_path / "sub" / ".." / "sub" / "dup-b.jsonl")]
    )
    assert len(rows) == 8            # 2 rows x 4 file args
    assert stats["n_input_files"] == 4
    assert stats["n_duplicate_inputs"] == 2
    captured = capsys.readouterr()
    assert "duplicate input" in captured.err
    # The duplicated rows still belong to per-arg universes; each copy
    # detects its own shock inside its own universe (4 args -> 4 shocks).
    res = detect(rows, shock=0.25, window_s=300, converge=0.05)
    assert res["summary"]["n_shocks"] == 4
    assert res["counts"]["n_sequences"] == 4

    # Relative vs absolute spelling of the same file also collapses.
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        _rows2, stats2 = load_data([str(fa), os.path.relpath(str(fa))])
    finally:
        os.chdir(cwd)
    assert stats2["n_duplicate_inputs"] == 1


def test_real_day_file_smoke(tmp_path):
    """End-to-end over actual capture data: runs clean, output is JSON-safe.

    Reviewer F10: anchored by GLOB over the rotating shadow corpus instead
    of one hardcoded dated filename -- when that file is retired the test
    should follow the corpus, not go dark behind a skip.
    """
    root = Path(__file__).resolve().parent.parent
    days = sorted(root.glob("data/shadow/mapentry-*.jsonl"))
    if not days:
        pytest.fail(  # fail loudly rather than silently skipping coverage
            "no data/shadow/mapentry-*.jsonl present — capture corpus is "
            "gone; cannot run end-to-end smoke over real data"
        )
    day = days[-1]  # most recent day file

    from scripts.postshock_lag import main

    out = tmp_path / "result.json"
    rc = main([str(day), "--out", str(out)])
    assert rc == 0
    doc = json.loads(out.read_text())
    assert set(doc) >= {"params", "counts", "shocks", "summary"}
    assert doc["params"]["shock"] == 0.25
    assert doc["counts"]["lines_read"] > 0
