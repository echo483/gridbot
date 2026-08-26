"""Polymarket US esports watch lane — public gateway, no auth, no orders.

Polls gateway.polymarket.us for active events in the esports series (cs2,
league-of-legends, call-of-duty), records events + per-market BBO to
data/pmus/ day files, and logs loudly when esports events first appear
(they were provisioned-but-empty on 2026-08-25). Separate from the capture
supervisor on purpose: capture continuity outranks refactors.

Deterministic, read-only against the venue. Run by gridbot-pmus.timer.
Run: uv run python scripts/pmus_watch.py [--once]
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime

from polymarket_us import PolymarketUS

from gridbot import config

ESPORTS_SERIES = {"cs2", "league-of-legends", "call-of-duty"}
PAGE_LIMIT = 100
MAX_PAGES = 8
OUT_DIR = config.DATA_DIR / "pmus"


def _append(record: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    day = f"{datetime.now(UTC):%Y%m%d}"
    with open(OUT_DIR / f"watch-{day}.jsonl", "a") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")


def esports_events(client: PolymarketUS) -> list[dict]:
    """Active, open events whose seriesSlug is an esports series."""
    found: list[dict] = []
    for page in range(MAX_PAGES):
        resp = client.events.list(
            {"limit": PAGE_LIMIT, "offset": page * PAGE_LIMIT, "active": True, "closed": False}
        )
        events = (resp or {}).get("events") or []
        found.extend(e for e in events if e.get("seriesSlug") in ESPORTS_SERIES)
        if len(events) < PAGE_LIMIT:
            break
    return found


def snapshot(client: PolymarketUS) -> dict:
    """One watch pass: events + BBO for each esports market found."""
    events = esports_events(client)
    n_markets = 0
    for event in events:
        _append({"type": "pmus_event", "ts_ns": time.time_ns(), "event": event})
        for market in event.get("markets") or []:
            slug = market.get("slug")
            if not slug:
                continue
            try:
                bbo = client.markets.bbo(slug)
            except Exception as exc:  # noqa: BLE001 — record the miss, keep going
                bbo = {"error": type(exc).__name__}
            _append({"type": "pmus_bbo", "ts_ns": time.time_ns(), "slug": slug, "bbo": bbo})
            n_markets += 1
    return {"events": len(events), "markets": n_markets}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="single pass (timer mode)")
    args = ap.parse_args()
    client = PolymarketUS()  # public gateway only — no credentials
    result = snapshot(client)
    stamp = f"{datetime.now(UTC):%H:%M}"
    if result["events"]:
        print(f"🎮 {stamp} PMUS ESPORTS LIVE: {result['events']} events / {result['markets']} markets")
    else:
        print(f"{stamp} pmus watch: no active esports events (series still empty)")
    if not args.once:
        print("(continuous mode not implemented — run via timer)")


if __name__ == "__main__":
    main()
