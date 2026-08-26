"""Polymarket CLOB isolation layer — the ONLY module that may touch keys.

Everything above this layer works with OrderIntent values and never sees a
credential. The client is constructed lazily, only when the arming gate has
passed, and the private key goes straight from the keyring into the client
constructor — never into a variable that outlives construction, never logged.

The wire call is deliberately one small function so the joint live-fire
session reviews exactly one thing. Until POLYMARKET_* keys exist in the
keyring this module is import-safe and place() degrades to an error status.
"""

from __future__ import annotations

import logging

from gridbot import config
from gridbot.executor.intents import OrderIntent

log = logging.getLogger("gridbot.executor.clob")

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet

_client = None


def _get_client():
    """Build (once) the authenticated CLOB client from keyring credentials."""
    global _client
    if _client is None:
        from py_clob_client.client import ClobClient

        _client = ClobClient(
            HOST,
            key=config.require("POLYMARKET_PRIVATE_KEY"),
            chain_id=CHAIN_ID,
            funder=config.require("POLYMARKET_FUNDER_ADDRESS"),
            signature_type=1,
        )
        _client.set_api_creds(_client.create_or_derive_api_creds())
    return _client


def place(intent: OrderIntent) -> str:
    """Place one capped maker BUY. Returns a status string, never raises.

    Statuses: "placed", "rejected:<detail>", "error:<class>". Secrets never
    appear in statuses or logs.
    """
    try:
        from py_clob_client.clob_types import OrderArgs
        from py_clob_client.order_builder.constants import BUY

        client = _get_client()
        args = OrderArgs(
            price=intent.price,
            size=intent.shares,
            side=BUY,
            token_id=intent.token_id,
        )
        signed = client.create_order(args)
        resp = client.post_order(signed)
        if isinstance(resp, dict) and resp.get("success"):
            return "placed"
        detail = str((resp or {}).get("errorMsg", "unknown"))[:120] if isinstance(resp, dict) else "unknown"
        return f"rejected:{detail}"
    except Exception as exc:  # noqa: BLE001 — engine must survive any wire failure
        log.warning("order error: %s", type(exc).__name__)
        return f"error:{type(exc).__name__}"
