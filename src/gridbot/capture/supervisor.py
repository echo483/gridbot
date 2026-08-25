"""Capture supervisor: PM discovery + book recording, GRID discovery + SS polling.

READ-ONLY data collection. Places no orders, has no trading surface, makes no
edge claim. Run: ``python -m gridbot.capture.supervisor`` (or ``--once`` smoke).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

import httpx

from gridbot import config
from gridbot.capture.files import DayWriter
from gridbot.capture.grid_poller import GridCapture
from gridbot.capture.pm_books import BookRecorder
from gridbot.capture.pm_discovery import CatalogTracker, fetch_esports_catalog
from gridbot.redaction import install_log_redaction

log = logging.getLogger("gridbot.capture")


async def run(once: bool = False) -> None:
    pm_writer = DayWriter(config.DATA_DIR / "pm", "books")
    catalog_writer = DayWriter(config.DATA_DIR / "pm", "catalog")
    grid_writer = DayWriter(config.DATA_DIR / "grid", "series_state")
    grid_disc_writer = DayWriter(config.DATA_DIR / "grid", "discovery")

    recorder = BookRecorder(pm_writer)
    catalog = CatalogTracker(catalog_writer)
    grid = GridCapture(grid_writer, grid_disc_writer)

    stop = asyncio.Event()
    if not once:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)

    async with httpx.AsyncClient() as client:
        async def pm_loop() -> None:
            while not stop.is_set():
                try:
                    assets = catalog.update(await fetch_esports_catalog(client))
                    await recorder.set_assets(assets)
                except Exception as exc:  # noqa: BLE001
                    log.warning("pm discovery failed: %s", exc)
                await _sleep(config.PM_DISCOVERY_CADENCE_S, stop)

        async def grid_discovery_loop() -> None:
            while not stop.is_set():
                try:
                    await grid.discover_once(client)
                except Exception as exc:  # noqa: BLE001
                    log.warning("grid discovery failed: %s", exc)
                await _sleep(config.DISCOVERY_CADENCE_S, stop)

        async def grid_poll_loop() -> None:
            while not stop.is_set():
                try:
                    await grid.poll_due(client)
                except Exception as exc:  # noqa: BLE001
                    log.warning("grid poll failed: %s", exc)
                await _sleep(2, stop)

        async def heartbeat() -> None:
            while not stop.is_set():
                await _sleep(60, stop)
                log.info(
                    "hb: pm_books=%d catalog=%d grid_ss=%d polls=%d tracked=%d live=%d",
                    pm_writer.written,
                    catalog_writer.written,
                    grid_writer.written,
                    grid.polls,
                    len(grid.tracked),
                    grid.live_count(),
                )

        tasks = [
            asyncio.create_task(pm_loop()),
            asyncio.create_task(grid_discovery_loop()),
            asyncio.create_task(grid_poll_loop()),
            asyncio.create_task(heartbeat()),
        ]
        if once:
            await asyncio.sleep(45)
            stop.set()
        await stop.wait()
        log.info("draining…")
        await recorder.stop()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    for w in (pm_writer, catalog_writer, grid_writer, grid_disc_writer):
        w.close()
    log.info(
        "shutdown clean: pm_books=%d catalog=%d grid=%d",
        pm_writer.written,
        catalog_writer.written,
        grid_writer.written,
    )


async def _sleep(seconds: float, stop: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="45s smoke run, then clean exit")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    install_log_redaction()
    asyncio.run(run(once=args.once))


if __name__ == "__main__":
    main()
