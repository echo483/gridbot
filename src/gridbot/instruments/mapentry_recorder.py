"""H-MAPENTRY1 recorder: tail shadow instances, attach PMUS map books.

Dry-run instrument — records evidence rows ONLY, never places orders and
never touches credentials: book tops come from the public gateway
(unauthenticated PolymarketUS client). Each shadow instance is joined to
the latest GRID<->PMUS mapping snapshot, the live map's winner book is
fetched at detection, the frozen mapwin-race-v1 fair value is computed and
the row (including unmapped/out-of-domain skips — the starvation clause
needs the full denominator) is appended to data/shadow/mapentry-YYYYMMDD.jsonl.

Run: uv run python -m gridbot.instruments.mapentry_recorder [--once] [--replay]
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime

from gridbot import config
from gridbot.instruments import mapentry
from gridbot.instruments.shadow import Follow

log = logging.getLogger("gridbot.mapentry")

CYCLE_S = 2.0
MAPPING_RELOAD_S = 60.0
BBO_TTL_S = 5.0


class BboCache:
    """Tiny per-slug TTL cache so instance bursts don't hammer the gateway."""

    def __init__(self, client=None, ttl: float = BBO_TTL_S) -> None:
        self._client = client
        self._ttl = ttl
        self._cache: dict[str, tuple[float, tuple[float | None, float | None]]] = {}

    def _get_client(self):
        if self._client is None:
            from polymarket_us import PolymarketUS

            self._client = PolymarketUS()  # public endpoints only — no keys
        return self._client

    def top(self, slug: str) -> tuple[float | None, float | None]:
        now = time.monotonic()
        hit = self._cache.get(slug)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        try:
            top = mapentry.parse_bbo(self._get_client().markets.bbo(slug))
        except Exception as exc:  # noqa: BLE001 — a book fetch must never kill the tail
            log.warning("bbo fetch failed for %s: %s", slug, type(exc).__name__)
            top = (None, None)
        self._cache[slug] = (now, top)
        return top


class Recorder:
    def __init__(self, books: BboCache | None = None) -> None:
        self.books = books or BboCache()
        self.out_dir = config.DATA_DIR / "shadow"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.mappings: dict[str, dict] = {}
        self._mappings_loaded = 0.0
        self.seen: set[tuple] = set()

    def _reload_mappings(self) -> None:
        if time.monotonic() - self._mappings_loaded < MAPPING_RELOAD_S:
            return
        self.mappings = mapentry.load_latest_mappings(config.DATA_DIR / "pmus")
        self._mappings_loaded = time.monotonic()

    def _append(self, row: dict) -> None:
        day = f"{datetime.now(UTC):%Y%m%d}"
        with open(self.out_dir / f"mapentry-{day}.jsonl", "a") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")

    def handle(self, record: dict) -> str | None:
        """Process one shadow record; returns the row's disposition or None."""
        if record.get("type") != "instance":
            return None
        if str(record.get("series_id")) in config.GRID_TEST_SERIES_IDS:
            return None
        key = (
            record.get("hypothesis_id"),
            record.get("series_id"),
            record.get("map_number"),
            record.get("round_number"),
            record.get("dedup_extra", ""),
        )
        if key in self.seen:
            return None
        self.seen.add(key)

        row = mapentry.build_row(record, ts_ns=time.time_ns())
        if row["p_map"] is None:
            row["skip_reason"] = (
                "no_round_probability" if row["q_round"] is None
                else "no_scores" if row["a"] is None
                else "overtime_domain"
            )
        else:
            mapping = self.mappings.get(str(record.get("series_id")))
            if mapping is None:
                row["skip_reason"] = "no_pmus_mapping"
            else:
                picked = mapentry.market_for_instance(
                    mapping,
                    int(record.get("map_number") or 0),
                    str(record.get("team") or ""),
                    str(record.get("opponent") or ""),
                )
                if picked is None:
                    row["skip_reason"] = "no_map_book"
                else:
                    slug, us_side = picked
                    bid, ask = self.books.top(slug)
                    mapentry.attach_book(row, slug, us_side, bid, ask)
                    if bid is None and ask is None:
                        row["skip_reason"] = "empty_book"
        self._append(row)
        return row["skip_reason"] or ("qualifies" if row["qualifies"] else "recorded")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    ap.add_argument(
        "--replay", action="store_true",
        help="read existing day files from the start (smoke/backfill)",
    )
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    rec = Recorder()
    follow = Follow(config.DATA_DIR / "shadow", "instances", from_start=args.replay)
    log.info("mapentry recorder up (replay=%s)", args.replay)
    while True:
        rec._reload_mappings()
        n = 0
        counts: dict[str, int] = {}
        for record in follow.lines():
            try:
                disposition = rec.handle(record)
            except Exception:
                log.exception("bad instance skipped")
                continue
            if disposition:
                n += 1
                counts[disposition] = counts.get(disposition, 0) + 1
        if n:
            log.info("recorded %d mapentry rows: %s", n, counts)
        if args.once:
            break
        time.sleep(CYCLE_S)


if __name__ == "__main__":
    main()
