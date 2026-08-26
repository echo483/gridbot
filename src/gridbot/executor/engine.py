"""Executor engine: tail shadow instances, emit capped maker intents.

dry_run (default): every intent is appended to the intent ledger with
status "dry_run" — the byte-identical decision path a live session takes,
minus the wire call. live (armed only): the same intent goes through the
Polymarket US wire layer (pmus.py) and the ack/error is recorded next to it.

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
from gridbot.executor import ledger as ledger_mod
from gridbot.executor import limits, pmus, reconcile
from gridbot.executor.intents import build_intent, build_us_intent
from gridbot.instruments.shadow import Follow

log = logging.getLogger("gridbot.executor")

CYCLE_S = 2.0
RECONCILE_EVERY = 30  # armed only: venue orders.list vs ledger every ~60s
MAKER_TTL_S = 150.0  # M5: pull unfilled maker quotes — a fair-value quote
# older than ~2 rounds is stale against a live map and pure adverse selection


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
        # H-MAPENTRY1 lane: qualifying mapentry rows already carry the US
        # market slug/side + the frozen entry rule evaluated against a real
        # book — the executor only converts, caps and dedups them.
        self.follow_us = Follow(config.DATA_DIR / "shadow", "mapentry", from_start=False)
        self.seen: set[str] = set()
        self._cycles = 0
        # Orders THIS process placed and has not yet released: order_id ->
        # {slug, dedup_key, placed_mono}. The TTL sweep cancels them when
        # stale. (Orders from a previous armed session are the reconcile
        # pass's job — they show up as ledger-open vs venue drift.)
        self.resting: dict[str, dict] = {}
        # Open exposure = placed-but-unsettled intents, recomputed from the
        # ledger each cycle (S3: survives restarts, releases on settle —
        # never a process-local monotonic counter). Incremented in-cycle on
        # each new "placed" for immediacy between recomputes. Venue-side
        # reconciliation (orders.list) is a pre-scaling item: LIVE_FIRE.md.
        self.open_count = 0
        self._daily_loss = 0.0

    def handle(self, record: dict, armed: limits.ArmingState) -> str | None:
        """Process one shadow record; returns the outcome status or None."""
        kind = record.get("type")
        if kind not in ("instance", "mapentry"):
            return None
        if str(record.get("series_id")) in config.GRID_TEST_SERIES_IDS:
            return None
        if kind == "mapentry":
            intent = build_us_intent(record, limits.max_stake_usd())
        else:
            intent = build_intent(record, limits.max_stake_usd())
        if intent is None:
            return None
        if intent.dedup_key in self.seen:
            return None
        # Capacity skips do NOT consume the dedup key (M6): when open slots
        # or loss budget free up, the same instance may retry next cycle.
        if self.open_count >= limits.max_open_positions():
            log.debug("capacity hold (max_open) for %s", intent.dedup_key)
            return None
        if self._daily_loss >= limits.max_daily_loss_usd():
            log.debug("capacity hold (daily_loss) for %s", intent.dedup_key)
            return None
        self.seen.add(intent.dedup_key)
        order_id = None
        if armed.live:
            status, order_id = pmus.place(intent)
            if status == "placed":
                self.open_count += 1
                self.resting[str(order_id)] = {
                    "slug": intent.market_slug,
                    "dedup_key": intent.dedup_key,
                    "placed_mono": time.monotonic(),
                }
        else:
            status = "dry_run"
        row = intent.record()
        row.update(
            {
                "status": status,
                "order_id": order_id,
                "ts_ns": time.time_ns(),
                "live": armed.live,
            }
        )
        self.ledger.append(row)
        log.info("intent %s %s @%.4f x%.2f -> %s", intent.hypothesis_id,
                 intent.team, intent.price, intent.shares, status)
        return status

    def sweep_stale(self, now_mono: float) -> int:
        """M5: cancel this session's maker orders older than MAKER_TTL_S.

        "cancelled" writes a clean cancel row (releases the exposure slot);
        "gone" writes gone_before_cancel (likely a fill — slot stays held
        until settle/reconcile); a failed cancel stays tracked, logs a
        MANUAL ACTION line, and is retried every sweep.
        """
        n = 0
        for order_id in list(self.resting):
            info = self.resting[order_id]
            if now_mono - info["placed_mono"] < MAKER_TTL_S:
                continue
            outcome = pmus.cancel(order_id, info["slug"])
            if outcome == "error:cancel_failed":
                log.error(
                    "MANUAL ACTION REQUIRED: cancel failed for order %s on %s "
                    "(dedup %s) — order may still be resting; will retry next "
                    "sweep; hand-cancel in the Polymarket US app if it persists.",
                    order_id, info["slug"], info["dedup_key"],
                )
                continue
            status = "cancelled" if outcome == "cancelled" else "gone_before_cancel"
            self.ledger.append(
                {
                    "type": "cancel",
                    "order_id": order_id,
                    "dedup_key": info["dedup_key"],
                    "market_slug": info["slug"],
                    "status": status,
                    "age_s": round(now_mono - info["placed_mono"], 1),
                    "ts_ns": time.time_ns(),
                }
            )
            if outcome == "gone":
                log.warning(
                    "order %s was gone before cancel (likely FILLED) on %s — "
                    "exposure slot stays held pending settle/reconcile",
                    order_id, info["slug"],
                )
            del self.resting[order_id]
            n += 1
        return n

    def cycle(self) -> int:
        armed = limits.check_armed()
        try:
            records = ledger_mod.load_records(self.ledger.root)
            self._daily_loss = ledger_mod.daily_loss_usd(
                records, f"{datetime.now(UTC):%Y%m%d}"
            )
            self.open_count = ledger_mod.open_exposure(records)
        except Exception:
            log.exception("ledger read failed; keeping previous caps state")
        n = 0
        for follow in (self.follow, self.follow_us):
            for record in follow.lines():
                try:
                    if self.handle(record, armed):
                        n += 1
                except Exception:
                    log.exception("bad shadow record skipped")
        self._cycles += 1
        if armed.live:
            self.sweep_stale(time.monotonic())
        if armed.live and self._cycles % RECONCILE_EVERY == 0:
            try:
                report = reconcile.run(
                    ledger_mod.load_records(self.ledger.root), pmus._get_client()
                )
                self.ledger.append(
                    {"type": "reconcile", **report, "ts_ns": time.time_ns()}
                )
            except Exception:
                log.exception("reconcile pass failed (engine continues)")
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
