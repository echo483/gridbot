"""GRID capture: Central Data discovery sweep + per-series Series State polling.

Cadences and ceilings per config (OA hard limits). Records verbatim raw
responses; parsing/features happen offline — capture never interprets.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta

import httpx

from gridbot import config
from gridbot.capture import grid_queries as q
from gridbot.capture.files import DayWriter
from gridbot.graphql import GraphQLError, post
from gridbot.ratelimit import RateLimiter

log = logging.getLogger(__name__)

STALE_LIVE_S = 180
# A series that has NEVER returned a payload is retired after this many consecutive
# empty (null seriesState) responses — ~6.7h at the idle cadence. Corpus scan of
# 2026-08-24..09-07: the longest empty run later followed by a VALID payload was
# 274 polls / 284 min (sid 3000941, 2026-08-29), so this keeps ~1.5x margin over
# observed truth. One valid payload immunises a series for good: the breaker can
# never be late to a series that eventually goes live.
EMPTY_BREAKER_N = 400
# A dropped series stays dropped this long. Without it the next discovery sweep
# re-adds it with last_hash="", which reads as "changed" on the following poll and
# so defeats both the stale alarm and the frozen prune. Outlasts the 5h discovery
# window, and expires — a series is never suppressed permanently.
DROP_TOMBSTONE_S = 6 * 3600


class GridCapture:
    def __init__(self, writer: DayWriter, discovery_writer: DayWriter) -> None:
        self.writer = writer
        self.discovery_writer = discovery_writer
        self.cd_limiter = RateLimiter(config.GRID_CENTRAL_RPM)
        self.ss_global = RateLimiter(config.GRID_SERIES_STATE_RPM_GLOBAL)
        self.tracked: dict[str, dict] = {}
        self._per_series: dict[str, RateLimiter] = {}
        self._recently_dropped: dict[str, float] = {}
        self._discover_query = q.DISCOVER_RICH
        self.polls = 0

    # ── discovery ────────────────────────────────────────────────────────────
    async def discover_once(self, client: httpx.AsyncClient) -> None:
        now = datetime.now(UTC)
        gte = (now + timedelta(seconds=config.DISCOVERY_WINDOW[0])).isoformat(timespec="seconds")
        lte = (now + timedelta(seconds=config.DISCOVERY_WINDOW[1])).isoformat(timespec="seconds")
        for title_key, title_id in config.TITLE_IDS.items():
            after = None
            while True:
                await self.cd_limiter.acquire()
                try:
                    data = await post(
                        client,
                        config.GRID_CENTRAL_DATA_URL,
                        self._discover_query,
                        {"titleId": title_id, "gte": gte, "lte": lte, "after": after},
                    )
                except GraphQLError as exc:
                    if self._discover_query is q.DISCOVER_RICH and "FieldUndefined" in str(exc):
                        log.warning("rich discovery rejected, falling back to minimal: %s", exc)
                        self._discover_query = q.DISCOVER_MINIMAL
                        continue
                    log.error("discovery failed for %s: %s", title_key, exc)
                    self.discovery_writer.append(
                        {"type": "discovery_error", "title": title_key, "error": str(exc)[:400]}
                    )
                    break
                conn = data.get("allSeries") or {}
                for edge in conn.get("edges") or []:
                    node = edge.get("node") or {}
                    sid = str(node.get("id"))
                    self.discovery_writer.append({"type": "series", "title": title_key, **node})
                    if sid in config.GRID_TEST_SERIES_IDS or (
                        (node.get("tournament") or {}).get("name") == "GRID-TEST"
                    ):
                        continue  # looping test fixture: recorded, never polled
                    if self._tombstoned(sid):
                        continue  # just dropped: recorded, never resurrected with fresh state
                    entry = self.tracked.setdefault(
                        sid,
                        {
                            "title": title_key,
                            "started": False,
                            "finished": False,
                            "next_poll": 0.0,
                            "last_hash": "",
                            "last_change": time.monotonic(),
                            "stale_flagged": False,
                            "empty_streak": 0,
                            "ever_valid": False,
                        },
                    )
                    entry["scheduled"] = node.get("startTimeScheduled")
                page = conn.get("pageInfo") or {}
                if not page.get("hasNextPage"):
                    break
                after = page.get("endCursor")
        self._prune(now)
        log.info("discovery: tracking %d series", len(self.tracked))

    def _prune(self, now: datetime) -> None:
        """Drop dead entries so the poll budget can't leak over days.

        - finished: final state already recorded
        - never started and scheduled >12h ago: canceled/rescheduled
        - started but frozen >6h: abandoned upstream
        """
        for sid, st in list(self.tracked.items()):
            drop = st["finished"] or sid in config.GRID_TEST_SERIES_IDS
            if not drop and not st["started"]:
                sched = st.get("scheduled")
                try:
                    sched_dt = datetime.fromisoformat(str(sched)) if sched else None
                    drop = bool(sched_dt and now - sched_dt > timedelta(hours=12))
                except (ValueError, TypeError):
                    drop = False
            if not drop and st["started"]:
                drop = time.monotonic() - st["last_change"] > 6 * 3600
            if drop:
                self._drop(sid)
        cutoff = time.monotonic() - DROP_TOMBSTONE_S
        for sid, dropped_at in list(self._recently_dropped.items()):
            if dropped_at < cutoff:
                self._recently_dropped.pop(sid)

    def _drop(self, sid: str) -> None:
        """Retire a series and tombstone it, so rediscovery can't hand it fresh state."""
        self.tracked.pop(sid, None)
        self._per_series.pop(sid, None)
        self._recently_dropped[sid] = time.monotonic()

    def _tombstoned(self, sid: str) -> bool:
        dropped_at = self._recently_dropped.get(sid)
        if dropped_at is None:
            return False
        if time.monotonic() - dropped_at > DROP_TOMBSTONE_S:
            self._recently_dropped.pop(sid, None)
            return False
        return True

    # ── series state polling ─────────────────────────────────────────────────
    def _limiter_for(self, sid: str) -> RateLimiter:
        if sid not in self._per_series:
            self._per_series[sid] = RateLimiter(config.GRID_SERIES_STATE_RPM_PER_SERIES)
        return self._per_series[sid]

    async def poll_due(self, client: httpx.AsyncClient) -> None:
        now = time.monotonic()
        due = [
            (sid, st)
            for sid, st in self.tracked.items()
            if not st["finished"] and st["next_poll"] <= now
        ]
        # Concurrent by series: measured batches run to 20, so a serial loop made the
        # last series wait out every sibling's HTTP round trip. The limiters stay the
        # true ceiling — each task still acquires both before it issues a request.
        results = await asyncio.gather(
            *(self._poll_one(client, sid, st) for sid, st in due), return_exceptions=True
        )
        for (sid, _st), result in zip(due, results, strict=True):
            if isinstance(result, BaseException):  # never silent: siblings survived, say so
                log.error("poll task for series %s crashed: %r", sid, result)

    async def _poll_one(self, client: httpx.AsyncClient, sid: str, st: dict) -> None:
        await self.ss_global.acquire()
        await self._limiter_for(sid).acquire()
        query = q.SERIES_STATE_CS2 if st["title"] == "cs2" else q.SERIES_STATE_GENERIC
        t0 = time.time_ns()
        try:
            data = await post(client, config.GRID_SERIES_STATE_URL, query, {"id": sid})
        except Exception as exc:
            # Any failure is this series' alone. Confirmed live 2026-09-06: httpx
            # DNS errors escaped the old GraphQLError-only catch and aborted every
            # other due series in the batch (3 gaps >200s in one day). CancelledError
            # is a BaseException, so a real shutdown still propagates.
            self.writer.append(
                {
                    "type": "ss_error",
                    "series_id": sid,
                    "title": st["title"],
                    "error": f"{type(exc).__name__}: {exc}"[:400],
                }
            )
            st["next_poll"] = time.monotonic() + config.SS_IDLE_POLL_S
            return
        self.polls += 1
        state = (data or {}).get("seriesState") or {}
        self.writer.append(
            {
                "type": "ss",
                "series_id": sid,
                "title": st["title"],
                "req_ns": t0,
                "latency_ms": (time.time_ns() - t0) // 1_000_000,
                "data": data,
            }
        )
        if state:
            st["ever_valid"] = True
            st["empty_streak"] = 0
        elif not st["ever_valid"]:
            # Never any feed for this id. Neither prune rule can reach it (it never
            # sets started), so it burns the poll budget until discovery ages out.
            # Direct indexing, like every other field access on `st`: both keys are
            # seeded by the single `setdefault` in discover_once, so a .get() default
            # here would only mask a genuine missing-key bug instead of raising.
            st["empty_streak"] += 1
            if st["empty_streak"] >= EMPTY_BREAKER_N:
                log.warning(
                    "series %s (%s): %d consecutive empty polls, no feed — retiring",
                    sid,
                    st["title"],
                    st["empty_streak"],
                )
                self.writer.append(
                    {
                        "type": "ss_empty_breaker",
                        "series_id": sid,
                        "title": st["title"],
                        "empty_polls": st["empty_streak"],
                    }
                )
                self._drop(sid)
                return
        st["started"] = bool(state.get("started"))
        st["finished"] = bool(state.get("finished"))
        digest = hashlib.sha1(
            json.dumps(state, sort_keys=True, default=str).encode()
        ).hexdigest()
        if digest != st["last_hash"]:
            st["last_hash"] = digest
            st["last_change"] = time.monotonic()
            st["stale_flagged"] = False
        live = st["started"] and not st["finished"]
        if live and time.monotonic() - st["last_change"] > STALE_LIVE_S and not st["stale_flagged"]:
            st["stale_flagged"] = True
            log.warning("STALE live series %s (%s): unchanged %ds", sid, st["title"], STALE_LIVE_S)
            self.writer.append({"type": "stale_alarm", "series_id": sid})
        st["next_poll"] = time.monotonic() + (
            config.SS_LIVE_POLL_S if live else config.SS_IDLE_POLL_S
        )
        if st["finished"]:
            log.info("series %s finished; final state recorded", sid)

    def live_count(self) -> int:
        return sum(1 for s in self.tracked.values() if s["started"] and not s["finished"])
