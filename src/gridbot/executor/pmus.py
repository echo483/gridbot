"""Polymarket US wire layer — the ONLY module that may touch trading keys.

Replaces the retired international-CLOB layer (clob.py): that venue is
close-only for US IPs and its v1 client died at the 2026-04-28 cutover.
This targets the official Polymarket US API (api.polymarket.us) through the
official `polymarket-us` SDK: Ed25519 key pair from polymarket.us/developer,
market slugs, integer contract quantities, and — matching our frozen maker
contract exactly — `participateDontInitiate` (exchange-enforced post-only).

The client is built lazily, only after the arming gate has passed; keyring
values go straight into the constructor and are never logged. The wire call
is one small function for joint review before the first live order.
"""

from __future__ import annotations

import logging
import time

from gridbot import config
from gridbot.executor.intents import OrderIntent

log = logging.getLogger("gridbot.executor.pmus")

_client = None


def _get_client():
    """Build (once) the authenticated Polymarket US client from the keyring."""
    global _client
    if _client is None:
        from polymarket_us import PolymarketUS

        _client = PolymarketUS(
            key_id=config.require("POLYMARKET_US_KEY_ID"),
            secret_key=config.require("POLYMARKET_US_SECRET_KEY"),
            timeout=10.0,  # M4: a hung call must not freeze the engine loop
        )
    return _client


def reset_client() -> None:
    """Drop the cached client (auth-staleness recovery)."""
    global _client
    _client = None


def wire_price(cost: float, side: str) -> float:
    """The venue's ``price`` for an intent that pays ``cost`` per contract.

    PRICE CONVENTION (pinned 2026-09-03 from venue records —
    data/executor/settle_truth-20260827.json, order C4KWZ5EV8GNE): the
    Polymarket US ``price`` field is ALWAYS the Yes/long price. A
    ``BUY_SHORT`` at price P is stored as ``ORDER_SIDE_SELL`` @ P and costs
    (1 - P) per contract; its fill ``avgPx`` is likewise the Yes price.
    Everywhere in gridbot (``side_ask``, ``entry_price_max``,
    ``OrderIntent.price``, ledger ``price``) a price is OUR side's cost —
    the number the frozen entry rule and the stake cap approved — so the
    wire converts here and nowhere else: long sends ``cost``, short sends
    ``1 - cost``.

    Rounding never raises our cost above what the intent approved (M3):
    the cost is floored to the venue's 0.001 grid, which for a short means
    the Yes price rounds UP. Before this conversion existed every short was
    posted at the Yes price ``cost`` instead of ``1 - cost``: 15 of 24
    armed-night shorts (every one with cost < 0.50, i.e. Yes bid >= 0.50)
    crossed the book and were REJECTED by post-only while the ledger said
    placed; the 9 that rested sat 2*(cost-0.5) further from the touch than
    intended.
    """
    thousandths = int(cost * 1000 + 1e-9)  # floor on the 0.001 grid
    if side == "long":
        return thousandths / 1000
    return (1000 - thousandths) / 1000


def _venue_rejection(resp: dict) -> str | None:
    """Reject reason when the create response proves NOTHING rested.

    ``CreateOrderResponse`` = ``{id, executions: [Execution]}``; a post-only
    order that would cross (stale book between the BBO read and the wire)
    comes back with an ``EXECUTION_TYPE_REJECTED`` execution and an order
    id that never rested. Recording that as "placed" consumed the book's
    dedup key and counted a phantom order against the caps until the fills
    sync retrieved it terminal (armed nights 08-26/27: 20/48 orders).

    Rejected ONLY when every execution is a rejection (reasoner review
    2026-09-03, F4a): a mixed list — NEW + REJECTED, or a partial fill and
    a rejected remainder — may have contracts live on venue, and a
    "rejected" verdict would drop that order from the TTL sweep, the fills
    sync and the caps. Anything ambiguous falls through to "placed", which
    the venue-truth pass resolves (terminal-zero-fill releases the slot).
    """
    execs = [ex for ex in resp.get("executions") or [] if isinstance(ex, dict)]
    if not execs or any(ex.get("type") != "EXECUTION_TYPE_REJECTED" for ex in execs):
        return None
    return str(execs[0].get("orderRejectReason") or "unspecified")


def place(intent: OrderIntent) -> tuple[str, str | None]:
    """Place one capped post-only maker order. Returns (status, order_id).

    Statuses: "placed", "rejected:<detail>", "error:<Class>". order_id is
    non-None for "placed" (the ledger persists it so cancels and venue-side
    reconciliation can reference the exact resting order) and for a
    synchronous venue rejection ("rejected:venue:<reason>" — nothing rests,
    the id is kept for forensics only). The intent must carry a US market
    slug (`market_slug`) and side (`us_side` in {"long", "short"}); anything
    ambiguous is refused — fail closed. ``intent.price`` is OUR side's cost;
    see :func:`wire_price` for the venue's Yes-price convention. Never
    raises.
    """
    slug = getattr(intent, "market_slug", None) or ""
    side = getattr(intent, "us_side", None)
    if not slug or side not in ("long", "short"):
        return "rejected:no_us_market_mapping", None
    quantity = int(intent.shares)
    if quantity < 1:
        return "rejected:quantity_below_one_contract", None
    price = wire_price(float(intent.price), side)
    body = {
        "marketSlug": slug,
        "intent": "ORDER_INTENT_BUY_LONG" if side == "long" else "ORDER_INTENT_BUY_SHORT",
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": f"{price:.3f}", "currency": "USD"},
        "quantity": quantity,
        "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
        "participateDontInitiate": True,
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
    }
    # S1: NEVER retry a create — an ambiguous failure may mean the exchange
    # accepted the order, and a resend would double-place. The engine dedups
    # per intent, so returning an error here is final for this intent; a
    # stale client is rebuilt for the NEXT intent via reset_client().
    try:
        resp = _get_client().orders.create(body)
    except Exception as exc:
        name = type(exc).__name__
        if "Auth" in name:
            reset_client()
        log.warning("order error: %s (not retried — creates are never resent)", name)
        return f"error:{name}", None
    data = resp or {}
    raw_id = data.get("orderId") or data.get("id") or (data.get("order") or {}).get("id")
    order_id = str(raw_id) if raw_id else None
    reason = _venue_rejection(data)
    if reason is not None:
        log.warning("venue rejected order synchronously: %s (never rested)", reason)
        return f"rejected:venue:{reason}", order_id
    if order_id:
        return "placed", order_id
    # S2: a 2xx we cannot parse is NOT a rejection — the order may be live.
    # Flag distinctly so reconciliation (orders.list) can find orphans.
    log.warning("ambiguous create ack: no order id in response")
    return "error:ambiguous_ack", None


def cancel(order_id: str, market_slug: str) -> str:
    """Cancel one resting order. Unlike creates, cancels ARE retried —
    cancelling is idempotent and abandoning a stale quote is the worse
    failure (B1). Returns:

    - "cancelled": the venue accepted the cancel; the order rested unfilled
      (or the venue treats cancel-after-fill as success — reconcile checks).
    - "gone": the venue no longer had the order — most likely it FILLED.
      Callers must NOT release exposure on this.
    - "error:cancel_failed": three attempts failed; the order may still be
      resting. Callers keep it tracked and retry next sweep.
    """
    for attempt in range(3):
        try:
            _get_client().orders.cancel(order_id, {"marketSlug": market_slug})
            return "cancelled"
        except Exception as exc:
            name = type(exc).__name__
            if "NotFound" in name or "404" in str(exc)[:120]:
                return "gone"
            if "Auth" in name:
                reset_client()
            log.warning("cancel %s attempt %d failed: %s", order_id, attempt + 1, name)
            time.sleep(0.5 * (attempt + 1))
    return "error:cancel_failed"
