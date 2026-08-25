"""GRID capture: Central Data discovery sweep + per-series Series State polling.

Cadences and ceilings per config (OA hard limits). Records verbatim raw
responses; parsing/features happen offline — capture never interprets.
"""

from __future__ import annotations

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


class GridCapture:
    def __init__(self, writer: DayWriter, discovery_writer: DayWriter) -> None:
        self.writer = writer
        self.discovery_writer = discovery_writer
        self.cd_limiter = RateLimiter(config.GRID_CENTRAL_RPM)
        self.ss_global = RateLimiter(config.GRID_SERIES_STATE_RPM_GLOBAL)
        self.tracked: dict[str, dict] = {}
        self._per_series: dict[str, RateLimiter] = {}
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
                self.tracked.pop(sid)
                self._per_series.pop(sid, None)

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
        for sid, st in due:
            await self.ss_global.acquire()
            await self._limiter_for(sid).acquire()
            query = q.SERIES_STATE_CS2 if st["title"] == "cs2" else q.SERIES_STATE_GENERIC
            t0 = time.time_ns()
            try:
                data = await post(client, config.GRID_SERIES_STATE_URL, query, {"id": sid})
            except GraphQLError as exc:
                self.writer.append(
                    {"type": "ss_error", "series_id": sid, "title": st["title"], "error": str(exc)[:400]}
                )
                st["next_poll"] = time.monotonic() + config.SS_IDLE_POLL_S
                continue
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
