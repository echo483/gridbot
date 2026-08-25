"""Shadow instrument engine: detect registered trigger states on the capture
stream and record hypothetical entries against the time-aligned book.

READ-ONLY BY CONSTRUCTION: tails capture day files, writes instance records to
data/shadow/. Places no orders, holds no keys, calls no LLM. Verdicts on
H-MANADV1 / H-ECONBUY1 / H-PISTOLCARRY1 are computed offline from these
records per their frozen falsification criteria in the registry.

Run: uv run python -m gridbot.instruments.shadow            # live tail
     uv run python -m gridbot.instruments.shadow --replay   # today's files, then exit
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import time
from pathlib import Path

from gridbot import config
from gridbot.capture.files import DayWriter
from gridbot.instruments.books import BookTops
from gridbot.instruments.detectors import detect
from gridbot.instruments.market_match import match_market

log = logging.getLogger(__name__)
HEARTBEAT_S = 60


class Follow:
    """Tail a day-file family (prefix-YYYYMMDD.jsonl), rolling to newer days."""

    def __init__(self, directory: Path, prefix: str, from_start: bool) -> None:
        self.directory = directory
        self.prefix = prefix
        self.path: Path | None = None
        self.fh = None
        self._open_latest(seek_end=not from_start)

    def _latest(self) -> Path | None:
        files = sorted(self.directory.glob(f"{self.prefix}-*.jsonl"))
        return files[-1] if files else None

    def _open_latest(self, seek_end: bool) -> None:
        latest = self._latest()
        if latest is None or latest == self.path:
            return
        if self.fh:
            self.fh.close()
        self.path = latest
        self.fh = open(latest)  # noqa: SIM115 — long-lived tail handle
        if seek_end:
            self.fh.seek(0, 2)

    def lines(self, max_lines: int = 1000) -> list[dict]:
        if self.fh is None:
            self._open_latest(seek_end=False)
            if self.fh is None:
                return []
        out: list[dict] = []
        for _ in range(max_lines):
            line = self.fh.readline()
            if not line:
                break
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not out:
            self._open_latest(seek_end=False)  # day rollover, if any
        return out

    def close(self) -> None:
        if self.fh:
            self.fh.close()
            self.fh = None


class ShadowEngine:
    def __init__(self, replay: bool = False, include_test: bool = False) -> None:
        self.grid = Follow(config.DATA_DIR / "grid", "series_state", from_start=replay)
        self.book_stream = Follow(config.DATA_DIR / "pm", "books", from_start=replay)
        self.catalog_stream = Follow(config.DATA_DIR / "pm", "catalog", from_start=True)
        self.tops = BookTops()
        self.catalog: dict[str, dict] = {}
        self.writer = DayWriter(config.DATA_DIR / "shadow", "replay" if replay else "instances")
        self.include_test = include_test
        self.seen: set[tuple] = set()
        self.counters = {"ss": 0, "book_msgs": 0, "instances": 0, "matched": 0}

    def cycle(self) -> bool:
        """One consume pass over all streams; True if anything was read."""
        got = False
        for record in self.catalog_stream.lines():
            got = True
            if record.get("type") == "catalog" and record.get("condition_id"):
                self.catalog[record["condition_id"]] = record
        for record in self.book_stream.lines(5000):
            got = True
            self.counters["book_msgs"] += 1
            self.tops.update(record)
        for record in self.grid.lines():
            got = True
            self._grid_record(record)
        return got

    def _grid_record(self, record: dict) -> None:
        if record.get("type") != "ss" or record.get("title") != "cs2":
            return
        series_id = str(record.get("series_id"))
        if not self.include_test and series_id in config.GRID_TEST_SERIES_IDS:
            return
        state = (record.get("data") or {}).get("seriesState") or {}
        self.counters["ss"] += 1
        for instance in detect(state):
            key = (
                instance["hypothesis_id"],
                instance["series_id"],
                instance["map_number"],
                instance["round_number"],
            )
            if key in self.seen:
                continue
            self.seen.add(key)
            market = match_market(self.catalog, instance["team"], instance["opponent"])
            books: dict[str, dict] = {}
            if market:
                self.counters["matched"] += 1
                for token in market.get("clob_token_ids") or []:
                    top = self.tops.top(str(token))
                    if top:
                        books[str(token)] = top
            self.counters["instances"] += 1
            self.writer.append(
                {
                    "type": "instance",
                    **instance,
                    "market": market,
                    "books": books,
                    "grid_ts_ns": record.get("ts_ns"),
                }
            )
            self.writer.flush()
            log.info(
                "instance %s %s map%s r%s side=%s team=%r fair=%s market=%s",
                instance["hypothesis_id"],
                instance["series_id"],
                instance["map_number"],
                instance["round_number"],
                instance["side"],
                instance["team"],
                instance["fair_value"],
                (market or {}).get("question"),
            )

    def close(self) -> None:
        for stream in (self.grid, self.book_stream, self.catalog_stream):
            stream.close()
        self.writer.flush()
        self.writer.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replay", action="store_true", help="process existing files from start, then exit"
    )
    parser.add_argument(
        "--include-test",
        action="store_true",
        help="replay only: include GRID-TEST fixtures (plumbing check, never real instances)",
    )
    args = parser.parse_args()

    engine = ShadowEngine(replay=args.replay, include_test=args.include_test and args.replay)
    stopping = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stopping.update(flag=True))

    idle_cycles = 0
    last_beat = time.monotonic()
    while not stopping["flag"]:
        got = engine.cycle()
        idle_cycles = 0 if got else idle_cycles + 1
        if args.replay and idle_cycles >= 2:
            break
        if not got:
            time.sleep(0.3)
        if time.monotonic() - last_beat >= HEARTBEAT_S:
            last_beat = time.monotonic()
            log.info("heartbeat %s", engine.counters)
    log.info("done %s", engine.counters)
    engine.close()


if __name__ == "__main__":
    main()
