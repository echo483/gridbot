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
        )
    return _client


def reset_client() -> None:
    """Drop the cached client (auth-staleness recovery)."""
    global _client
    _client = None


def place(intent: OrderIntent) -> str:
    """Place one capped post-only maker BUY. Returns a status, never raises.

    Statuses: "placed", "rejected:<detail>", "error:<Class>". The intent
    must carry a US market slug (`market_slug`) and side (`us_side` in
    {"long", "short"}); anything ambiguous is refused — fail closed.
    """
    slug = getattr(intent, "market_slug", None) or ""
    side = getattr(intent, "us_side", None)
    if not slug or side not in ("long", "short"):
        return "rejected:no_us_market_mapping"
    quantity = int(intent.shares)
    if quantity < 1:
        return "rejected:quantity_below_one_contract"
    body = {
        "marketSlug": slug,
        "intent": "ORDER_INTENT_BUY_LONG" if side == "long" else "ORDER_INTENT_BUY_SHORT",
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": f"{intent.price:.3f}", "currency": "USD"},
        "quantity": quantity,
        "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
        "participateDontInitiate": True,
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
    }
    for attempt in (1, 2):
        try:
            resp = _get_client().orders.create(body)
            order_id = (resp or {}).get("orderId") or (resp or {}).get("id")
            if order_id:
                return "placed"
            detail = str((resp or {}).get("status", "unknown"))[:120]
            return f"rejected:{detail}"
        except Exception as exc:  # noqa: BLE001 — engine must survive any wire failure
            name = type(exc).__name__
            if attempt == 1 and "Auth" in name:
                reset_client()  # one rebuild on stale creds, then give up
                continue
            log.warning("order error: %s", name)
            return f"error:{name}"
    return "error:unreachable"
