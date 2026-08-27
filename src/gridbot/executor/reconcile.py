"""Venue-side reconciliation: ledger truth vs exchange truth.

DETECT every disagreement between what the intent ledger believes
(placed orders by order_id) and what the venue reports (open orders),
and surface partial fills. The engine runs this whenever a venue client
exists — a mid-run disarm must not stop truth ingestion for positions
already open — and appends a "reconcile" row when the anomaly set
changes, so the morning report can show drift. Writing the fill and
settle rows themselves is executor/fills.py's job.

Pure diff logic is separated from the one thin authed fetch so tests
never need a client.
"""

from __future__ import annotations

import logging

log = logging.getLogger("gridbot.executor.reconcile")


def _keys_of(records: list[dict], row_type: str) -> set[object]:
    """dedup_keys carried by every row of ``row_type``."""
    return {
        row.get("dedup_key")
        for row in records
        if isinstance(row, dict) and row.get("type") == row_type
    }


def open_ledger_orders(records: list[dict]) -> dict[str, dict]:
    """order_id -> placed intent row the ledger still believes is live.

    Released by a settle row (dedup_key) or a CLEAN cancel row (order_id,
    status "cancelled") — but a clean cancel releases ONLY when no fill
    row exists for the dedup_key: the venue reports "cancelled" even for
    cancel-after-partial-fill (pmus.cancel B1 note), and a filled book
    must stay visible here until it settles (peer review 08-26, finding
    3 — mirrors ledger.open_exposure). ``gone_before_cancel`` never
    releases: that order likely filled; surfacing it is this pass's job.
    """
    settled = _keys_of(records, "settle")
    # Order-scoped fill override, mirroring ledger.open_exposure (audit
    # F5): a clean-cancelled sibling of a FILLED re-entry order must not
    # stay in the open view forever. Keyless fill rows fall back to key
    # scope (fail closed).
    filled_oids = {
        str(row.get("order_id"))
        for row in records
        if isinstance(row, dict) and row.get("type") == "fill"
        and row.get("order_id")
    }
    filled_keys_keyless = {
        row.get("dedup_key")
        for row in records
        if isinstance(row, dict) and row.get("type") == "fill"
        and not row.get("order_id")
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
        if not oid:
            continue
        key = row.get("dedup_key")
        if key in settled:
            continue
        if (str(oid) in cancelled and str(oid) not in filled_oids
                and key not in filled_keys_keyless):
            continue
        out[str(oid)] = row
    return out


def _venue_id(order: object) -> str | None:
    """Venue order id under either spelling (``id`` / ``orderId``), else None."""
    if not isinstance(order, dict):
        return None
    oid = order.get("id") or order.get("orderId")
    return str(oid) if oid else None


def venue_order_ids(venue_orders: list[dict]) -> set[str]:
    return {oid for o in venue_orders if (oid := _venue_id(o))}


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
    slug_by_id = {
        oid: o.get("marketSlug")
        for o in venue_orders
        if (oid := _venue_id(o))
    }
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
    unknown = sorted(venue_ids - set(ledger_open))
    both = sorted(set(ledger_open) & venue_ids)
    return {
        "ledger_open": len(ledger_open),
        "venue_open": len(venue_ids),
        "gone_from_venue": sorted(set(ledger_open) - venue_ids),
        "unknown_on_venue": unknown,
        "partial_fills": sorted(partial),
        # Adoption inputs (glm review findings 1/3): every live venue order
        # must end up under some process's TTL sweep. Slugs are needed to
        # cancel; dedup keys come from the ledger row when we have one.
        "unknown_orders": [
            {"id": i, "slug": slug_by_id.get(i)} for i in unknown
        ],
        "open_orders": [
            {
                "id": i,
                "slug": (ledger_open[i].get("market_slug") or slug_by_id.get(i)),
                "dedup_key": ledger_open[i].get("dedup_key"),
            }
            for i in both
        ],
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
