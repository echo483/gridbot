"""PMUS trades tape — a READ-ONLY recorder of the actual prints on books we trade.

Prerequisite instrument for every future resting-entry strategy. Registry
``H-MAPENTRY1-NOTE10`` made it a binding rule: **a maker strategy cannot be
validated on a best-bid/best-ask tape.** A BBO tape cannot separate a sale
through our resting level from a cancel of that level, and it cannot observe
queue position at all — which is why the EXEC1 fill-detection recall topped out
at 0.46 against a 0.60 bar. Only actual prints settle "did a trade happen at my
price, and how much size went through before me". This records those prints.

What it does: subscribes to the Polymarket US markets WebSocket ``trade`` stream
for the live CS2 match + map-winner slugs (the SAME live set ``pmus_books.py``
polls — ``LiveTracker`` and the ``slugs_for`` mapping are imported from it, not
reimplemented) and appends every frame VERBATIM, plus our own receive
``ts_ns``, to ``data/pmus/trades-YYYYMMDD.jsonl``. Capture never interprets
(``grid_poller`` docstring): the venue payload is stored untouched; only the
market slug is lifted out for indexing.

READ-ONLY RAILS — the reason this file is paranoid. Unlike ``pmus_books``, this
recorder CANNOT run unauthenticated: ``BaseWebSocket.connect`` calls
``create_auth_headers(key_id, secret_key, "GET", path)`` unconditionally, so the
process holds the same Ed25519 key pair that can place real orders. Isolation is
therefore structural and test-enforced, not conventional:

1. No order surface. Every websocket object is screened at connect time against
   ``ORDER_SURFACE_RE`` (``order|place|cancel|submit|buy|sell``) and a hit is
   FATAL — never retried, never backed off. If the vendored SDK ever grows an
   order method on ``MarketsWebSocket``, this process refuses to start.
2. No order-capable import. Nothing here imports ``gridbot.executor`` (the wire
   layer ``executor.pmus`` is the only module permitted to touch trading keys)
   nor ``polymarket_us.client`` / ``async_client``. The SDK import is LAZY, inside
   ``build_markets_ws``, so merely importing this module loads no venue SDK at
   all. Two transitive leaks are known, measured and allowed by name in
   ``tests/test_pmus_trades.py``: ``gridbot.executor.us_mapping`` (a pure
   name-matcher, reached through ``instruments.mapentry``, which is the slug
   mapping we are required to reuse), and — only once ``build_markets_ws`` runs —
   ``polymarket_us.client``, which the SDK's own ``__init__`` imports before any
   submodule of it can load. We never bind a name to it, never construct it, and
   guardrail 1 screens what we do hold.
3. Secrets never surface. Credentials are read only through
   ``gridbot.config.require`` (keyring, never ``os.environ`` directly, never a
   file), handed straight to the SDK constructor, and never stored on this
   object. ``install_log_redaction`` is armed before the first read and every
   free-text field written to the tape passes through ``redact_text``.
4. Read-only intent is on the tape. Every row carries ``type`` and ``ts_ns``;
   each session opens with a ``session_start`` status row stamped
   ``mode="read_only"``.

Liveness note: unlike ``pmus_books``, this recorder does NOT prime from the tail
of the series_state day file. It tails from EOF and learns the live set from the
next ``ss`` rows, which land on a 12s cadence — its smoke window is tens of
seconds, not one 2s poll, so a bounded (up to 300MB) startup read would buy ~12s
of coverage at the cost of a blocked event loop. A restart is blind for one SS
cadence; trades are sparse enough that this is the cheaper trade.

Run: uv run python -m gridbot.capture.pmus_trades [--once [--seconds N]]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from gridbot import config
from gridbot.capture.files import DayWriter
from gridbot.capture.pmus_books import LiveTracker, live_map_number, slugs_for
from gridbot.instruments import mapentry
from gridbot.instruments.shadow import Follow
from gridbot.redaction import install_log_redaction, redact_text

# Re-exported: the liveness primitives this recorder reuses from pmus_books
# rather than reimplementing (live_map_number is used inside LiveTracker).
__all__ = [
    "LiveSlugs",
    "LiveTracker",
    "OrderSurfaceError",
    "TradeTape",
    "TradesRecorder",
    "assert_no_order_surface",
    "backoff_delay",
    "build_markets_ws",
    "live_map_number",
    "main",
    "order_surface",
    "slugs_for",
]

log = logging.getLogger("gridbot.pmus_trades")

POLL_S = 2.0                 # how often we re-derive the live slug set
MAPPING_RELOAD_S = 60.0
HEARTBEAT_ROW_S = 30.0       # "recorder was up, book was quiet" != "recorder was down"
# No frame at all -> recycle. NOT 90s: measured 2026-09-07 over 687 real prints,
# the venue sends NO heartbeats on the trade subscription (every stats line
# reported frames == trades), so this bounds how long a LIVE market can go
# without a PRINT, not how long a connection can be silent. Observed gaps
# between consecutive prints on an active slate: p50 0.0s, p90 17.4s, p99 54.9s,
# max 132.1s. At 90s it fired twice in fifty minutes on healthy sockets, and each
# recycle drops the subscription — losing prints from the moat to defend against
# a reader death that has never been observed. On a market genuinely quiet for
# 15 minutes a recycle is nearly free: there are no prints to miss.
SILENCE_TIMEOUT_S = 900.0
BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 60.0
STATS_LOG_S = 60.0
SMOKE_SECONDS = 45.0
MAX_DETAIL_CHARS = 500

KEY_ID_SECRET = "POLYMARKET_US_KEY_ID"  # pragma: allowlist secret (keyring ENTRY NAME)
SECRET_KEY_SECRET = "POLYMARKET_US_SECRET_KEY"  # pragma: allowlist secret (keyring ENTRY NAME)

# Message keys we know how to classify (websocket/markets.py `_handle_message`).
# Anything else is written verbatim as `pmus_trades_unknown` — a schema-drift
# canary, so a renamed `trade` field never silently empties the tape.
KNOWN_KEYS = frozenset({"heartbeat", "error", "trade"})
# Protocol ACKs: recorded, but not schema drift. See TradeTape.classify.
ACK_KEYS = frozenset({"unsubscribed", "subscribed"})

# Guardrail 1's screen. Nothing on a read-only market socket may match this.
ORDER_SURFACE_RE = re.compile(r"order|place|cancel|submit|buy|sell", re.IGNORECASE)


class OrderSurfaceError(RuntimeError):
    """The websocket object exposes something that could place or cancel an order."""


# --- guardrail 1: no order surface -------------------------------------------


def order_surface(obj: object) -> list[str]:
    """Attribute names on ``obj`` that look like a trading capability."""
    return sorted(n for n in dir(obj) if ORDER_SURFACE_RE.search(n))


def assert_no_order_surface(obj: object) -> None:
    """Fail fast and loudly: this process must be incapable of placing an order."""
    hits = order_surface(obj)
    if hits:
        raise OrderSurfaceError(
            f"{type(obj).__name__ if not isinstance(obj, type) else obj.__name__} exposes "
            f"order-capable attribute(s) {hits}; the trades recorder holds live trading "
            f"credentials and refuses to run against a socket that can trade"
        )


# --- credentials --------------------------------------------------------------


def build_markets_ws(cls: type | None = None):
    """Build the authenticated markets socket. Keys go straight in and are never kept.

    The SDK import is deliberately lazy and points at the leaf module: importing
    ``gridbot.capture.pmus_trades`` must load no venue SDK at all (guardrail 2).
    ``MarketsWebSocket`` sets ``path="/v1/ws/markets"`` itself — passing it here
    would be a duplicate keyword argument.
    """
    if cls is None:
        from polymarket_us.websocket.markets import MarketsWebSocket

        cls = MarketsWebSocket
    return cls(
        key_id=config.require(KEY_ID_SECRET),
        secret_key=config.require(SECRET_KEY_SECRET),
    )


# --- tape ---------------------------------------------------------------------


def slug_of(message: dict) -> str | None:
    """The market slug of a trade frame (indexing only — the payload stays verbatim)."""
    trade = message.get("trade")
    return trade.get("marketSlug") if isinstance(trade, dict) else None


def _detail(text: str) -> str:
    """Redact registered secrets and bound the length of any free text on the tape."""
    return redact_text(str(text))[:MAX_DETAIL_CHARS]


class TradeTape:
    """Row-writing policy over a DayWriter-shaped sink (``append``).

    Every row: a ``type`` and ``ts_ns`` = the moment WE received the frame
    (guardrail 4). Trade frames are stored whole and uninterpreted.
    """

    def __init__(self, writer, clock: Callable[[], float] = time.monotonic,
                 heartbeat_s: float = HEARTBEAT_ROW_S) -> None:
        self.writer = writer
        self.clock = clock
        self.heartbeat_s = heartbeat_s
        self._last_beat: float | None = None

    def _row(self, kind: str, **fields) -> None:
        self.writer.append({"type": kind, "ts_ns": time.time_ns(), **fields})

    def trade(self, message: dict) -> None:
        self._row("pmus_trade", slug=slug_of(message), payload=message)

    def unknown(self, message: dict) -> None:
        self._row("pmus_trades_unknown", payload=message)

    def ack(self, message: dict) -> None:
        self._row("pmus_trades_ack", payload=message)

    def classify(self, message: dict) -> None:
        """Record a frame the specific-event handlers do not claim.

        Protocol ACKs are recorded as `pmus_trades_ack`, NOT as schema drift.
        Observed live 2026-09-07: an unsubscribe is ACKed with
        ``{"requestId": ..., "unsubscribed": true}``, and the recorder
        resubscribes every time the live slug set changes — so labelling that
        "unknown" would fire the drift canary on routine churn. A canary that
        cries on benign traffic stops being read, which is exactly how the
        venue freeze went unnoticed. Anything genuinely unrecognised still
        lands as `pmus_trades_unknown`, verbatim, so a renamed `trade` field
        can never silently empty the tape.
        """
        if ACK_KEYS & message.keys():
            self.ack(message)
        else:
            self.unknown(message)

    def error(self, exc: BaseException) -> None:
        self._row("pmus_trades_error", error=type(exc).__name__, detail=_detail(exc))

    def status(self, event: str, **fields) -> None:
        self._row("pmus_trades_status", event=event, mode="read_only", **fields)

    def heartbeat(self, slugs: list[str]) -> bool:
        """At most one liveness row per window. True if written."""
        now = self.clock()
        if self._last_beat is not None and now - self._last_beat < self.heartbeat_s:
            return False
        self._last_beat = now
        self._row("pmus_trades_heartbeat", slugs=slugs)
        return True


# --- liveness -----------------------------------------------------------------


class LiveSlugs:
    """Live CS2 match + map-winner slugs, from the same source pmus_books polls.

    Composition only: ``Follow`` over the capture daemon's own ``series_state``
    tape (a file tail, never a GRID call), ``LiveTracker`` for the live set,
    ``mapentry.load_latest_mappings`` + ``slugs_for`` for the GRID -> PMUS join.
    """

    def __init__(self, grid_dir: Path, pmus_dir: Path, from_start: bool = False,
                 mapping_reload_s: float = MAPPING_RELOAD_S) -> None:
        self.pmus_dir = pmus_dir
        self.mapping_reload_s = mapping_reload_s
        self.tracker = LiveTracker()
        self.follow = Follow(grid_dir, "series_state", from_start=from_start)
        self.mappings: dict[str, dict] = {}
        self._mappings_loaded: float | None = None

    def _reload_mappings(self, now: float) -> None:
        if (self._mappings_loaded is not None
                and now - self._mappings_loaded < self.mapping_reload_s):
            return
        self.mappings = mapentry.load_latest_mappings(self.pmus_dir)
        self._mappings_loaded = now

    def specs(self, now: float) -> list[dict]:
        self._reload_mappings(now)
        for _ in range(50):  # bounded drain (lines() hands back <= 1000 rows a call)
            batch = self.follow.lines()
            if not batch:
                break
            for record in batch:
                self.tracker.ingest(record, now)
        return slugs_for(self.tracker.live_maps(now), self.mappings)

    def slugs(self, now: float) -> list[str]:
        return sorted({s["slug"] for s in self.specs(now)})

    def close(self) -> None:
        self.follow.close()


# --- reconnect ----------------------------------------------------------------


def backoff_delay(attempt: int) -> float:
    """Bounded exponential backoff. Never zero — a dead venue must not busy-loop."""
    return min(BACKOFF_MIN_S * (2 ** max(0, attempt)), BACKOFF_MAX_S)


# --- recorder -----------------------------------------------------------------


class TradesRecorder:
    def __init__(self, ws_factory: Callable[[], object] | None = None, writer=None,
                 grid_dir: Path | None = None, pmus_dir: Path | None = None,
                 from_start: bool = False,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 poll_s: float = POLL_S, heartbeat_s: float = HEARTBEAT_ROW_S,
                 silence_timeout_s: float = SILENCE_TIMEOUT_S,
                 mapping_reload_s: float = MAPPING_RELOAD_S) -> None:
        self.ws_factory = ws_factory or build_markets_ws
        self.pmus_dir = pmus_dir or (config.DATA_DIR / "pmus")
        self.writer = writer or DayWriter(self.pmus_dir, "trades")
        self.clock = clock
        self.sleep = sleep
        self.poll_s = poll_s
        self.silence_timeout_s = silence_timeout_s
        self.tape = TradeTape(self.writer, clock=clock, heartbeat_s=heartbeat_s)
        self.live = LiveSlugs(grid_dir or (config.DATA_DIR / "grid"), self.pmus_dir,
                              from_start=from_start, mapping_reload_s=mapping_reload_s)
        self.subscribed: list[str] = []
        self._request_seq = 0
        self._request_id: str | None = None
        self._last_frame: float = clock()
        self._closed = False
        self.stats = {"trades": 0, "frames": 0, "errors": 0}
        self._stats_at = clock()

    # -- credentials ----------------------------------------------------------

    def assert_credentials(self) -> None:
        """Read both secrets through config.require so a missing one fails NOW.

        The values are deliberately not returned or stored: this only proves the
        keyring can answer before we start a reconnect loop. Raises MissingSecret.
        """
        config.require(KEY_ID_SECRET)
        config.require(SECRET_KEY_SECRET)

    # -- event handlers -------------------------------------------------------

    def _bind(self, ws) -> None:
        ws.on("message", self._on_message)
        ws.on("trade", self._on_trade)
        ws.on("error", self._on_error)
        ws.on("heartbeat", self._on_heartbeat)
        ws.on("close", self._on_close)

    def _on_message(self, message) -> None:
        """Fires for EVERY parsed frame, before the specific event (base.py)."""
        self._last_frame = self.clock()
        self.stats["frames"] += 1
        if isinstance(message, dict) and not (KNOWN_KEYS & message.keys()):
            self.tape.classify(message)

    def _on_trade(self, message) -> None:
        self.stats["trades"] += 1
        self.tape.trade(message)

    def _on_error(self, exc) -> None:
        self._last_frame = self.clock()
        self.stats["errors"] += 1
        log.warning("venue error frame: %s", type(exc).__name__)
        self.tape.error(exc if isinstance(exc, BaseException) else RuntimeError(str(exc)))

    def _on_heartbeat(self) -> None:
        self._last_frame = self.clock()
        self.tape.heartbeat(list(self.subscribed))

    def _on_close(self) -> None:
        self._closed = True
        self.tape.status("disconnected")

    # -- subscription ---------------------------------------------------------

    async def _resubscribe(self, ws, slugs: list[str]) -> None:
        """One active subscription at a time. NEVER send an empty slug list.

        ``BaseWebSocket.subscribe`` does ``if market_slugs:`` — an empty list omits
        ``marketSlugs`` from the frame entirely, which the venue may read as "every
        market". A firehose of unrelated books is both a rate risk and a corpus
        pollutant, so an empty live set means: no subscription at all.
        """
        if self._request_id is not None:
            await ws.unsubscribe(self._request_id)
            self.tape.status("unsubscribed", request_id=self._request_id,
                             slugs=list(self.subscribed))
            self._request_id = None
        self.subscribed = list(slugs)
        if not slugs:
            log.info("no live mapped slugs; subscribed to nothing")
            return
        self._request_seq += 1
        self._request_id = f"trades-{self._request_seq}"
        # A real subscription earns a fresh silence window. Without this the frame
        # clock, stale from an idle overnight stretch, would trip the watchdog on
        # the first loop after subscribing — recycling the socket at exactly the
        # moment real trades begin arriving. An EMPTY resubscribe deliberately
        # does not reset it, so an idle loop can never mask a dead reader.
        self._last_frame = self.clock()
        await ws.subscribe_trades(self._request_id, list(slugs))
        log.info("subscribed %d slug(s) as %s: %s", len(slugs), self._request_id, slugs)
        self.tape.status("subscribed", request_id=self._request_id, slugs=list(slugs))

    # -- session --------------------------------------------------------------

    async def _session(self, ws, deadline: float | None) -> None:
        """Poll the live set and pump the socket until it drops or the deadline hits."""
        while True:
            now = self.clock()
            if deadline is not None and now >= deadline:
                return
            if self._closed or not ws.is_connected:
                return
            if self.subscribed and now - self._last_frame > self.silence_timeout_s:
                # The SDK's reader task can die on a non-ConnectionClosed exception
                # without clearing self._ws, so is_connected still reports OPEN.
                #
                # Guarded by `self.subscribed`: silence is only evidence of a dead
                # reader when we ASKED for frames. Observed live on the first night
                # — with no CS2 series in the slate the recorder subscribed to
                # nothing, so it recycled every 90s all night, ~40 authenticated
                # reconnects an hour for no reason. An empty subscription is a
                # quiet slate, not a fault.
                log.warning("no frame for %.0fs; recycling the connection",
                            now - self._last_frame)
                self.tape.status("silent", quiet_s=round(now - self._last_frame, 1))
                return
            slugs = self.live.slugs(now)
            if slugs != self.subscribed:
                await self._resubscribe(ws, slugs)
            self._log_stats(now)
            await self.sleep(self.poll_s)

    def _log_stats(self, now: float) -> None:
        if now - self._stats_at < STATS_LOG_S:
            return
        # `reconnects` is deliberately NOT in this window dict. It used to be, was
        # never incremented, and so this line reported "0 reconnect(s)" while the
        # socket recycled every 90s — a monitoring line that lied in a recorder
        # whose whole job is evidence. Reconnects are counted once, cumulatively,
        # in run()'s `totals`, which is a different question from this window.
        log.info("last %.0fs: %d trade(s), %d frame(s), %d error(s); subscribed=%s",
                 now - self._stats_at, self.stats["trades"], self.stats["frames"],
                 self.stats["errors"], self.subscribed)
        self.stats = {"trades": 0, "frames": 0, "errors": 0}
        self._stats_at = now

    async def _shutdown(self, ws) -> None:
        if ws is None:
            return
        try:
            await ws.close()
        except Exception as exc:  # a failed close must not stop the reconnect loop
            log.warning("close failed: %s", type(exc).__name__)

    # -- run ------------------------------------------------------------------

    async def run(self, seconds: float | None = None) -> dict:
        """Connect / subscribe / record until ``seconds`` elapse (None = forever).

        Fatal-by-design: OrderSurfaceError (guardrail 1) and MissingSecret are
        re-raised, never backed off — a structurally unsafe or unconfigured
        process must die, not retry.
        """
        deadline = None if seconds is None else self.clock() + seconds
        attempt = 0
        totals = {"trades": 0, "frames": 0, "errors": 0, "reconnects": 0}
        self.tape.status("session_start", poll_s=self.poll_s)
        try:
            while deadline is None or self.clock() < deadline:
                ws = None
                try:
                    ws = self.ws_factory()
                    assert_no_order_surface(ws)
                    self._closed = False
                    self._last_frame = self.clock()
                    self._bind(ws)
                    await ws.connect()
                    self.tape.status("connected")
                    log.info("connected (read-only market data; no order surface)")
                    attempt = 0
                    await self._session(ws, deadline)
                except OrderSurfaceError as exc:
                    log.error("REFUSING TO RUN: %s", exc)
                    self.tape.status("order_surface_refused", detail=_detail(exc))
                    raise
                except config.MissingSecret:
                    raise
                except Exception as exc:
                    log.warning("session failed: %s", type(exc).__name__)
                    self.tape.status("connect_failed", error=type(exc).__name__,
                                     detail=_detail(exc))
                finally:
                    await self._shutdown(ws)
                    # Drain the rolling window into the cumulative counters. Only
                    # the keys the window actually tracks — `reconnects` lives on
                    # `totals` alone, counted below, because a reconnect is a
                    # property of the run, not of a 60s observation window.
                    for k in self.stats:
                        totals[k] += self.stats[k]
                        self.stats[k] = 0
                    self._request_id = None
                    self.subscribed = []
                if deadline is not None and self.clock() >= deadline:
                    break
                totals["reconnects"] += 1
                await self.sleep(backoff_delay(attempt))
                attempt += 1
        finally:
            self.tape.status("session_end", **totals)
        return totals

    def close(self) -> None:
        self.live.close()
        if hasattr(self.writer, "close"):
            self.writer.close()


# --- entry point --------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PMUS trades tape (read-only)")
    ap.add_argument("--once", action="store_true",
                    help="smoke mode: connect, subscribe, record for --seconds, exit 0")
    ap.add_argument("--seconds", type=float, default=SMOKE_SECONDS,
                    help=f"smoke-mode duration (default {SMOKE_SECONDS:.0f}s)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    install_log_redaction()  # armed BEFORE the first secret read

    rec = TradesRecorder(ws_factory=lambda: build_markets_ws())
    try:
        rec.assert_credentials()
    except config.MissingSecret as exc:
        # exc's message names the secret and the keyring service — never a value.
        log.error("cannot start: %s", exc)
        rec.close()
        return 2

    log.info("pmus trades recorder up (read-only trade prints; poll %.1fs)", POLL_S)
    try:
        totals = asyncio.run(rec.run(seconds=args.seconds if args.once else None))
    except OrderSurfaceError:
        rec.close()
        return 3
    except config.MissingSecret as exc:
        log.error("cannot continue: %s", exc)
        rec.close()
        return 2
    except KeyboardInterrupt:
        totals = None
    finally:
        rec.close()

    if args.once and totals is not None:
        print(f"once: {args.seconds:.0f}s window, subscribed {rec.subscribed}")
        print(f"once: {totals['trades']} trade(s), {totals['frames']} frame(s), "
              f"{totals['errors']} error(s), {totals['reconnects']} reconnect(s)")
        print(f"once: tape -> {rec.pmus_dir}/trades-*.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
