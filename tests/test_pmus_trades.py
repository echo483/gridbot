"""Hermetic tests for the PMUS trades tape (no network, no keyring, no real data).

The guardrail tests (TestNoOrderSurface, TestImportIsolation, TestSecretsNeverSurface)
are the point of this file: the recorder authenticates with the SAME Ed25519 key pair
that can place real orders, so its read-only-ness has to be a structural property the
suite re-checks on every run — not a convention someone remembers.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gridbot import config
from gridbot.capture import pmus_trades as pt

FAKE_KEY_ID = "fake-key-id-0123456789abcdef"  # pragma: allowlist secret (test fixture)
FAKE_SECRET = "fake-secret-key-ZZZZ-0123456789abcdef"  # pragma: allowlist secret (test fixture)
SID = "2999839"


# --- fixtures ----------------------------------------------------------------


def _ss_row(sid: str = SID, seq: int = 2, started: bool = True,
            finished: bool = False) -> dict:
    """Minimal series_state capture row, shaped like data/grid/series_state-*.jsonl."""
    return {
        "type": "ss",
        "series_id": sid,
        "ts_ns": time.time_ns(),
        "data": {"seriesState": {
            "id": sid, "started": started, "finished": finished,
            "games": [{"sequenceNumber": seq, "started": True, "finished": False}],
        }},
    }


def _mapping_row(sid: str = SID, seq: int = 2) -> dict:
    return {
        "type": "grid_pmus_mapping",
        "grid_series_id": sid,
        "match": {"coverage": 1.0},
        "us_markets": {
            f"map{seq}": {"market_slug": f"acme-cs2-aaa-bbb-2026-09-07-map{seq}"},
            "match": {"market_slug": "acme-cs2-aaa-bbb-2026-09-07"},
        },
    }


def _write_tape(directory: Path, stem: str, rows: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}-20260907.jsonl"
    with open(path, "w") as fh:
        fh.writelines(json.dumps(row) + "\n" for row in rows)
    return path


def _trade_msg(slug: str = "acme-cs2-aaa-bbb-2026-09-07-map2", px: str = "0.42") -> dict:
    """Exactly the shape of polymarket_us.websocket.types.Trade."""
    return {
        "requestId": "trades-1",
        "subscriptionType": "SUBSCRIPTION_TYPE_TRADE",
        "trade": {
            "marketSlug": slug,
            "price": {"value": px, "currency": "USD"},
            "quantity": {"value": "17", "currency": "USD"},
            "tradeTime": "2026-09-07T18:04:11.123Z",
            "maker": {"side": "ORDER_SIDE_BUY", "intent": "INTENT_OPEN"},
            "taker": {"side": "ORDER_SIDE_SELL", "intent": "INTENT_CLOSE"},
        },
    }


class FakeWriter:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def append(self, obj: dict) -> None:
        obj.setdefault("ts_ns", time.time_ns())
        self.rows.append(obj)

    def close(self) -> None:
        pass


class FakeWS:
    """A MarketsWebSocket-shaped double. Deliberately has NO order surface."""

    def __init__(self) -> None:
        self._listeners: dict[str, list] = {}
        self.calls: list[tuple] = []
        self.connect_calls = 0
        self.closed = False
        self.alive = True

    async def connect(self) -> None:
        self.connect_calls += 1
        self.closed = False
        self.alive = True
        self.emit("open")

    def on(self, event: str, cb):
        self._listeners.setdefault(event, []).append(cb)
        return self

    def emit(self, event: str, *args) -> None:
        for cb in list(self._listeners.get(event, [])):
            cb(*args)

    @property
    def is_connected(self) -> bool:
        return self.alive and not self.closed

    async def subscribe_trades(self, request_id: str, market_slugs: list[str]) -> None:
        self.calls.append(("subscribe_trades", request_id, list(market_slugs)))

    async def unsubscribe(self, request_id: str) -> None:
        self.calls.append(("unsubscribe", request_id))

    async def close(self) -> None:
        self.closed = True


class OrderCapableWS(FakeWS):
    """What the SDK must never become: a market socket that can also trade."""

    async def place_order(self, *a, **k):  # pragma: no cover - never called
        raise AssertionError("guardrail failed: an order was placed")


class Driver:
    """Injectable clock + async sleep. Each sleep advances virtual time and may
    fire a scripted step (emit a trade, drop the socket, ...)."""

    def __init__(self, steps=None) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []
        self.steps = list(steps or [])

    def clock(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += max(seconds, 0.001)
        if self.steps:
            self.steps.pop(0)()


def _recorder(tmp_path: Path, writer, ws, driver: Driver, **kw):
    grid = tmp_path / "grid"
    pmus = tmp_path / "pmus"
    _write_tape(grid, "series_state", [_ss_row()])
    _write_tape(pmus, "mappings", [_mapping_row()])
    return pt.TradesRecorder(
        ws_factory=lambda: ws, writer=writer, grid_dir=grid, pmus_dir=pmus,
        from_start=True, clock=driver.clock, sleep=driver.sleep,
        poll_s=kw.pop("poll_s", 0.01), **kw,
    )


# --- GUARDRAIL 1: no order surface -------------------------------------------


class TestNoOrderSurface:
    def test_order_surface_names_the_offending_attributes(self):
        assert pt.order_surface(OrderCapableWS()) == ["place_order"]

    def test_clean_object_has_no_order_surface(self):
        assert pt.order_surface(FakeWS()) == []

    def test_real_sdk_markets_websocket_is_still_read_only(self):
        """Canary: fires the day the vendored SDK grows an order method on the
        market socket we hand real trading credentials to."""
        from polymarket_us.websocket.markets import MarketsWebSocket

        assert pt.order_surface(MarketsWebSocket) == []
        pt.assert_no_order_surface(MarketsWebSocket)
        # ...and on a live instance too: the constructor is inert (create_auth_headers
        # only runs in connect()), but it sets instance attributes dir() would show.
        inst = MarketsWebSocket(key_id="not-a-real-key", secret_key="not-a-real-secret")
        assert pt.order_surface(inst) == []

    def test_assert_raises_on_order_capable_object(self):
        with pytest.raises(pt.OrderSurfaceError) as exc:
            pt.assert_no_order_surface(OrderCapableWS())
        assert "place_order" in str(exc.value)

    async def test_recorder_refuses_to_run_and_never_subscribes(self, tmp_path):
        ws = OrderCapableWS()
        writer = FakeWriter()
        rec = _recorder(tmp_path, writer, ws, Driver())
        with pytest.raises(pt.OrderSurfaceError):
            await rec.run(seconds=1.0)
        assert ws.calls == []  # never subscribed, never sent a frame
        assert any(r["type"] == "pmus_trades_status" and r["event"] == "order_surface_refused"
                   for r in writer.rows)

    async def test_order_surface_error_is_not_swallowed_by_the_retry_loop(self, tmp_path):
        """A structural violation must be fatal, not a thing we back off and retry."""
        ws = OrderCapableWS()
        driver = Driver()
        rec = _recorder(tmp_path, FakeWriter(), ws, driver)
        with pytest.raises(pt.OrderSurfaceError):
            await rec.run(seconds=100.0)
        assert ws.connect_calls <= 1


# --- GUARDRAIL 2: no order-capable module in the import graph ----------------


ORDER_CAPABLE_MODULES = (
    "gridbot.executor.pmus",       # the ONLY module allowed to touch trading keys
    "gridbot.executor.engine",
    "gridbot.executor.intents",
    "gridbot.executor.ledger",
    "gridbot.executor.fills",
    "gridbot.executor.limits",
    "gridbot.executor.reconcile",
    "polymarket_us.client",        # order-capable REST client
    "polymarket_us.async_client",
)


class TestImportIsolation:
    def test_fresh_import_pulls_in_no_order_capable_module(self):
        """WHY: this process holds the Ed25519 keys that can place real orders.
        Importing the wire layer (gridbot.executor.pmus) or the REST client would
        put an order-capable object one attribute lookup away from the recorder's
        credentials. A fresh interpreter is the only honest way to measure the
        transitive closure, so this runs in a subprocess and walks sys.modules."""
        code = (
            "import sys, json\n"
            "import gridbot.capture.pmus_trades\n"
            "print(json.dumps(sorted(sys.modules)))\n"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        loaded = set(json.loads(out.stdout.strip().splitlines()[-1]))
        assert loaded & set(ORDER_CAPABLE_MODULES) == set()
        # ...and the whole polymarket_us SDK stays unloaded until we actually connect.
        assert not any(m.startswith("polymarket_us") for m in loaded)

    def test_only_allowed_executor_submodule_is_the_pure_name_matcher(self):
        """gridbot.executor.us_mapping is a pure GRID<->PMUS name matcher (imports
        only gridbot.config). It arrives via instruments.mapentry, which pmus_books
        uses for the slug mapping we are required to reuse. Nothing else from that
        package may appear."""
        code = (
            "import sys, json\n"
            "import gridbot.capture.pmus_trades\n"
            "print(json.dumps(sorted(m for m in sys.modules "
            "if m.startswith('gridbot.executor'))))\n"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        loaded = json.loads(out.stdout.strip().splitlines()[-1])
        assert loaded == ["gridbot.executor", "gridbot.executor.us_mapping"]

    def _module_ast(self) -> ast.Module:
        return ast.parse(Path(pt.__file__).read_text())

    def test_source_imports_no_executor_and_no_rest_client(self):
        bad = []
        for node in ast.walk(self._module_ast()):
            if isinstance(node, ast.Import):
                bad += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                bad += [f"{node.module}.{a.name}" for a in node.names if node.module]
        for name in bad:
            assert not name.startswith("gridbot.executor"), name
            assert "polymarket_us.client" not in name, name
            assert "async_client" not in name, name
            assert not name.endswith(".PolymarketUS"), name

    def test_source_never_calls_an_order_shaped_method(self):
        """No `.send(...)`, no `.place(...)`: the module may only reach the venue
        through subscribe_trades / unsubscribe / connect / close."""
        called = set()
        for node in ast.walk(self._module_ast()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
        assert "send" not in called
        assert not [n for n in called if pt.ORDER_SURFACE_RE.search(n)]


# --- GUARDRAIL 3: secrets never surface --------------------------------------


class TestSecretsNeverSurface:
    def test_credentials_come_from_config_require_only(self, monkeypatch):
        monkeypatch.setenv("POLYMARKET_US_KEY_ID", FAKE_KEY_ID)
        monkeypatch.setenv("POLYMARKET_US_SECRET_KEY", FAKE_SECRET)
        seen: dict = {}

        class Cls:
            def __init__(self, **kwargs):
                seen.update(kwargs)

        pt.build_markets_ws(cls=Cls)
        assert seen == {"key_id": FAKE_KEY_ID, "secret_key": FAKE_SECRET}
        # MarketsWebSocket sets path itself; passing it would be a duplicate kwarg.
        assert "path" not in seen

    async def test_no_secret_reaches_the_log_or_the_tape(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv("POLYMARKET_US_KEY_ID", FAKE_KEY_ID)
        monkeypatch.setenv("POLYMARKET_US_SECRET_KEY", FAKE_SECRET)
        caplog.set_level("DEBUG")
        ws = FakeWS()
        writer = FakeWriter()
        driver = Driver(steps=[
            lambda: ws.emit("trade", _trade_msg()),
            lambda: ws.emit("error", RuntimeError(f"venue said {FAKE_SECRET}")),
            lambda: ws.emit("close"),
        ])
        rec = _recorder(tmp_path, writer, ws, driver)
        rec.assert_credentials()  # reads both secrets through config.require
        await rec.run(seconds=0.2)

        blob = json.dumps(writer.rows) + "\n" + caplog.text
        assert FAKE_KEY_ID not in blob
        assert FAKE_SECRET not in blob
        # and prove the absence is redaction doing work, not a missing row:
        errs = [r for r in writer.rows if r["type"] == "pmus_trades_error"]
        assert errs and "redacted" in errs[0]["detail"]

    def test_missing_credential_exits_nonzero_with_a_secret_free_message(
        self, tmp_path, monkeypatch, caplog
    ):
        monkeypatch.delenv("POLYMARKET_US_KEY_ID", raising=False)
        monkeypatch.delenv("POLYMARKET_US_SECRET_KEY", raising=False)
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        caplog.set_level("ERROR")
        rc = pt.main(["--once", "--seconds", "0.05"])
        assert rc == 2
        assert "POLYMARKET_US_KEY_ID" in caplog.text
        assert "Traceback" not in caplog.text


# --- GUARDRAIL 4: read-only intent recorded in the tape ----------------------


class TestTapeShape:
    async def test_every_row_carries_a_type_and_a_receive_timestamp(self, tmp_path):
        ws = FakeWS()
        writer = FakeWriter()
        driver = Driver(steps=[
            lambda: ws.emit("trade", _trade_msg()),
            lambda: ws.emit("message", {"somethingNew": {}}),
            lambda: ws.emit("error", RuntimeError("boom")),
            lambda: ws.emit("heartbeat"),
        ])
        rec = _recorder(tmp_path, writer, ws, driver)
        await rec.run(seconds=0.2)
        assert writer.rows
        for row in writer.rows:
            assert isinstance(row["type"], str) and row["type"].startswith("pmus_trade")
            assert isinstance(row["ts_ns"], int)

    async def test_session_row_records_read_only_mode(self, tmp_path):
        ws, writer = FakeWS(), FakeWriter()
        rec = _recorder(tmp_path, writer, ws, Driver())
        await rec.run(seconds=0.05)
        starts = [r for r in writer.rows if r.get("event") == "session_start"]
        assert starts and all(r["mode"] == "read_only" for r in starts)

    async def test_trade_row_is_the_venue_payload_verbatim_plus_slug(self, tmp_path):
        msg = _trade_msg()
        ws, writer = FakeWS(), FakeWriter()
        driver = Driver(steps=[lambda: ws.emit("trade", msg)])
        rec = _recorder(tmp_path, writer, ws, driver)
        await rec.run(seconds=0.05)
        trades = [r for r in writer.rows if r["type"] == "pmus_trade"]
        assert len(trades) == 1
        assert trades[0]["payload"] == msg  # untouched, uninterpreted
        assert trades[0]["slug"] == msg["trade"]["marketSlug"]

    async def test_unknown_frame_is_recorded_verbatim_not_dropped(self, tmp_path):
        """Schema-drift canary: if the venue renames `trade`, the tape still has it."""
        ws, writer = FakeWS(), FakeWriter()
        driver = Driver(steps=[lambda: ws.emit("message", {"fill": {"px": "0.5"}})])
        rec = _recorder(tmp_path, writer, ws, driver)
        await rec.run(seconds=0.05)
        unknown = [r for r in writer.rows if r["type"] == "pmus_trades_unknown"]
        assert unknown and unknown[0]["payload"] == {"fill": {"px": "0.5"}}

    async def test_error_row_carries_the_exception_class(self, tmp_path):
        ws, writer = FakeWS(), FakeWriter()
        driver = Driver(steps=[lambda: ws.emit("error", ValueError("bad slug"))])
        rec = _recorder(tmp_path, writer, ws, driver)
        await rec.run(seconds=0.05)
        errs = [r for r in writer.rows if r["type"] == "pmus_trades_error"]
        assert errs and errs[0]["error"] == "ValueError"

    async def test_heartbeat_rows_are_throttled(self, tmp_path):
        ws, writer = FakeWS(), FakeWriter()
        driver = Driver(steps=[lambda: ws.emit("heartbeat")] * 8)
        rec = _recorder(tmp_path, writer, ws, driver, heartbeat_s=1000.0)
        await rec.run(seconds=0.2)
        beats = [r for r in writer.rows if r["type"] == "pmus_trades_heartbeat"]
        assert len(beats) == 1


# --- subscription behaviour ---------------------------------------------------


class TestSubscription:
    async def test_subscribes_to_the_live_map_and_match_slugs(self, tmp_path):
        ws, writer = FakeWS(), FakeWriter()
        rec = _recorder(tmp_path, writer, ws, Driver())
        await rec.run(seconds=0.05)
        subs = [c for c in ws.calls if c[0] == "subscribe_trades"]
        assert len(subs) == 1
        assert sorted(subs[0][2]) == [
            "acme-cs2-aaa-bbb-2026-09-07",
            "acme-cs2-aaa-bbb-2026-09-07-map2",
        ]

    async def test_never_subscribes_with_an_empty_slug_list(self, tmp_path):
        """SDK: `if market_slugs:` — an empty list omits marketSlugs entirely, which
        the venue may read as 'every market'. Never send that frame."""
        grid, pmus = tmp_path / "grid", tmp_path / "pmus"
        _write_tape(grid, "series_state", [])
        _write_tape(pmus, "mappings", [])
        ws, writer = FakeWS(), FakeWriter()
        driver = Driver()
        rec = pt.TradesRecorder(ws_factory=lambda: ws, writer=writer, grid_dir=grid,
                                pmus_dir=pmus, from_start=True, clock=driver.clock,
                                sleep=driver.sleep, poll_s=0.01)
        await rec.run(seconds=0.1)
        assert [c for c in ws.calls if c[0] == "subscribe_trades"] == []

    async def test_resubscribes_when_the_live_set_changes(self, tmp_path):
        grid, pmus = tmp_path / "grid", tmp_path / "pmus"
        ss = _write_tape(grid, "series_state", [_ss_row(seq=2)])
        _write_tape(pmus, "mappings", [_mapping_row(seq=2), _mapping_row(seq=3)])
        ws, writer = FakeWS(), FakeWriter()

        def advance_map():
            with open(ss, "a") as fh:
                fh.write(json.dumps(_ss_row(seq=3)) + "\n")

        driver = Driver(steps=[advance_map])
        rec = pt.TradesRecorder(ws_factory=lambda: ws, writer=writer, grid_dir=grid,
                                pmus_dir=pmus, from_start=True, clock=driver.clock,
                                sleep=driver.sleep, poll_s=0.01, mapping_reload_s=0.0)
        await rec.run(seconds=0.1)
        subs = [c for c in ws.calls if c[0] == "subscribe_trades"]
        assert len(subs) >= 2
        assert "acme-cs2-aaa-bbb-2026-09-07-map3" in subs[-1][2]
        assert ("unsubscribe", subs[0][1]) in ws.calls  # old subscription cancelled


# --- reconnect ----------------------------------------------------------------


class TestReconnect:
    def test_backoff_is_bounded_exponential_and_never_zero(self):
        delays = [pt.backoff_delay(i) for i in range(12)]
        assert delays[0] == pt.BACKOFF_MIN_S
        assert all(d > 0 for d in delays)
        assert delays == sorted(delays)
        assert max(delays) == pt.BACKOFF_MAX_S

    async def test_reconnects_after_a_drop_and_sleeps_first(self, tmp_path):
        ws, writer = FakeWS(), FakeWriter()

        def drop():
            ws.alive = False
            ws.emit("close")

        driver = Driver(steps=[drop])
        rec = _recorder(tmp_path, writer, ws, driver)
        await rec.run(seconds=5.0)
        assert ws.connect_calls >= 2
        assert pt.BACKOFF_MIN_S in driver.sleeps  # backed off, did not busy-loop
        assert any(r.get("event") == "disconnected" for r in writer.rows)

    async def test_connect_failure_backs_off_and_retries(self, tmp_path):
        attempts = {"n": 0}

        def factory():
            attempts["n"] += 1
            raise OSError("dns")

        grid, pmus = tmp_path / "grid", tmp_path / "pmus"
        _write_tape(grid, "series_state", [_ss_row()])
        _write_tape(pmus, "mappings", [_mapping_row()])
        writer, driver = FakeWriter(), Driver()
        rec = pt.TradesRecorder(ws_factory=factory, writer=writer, grid_dir=grid,
                                pmus_dir=pmus, from_start=True, clock=driver.clock,
                                sleep=driver.sleep, poll_s=0.01)
        await rec.run(seconds=8.0)
        assert attempts["n"] >= 2
        assert driver.sleeps[:2] == [pt.BACKOFF_MIN_S, pt.BACKOFF_MIN_S * 2]
        assert any(r.get("event") == "connect_failed" for r in writer.rows)

    async def test_silence_watchdog_recycles_a_dead_reader(self, tmp_path):
        """The SDK's reader task can die without closing the socket (a non
        ConnectionClosed exception only emits `error`). is_connected still says
        OPEN, so silence is the only honest liveness signal."""
        ws, writer = FakeWS(), FakeWriter()
        driver = Driver()
        rec = _recorder(tmp_path, writer, ws, driver, silence_timeout_s=0.05)
        await rec.run(seconds=5.0)  # room for a BACKOFF_MIN_S sleep inside the window
        assert ws.connect_calls >= 2
        assert any(r.get("event") == "silent" for r in writer.rows)

    async def test_subscribing_gives_the_silence_watchdog_a_fresh_window(self, tmp_path):
        """Second-order bug from the guard above: while subscribed to nothing the
        frame clock goes stale (hours, overnight). Without a reset, the first
        subscription of the morning would find `now - _last_frame` already past
        the timeout and recycle immediately — churning the socket at exactly the
        moment real trades start arriving.
        """
        ws, writer = FakeWS(), FakeWriter()
        driver = Driver()
        rec = _recorder(tmp_path, writer, ws, driver, silence_timeout_s=0.05)
        rec._last_frame = driver.clock() - 10_000.0      # idle all night
        await rec._resubscribe(ws, ["acme-cs2-aaa-bbb-2026-09-07"])
        assert driver.clock() - rec._last_frame < 1.0, "stale frame clock survived subscribe"

    async def test_subscribing_to_nothing_does_not_reset_the_frame_clock(self, tmp_path):
        """Only a real subscription earns a fresh window — otherwise an empty
        resubscribe loop would mask a genuinely dead reader forever.
        """
        ws, writer = FakeWS(), FakeWriter()
        driver = Driver()
        rec = _recorder(tmp_path, writer, ws, driver, silence_timeout_s=0.05)
        stale = driver.clock() - 10_000.0
        rec._last_frame = stale
        await rec._resubscribe(ws, [])
        assert rec._last_frame == stale

    async def test_silence_with_nothing_subscribed_does_not_recycle(self, tmp_path):
        """Observed live on the first night: with no CS2 series in the slate the
        recorder subscribed to nothing, received nothing, and recycled the socket
        every 90s — ~40 authenticated reconnects an hour, all night, for no
        reason. Silence is only evidence of a dead reader when we ASKED for
        frames; with an empty subscription it is the expected state.
        """
        ws, writer = FakeWS(), FakeWriter()
        driver = Driver()
        grid, pmus = tmp_path / "grid", tmp_path / "pmus"
        _write_tape(grid, "series_state", [])      # no live series at all
        _write_tape(pmus, "mappings", [])
        rec = pt.TradesRecorder(
            ws_factory=lambda: ws, writer=writer, grid_dir=grid, pmus_dir=pmus,
            from_start=True, clock=driver.clock, sleep=driver.sleep,
            poll_s=0.01, silence_timeout_s=0.05,
        )
        await rec.run(seconds=5.0)
        assert ws.connect_calls == 1, "recycled while subscribed to nothing"
        assert not any(r.get("event") == "silent" for r in writer.rows)


# --- liveness / smoke ---------------------------------------------------------


class TestLiveSlugs:
    def test_maps_live_series_to_map_and_match_slugs(self, tmp_path):
        grid, pmus = tmp_path / "grid", tmp_path / "pmus"
        _write_tape(grid, "series_state", [_ss_row()])
        _write_tape(pmus, "mappings", [_mapping_row()])
        live = pt.LiveSlugs(grid, pmus, from_start=True)
        assert live.slugs(0.0) == [
            "acme-cs2-aaa-bbb-2026-09-07",
            "acme-cs2-aaa-bbb-2026-09-07-map2",
        ]
        live.close()

    def test_unmapped_or_not_started_series_yield_nothing(self, tmp_path):
        grid, pmus = tmp_path / "grid", tmp_path / "pmus"
        _write_tape(grid, "series_state", [_ss_row(started=False)])
        _write_tape(pmus, "mappings", [_mapping_row()])
        live = pt.LiveSlugs(grid, pmus, from_start=True)
        assert live.slugs(0.0) == []
        live.close()


class TestSmokeMode:
    def test_once_returns_zero_and_writes_a_tape(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POLYMARKET_US_KEY_ID", FAKE_KEY_ID)
        monkeypatch.setenv("POLYMARKET_US_SECRET_KEY", FAKE_SECRET)
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        _write_tape(tmp_path / "grid", "series_state", [_ss_row()])
        _write_tape(tmp_path / "pmus", "mappings", [_mapping_row()])
        ws = FakeWS()
        monkeypatch.setattr(pt, "build_markets_ws", lambda cls=None: ws)
        rc = pt.main(["--once", "--seconds", "0.05"])
        assert rc == 0
        written = (tmp_path / "pmus").glob("trades-*.jsonl")
        rows = [json.loads(ln) for p in written for ln in p.read_text().splitlines()]
        assert any(r["type"] == "pmus_trades_status" for r in rows)
