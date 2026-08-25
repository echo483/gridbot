"""Tests for scripts/morning_report.py — synthetic records only, no real files."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from morning_report import (
    capture_stats,
    corpus_lines,
    day_files,
    final_verdict,
    in_window_ns,
    parse_iso_at,
    window_start,
)


def ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


class TestWindow:
    def test_window_start_subtracts_hours(self) -> None:
        now = datetime(2026, 8, 25, 12, tzinfo=UTC)
        assert window_start(now, 10) == now - timedelta(hours=10)

    def test_in_window_ns_bounds(self) -> None:
        now = datetime(2026, 8, 25, 12, tzinfo=UTC)
        since = now - timedelta(hours=10)
        assert in_window_ns(ns(now), since, now)
        assert not in_window_ns(ns(since), since, now)  # exclusive lower bound
        mid = since + timedelta(hours=5)
        assert in_window_ns(ns(mid), since, now)
        assert not in_window_ns(ns(now + timedelta(minutes=1)), since, now)

    def test_in_window_ns_mixed_sign_safe(self) -> None:
        now = datetime(2026, 8, 25, 12, tzinfo=UTC)
        since = now - timedelta(hours=1)
        assert not in_window_ns(ns(datetime(2020, 1, 1, tzinfo=UTC)), since, now)


class TestParseIsoAt:
    def test_z_suffix(self) -> None:
        assert parse_iso_at("2026-08-25T06:00:00Z") == datetime(2026, 8, 25, 6, tzinfo=UTC)

    def test_offset_normalized(self) -> None:
        dt = parse_iso_at("2026-08-25T06:00:00+02:00")
        assert dt == datetime(2026, 8, 25, 4, tzinfo=UTC)

    def test_naive_treated_as_utc(self) -> None:
        assert parse_iso_at("2026-08-25T06:00:00") == datetime(2026, 8, 25, 6, tzinfo=UTC)

    def test_garbage_returns_none(self) -> None:
        assert parse_iso_at("not-a-date") is None
        assert parse_iso_at(None) is None
        assert parse_iso_at("") is None


class TestCaptureStats:
    def test_counts_and_live_filtering(self) -> None:
        now = datetime(2026, 8, 25, 12, tzinfo=UTC)
        since = now - timedelta(hours=10)
        mid = ns(now - timedelta(hours=2))
        old = ns(now - timedelta(hours=20))
        recs = [
            {"type": "ss", "ts_ns": mid, "series_id": "101", "title": "cs2", "data": {"seriesState": {"started": True}}},
            {"type": "ss", "ts_ns": mid, "series_id": "102", "title": "cs2", "data": {"seriesState": {"started": True, "finished": True}}},
            {"type": "ss", "ts_ns": mid, "series_id": "2", "title": "cs2", "data": {"seriesState": {"started": True}}},  # test id
            {"type": "ss", "ts_ns": mid, "series_id": "555", "title": "dota", "data": {"seriesState": {"started": True}}},
            {"type": "ss", "ts_ns": old, "series_id": "103", "title": "cs2", "data": {"seriesState": {"started": True}}},
            {"type": "ss_error", "ts_ns": mid},
            {"type": "ss_error", "ts_ns": old},
            {"type": "stale_alarm", "ts_ns": mid},
        ]
        s = capture_stats(recs, since, now)
        assert s["polls"] == 4
        assert s["live"] == {"cs2": {"101"}, "dota": {"555"}}
        assert s["errors"] == 1
        assert s["stale"] == 1

    def test_missing_ts_excluded(self) -> None:
        now = datetime(2026, 8, 25, 12, tzinfo=UTC)
        s = capture_stats([{"type": "ss", "series_id": "1"}], now - timedelta(hours=1), now)
        assert s["polls"] == 0


class TestCorpusLines:
    def test_nested_series_shape(self, tmp_path: Path) -> None:
        # Real .ingested.json nests entries under a top-level "series" key.
        now = datetime(2026, 8, 25, 12, tzinfo=UTC)
        since = now - timedelta(hours=10)
        (tmp_path / "corpus").mkdir()
        (tmp_path / "corpus" / ".ingested.json").write_text(
            '{"series": {"55": {"at": "2026-08-25T06:00:00Z", "rows": 40},'
            ' "66": {"at": "2026-08-20T06:00:00Z", "rows": 10}}}'
        )
        lines = corpus_lines(since, now, tmp_path)
        assert lines[0] == "corpus: ingested overnight 55:40"
        assert lines[1] == "  ingested total: 2 series / 50 rows"

    def test_missing_file_degrades(self, tmp_path: Path) -> None:
        now = datetime(2026, 8, 25, 12, tzinfo=UTC)
        assert corpus_lines(now - timedelta(hours=1), now, tmp_path) == ["corpus: 0 series ingested overnight"]

    def test_flat_legacy_shape_degrades(self, tmp_path: Path) -> None:
        # A file without the "series" key must not crash or miscount.
        now = datetime(2026, 8, 25, 12, tzinfo=UTC)
        (tmp_path / "corpus").mkdir()
        (tmp_path / "corpus" / ".ingested.json").write_text(
            '{"55": {"at": "2026-08-25T06:00:00Z", "rows": 40}}'
        )
        lines = corpus_lines(now - timedelta(hours=10), now, tmp_path)
        assert lines == ["corpus: 0 series ingested overnight"]


class TestDayFiles:
    def test_filters_old_days(self, tmp_path: Path) -> None:
        keep = tmp_path / "series_state-20260825.jsonl"
        keep.touch()
        (tmp_path / "series_state-20260101.jsonl").touch()
        since = datetime(2026, 8, 25, 0, tzinfo=UTC)
        assert day_files(tmp_path, "series_state", since) == [keep]

    def test_missing_dir(self, tmp_path: Path) -> None:
        assert day_files(tmp_path / "nope", "series_state", datetime(2026, 8, 25, tzinfo=UTC)) == []


class TestVerdict:
    def test_quiet_night(self) -> None:
        stats = {"errors": 0, "stale": 0}
        assert final_verdict(stats, ["ops: ingest=success · backup=success"]) == "verdict: quiet night"

    def test_top_issue(self) -> None:
        stats = {"errors": 3, "stale": 5}
        ops = ["ops: ingest=failed · backup=success"]
        assert final_verdict(stats, ops) == "verdict: 3 ss errors (+2 more)"

    def test_failed_unit_flags(self) -> None:
        assert "ingest=failed" in final_verdict({}, ["ops: ingest=failed · backup=success"])

    def test_escalated_flags(self) -> None:
        ops = ["ops: ingest=success · escalated=gridbot-capture"]
        assert final_verdict({}, ops) == "verdict: escalated=gridbot-capture"
