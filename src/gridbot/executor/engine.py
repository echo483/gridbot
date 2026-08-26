"""Executor engine: tail shadow instances, emit capped maker intents.

dry_run (default): every intent is appended to the intent ledger with
status "dry_run" — the byte-identical decision path a live session takes,
minus the wire call. live (armed only): the same intent goes through the
clob isolation layer and the ack/error is recorded next to it.

The engine re-checks the arming gate EVERY cycle: pulling keys from the
keyring or unsetting GRIDBOT_LIVE mid-flight drops it back to dry_run on
the next tick. It never mutates arming state itself (iron rail 3).

Run: uv run python -m gridbot.executor.engine [--once]
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from gridbot import config
from gridbot.executor import clob, limits
from gridbot.executor.intents import build_intent
from gridbot.instruments.shadow import Follow

log = logging.getLogger("gridbot.executor")

CYCLE_S = 2.0


class IntentLedger:
    """Append-only day-file ledger under data/executor/."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or config.DATA_DIR) / "executor"
        self.root.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict) -> None:
        day = f"{datetime.now(UTC):%Y%m%d}"
        path = self.root / f"intents-{day}.jsonl"
        with open(path, "a") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")


class Engine:
    """One instance-file pass per cycle; dedup within process lifetime."""

    def __init__(self, ledger: IntentLedger | None = None) -> None:
        self.ledger = ledger or IntentLedger()
        self.follow = Follow(config.DATA_DIR / "shadow", "instances", from_start=False)
        self.seen: set[str] = set()
        self.open_count = 0

    def handle(self, record: dict, armed: limits.ArmingState) -> str | None:
        """Process one instance record; returns the outcome status or None."""
        if record.get("type") != "instance":
            return None
        if str(record.get("series_id")) in config.GRID_TEST_SERIES_IDS:
            return None
        intent = build_intent(record, limits.max_stake_usd())
        if intent is None:
            return None
        if intent.dedup_key in self.seen:
            return None
        self.seen.add(intent.dedup_key)
        if self.open_count >= limits.max_open_positions():
            status = "skipped_max_open"
        elif armed.live:
            status = clob.place(intent)
            if status == "placed":
                self.open_count += 1
        else:
            status = "dry_run"
        row = intent.record()
        row.update({"status": status, "ts_ns": time.time_ns(), "live": armed.live})
        self.ledger.append(row)
        log.info("intent %s %s @%.4f x%.2f -> %s", intent.hypothesis_id,
                 intent.team, intent.price, intent.shares, status)
        return status

    def cycle(self) -> int:
        armed = limits.check_armed()
        n = 0
        for record in self.follow.lines():
            try:
                if self.handle(record, armed):
                    n += 1
            except Exception:
                log.exception("bad instance record skipped")
        return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="single 30s pass then exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    armed = limits.check_armed()
    mode = "LIVE (user-armed)" if armed.live else "dry_run"
    log.info("executor starting: %s; stake cap $%.2f; max open %d",
             mode, limits.max_stake_usd(), limits.max_open_positions())
    for reason in armed.reasons:
        log.info("  dry_run because: %s", reason)

    engine = Engine()
    deadline = time.monotonic() + 30 if args.once else None
    while True:
        engine.cycle()
        if deadline and time.monotonic() > deadline:
            break
        time.sleep(CYCLE_S)


if __name__ == "__main__":
    main()
