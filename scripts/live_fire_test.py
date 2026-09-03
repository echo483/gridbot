"""First-fire plumbing test: ONE tiny post-only order, verified, canceled.

Validates the last untestable inch of the wire path — place → ack → appears
in open orders → cancel → confirmed gone — with a far-from-touch post-only
limit so a fill is effectively impossible, and measures each leg's latency.

This is a PLUMBING test, not a strategy: no instrument, no hypothesis, no
registry claim. It runs the same pmus wire layer the engine uses.

Gating (iron rail 3): without the user-armed gate (GRIDBOT_LIVE=1 + keyring
keys + compliance record) it stops after the server-side PREVIEW — which
already validates auth, schema, and margin without creating anything.

SIDE CHECK (added 2026-09-03, registry H-MAPENTRY1-NOTE7): the 08-25 first
fire was long-only, and the venue's price convention — ``price`` is ALWAYS the
Yes/long price, a BUY_SHORT at P is stored as SELL @ P and costs 1-P — went
unverified while every armed-night short was posted at the wrong level.
``--side short`` places the far-from-touch order through the SAME conversion
the engine uses (pmus.wire_price) and then reads the order back: the venue
must report ORDER_SIDE_SELL at exactly the Yes price we computed. Run BOTH
sides before any armed session that can short.

Run: uv run python scripts/live_fire_test.py [--stake 1.0] [--side long|short]
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass

import keyring

from gridbot.executor import limits
from gridbot.executor.pmus import wire_price

FAR_FRACTION = 0.5  # quote halfway from the touch to the far end — unfillable by design
MAX_STAKE = 2.0     # this script never risks more than $2 regardless of args


@dataclass(frozen=True)
class FarQuote:
    side: str
    cost: float        # what WE pay per contract (gridbot's price convention)
    wire_price: float  # the venue's Yes-price field for that cost


def far_quote(side: str, bid: float, ask: float, px: float) -> FarQuote:
    """An unfillable post-only quote for ``side``, in both conventions.

    long: buy Yes at half the best bid (floored to a cent) — strictly below
    the bid or refuse. short: SELL Yes halfway from the best ask to 1.00
    (ceiled to a cent) — strictly above the ask or refuse; our cost is
    1 - that Yes price, and the wire value is derived through the engine's
    own conversion so this test exercises the real path.
    """
    if side == "long":
        cost = max(0.01, int(min(bid, px) * FAR_FRACTION * 100) / 100)
        if cost >= bid:
            raise SystemExit(f"refusing: long quote {cost:.2f} not strictly below bid {bid:.2f}")
        return FarQuote("long", cost, wire_price(cost, "long"))
    yes_price = min(0.99, math.ceil((ask + (1.0 - ask) * FAR_FRACTION) * 100 - 1e-9) / 100)
    if yes_price <= ask:
        raise SystemExit(f"refusing: short Yes price {yes_price:.2f} not strictly above ask {ask:.2f}")
    cost = round(1.0 - yes_price, 4)
    return FarQuote("short", cost, wire_price(cost, "short"))


def _client():
    from polymarket_us import PolymarketUS

    return PolymarketUS(
        key_id=keyring.get_password("gridbot", "POLYMARKET_US_KEY_ID"),
        secret_key=keyring.get_password("gridbot", "POLYMARKET_US_SECRET_KEY"),
    )


def pick_liquid_market(c) -> tuple[str, float, float, float]:
    """A liquid active market: (slug, current price, best BID, best ASK).

    The far-from-touch quote must anchor on the actual touch (review S4):
    on a thin book currentPx can sit far above the bid, and a quote derived
    from currentPx alone could land AT the touch.
    """
    resp = c.events.list({"limit": 40, "active": True, "closed": False})
    for ev in resp.get("events") or []:
        for m in ev.get("markets") or []:
            if not (m.get("active") and not m.get("closed") and m.get("slug")):
                continue
            bbo = c.markets.bbo(m["slug"])
            data = bbo.get("marketData") or {}
            px = float((data.get("currentPx") or {}).get("value") or 0)
            bid = float((data.get("bestBid") or data.get("bidPx") or {}).get("value") or 0)
            ask = float((data.get("bestAsk") or data.get("askPx") or {}).get("value") or 0)
            if 0.10 <= px <= 0.90 and bid >= 0.05 and ask > bid:
                return m["slug"], px, bid, ask
    raise SystemExit("no liquid two-sided market found for the test")


def order_body(slug: str, price: float, quantity: int, side: str = "long") -> dict:
    return {
        "marketSlug": slug,
        "intent": "ORDER_INTENT_BUY_LONG" if side == "long" else "ORDER_INTENT_BUY_SHORT",
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": f"{price:.3f}", "currency": "USD"},
        "quantity": quantity,
        "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
        "participateDontInitiate": True,
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stake", type=float, default=1.0, help="test stake in USD (hard cap $2)")
    ap.add_argument("--side", choices=["long", "short"], default="long",
                    help="which side to fire; run BOTH before an armed session that can short")
    args = ap.parse_args()
    stake = min(max(args.stake, 0.1), MAX_STAKE)

    c = _client()
    slug, px, bid, ask = pick_liquid_market(c)
    fq = far_quote(args.side, bid, ask, px)
    quote = fq.wire_price           # what the venue sees (Yes price)
    quantity = max(1, int(stake / fq.cost))
    print(f"market: {slug} (px {px:.3f} bid {bid:.3f} ask {ask:.3f}) — {fq.side} "
          f"cost {fq.cost:.2f} → wire {quote:.3f} x{quantity} "
          f"(${fq.cost * quantity:.2f} at risk, far-from-touch post-only)")

    t0 = time.monotonic()
    c.orders.preview({"request": order_body(slug, quote, quantity, fq.side)})
    print(f"[1] preview OK in {(time.monotonic() - t0) * 1000:.0f}ms "
          f"(margin path validated, nothing created)")

    armed = limits.check_armed()
    if armed.dry_run:
        print("\nSTOPPING at preview — not armed (this is the designed gate):")
        for r in armed.reasons:
            print(f"  - {r}")
        print("To run the full place->cancel test, the user sets GRIDBOT_LIVE=1.")
        return

    t1 = time.monotonic()
    resp = c.orders.create(order_body(slug, quote, quantity, fq.side))
    order = (resp or {}).get("order") or {}
    order_id = (resp or {}).get("orderId") or (resp or {}).get("id") or order.get("id")
    print(f"[2] place ack in {(time.monotonic() - t1) * 1000:.0f}ms -> id={order_id}")
    if not order_id:
        print("AMBIGUOUS ACK — no order id. Check open orders manually before rerunning:")
        print(json.dumps(resp)[:400])
        return
    cum = order.get("cumQuantity") or 0
    if cum:
        print(f"NOTE: {cum} contracts filled on entry despite post-only — reporting honestly")

    # CONVENTION CHECK (NOTE7): read the order back and compare what the venue
    # stored with what the conversion intended. A short must be SELL at
    # exactly our Yes price; any mismatch is reported before the cancel.
    try:
        got = (c.orders.retrieve(order_id) or {}).get("order") or {}
        v_side, v_px = got.get("side"), ((got.get("price") or {}).get("value"))
        want_side = "ORDER_SIDE_BUY" if fq.side == "long" else "ORDER_SIDE_SELL"
        ok = v_side == want_side and v_px is not None and abs(float(v_px) - quote) < 1e-6
        print(f"[2b] venue stored side={v_side} price={v_px} state={got.get('state')} -> "
              f"{'CONVENTION VERIFIED' if ok else 'CONVENTION MISMATCH — do not arm shorts'}")
    except Exception as exc:  # noqa: BLE001 — the cancel below must still run
        print(f"[2b] order retrieve failed: {type(exc).__name__} (cancel proceeds)")

    t2 = time.monotonic()
    listed = any(
        (o.get("id") == order_id) for o in (c.orders.list() or {}).get("orders") or []
    )
    print(f"[3] visible in open orders: {listed} ({(time.monotonic() - t2) * 1000:.0f}ms)")

    # B1: cancel is the one leg that MUST NOT silently fail — a resting live
    # order would be abandoned. Retry with backoff; on final failure print a
    # manual-action block with everything needed to cancel by hand.
    t3 = time.monotonic()
    canceled = False
    for attempt in range(3):
        try:
            c.orders.cancel(order_id, {"marketSlug": slug})
            canceled = True
            break
        except Exception as exc:  # noqa: BLE001 — retrying is the whole point here
            print(f"[4] cancel attempt {attempt + 1} failed: {type(exc).__name__}")
            time.sleep(1.5 * (attempt + 1))
    if canceled:
        print(f"[4] cancel ack in {(time.monotonic() - t3) * 1000:.0f}ms")
    else:
        print("=" * 60)
        print("MANUAL ACTION REQUIRED — cancel failed 3x; order may be RESTING:")
        print(f"  order id: {order_id}\n  market:   {slug}")
        print(f"  quote:    {fq.side} wire {quote:.3f} x{quantity} (max exposure ${fq.cost * quantity:.2f})")
        print("  cancel in the app, or: client.orders.cancel(id, {'marketSlug': slug})")
        print("=" * 60)

    t4 = time.monotonic()
    still = any(
        (o.get("id") == order_id) for o in (c.orders.list() or {}).get("orders") or []
    )
    print(f"[5] confirmed gone: {not still} ({(time.monotonic() - t4) * 1000:.0f}ms)")
    if canceled and not still:
        print("\nfull wire path verified: preview -> place -> list -> cancel -> gone")
    else:
        print("\nwire path INCOMPLETE — see messages above before any further orders")


if __name__ == "__main__":
    main()
