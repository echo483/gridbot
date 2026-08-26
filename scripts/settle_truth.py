"""Read-only venue truth pull: what actually happened to our placed orders.

Joins the intent ledger's placed order ids against the venue's order
records, portfolio activities (fills + settlements), open positions, and
cash balance. GET-only — never places, modifies, or cancels anything.

Run: uv run python scripts/settle_truth.py [YYYYMMDD]
Writes: data/executor/settle_truth-<day>.json (summary, no secrets)
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gridbot import config  # noqa: E402


def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else f"{datetime.now(UTC):%Y%m%d}"
    ledger_path = config.DATA_DIR / "executor" / f"intents-{day}.jsonl"
    rows = [json.loads(l) for l in ledger_path.read_text().splitlines() if l.strip()]
    placed = [r for r in rows if r.get("type") == "order_intent" and r.get("status") == "placed"]
    print(f"ledger {day}: {len(placed)} placed orders")

    from polymarket_us import PolymarketUS

    client = PolymarketUS(
        key_id=config.require("POLYMARKET_US_KEY_ID"),
        secret_key=config.require("POLYMARKET_US_SECRET_KEY"),
        timeout=15.0,
    )

    orders_out = []
    for r in placed:
        oid = str(r.get("order_id"))
        try:
            resp = client.orders.retrieve(oid)
        except Exception as exc:  # noqa: BLE001 — report the gap, keep pulling
            orders_out.append({"order_id": oid, "error": type(exc).__name__})
            continue
        o = (resp or {}).get("order") or resp or {}
        orders_out.append({
            "order_id": oid,
            "slug": r.get("market_slug"),
            "us_side": r.get("us_side"),
            "ledger_price": r.get("price"),
            "ledger_qty": r.get("shares"),
            "venue": {k: o.get(k) for k in (
                "status", "state", "price", "quantity", "cumQuantity",
                "filledQuantity", "avgPrice", "averagePrice", "side",
                "intent", "createdAt", "updatedAt") if o.get(k) is not None},
        })

    def _pull(name, fn):
        try:
            return fn() or {}
        except Exception as exc:  # noqa: BLE001
            print(f"{name} pull failed: {type(exc).__name__}")
            return {"error": type(exc).__name__}

    activities = _pull("activities", lambda: client.portfolio.activities())
    positions = _pull("positions", lambda: client.portfolio.positions())
    balances = _pull("balances", lambda: client.account.balances())

    out = {
        "day": day,
        "pulled_at": datetime.now(UTC).isoformat(),
        "orders": orders_out,
        "activities": activities,
        "positions": positions,
        "balances": balances,
    }
    out_path = config.DATA_DIR / "executor" / f"settle_truth-{day}.json"
    out_path.write_text(json.dumps(out, indent=1, default=str))
    print(f"wrote {out_path}")

    filled = [o for o in orders_out
              if any(float(o.get("venue", {}).get(k) or 0) > 0
                     for k in ("cumQuantity", "filledQuantity"))]
    print(f"\norders with venue-reported fills: {len(filled)}")
    for o in filled:
        print(" ", o["order_id"], o["slug"], o["us_side"],
              "ledger", o["ledger_price"], "x", o["ledger_qty"], "|", o["venue"])


if __name__ == "__main__":
    main()
