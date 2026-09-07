"""GRID poller resilience: batch isolation, concurrency, prune tombstones, breaker.

Every test here pins a defect measured on the live capture stream (2026-09-06
cadence audit: p50 13.83s / p99 31.79s / max 216.23s against a 12s target).
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from gridbot import config
from gridbot.capture import grid_poller
from gridbot.capture.files import DayWriter
from gridbot.capture.grid_poller import STALE_LIVE_S, GridCapture
from gridbot.graphql import GraphQLError
from gridbot.ratelimit import RateLimiter


def _capture(tmp_path) -> GridCapture:
    return GridCapture(DayWriter(tmp_path, "ss"), DayWriter(tmp_path, "disc"))


def _entry(**over) -> dict:
    base = {
        "title": "cs2",
        "started": False,
        "finished": False,
        "next_poll": 0.0,
        "last_hash": "",
        "last_change": time.monotonic(),
        "stale_flagged": False,
        "empty_streak": 0,
        "ever_valid": False,
        "scheduled": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    base.update(over)
    return base


def _node(sid: str) -> dict:
    """A discovery edge node scheduled now, so no prune rule can claim it."""
    return {"id": sid, "startTimeScheduled": datetime.now(UTC).isoformat(timespec="seconds")}


def _rows(tmp_path: Path, stem: str = "ss") -> list[dict]:
    out: list[dict] = []
    for path in sorted(tmp_path.glob(f"{stem}-*.jsonl")):
        out += [json.loads(line) for line in path.read_text().splitlines() if line]
    return out


async def _discover(g: GridCapture, monkeypatch, nodes: list[dict]) -> None:
    async def fake_post(client, url, query, variables=None, **kw):
        return {
            "allSeries": {
                "edges": [{"node": n} for n in nodes],
                "pageInfo": {"hasNextPage": False},
            }
        }

    monkeypatch.setattr(grid_poller, "post", fake_post)
    monkeypatch.setattr(config, "TITLE_IDS", {"cs2": config.TITLE_ID_CS2})
    await g.discover_once(None)


class _CountingLimiter(RateLimiter):
    """RateLimiter that records acquisitions, so ceilings can be asserted."""

    def __init__(self, rpm: int) -> None:
        super().__init__(rpm)
        self.acquires = 0

    async def acquire(self) -> None:
        self.acquires += 1
        await super().acquire()


# ── FIX 1: one series' failure must not abort its siblings ───────────────────
async def test_transport_failure_on_one_series_does_not_abort_the_batch(tmp_path, monkeypatch):
    g = _capture(tmp_path)
    g.tracked = {sid: _entry(started=True) for sid in ("a", "bad", "c", "d")}

    async def fake_post(client, url, query, variables=None, **kw):
        sid = variables["id"]
        if sid == "bad":
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
        return {"seriesState": {"id": sid, "started": True, "finished": False}}

    monkeypatch.setattr(grid_poller, "post", fake_post)
    t0 = time.monotonic()
    await g.poll_due(None)

    rows = _rows(tmp_path)
    assert {r["series_id"] for r in rows if r["type"] == "ss"} == {"a", "c", "d"}
    errs = [r for r in rows if r["type"] == "ss_error"]
    assert [r["series_id"] for r in errs] == ["bad"]
    assert "ConnectError" in errs[0]["error"]
    assert g.polls == 3
    assert g.tracked["bad"]["next_poll"] >= t0 + config.SS_IDLE_POLL_S
    for sid in ("a", "c", "d"):
        assert g.tracked[sid]["next_poll"] >= t0 + config.SS_LIVE_POLL_S


async def test_graphql_error_still_isolates_to_its_own_series(tmp_path, monkeypatch):
    g = _capture(tmp_path)
    g.tracked = {sid: _entry(started=True) for sid in ("a", "bad")}

    async def fake_post(client, url, query, variables=None, **kw):
        if variables["id"] == "bad":
            raise GraphQLError("GraphQL errors: [{'message': 'nope'}]")
        return {"seriesState": {"id": "a", "started": True, "finished": False}}

    monkeypatch.setattr(grid_poller, "post", fake_post)
    await g.poll_due(None)

    rows = _rows(tmp_path)
    assert {r["series_id"] for r in rows if r["type"] == "ss"} == {"a"}
    assert [r["series_id"] for r in rows if r["type"] == "ss_error"] == ["bad"]


async def test_unexpected_error_type_is_contained_per_series(tmp_path, monkeypatch):
    """A defect in one series' payload handling must not silence the whole batch."""
    g = _capture(tmp_path)
    g.tracked = {sid: _entry(started=True) for sid in ("a", "bad")}

    async def fake_post(client, url, query, variables=None, **kw):
        if variables["id"] == "bad":
            raise RuntimeError("something nobody predicted")
        return {"seriesState": {"id": "a", "started": True, "finished": False}}

    monkeypatch.setattr(grid_poller, "post", fake_post)
    await g.poll_due(None)

    rows = _rows(tmp_path)
    assert {r["series_id"] for r in rows if r["type"] == "ss"} == {"a"}
    assert [r["series_id"] for r in rows if r["type"] == "ss_error"] == ["bad"]


async def test_a_crash_outside_the_request_still_spares_siblings(tmp_path, monkeypatch, caplog):
    """Nothing in a per-series task — not even the limiter/query setup — may abort
    the batch, and gather(return_exceptions=True) must never swallow it silently."""
    g = _capture(tmp_path)
    g.tracked = {"a": _entry(started=True), "bad": _entry(started=True)}
    del g.tracked["bad"]["title"]

    async def fake_post(client, url, query, variables=None, **kw):
        return {"seriesState": {"id": "a", "started": True, "finished": False}}

    monkeypatch.setattr(grid_poller, "post", fake_post)
    with caplog.at_level("ERROR", logger=grid_poller.log.name):
        await g.poll_due(None)

    assert [r["series_id"] for r in _rows(tmp_path) if r["type"] == "ss"] == ["a"]
    assert "poll task for series bad crashed" in caplog.text


# ── FIX 2: the due batch is polled concurrently ──────────────────────────────
async def test_due_series_are_polled_concurrently(tmp_path, monkeypatch):
    n = 6
    g = _capture(tmp_path)
    g.tracked = {str(i): _entry(started=True) for i in range(n)}
    g.ss_global = _CountingLimiter(config.GRID_SERIES_STATE_RPM_GLOBAL)
    for sid in g.tracked:
        g._per_series[sid] = _CountingLimiter(config.GRID_SERIES_STATE_RPM_PER_SERIES)

    all_in_flight = asyncio.Event()
    starts: list[str] = []

    async def fake_post(client, url, query, variables=None, **kw):
        starts.append(variables["id"])
        if len(starts) >= n:
            all_in_flight.set()
        await all_in_flight.wait()  # never completes unless all n are in flight at once
        return {"seriesState": {"id": variables["id"], "started": True, "finished": False}}

    monkeypatch.setattr(grid_poller, "post", fake_post)
    await asyncio.wait_for(g.poll_due(None), timeout=5)

    assert sorted(starts) == sorted(g.tracked)
    assert g.polls == n
    assert g.ss_global.acquires == n
    assert all(lim.acquires == 1 for lim in g._per_series.values())


async def test_concurrent_batch_survives_a_failing_sibling(tmp_path, monkeypatch):
    """FIX 1 and FIX 2 compose: a raising task must not cancel the gather."""
    n = 5
    g = _capture(tmp_path)
    g.tracked = {str(i): _entry(started=True) for i in range(n)}
    all_in_flight = asyncio.Event()
    starts: list[str] = []

    async def fake_post(client, url, query, variables=None, **kw):
        starts.append(variables["id"])
        if len(starts) >= n:
            all_in_flight.set()
        await all_in_flight.wait()
        if variables["id"] == "0":
            raise httpx.ReadTimeout("read timeout")
        return {"seriesState": {"id": variables["id"], "started": True, "finished": False}}

    monkeypatch.setattr(grid_poller, "post", fake_post)
    await asyncio.wait_for(g.poll_due(None), timeout=5)

    rows = _rows(tmp_path)
    assert {r["series_id"] for r in rows if r["type"] == "ss"} == {"1", "2", "3", "4"}
    assert [r["series_id"] for r in rows if r["type"] == "ss_error"] == ["0"]


# ── FIX 3: pruned series are tombstoned, not silently resurrected ────────────
async def test_pruned_series_is_not_resurrected_with_fresh_state(tmp_path, monkeypatch):
    g = _capture(tmp_path)
    g.tracked = {"s1": _entry(finished=True, last_hash="deadbeef")}
    g._prune(datetime.now(UTC))
    assert "s1" not in g.tracked

    await _discover(g, monkeypatch, [_node("s1")])
    assert "s1" not in g.tracked
    assert [r["id"] for r in _rows(tmp_path, "disc") if r["type"] == "series"] == ["s1"]


async def test_tombstone_expires_so_a_series_is_never_suppressed_forever(tmp_path, monkeypatch):
    g = _capture(tmp_path)
    g.tracked = {"s1": _entry(finished=True)}
    g._prune(datetime.now(UTC))
    g._recently_dropped["s1"] = time.monotonic() - grid_poller.DROP_TOMBSTONE_S - 1

    await _discover(g, monkeypatch, [_node("s1")])
    assert "s1" in g.tracked
    assert g.tracked["s1"]["last_hash"] == ""


def test_tombstones_are_swept_so_they_cannot_leak_across_days(tmp_path):
    g = _capture(tmp_path)
    g._recently_dropped = {
        "old": time.monotonic() - grid_poller.DROP_TOMBSTONE_S - 1,
        "fresh": time.monotonic(),
    }
    g._prune(datetime.now(UTC))
    assert set(g._recently_dropped) == {"fresh"}


def test_tombstone_window_outlasts_the_discovery_window(tmp_path):
    """A dropped series must stay dropped for as long as discovery can still see it."""
    span = config.DISCOVERY_WINDOW[1] - config.DISCOVERY_WINDOW[0]
    assert grid_poller.DROP_TOMBSTONE_S >= span


async def test_frozen_live_series_trips_stale_alarm_and_stays_pruned(tmp_path, monkeypatch):
    g = _capture(tmp_path)
    frozen = {"id": "z", "started": True, "finished": False}

    async def fake_post(client, url, query, variables=None, **kw):
        return {"seriesState": frozen}

    monkeypatch.setattr(grid_poller, "post", fake_post)
    g.tracked = {"z": _entry(started=True)}
    await g.poll_due(None)  # seeds last_hash
    g.tracked["z"]["next_poll"] = 0.0
    g.tracked["z"]["last_change"] -= STALE_LIVE_S + 1
    await g.poll_due(None)
    assert [r["series_id"] for r in _rows(tmp_path) if r["type"] == "stale_alarm"] == ["z"]

    g.tracked["z"]["last_change"] = time.monotonic() - 6 * 3600 - 1
    g._prune(datetime.now(UTC))
    assert "z" not in g.tracked

    await _discover(g, monkeypatch, [_node("z")])
    assert "z" not in g.tracked  # no fresh 6h clock, no cleared stale_flagged


async def test_finished_series_is_not_polled_again_after_rediscovery(tmp_path, monkeypatch):
    """133 wasted post-finish polls across 44 of 45 finished series (2026-09-06)."""
    g = _capture(tmp_path)
    g.tracked = {"done": _entry(started=True, finished=True)}
    g._prune(datetime.now(UTC))
    await _discover(g, monkeypatch, [_node("done")])
    assert "done" not in g.tracked

    asked: list[str] = []

    async def fake_post(client, url, query, variables=None, **kw):
        asked.append(variables["id"])
        return {"seriesState": {"id": "done", "started": True, "finished": True}}

    monkeypatch.setattr(grid_poller, "post", fake_post)
    await g.poll_due(None)
    assert asked == []
    assert g.polls == 0


# ── FIX 4: circuit breaker for series that never return a payload ────────────
def test_empty_breaker_threshold_clears_the_measured_worst_legitimate_lead():
    """Corpus scan of data/grid/series_state-2026082?..0907: the longest run of
    empty responses later followed by a VALID payload was 274 polls / 284 min
    (sid 3000941, 2026-08-29). The breaker must never fire that early."""
    assert grid_poller.EMPTY_BREAKER_N > 274


async def test_empty_breaker_drops_a_never_valid_series(tmp_path, monkeypatch):
    monkeypatch.setattr(grid_poller, "EMPTY_BREAKER_N", 3)
    g = _capture(tmp_path)
    g.tracked = {"ghost": _entry()}

    async def fake_post(client, url, query, variables=None, **kw):
        return {}  # GRID's observed shape for a series with no state feed

    monkeypatch.setattr(grid_poller, "post", fake_post)
    for _ in range(3):
        if "ghost" not in g.tracked:
            break
        g.tracked["ghost"]["next_poll"] = 0.0
        await g.poll_due(None)

    assert "ghost" not in g.tracked
    assert "ghost" in g._recently_dropped
    brk = [r for r in _rows(tmp_path) if r["type"] == "ss_empty_breaker"]
    assert [r["series_id"] for r in brk] == ["ghost"]
    assert brk[0]["empty_polls"] == 3
    assert len([r for r in _rows(tmp_path) if r["type"] == "ss"]) == 3  # all recorded


async def test_one_valid_payload_immunises_a_series_from_the_breaker(tmp_path, monkeypatch):
    monkeypatch.setattr(grid_poller, "EMPTY_BREAKER_N", 2)
    g = _capture(tmp_path)
    g.tracked = {"real": _entry()}
    payloads = [
        {"seriesState": {"id": "real", "started": False, "finished": False}},
        {},
        {},
        {},
    ]

    async def fake_post(client, url, query, variables=None, **kw):
        return payloads.pop(0)

    monkeypatch.setattr(grid_poller, "post", fake_post)
    for _ in range(4):
        assert "real" in g.tracked
        g.tracked["real"]["next_poll"] = 0.0
        await g.poll_due(None)

    assert g.tracked["real"]["ever_valid"] is True
    assert not [r for r in _rows(tmp_path) if r["type"] == "ss_empty_breaker"]


async def test_transport_errors_do_not_count_toward_the_empty_breaker(tmp_path, monkeypatch):
    """A DNS outage is not evidence that a series has no feed."""
    monkeypatch.setattr(grid_poller, "EMPTY_BREAKER_N", 2)
    g = _capture(tmp_path)
    g.tracked = {"s": _entry()}

    async def fake_post(client, url, query, variables=None, **kw):
        raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")

    monkeypatch.setattr(grid_poller, "post", fake_post)
    for _ in range(3):
        g.tracked["s"]["next_poll"] = 0.0
        await g.poll_due(None)

    assert "s" in g.tracked
    assert g.tracked["s"]["empty_streak"] == 0


async def test_empty_streak_resets_when_a_payload_arrives(tmp_path, monkeypatch):
    g = _capture(tmp_path)
    g.tracked = {"s": _entry(empty_streak=7)}

    async def fake_post(client, url, query, variables=None, **kw):
        return {"seriesState": {"id": "s", "started": False, "finished": False}}

    monkeypatch.setattr(grid_poller, "post", fake_post)
    await g.poll_due(None)
    assert g.tracked["s"]["empty_streak"] == 0
    assert g.tracked["s"]["ever_valid"] is True


async def test_discovery_seeds_breaker_fields(tmp_path, monkeypatch):
    g = _capture(tmp_path)
    await _discover(g, monkeypatch, [_node("new1")])
    entry = g.tracked["new1"]
    assert entry["empty_streak"] == 0
    assert entry["ever_valid"] is False
