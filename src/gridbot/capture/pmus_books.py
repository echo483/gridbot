"""PMUS book recorder — a read-only BBO tape of the books we actually trade.

Timing-chain instrument, part A (docs/superpowers/specs/2026-09-03-timing-chain.md).
Diagnostic, not a strategy: no registry bar, no trading, no keys. Every
`CYCLE_S` it polls the public Polymarket US gateway for the LIVE map's book
and the match book of each live, mapped GRID series and appends change-only
rows (plus one heartbeat per `HEARTBEAT_S` per slug, so "book did not move"
stays distinguishable from "recorder was not running") to
``data/pmus/books-YYYYMMDD.jsonl``. ``scripts/timing_chain.py`` joins that
tape to GRID round ends offline.

Read-only rails: unauthenticated ``PolymarketUS(timeout=4.0)`` exactly like
``gridbot.instruments.mapentry_recorder.BboCache``; no WebSocket (it needs
Ed25519 auth); nothing here imports ``gridbot.executor`` — the mapentry
helpers reused (mappings loader, BBO parser) pull in only the pure
team-name matchers of ``us_mapping``.

Liveness comes from the capture daemon's own ``series_state`` tape (a file
tail, never a GRID call): a series is live while ``started and not
finished``, its live map is ``current_game(state)["sequenceNumber"]``, and a
series is dropped once finished or silent for `STALE_S`.

Run: uv run python -m gridbot.capture.pmus_books [--once]
(``--once`` = one poll cycle then exit; prints the slugs polled and rows written.)
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

from gridbot import config
from gridbot.capture.files import DayWriter
from gridbot.corpus import rows as r
from gridbot.instruments import mapentry
from gridbot.instruments.shadow import Follow

log = logging.getLogger("gridbot.pmus_books")

CYCLE_S = 2.0
MAPPING_RELOAD_S = 60.0
STALE_S = 900.0
MAX_SLUGS_PER_CYCLE = 8  # <= 4 req/s; shares the venue's 20 req/s with mapentry + postquote
HEARTBEAT_S = 30.0
GATEWAY_TIMEOUT_S = 4.0
REQUEST_SPACING_S = 0.1  # spread the cycle's calls out — never a burst
PRIME_TAIL_BYTES = 32 * 1024 * 1024  # startup: seed liveness from the newest ss file's tail
STATS_LOG_S = 60.0


# --- liveness ----------------------------------------------------------------


class LiveTracker:
    """Latest ``seriesState`` per series -> the live ``{series_id: map_number}`` set."""

    def __init__(self, stale_s: float = STALE_S,
                 test_ids: frozenset[str] = config.GRID_TEST_SERIES_IDS) -> None:
        self.stale_s = stale_s
        self.test_ids = test_ids
        self._states: dict[str, tuple[dict, float]] = {}

    def ingest(self, record: dict, seen: float) -> None:
        """Keep the newest state per series; ``seen`` is the clock reading it arrived at."""
        if record.get("type") != "ss":
            return
        sid = str(record.get("series_id") or "")
        if not sid or sid in self.test_ids:
            return
        state = (record.get("data") or {}).get("seriesState") or {}
        self._states[sid] = (state, seen)

    def live_maps(self, now: float) -> dict[str, int]:
        out: dict[str, int] = {}
        for sid, (state, seen) in list(self._states.items()):
            if state.get("finished") or now - seen > self.stale_s:
                del self._states[sid]  # finished or silent: never poll it again
                continue
            if not state.get("started"):
                continue
            game = r.current_game(state)
            if game is None:
                continue
            seq = game.get("sequenceNumber")
            if seq is None:
                continue
            out[sid] = int(seq)
        return out


# --- slug selection ----------------------------------------------------------


def slugs_for(live_maps: dict[str, int], mappings: dict[str, dict]) -> list[dict]:
    """Poll specs for every live, mapped series: the live map's book, then the match book.

    Missing entries (unmapped series, no ``map{n}`` book, empty slug) are skipped.
    """
    out: list[dict] = []
    for sid in sorted(live_maps):
        mapping = mappings.get(sid)
        if not mapping:
            continue
        us = mapping.get("us_markets") or {}
        n = live_maps[sid]
        for key, kind in ((f"map{n}", "map"), ("match", "match")):
            slug = (us.get(key) or {}).get("market_slug")
            if slug:
                out.append({"slug": str(slug), "series_id": sid, "map_number": n, "kind": kind})
    return out


class RoundRobin:
    """Cap the slugs polled per cycle; rotate the window so every slug is reached."""

    def __init__(self, cap: int = MAX_SLUGS_PER_CYCLE) -> None:
        self.cap = cap
        self._cursor = 0

    def pick(self, specs: list[dict]) -> list[dict]:
        if len(specs) <= self.cap:
            self._cursor = 0
            return list(specs)
        start = self._cursor % len(specs)
        picked = [specs[(start + i) % len(specs)] for i in range(self.cap)]
        self._cursor = (start + self.cap) % len(specs)
        return picked


# --- write policy ------------------------------------------------------------


class BookTape:
    """Change-only + heartbeat write policy over a DayWriter-shaped sink (``append``)."""

    def __init__(self, writer, clock: Callable[[], float] = time.monotonic,
                 heartbeat_s: float = HEARTBEAT_S) -> None:
        self.writer = writer
        self.clock = clock
        self.heartbeat_s = heartbeat_s
        self._last: dict[str, tuple[tuple[float | None, float | None], float]] = {}

    def observe(self, spec: dict, bid: float | None, ask: float | None,
                req_ns: int, latency_ms: int) -> bool:
        """Write when (bid, ask) changed, else at most one heartbeat per window. True if written."""
        now = self.clock()
        prev = self._last.get(spec["slug"])
        changed = prev is None or prev[0] != (bid, ask)
        if not changed and now - prev[1] < self.heartbeat_s:
            return False
        self.writer.append({
            "type": "pmus_bbo",
            "slug": spec["slug"],
            "series_id": spec["series_id"],
            "map_number": spec["map_number"],
            "kind": spec["kind"],
            "bid": bid,
            "ask": ask,
            "req_ns": req_ns,
            "latency_ms": latency_ms,
            "heartbeat": not changed,
        })
        self._last[spec["slug"]] = ((bid, ask), now)
        return True

    def error(self, spec: dict, exc: BaseException) -> None:
        """Class name only — a message body may echo request details."""
        self.writer.append({
            "type": "pmus_bbo_error",
            "slug": spec["slug"],
            "series_id": spec["series_id"],
            "kind": spec["kind"],
            "error": type(exc).__name__,
        })


# --- recorder ----------------------------------------------------------------


class Recorder:
    def __init__(self, client=None, writer=None, grid_dir: Path | None = None,
                 pmus_dir: Path | None = None, from_start: bool = False,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 cap: int = MAX_SLUGS_PER_CYCLE, prime: bool = True) -> None:
        self._client = client
        self.pmus_dir = pmus_dir or (config.DATA_DIR / "pmus")
        self.writer = writer or DayWriter(self.pmus_dir, "books")
        self.clock = clock
        self.sleep = sleep
        self.tape = BookTape(self.writer, clock=clock)
        self.tracker = LiveTracker()
        self.rr = RoundRobin(cap)
        self.follow = Follow(grid_dir or (config.DATA_DIR / "grid"), "series_state",
                             from_start=from_start)
        self.mappings: dict[str, dict] = {}
        self._mappings_loaded: float | None = None
        self._last_candidates: set[str] | None = None
        self._stats = {"polls": 0, "written": 0, "errors": 0}
        self._stats_at = clock()
        if prime and not from_start:
            n = self._prime_from_tail(clock())
            log.info("primed liveness from %d tail row(s) of %s", n,
                     self.follow.path.name if self.follow.path else "<no ss file>")

    def _get_client(self):
        if self._client is None:
            from polymarket_us import PolymarketUS

            self._client = PolymarketUS(timeout=GATEWAY_TIMEOUT_S)  # public endpoints only — no keys
        return self._client

    def _prime_from_tail(self, now: float) -> int:
        """Seed the live set from the tail of the newest series_state day file.

        ``Follow(from_start=False)`` opens at EOF, so a fresh process would know
        no live series until the next 12s poll lands — and ``--once`` would poll
        nothing. Rows are aged by their own ``ts_ns`` so the stale rule holds.
        """
        path = self.follow.path
        if path is None:
            return 0
        size = path.stat().st_size
        start = max(0, size - PRIME_TAIL_BYTES)
        with open(path, "rb") as fh:
            fh.seek(start)
            blob = fh.read(size - start)
        lines = blob.split(b"\n")
        if start > 0:
            lines = lines[1:]  # the first fragment is a torn line
        wall_ns = time.time_ns()
        n = 0
        for raw in lines:
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ts = rec.get("ts_ns") if isinstance(rec.get("ts_ns"), int) else rec.get("req_ns")
            age_s = max(0.0, (wall_ns - ts) / 1e9) if isinstance(ts, int) else 0.0
            self.tracker.ingest(rec, now - age_s)
            n += 1
        return n

    def _reload_mappings(self, now: float) -> None:
        if self._mappings_loaded is not None and now - self._mappings_loaded < MAPPING_RELOAD_S:
            return
        self.mappings = mapentry.load_latest_mappings(self.pmus_dir)
        self._mappings_loaded = now

    def fetch(self, slug: str) -> tuple[float | None, float | None, int, int]:
        """(bid, ask, req_ns, latency_ms) — req_ns is taken right before the call."""
        client = self._get_client()
        req_ns = time.time_ns()
        t0 = time.perf_counter()
        payload = client.markets.bbo(slug)
        latency_ms = round((time.perf_counter() - t0) * 1000)
        bid, ask = mapentry.parse_bbo(payload)
        return bid, ask, req_ns, latency_ms

    def cycle(self, now: float | None = None) -> dict:
        """One poll cycle: ingest ss rows, derive the poll set, fetch, write. Never raises."""
        now = self.clock() if now is None else now
        self._reload_mappings(now)
        for _ in range(50):  # bounded drain (lines() hands back <= 1000 rows a call)
            batch = self.follow.lines()
            if not batch:
                break
            for record in batch:
                self.tracker.ingest(record, now)
        live = self.tracker.live_maps(now)
        specs = slugs_for(live, self.mappings)
        candidates = {s["slug"] for s in specs}
        if candidates != self._last_candidates:
            log.info("poll set: %d live series, %d slug(s): %s",
                     len(live), len(specs), sorted(candidates))
            self._last_candidates = candidates
        picked = self.rr.pick(specs)
        written = errors = 0
        for i, spec in enumerate(picked):
            if i:
                self.sleep(REQUEST_SPACING_S)
            try:
                bid, ask, req_ns, latency_ms = self.fetch(spec["slug"])
            except Exception as exc:  # noqa: BLE001 — a book fetch must never kill the tape
                log.warning("bbo fetch failed for %s: %s", spec["slug"], type(exc).__name__)
                self.tape.error(spec, exc)
                errors += 1
                continue
            if self.tape.observe(spec, bid, ask, req_ns, latency_ms):
                written += 1
        self._stats["polls"] += len(picked)
        self._stats["written"] += written
        self._stats["errors"] += errors
        if now - self._stats_at >= STATS_LOG_S:
            log.info("last %.0fs: %d poll(s), %d row(s) written, %d error(s); live=%s",
                     now - self._stats_at, self._stats["polls"], self._stats["written"],
                     self._stats["errors"], live)
            self._stats = {"polls": 0, "written": 0, "errors": 0}
            self._stats_at = now
        return {
            "live": live,
            "candidates": len(specs),
            "polled": [s["slug"] for s in picked],
            "written": written,
            "errors": errors,
        }

    def close(self) -> None:
        self.follow.close()
        if hasattr(self.writer, "close"):
            self.writer.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="one poll cycle then exit (smoke test)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # httpx logs every request at INFO — at <= 4 req/s that is ~300k journal
    # lines a day for nothing the 60s stats line does not already say.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    rec = Recorder()
    log.info("pmus book recorder up (read-only public BBO; cycle %.1fs, cap %d slugs/cycle)",
             CYCLE_S, MAX_SLUGS_PER_CYCLE)
    try:
        while True:
            t0 = time.monotonic()
            try:
                summary = rec.cycle(t0)
            except Exception:  # the tape outlives any one bad cycle
                log.exception("cycle failed; continuing")
                summary = None
            if args.once:
                if summary is None:
                    print("once: cycle failed (see log)")
                else:
                    print(f"once: live series -> map: {summary['live']}")
                    print(f"once: polled {len(summary['polled'])} of {summary['candidates']} "
                          f"slug(s): {summary['polled']}")
                    print(f"once: rows written {summary['written']}, errors {summary['errors']}")
                return
            time.sleep(max(0.0, CYCLE_S - (time.monotonic() - t0)))
    finally:
        rec.close()


if __name__ == "__main__":
    main()
