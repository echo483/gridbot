"""Tests for the post-quote oriented drift sampler (pure + stubbed wire)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from gridbot.instruments import postquote
from gridbot.instruments.postquote import Sampler, mid, oriented, samples_from_row

# The sampler writes to TODAY's dated file — a hardcoded date here passes on
# the day the test is written and breaks at the next UTC midnight.
TODAY = f"{datetime.now(UTC):%Y%m%d}"


def _qrow(**over) -> dict:
    row = {
        "type": "mapentry",
        "qualifies": True,
        "market_slug": "cs2-aur-g2-2026-08-26-map1-aur",
        "us_side": "long",
        "bid": 0.78,
        "ask": 0.80,
        "series_id": "2864000",
        "map_number": 1,
        "dedup_extra": "team:0.85",
        "ts_ns": 1756190000000000000,
    }
    row.update(over)
    return row


class FakeBooks:
    def __init__(self, top=(0.80, 0.82)):
        self._top = top
        self.calls: list[str] = []

    def top(self, slug):
        self.calls.append(slug)
        return self._top


def _sampler(tmp_path, books=None) -> Sampler:
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / f"mapentry-{TODAY}.jsonl").write_text("")
    return Sampler(books=books or FakeBooks(), out_dir=shadow,
                   shadow_dir=shadow, from_start=True)


class TestPure:
    def test_mid(self):
        assert mid(0.78, 0.80) == 0.79
        assert mid(None, 0.80) is None
        assert mid(0.78, None) is None

    def test_oriented_long_up_is_favorable(self):
        assert round(oriented("long", 0.79, 0.83), 4) == 0.04

    def test_oriented_short_down_is_favorable(self):
        assert round(oriented("short", 0.79, 0.75), 4) == 0.04

    def test_oriented_none_propagates(self):
        assert oriented("long", None, 0.8) is None
        assert oriented("long", 0.8, None) is None

    def test_samples_only_from_qualifying_rows(self):
        assert samples_from_row(_qrow(qualifies=False), 0.0) == []
        assert samples_from_row(_qrow(type="instance"), 0.0) == []
        assert samples_from_row(_qrow(market_slug=None), 0.0) == []
        assert samples_from_row(_qrow(bid=None), 0.0) == []

    def test_samples_one_per_offset_with_due_times(self):
        entries = samples_from_row(_qrow(), 100.0)
        assert [due for due, _ in entries] == [130.0, 160.0, 220.0]
        assert all(s["mid0"] == 0.79 for _, s in entries)
        assert {s["dt_s"] for _, s in entries} == {30.0, 60.0, 120.0}


class TestSampler:
    def _feed(self, sampler: Sampler, row: dict) -> None:
        path = sampler.follow.directory / f"mapentry-{TODAY}.jsonl"
        with open(path, "a") as fh:
            fh.write(json.dumps(row) + "\n")

    def test_end_to_end_oriented_rows(self, tmp_path):
        books = FakeBooks(top=(0.80, 0.82))  # mid 0.81, +0.02 vs mid0 0.79
        s = _sampler(tmp_path, books=books)
        self._feed(s, _qrow())
        assert s.poll_once(1000.0) == 0  # ingested, nothing due yet
        assert len(s.pending) == 3
        assert s.poll_once(1030.0) == 1  # +30s due
        assert s.poll_once(1220.0) == 2  # +60 and +120 due together
        out = [json.loads(x) for x in
               (tmp_path / "shadow" / f"postquote-{TODAY}.jsonl")
               .read_text().splitlines()]
        assert [r["dt_s"] for r in out] == [30.0, 60.0, 120.0]
        assert all(r["oriented_move"] == 0.02 for r in out)
        assert all(r["src_ts_ns"] == 1756190000000000000 for r in out)

    def test_short_side_sign_flip(self, tmp_path):
        s = _sampler(tmp_path, books=FakeBooks(top=(0.80, 0.82)))
        self._feed(s, _qrow(us_side="short"))
        s.poll_once(1000.0)
        s.poll_once(1300.0)
        out = [json.loads(x) for x in
               (tmp_path / "shadow" / f"postquote-{TODAY}.jsonl")
               .read_text().splitlines()]
        assert all(r["oriented_move"] == -0.02 for r in out)

    def test_dead_book_writes_null_move(self, tmp_path):
        s = _sampler(tmp_path, books=FakeBooks(top=(None, None)))
        self._feed(s, _qrow())
        s.poll_once(1000.0)
        s.poll_once(1300.0)
        out = [json.loads(x) for x in
               (tmp_path / "shadow" / f"postquote-{TODAY}.jsonl")
               .read_text().splitlines()]
        assert all(r["oriented_move"] is None and r["mid"] is None for r in out)

    def test_non_qualifying_rows_ignored(self, tmp_path):
        s = _sampler(tmp_path)
        self._feed(s, _qrow(qualifies=False))
        self._feed(s, {"type": "instance", "q": 0.9})
        assert s.poll_once(1000.0) == 0
        assert s.pending == []

    def test_pending_cap_drops_oldest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(postquote, "MAX_PENDING", 4)
        s = _sampler(tmp_path)
        self._feed(s, _qrow(ts_ns=1))
        self._feed(s, _qrow(ts_ns=2))
        s.poll_once(1000.0)
        assert len(s.pending) == 4  # 6 scheduled, capped at 4
