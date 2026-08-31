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
# Bounds the synchronous gateway fetch in the single-threaded eval loop. The
# library's defaults are 30s; a hung/slow gateway (book or bbo) must not stall
# the recorder for that long. A short timeout fails the call closed (empty book,
# gate unaffected) instead of blocking the tail. See F2.
GATEWAY_TIMEOUT_S = 4.0


class BboCache:
    """Tiny per-slug TTL cache so instance bursts don't hammer the gateway."""

    def __init__(self, client=None, ttl: float = BBO_TTL_S) -> None:
        self._client = client
        self._ttl = ttl
        self._cache: dict[str, tuple[float, tuple[float | None, float | None]]] = {}
        # Separate store for full-depth summaries so top() and book() never
        # collide on the same slug (a dict is not a bare bid/ask pair).
        self._deep: dict[str, tuple[float, dict]] = {}

    def _get_client(self):
        if self._client is None:
            from polymarket_us import PolymarketUS

            # Public endpoints only — no keys. Explicit short timeout so a hung
            # gateway fails closed fast instead of stalling the single-threaded
            # tail on the library default (30s). See GATEWAY_TIMEOUT_S.
            self._client = PolymarketUS(timeout=GATEWAY_TIMEOUT_S)
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

    def book(self, slug: str) -> dict:
        """Full-depth book summary (see mapentry.parse_book) with the same TTL.

        Shares the per-slug TTL budget but lives in its own cache slot so the
        bare ``top()`` result is unaffected. Returns an empty-shaped summary
        (all-None fields) if the fetch fails — never raises into the tail.
        """
        now = time.monotonic()
        hit = self._deep.get(slug)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        try:
            summary = mapentry.parse_book(self._get_client().markets.book(slug))
        except Exception as exc:  # noqa: BLE001 — a book fetch must never kill the tail
            log.warning("book fetch failed for %s: %s", slug, type(exc).__name__)
            summary = mapentry.parse_book({})
        self._deep[slug] = (now, summary)
        return summary


class Recorder:
    def __init__(self, books: BboCache | None = None, out_dir=None) -> None:
        self.books = books or BboCache()
        self.out_dir = out_dir or (config.DATA_DIR / "shadow")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.mappings: dict[str, dict] = {}
        self._mappings_loaded = 0.0
        self.seen: set[tuple] = set()
        self.slate = None  # optional CrossingTracker: served q for slate rows

    def handle_slate(self, record: dict) -> str | None:
        """Queue 16 full-slate coverage: one mapentry evaluation per coherent
        round snapshot of every live cs2 series, from series_state directly —
        a series that starts mid-session is evaluated from its first polled
        round even when no hypothesis trigger fires (the night-3 gap)."""
        if record.get("type") != "ss" or record.get("title") != "cs2":
            return None
        sid = str(record.get("series_id"))
        if sid in config.GRID_TEST_SERIES_IDS:
            return None
        state = (record.get("data") or {}).get("seriesState") or {}
        if not state.get("started") or state.get("finished"):
            return None
        q_ct = None
        if self.slate is not None:
            try:
                from gridbot.corpus import rows as r

                game = r.current_game(state)
                if game is not None:
                    q_ct = float(self.slate.predict(r.build_features(game)))
            except Exception as exc:  # noqa: BLE001 — a bad model fetch must not kill the tail
                log.warning("slate q fetch failed for %s: %s", sid, type(exc).__name__)
        instance = mapentry.slate_instance(
            state, q_ct=q_ct, grid_ts_ns=record.get("ts_ns") or record.get("req_ns")
        )
        if instance is None:
            return None
        return self.handle(instance)

    def _reload_mappings(self) -> None:
        if time.monotonic() - self._mappings_loaded < MAPPING_RELOAD_S:
            return
        self.mappings = mapentry.load_latest_mappings(config.DATA_DIR / "pmus")
        self._mappings_loaded = time.monotonic()

    def _append(self, row: dict) -> None:
        day = f"{datetime.now(UTC):%Y%m%d}"
        with open(self.out_dir / f"mapentry-{day}.jsonl", "a") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")

    def _safe_book(self, slug: str) -> dict | None:
        """Full-depth summary via ``books.book()`` if the client exposes it.

        Legacy book clients (e.g. tests' top-only fakes) must keep working —
        they simply yield no depth evidence. A fetch failure is logged and
        yields nothing; it must never kill the tail.
        """
        book = getattr(self.books, "book", None)
        if book is None:
            return None
        try:
            summary = book(slug)
        except Exception as exc:  # noqa: BLE001 — a book fetch must never kill the tail
            log.warning("book fetch failed for %s: %s", slug, type(exc).__name__)
            return None
        return summary if summary else None

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
                    mapentry.attach_book(row, slug, us_side, bid, ask, self._safe_book(slug))
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
    rec.slate = None
    try:
        from gridbot.instruments.crossing import load_served

        rec.slate = load_served()
    except Exception as exc:  # noqa: BLE001 — slate rows without q still count coverage
        log.warning("slate predictor unavailable: %s", exc)
    if rec.slate:
        log.info("full-slate coverage armed (roundwin-v1)")
    grid_follow = Follow(config.DATA_DIR / "grid", "series_state", from_start=args.replay)
    follow = Follow(config.DATA_DIR / "shadow", "instances", from_start=args.replay)
    log.info("mapentry recorder up (replay=%s)", args.replay)
    while True:
        rec._reload_mappings()
        n = 0
        counts: dict[str, int] = {}
        for record in grid_follow.lines():
            try:
                disposition = rec.handle_slate(record)
            except Exception:
                log.exception("bad ss record skipped")
                continue
            if disposition:
                n += 1
                counts[disposition] = counts.get(disposition, 0) + 1
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
