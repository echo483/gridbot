"""Venue-side reconciliation: ledger truth vs exchange truth.

Closes the S3 residue and the fills/settles gap in skeleton form: DETECT
every disagreement between what the intent ledger believes (placed orders
by order_id) and what the venue reports (open orders), and surface fills.
The engine runs this only when ARMED — dry_run has no venue state — and
appends a "reconcile" row per pass so the morning report can show drift.

Writing settle rows from venue settlement data (which finally makes the
daily-loss cap bind on real numbers) builds on these primitives next.

Pure diff logic is separated from the two thin authed fetches so tests
never need a client.
"""

from __future__ import annotations

import logging

log = logging.getLogger("gridbot.executor.reconcile")


def open_ledger_orders(records: list[dict]) -> dict[str, dict]:
    """order_id -> placed intent row the ledger still believes is live.

    Released by a settle row (dedup_key) or a CLEAN cancel row (order_id,
    status "cancelled" — the venue confirmed it died unfilled). A
    ``gone_before_cancel`` cancel does NOT release: that order likely
    filled, and it should stay flagged until a settle row lands — that is
    the drift this pass exists to surface.
    """
    settled = {
        row.get("dedup_key")
        for row in records
        if isinstance(row, dict) and row.get("type") == "settle"
    }
    cancelled = {
        str(row.get("order_id"))
        for row in records
        if isinstance(row, dict)
        and row.get("type") == "cancel"
        and row.get("status") == "cancelled"
        and row.get("order_id")
    }
    out: dict[str, dict] = {}
    for row in records:
        if not isinstance(row, dict) or row.get("status") != "placed":
            continue
        oid = row.get("order_id")
        if not oid or str(oid) in cancelled:
            continue
        if row.get("dedup_key") not in settled:
            out[str(oid)] = row
    return out


def venue_order_ids(venue_orders: list[dict]) -> set[str]:
    return {
        str(o.get("id") or o.get("orderId"))
        for o in venue_orders
        if isinstance(o, dict) and (o.get("id") or o.get("orderId"))
    }


def diff(records: list[dict], venue_orders: list[dict]) -> dict:
    """Disagreements between ledger open orders and venue open orders.

    - ``gone_from_venue``: ledger thinks the order rests; the venue does not
      list it -> it filled, was cancelled, or expired. Needs a fill/settle
      row before the exposure accounting is trustworthy.
    - ``unknown_on_venue``: the venue lists an open order the ledger never
      recorded (ambiguous ack orphan, manual order, or a lost write). The
      loudest possible signal — single-account rail means NOTHING should
      trade here but us.
    - ``partial_fills``: still-open orders with cumQuantity > 0.
    """
    ledger_open = open_ledger_orders(records)
    venue_ids = venue_order_ids(venue_orders)
    partial = []
    for o in venue_orders:
        if not isinstance(o, dict):
            continue
        try:
            cum = float(o.get("cumQuantity") or 0)
        except (TypeError, ValueError):
            cum = 0.0
        if cum > 0:
            partial.append(str(o.get("id") or o.get("orderId")))
    return {
        "ledger_open": len(ledger_open),
        "venue_open": len(venue_ids),
        "gone_from_venue": sorted(set(ledger_open) - venue_ids),
        "unknown_on_venue": sorted(venue_ids - set(ledger_open)),
        "partial_fills": sorted(partial),
    }


def fetch_venue_orders(client) -> list[dict] | None:
    """Open orders from the venue; None on any error — a failed fetch is
    NOT an empty book (treating it as one flags every open order gone)."""
    try:
        return list((client.orders.list() or {}).get("orders") or [])
    except Exception as exc:  # noqa: BLE001 — reconcile must never kill the engine
        log.warning("venue orders.list failed: %s", type(exc).__name__)
        return None


def run(records: list[dict], client, last: dict | None = None) -> dict:
    """One reconcile pass; returns the diff (already logged).

    ``last`` is the previous pass's report: an UNCHANGED anomaly set logs
    at DEBUG instead of re-warning every pass. A failed venue fetch skips
    the diff entirely and returns {"error": "venue_fetch_failed"}.
    """
    venue = fetch_venue_orders(client)
    if venue is None:
        return {"error": "venue_fetch_failed"}
    report = diff(records, venue)
    changed = last is None or any(
        report.get(k) != last.get(k)
        for k in ("gone_from_venue", "unknown_on_venue", "partial_fills")
    )
    if report["gone_from_venue"]:
        log.log(
            logging.WARNING if changed else logging.DEBUG,
            "reconcile: %d ledger orders no longer on venue (fill/cancel "
            "unrecorded): %s", len(report["gone_from_venue"]),
            report["gone_from_venue"],
        )
    if report["unknown_on_venue"]:
        log.log(
            logging.ERROR if changed else logging.DEBUG,
            "reconcile: %d venue orders NOT in ledger (orphans!): %s",
            len(report["unknown_on_venue"]), report["unknown_on_venue"],
        )
    if report["partial_fills"]:
        log.log(
            logging.INFO if changed else logging.DEBUG,
            "reconcile: partial fills on %s", report["partial_fills"],
        )
    return report
