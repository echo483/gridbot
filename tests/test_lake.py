"""Lake tests: ingest reconciliation, the event tape, gates — all hermetic.

Every test builds its own raw capture in ``tmp_path`` via ``synth_tape`` and
points the lake env knobs at it. No network, no keyring, no corpus, no GRID key.
"""

from __future__ import annotations

import json

import pytest
from synth_tape import Round, Series, default_series, write_day

from gridbot.lake import discover, rebuild_raw
from gridbot.lake.catalog import lake_coverage, scan_raw
from gridbot.lake.events import EVENT_COLUMNS, build_events, load_events
from gridbot.lake.gates import run_gates


@pytest.fixture
def raw_root(tmp_path, monkeypatch):
    root = tmp_path / "raw"
    lake = tmp_path / "lake"
    monkeypatch.setenv("GRIDBOT_RAW_DIR", str(root))
    monkeypatch.setenv("GRIDBOT_LAKE_DIR", str(lake))
    return root


def test_discover_finds_stream_and_day(raw_root):
    write_day(raw_root / "grid", [default_series()], day="20261027")
    write_day(raw_root / "grid", [default_series("7")], day="20261028")
    found = discover(raw_root)
    assert [f.stream for f in found] == ["series_state", "series_state"]
    assert [f.day for f in found] == ["2026-10-27", "2026-10-28"]


def test_ingest_counts_torn_tail_as_dropped(raw_root):
    path = write_day(raw_root / "grid", [default_series()], torn_tail=True)
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    malformed = sum(1 for line in lines if not _parses(line))
    assert malformed == 1  # the fixture really did tear the file

    result = rebuild_raw(root=raw_root)[0]
    assert result.dropped == malformed
    assert result.rows == len(lines) - malformed  # the torn line never becomes a row


def _parses(line: str) -> bool:
    try:
        json.loads(line)
    except json.JSONDecodeError:
        return False
    return True


def test_ingest_is_idempotent(raw_root):
    write_day(raw_root / "grid", [default_series()])
    first = rebuild_raw(root=raw_root)[0]
    second = rebuild_raw(root=raw_root)[0]
    assert not first.skipped
    assert second.skipped
    assert second.rows == first.rows


def test_ingest_reruns_when_the_file_grew(raw_root):
    directory = raw_root / "grid"
    write_day(directory, [default_series()], torn_tail=True)
    rebuild_raw(root=raw_root)
    # Capture keeps appending: the same day file grows after a first ingest.
    with open(directory / "series_state-20261028.jsonl", "a") as fh:
        fh.write(json.dumps({"type": "heartbeat", "ts_ns": 1}) + "\n")
    result = rebuild_raw(root=raw_root)[0]
    assert not result.skipped, "a changed file must be re-ingested, not skipped"


def test_event_tape_row_shape_and_scores(raw_root):
    write_day(raw_root / "grid", [default_series(n_rounds=3)])
    rebuild_raw(root=raw_root)
    build_events()
    con = load_events()
    try:
        rows = con.execute(
            "SELECT series_id, title, map_number, round_number, rounds_played, "
            "a_name, a_side, a_score, b_name, b_side, b_score "
            "FROM events ORDER BY round_number"
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 4  # one observation per resolved boundary + the live one
    # Slot a is the CT side, so the historical CT framing is reproduced.
    assert all(r[1] == "cs2" for r in rows)
    assert all(r[6] == "ct" and r[9] == "t" for r in rows)
    # The scoreboard and the in-progress round agree at every observation.
    assert all(r[4] == r[3] - 1 for r in rows)
    final = rows[-1]
    assert (final[7], final[10]) == (2, 1)  # AUR 2 - NAVI 1 after three rounds


def test_round_results_oracle_is_first_observation(raw_root):
    write_day(raw_root / "grid", [default_series(n_rounds=3)])
    rebuild_raw(root=raw_root)
    build_events()
    con = load_events()
    try:
        rows = con.execute(
            "SELECT round_number, winner_name, winner_side FROM round_results ORDER BY round_number"
        ).fetchall()
    finally:
        con.close()
    assert rows == [(1, "AUR", "ct"), (2, "NAVI", "t"), (3, "AUR", "ct")]


def test_tape_columns_are_stable(raw_root):
    """The Parquet schema must carry exactly EVENT_COLUMNS — a rename would
    silently reinterpret every number derived from the tape."""
    write_day(raw_root / "grid", [default_series()])
    rebuild_raw(root=raw_root)
    build_events()
    con = load_events()
    try:
        names = tuple(c[0] for c in con.execute("SELECT * FROM events LIMIT 0").description)
    finally:
        con.close()
    assert names == EVENT_COLUMNS


def test_series_with_unresolved_round_emits_no_label(raw_root):
    """A round whose winner is never observed emits no resolution row."""
    series = Series(series_id="42", winners=["AUR", None], rounds=[Round("AUR", "NAVI", 0, 0)] * 3)
    write_day(raw_root / "grid", [series])
    rebuild_raw(root=raw_root)
    build_events()
    con = load_events()
    try:
        rounds = [r[0] for r in con.execute("SELECT round_number FROM round_results").fetchall()]
    finally:
        con.close()
    assert rounds == [1]


def test_raw_scan_and_coverage(raw_root):
    write_day(raw_root / "grid", [default_series()], day="20261027")
    write_day(raw_root / "grid", [default_series("7")], day="20261029")
    rebuild_raw(root=raw_root)
    streams = scan_raw(raw_root)
    assert len(streams) == 1
    assert streams[0].days == ("2026-10-27", "2026-10-29")
    cov = lake_coverage()
    assert cov[0].days == 2
    assert cov[0].missing_days == ["2026-10-28"]  # a gap is a day, not a row count


def test_gates_pass_on_a_clean_tape(raw_root):
    write_day(raw_root / "grid", [default_series(n_rounds=4)])
    rebuild_raw(root=raw_root)
    build_events()
    report = run_gates()
    assert report.ok, report.render()
    names = {g.name for g in report.gates}
    assert "raw_reconciled" in names and "round_coherence" in names


def test_gates_detect_a_capture_gap(raw_root):
    """Two day files with a long silence between them must fail snapshot_gaps."""
    import time

    base = time.time_ns() - 7_200_000_000_000
    write_day(raw_root / "grid", [default_series()], day="20261027", base_ts_ns=base)
    write_day(raw_root / "grid", [default_series()], day="20261028", base_ts_ns=base + 3_600_000_000_000)
    rebuild_raw(root=raw_root)
    build_events()
    report = run_gates(max_gap_s=300.0)
    gap = next(g for g in report.gates if g.name == "snapshot_gaps")
    assert gap.passed is False
    assert "1 gap(s)" in gap.detail
    assert "worst 35" in gap.detail  # ~1h silence, the hour-apart fixture


def test_gates_report_not_run_on_an_empty_lake(raw_root):
    report = run_gates()
    assert not report.ok
    assert all(g.passed is None for g in report.gates if g.name != "raw_reconciled")
