"""Venue fills and settlements → ledger truth rows (closes the S3 gap).

The first armed night proved fills happen with ZERO ledger evidence: a
post-only bid partially fills, the TTL cancel then succeeds on the
remainder, and the ledger shows a clean "cancelled" — while the account
holds contracts. This module turns venue order records and portfolio
activities into the ``fill`` and ``settle`` rows ledger.py already
documents, so exposure and the daily-loss cap bind on real numbers.

Row construction is pure (testable without a client); :func:`sync` is
the one orchestrator the engine calls on its reconcile cadence. It never
raises into the engine and never writes a row it cannot defend: no
resolution truth → no settle row (fail closed).
"""

from __future__ import annotations

import logging

log = logging.getLogger("gridbot.executor.fills")

# Venue order states after which cumQuantity can never change again.
TERMINAL_STATES = frozenset({
    "ORDER_STATE_FILLED",
    "ORDER_STATE_CANCELED",
    "ORDER_STATE_REJECTED",
    "ORDER_STATE_EXPIRED",
})

MAX_RETRIEVES_PER_PASS = 10
ACTIVITY_PAGE_LIMIT = 50


def _amount(a: object) -> float | None:
    """Float value of a venue Amount ``{"value": "0.53", "currency": "USD"}``."""
    if isinstance(a, dict):
        try:
            return float(a.get("value"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    return None


def _num(v: object) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def fill_row(intent_row: dict, order: dict, ts_ns: int) -> dict | None:
    """A ``fill`` row for a venue order with executed quantity, else None.

    Price prefers the venue's average execution price; a missing/zero
    avgPx falls back to the intent's limit price (post-only maker never
    fills worse than its limit).
    """
    cum = _num(order.get("cumQuantity"))
    if cum <= 0:
        return None
    price = _amount(order.get("avgPx"))
    if price is None or price <= 0:
        price = intent_row.get("price")
    return {
        "type": "fill",
        "dedup_key": intent_row.get("dedup_key"),
        "order_id": str(order.get("id") or intent_row.get("order_id")),
        "market_slug": intent_row.get("market_slug"),
        "price": price,
        "shares": cum,
        "partial": cum < _num(order.get("quantity") or cum),
        "source": "venue_order",
        "ts_ns": ts_ns,
    }


def settle_row(intent_row: dict, resolution: dict, ts_ns: int) -> dict | None:
    """A ``settle`` row from a venue position resolution, else None.

    ``won`` derives from the realized-PnL delta of the resolution. For
    our BUY-only maker book that sign is decisive: a win realizes
    +(1-p)·s, a loss −p·s, with p strictly inside (0, 1). No delta →
    no row (fail closed; never guess a settlement).
    """
    before = resolution.get("beforePosition") or {}
    after = resolution.get("afterPosition") or {}
    rb = _amount(before.get("realized"))
    ra = _amount(after.get("realized"))
    if rb is None or ra is None:
        return None
    pnl = ra - rb
    # Venue resolution time when parseable, else sync time — a settle
    # bucketed into the wrong UTC day mis-binds the daily-loss cap
    # (qwen review f9).
    ts = _iso_ns(resolution.get("updateTime")) or ts_ns
    return {
        "type": "settle",
        "dedup_key": intent_row.get("dedup_key"),
        "market_slug": intent_row.get("market_slug"),
        "won": pnl > 0,
        "venue_pnl": round(pnl, 6),
        "source": "venue_resolution",
        "ts_ns": ts,
    }


def _iso_ns(s: object) -> int | None:
    """Epoch ns for an ISO-8601 string, else None."""
    if not isinstance(s, str) or not s:
        return None
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1e9)
    except (ValueError, OverflowError):
        return None


def _rows_of(records: list[dict], row_type: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in records:
        if isinstance(row, dict) and row.get("type") == row_type:
            key = row.get("dedup_key")
            if isinstance(key, str) and key and key not in out:
                out[key] = row
    return out


def fill_candidates(records: list[dict], checked: set[str]) -> list[dict]:
    """Placed intents whose order still needs a venue fill-state check.

    ORDER-scoped: skips order_ids that already have their own fill row or
    were confirmed terminal-with-no-fill. Key-scoped skipping made a
    re-entry sibling's fill invisible forever (qwen review f3).
    """
    filled_orders = {
        str(row.get("order_id"))
        for row in records
        if isinstance(row, dict) and row.get("type") == "fill"
        and row.get("order_id")
    }
    out = []
    for row in records:
        if not isinstance(row, dict) or row.get("type") != "order_intent":
            continue
        if row.get("status") != "placed":
            continue
        oid = row.get("order_id")
        if not oid or str(oid) in checked or str(oid) in filled_orders:
            continue
        out.append(row)
    return out


def match_resolution(intents_by_slug: dict[str, list[dict]], resolution: dict) -> dict | None:
    """The unsettled filled intent a resolution belongs to, else None.

    Slug match first; when BOTH sides of one book are held (distinct
    dedup keys, same slug) the resolution's ``side`` string must name
    the intent's us_side — ambiguity is skipped, never guessed.
    """
    slug = resolution.get("marketSlug")
    candidates = intents_by_slug.get(slug) or []
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        side = str(resolution.get("side") or "").lower()
        hits = [r for r in candidates if str(r.get("us_side") or "").lower() in side and r.get("us_side")]
        if len(hits) == 1:
            return hits[0]
        log.warning("resolution on %s ambiguous across %d intents; skipped",
                    slug, len(candidates))
    return None


def fetch_resolutions(client) -> list[dict] | None:
    """Recent position resolutions; None on error (callers skip, retry later)."""
    try:
        resp = client.portfolio.activities({
            "limit": ACTIVITY_PAGE_LIMIT,
            "types": ["ACTIVITY_TYPE_POSITION_RESOLUTION"],
            "sortOrder": "SORT_ORDER_DESCENDING",
        })
    except Exception as exc:  # noqa: BLE001 — fills sync must never kill the engine
        log.warning("activities fetch failed: %s", type(exc).__name__)
        return None
    out = []
    for act in (resp or {}).get("activities") or []:
        if isinstance(act, dict) and act.get("type") == "ACTIVITY_TYPE_POSITION_RESOLUTION":
            res = act.get("positionResolution")
            if isinstance(res, dict):
                out.append(res)
    return out


def sync(records: list[dict], client, checked: set[str], now_ns: int) -> list[dict]:
    """One fills/settles pass. Returns new ledger rows (caller appends).

    Mutates ``checked`` with order_ids proven terminal so they are never
    re-fetched. Bounded venue load: at most MAX_RETRIEVES_PER_PASS order
    retrieves plus one activities page per pass.
    """
    new_rows: list[dict] = []

    gone_orders = {
        str(row.get("order_id"))
        for row in records
        if isinstance(row, dict) and row.get("type") == "cancel"
        and row.get("status") == "gone_before_cancel" and row.get("order_id")
    }

    retrieves = 0
    for intent in fill_candidates(records, checked):
        if retrieves >= MAX_RETRIEVES_PER_PASS:
            break
        oid = str(intent.get("order_id"))
        retrieves += 1
        try:
            resp = client.orders.retrieve(oid)
        except Exception as exc:  # noqa: BLE001
            log.warning("order retrieve %s failed: %s", oid, type(exc).__name__)
            continue
        order = (resp or {}).get("order") or {}
        terminal = order.get("state") in TERMINAL_STATES
        if not terminal:
            # A live order's cumQuantity can still grow; writing a fill row
            # now would freeze an understated quantity forever (ledger fills
            # are first-wins — glm review 08-26, finding 4). The TTL cancel
            # makes every order terminal within minutes; wait for that.
            if _num(order.get("cumQuantity")) > 0:
                log.info("partial fill in progress on %s (%s) — recording at "
                         "terminal state", intent.get("market_slug"), oid)
            continue
        row = fill_row(intent, order, now_ns)
        if row:
            new_rows.append(row)
            log.warning("FILL recorded from venue: %s %s x%s @%s%s",
                        intent.get("market_slug"), intent.get("us_side"),
                        row["shares"], row["price"],
                        " (partial)" if row["partial"] else "")
        elif oid in gone_orders:
            # gone_before_cancel but the venue says terminal with ZERO
            # executed (expired / hand-cancelled): without this corrective
            # row the exposure slot leaks forever (glm review 08-26,
            # finding 8). A clean-cancel row releases it via the normal
            # ledger semantics.
            new_rows.append({
                "type": "cancel", "order_id": oid,
                "dedup_key": intent.get("dedup_key"),
                "market_slug": intent.get("market_slug"),
                "status": "cancelled", "source": "venue_terminal_no_fill",
                "ts_ns": now_ns,
            })
            log.warning("order %s was gone_before_cancel but venue shows "
                        "terminal with no fill — exposure released", oid)
        checked.add(oid)

    all_rows = records + new_rows
    fills = _rows_of(all_rows, "fill")
    settles = _rows_of(all_rows, "settle")
    placed = {
        row.get("dedup_key"): row
        for row in records
        if isinstance(row, dict) and row.get("type") == "order_intent"
        and row.get("status") == "placed" and isinstance(row.get("dedup_key"), str)
    }
    unsettled = set(fills) & set(placed) - set(settles)
    if not unsettled:
        return new_rows

    by_slug: dict[str, list[dict]] = {}
    for key in unsettled:
        intent = placed[key]
        slug = intent.get("market_slug")
        if isinstance(slug, str) and slug:
            by_slug.setdefault(slug, []).append(intent)

    resolutions = fetch_resolutions(client)
    if resolutions is None:
        return new_rows
    done: set[str] = set()
    for res in resolutions:
        intent = match_resolution(by_slug, res)
        if intent is None or intent.get("dedup_key") in done:
            continue
        row = settle_row(intent, res, now_ns)
        if row:
            done.add(str(intent.get("dedup_key")))
            new_rows.append(row)
            log.warning("SETTLE recorded from venue: %s won=%s pnl=%s",
                        row["market_slug"], row["won"], row["venue_pnl"])
    return new_rows
