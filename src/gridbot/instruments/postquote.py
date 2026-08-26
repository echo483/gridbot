"""Post-quote oriented drift sampler for H-MAPENTRY1 fill-quality diagnosis.

Adverse-selection instrument (mechanism credit: cs2agent H-MAKER-ANCHOR1 /
H-SCALE1): a passive quote at fair-4pts gets lifted preferentially when the
book is about to move through it, so realized fills can be negative while
unconditional Brier looks excellent. Detection: for every QUALIFYING mapentry
row, sample the same book's top at +30/+60/+120s and record the mid move
ORIENTED to the side we would take. Filled-vs-unfilled splits join later via
the executor intents ledger (dedup identity fields are carried through).

Read-only toward the trade path: tails data/shadow/mapentry-*.jsonl, polls
the public BBO gateway (no keys), writes data/shadow/postquote-*.jsonl.
Pooling rule for analysis: NEVER pool unoriented moves across mixed sides --
that statistic collapses toward zero regardless of the true effect.
"""

from __future__ import annotations

import argparse
import heapq
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from gridbot import config
from gridbot.instruments.mapentry_recorder import BboCache
from gridbot.instruments.shadow import Follow

log = logging.getLogger("gridbot.postquote")

SAMPLE_OFFSETS_S: tuple[float, ...] = (30.0, 60.0, 120.0)
POLL_INTERVAL_S = 2.0
MAX_PENDING = 600  # ~50 concurrent quotes; far above any realistic night


def mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def oriented(us_side: str, mid0: float | None, mid1: float | None) -> float | None:
    """Mid move signed so that + means the book moved WITH the taken side."""
    if mid0 is None or mid1 is None:
        return None
    delta = float(mid1) - float(mid0)
    return delta if us_side == "long" else -delta


def samples_from_row(row: dict, now_mono: float) -> list[tuple[float, dict]]:
    """(due_mono, spec) heap entries for one mapentry row; [] if not sampleable.

    Only QUALIFYING rows with an attached book are sampled -- those are the
    rows that become (or would become) resting orders.
    """
    if row.get("type") != "mapentry" or not row.get("qualifies"):
        return []
    slug = row.get("market_slug")
    us_side = row.get("us_side")
    mid0 = mid(row.get("bid"), row.get("ask"))
    if not slug or us_side not in ("long", "short") or mid0 is None:
        return []
    spec = {
        "slug": slug,
        "us_side": us_side,
        "mid0": round(mid0, 4),
        "src_ts_ns": row.get("ts_ns"),
        "series_id": row.get("series_id"),
        "map_number": row.get("map_number"),
        "dedup_extra": row.get("dedup_extra", ""),
    }
    return [(now_mono + dt, {**spec, "dt_s": dt}) for dt in SAMPLE_OFFSETS_S]


class Sampler:
    def __init__(self, books: BboCache | None = None,
                 out_dir: Path | None = None,
                 shadow_dir: Path | None = None,
                 from_start: bool = False) -> None:
        self.books = books or BboCache()
        self.out_dir = out_dir or (config.DATA_DIR / "shadow")
        self.follow = Follow(shadow_dir or (config.DATA_DIR / "shadow"),
                             "mapentry", from_start=from_start)
        self.pending: list[tuple[float, int, dict]] = []
        self._seq = 0  # heap tiebreak; specs are dicts and unorderable

    def _append(self, row: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        day = f"{datetime.now(UTC):%Y%m%d}"
        with open(self.out_dir / f"postquote-{day}.jsonl", "a") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")

    def poll_once(self, now_mono: float) -> int:
        """Ingest new mapentry rows, take due samples; returns samples written."""
        for row in self.follow.lines():
            for due, spec in samples_from_row(row, now_mono):
                if len(self.pending) >= MAX_PENDING:
                    log.warning("pending cap hit; dropping oldest sample")
                    heapq.heappop(self.pending)
                self._seq += 1
                heapq.heappush(self.pending, (due, self._seq, spec))
        written = 0
        while self.pending and self.pending[0][0] <= now_mono:
            _, _, spec = heapq.heappop(self.pending)
            bid, ask = self.books.top(spec["slug"])
            m1 = mid(bid, ask)
            move = oriented(spec["us_side"], spec["mid0"], m1)
            self._append({
                "type": "postquote",
                **spec,
                "bid": bid,
                "ask": ask,
                "mid": None if m1 is None else round(m1, 4),
                "oriented_move": None if move is None else round(move, 4),
                "ts_ns": time.time_ns(),
            })
            written += 1
        return written

    def run(self) -> None:
        log.info("postquote sampler armed (offsets %s)", SAMPLE_OFFSETS_S)
        while True:
            self.poll_once(time.monotonic())
            time.sleep(POLL_INTERVAL_S)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true",
                        help="single poll cycle (smoke test) then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sampler = Sampler()
    if args.once:
        n = sampler.poll_once(time.monotonic())
        log.info("once: %d samples written, %d pending", n, len(sampler.pending))
        return
    sampler.run()


if __name__ == "__main__":
    main()
