"""Polymarket CLOB order-book recorder: websocket stream + periodic REST snapshots.

Raw messages are recorded verbatim with a local receive timestamp. Reconnects
resubscribe; the server sends a full book event on subscribe, and REST snapshots
provide integrity baselines even if the websocket misbehaves.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time

import httpx
import websockets

from gridbot import config
from gridbot.capture.files import DayWriter

log = logging.getLogger(__name__)

CHUNK = 50


class BookRecorder:
    def __init__(self, writer: DayWriter) -> None:
        self.writer = writer
        self.assets: set[str] = set()
        self._tasks: list[asyncio.Task] = []
        self._snapshot_task: asyncio.Task | None = None
        self._gen = 0

    async def set_assets(self, assets: set[str]) -> None:
        if assets == self.assets:
            return
        added, removed = assets - self.assets, self.assets - assets
        log.info("book assets: %d (+%d/-%d)", len(assets), len(added), len(removed))
        self.assets = set(assets)
        await self._restart()

    async def _restart(self) -> None:
        self._gen += 1
        for t in self._tasks:
            t.cancel()
        self._tasks = [
            asyncio.create_task(self._ws_loop(chunk, self._gen))
            for chunk in _chunks(sorted(self.assets), CHUNK)
        ]
        if self._snapshot_task is None:
            self._snapshot_task = asyncio.create_task(self._snapshot_loop())

    async def _ws_loop(self, chunk: list[str], gen: int) -> None:
        backoff = 2.0
        while gen == self._gen:
            try:
                async with websockets.connect(
                    config.PM_WS_MARKET_URL, ping_interval=None, max_size=2**23
                ) as ws:
                    await ws.send(json.dumps({"assets_ids": chunk, "type": "market"}))
                    self.writer.append({"type": "ws_subscribed", "n_assets": len(chunk)})
                    backoff = 2.0
                    last_ping = time.monotonic()
                    while gen == self._gen:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                        except TimeoutError:
                            raw = None
                        if raw is not None and raw != "PONG":
                            self.writer.append({"type": "ws", "raw": _maybe_json(raw)})
                        if time.monotonic() - last_ping > 10.0:
                            await ws.send("PING")
                            last_ping = time.monotonic()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 — recorder must survive anything
                log.warning("ws reconnect in %.0fs: %s", backoff, exc)
                self.writer.append({"type": "ws_error", "error": str(exc)[:300]})
                await asyncio.sleep(backoff + random.uniform(0, 1))
                backoff = min(backoff * 2, 60.0)

    async def _snapshot_loop(self) -> None:
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    await self._snapshot_once(client)
                except asyncio.CancelledError:
                    return
                except Exception as exc:  # noqa: BLE001
                    log.warning("snapshot failed: %s", exc)
                await asyncio.sleep(config.PM_SNAPSHOT_CADENCE_S)

    async def _snapshot_once(self, client: httpx.AsyncClient) -> None:
        assets = sorted(self.assets)
        for chunk in _chunks(assets, CHUNK):
            try:
                resp = await client.post(
                    f"{config.PM_CLOB_URL}/books",
                    json=[{"token_id": t} for t in chunk],
                    timeout=30.0,
                )
                resp.raise_for_status()
                books = resp.json()
                if not isinstance(books, list):
                    raise ValueError("unexpected /books shape")  # engage per-token fallback
                self.writer.append({"type": "rest_books", "books": books})
            except Exception:  # noqa: BLE001 — batch shape unverified; fall back to per-token GET
                # Fallback: one-by-one GET; slower but shape-proof.
                for t in chunk[:20]:
                    r = await client.get(
                        f"{config.PM_CLOB_URL}/book", params={"token_id": t}, timeout=15.0
                    )
                    if r.status_code == 200:
                        self.writer.append({"type": "rest_book", "token_id": t, "book": r.json()})
                return

    async def stop(self) -> None:
        self._gen += 1
        for t in [*self._tasks, self._snapshot_task]:
            if t:
                t.cancel()


def _chunks(seq: list[str], n: int) -> list[list[str]]:
    return [seq[i : i + n] for i in range(0, len(seq), n)]


def _maybe_json(raw: str | bytes):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return {"unparsed": str(raw)[:2000]}
