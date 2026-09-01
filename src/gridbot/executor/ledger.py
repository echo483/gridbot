"""Pure position + PnL accounting over the executor intent ledger.

Reads the append-only day files the engine writes (data/executor/
intents-YYYYMMDD.jsonl) and answers the questions the engine and the
reports need: which positions are still open, what settled and for how
much, what today's realized loss is.

Ledger row types understood here:

- ``order_intent`` — as written by :meth:`OrderIntent.record`, plus
  ``status`` ('dry_run' | 'wire_pending' | 'placed' | 'rejected:...' |
  'error:...' | 'skipped:...'), ``ts_ns`` (epoch ns int), ``live``
  (bool). Only ``status == 'placed'`` rows can open positions.
- ``fill`` — ``{type, dedup_key, order_id, price, shares, ts_ns}``.
- ``settle`` — ``{type, dedup_key, won, venue_pnl, ts_ns}``.
- ``cancel`` — ``{type, dedup_key, order_id, status, ts_ns}``; only
  ``status == 'cancelled'`` releases exposure (see :func:`open_exposure`).

``fill``/``settle``/``cancel`` rows come from the engine's TTL sweep and
from executor/fills.py's venue sync.

Everything is pure over ``list[dict]``; :func:`load_records` does the
only I/O. Unknown row types and rows with missing fields are skipped —
this module never raises on ledger contents.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from gridbot.executor.fills import MANUAL_EXIT_SOURCE, TRUSTED_SETTLE_SOURCES
from gridbot.jsonl import iter_jsonl


def load_records(root: Path) -> list[dict]:
    """Read every intents-*.jsonl file under ``root``.

    Args:
        root: Directory holding the day files (e.g. data/executor).

    Returns:
        All parsed rows in filename order; unreadable or non-dict
        JSON lines are skipped, never raised.
    """
    records: list[dict] = []
    for path in sorted(Path(root).glob("intents-*.jsonl")):
        records.extend(iter_jsonl(path))
    return records


def fill_scopes(records: list[dict]) -> tuple[set[str], set[object]]:
    """(filled order_ids, dedup_keys of KEYLESS fill rows).

    THE shared order-scoped fill-override scan (audit F5): a clean cancel
    releases an order only when no fill row names ITS order_id — after a
    successful re-entry the cancelled sibling must not be held open by the
    key's fill. Keyless fill rows fall back to key scope (fail closed).
    Consumed by open_exposure here AND reconcile.open_ledger_orders — one
    derivation, so the release predicate cannot drift into two copies.
    """
    filled_oids: set[str] = set()
    keyless_keys: set[object] = set()
    for row in records:
        if isinstance(row, dict) and row.get("type") == "fill":
            foid = row.get("order_id")
            if foid:
                filled_oids.add(str(foid))
            else:
                keyless_keys.add(row.get("dedup_key"))
    return filled_oids, keyless_keys


def _first_by_key(
    records: list[dict], row_type: str, status: str | None = None
) -> dict[str, dict]:
    """Map dedup_key -> first row of ``row_type`` (first wins).

    ``status``, when given, additionally requires that row status. Rows
    without a non-empty string dedup_key are skipped.
    """
    out: dict[str, dict] = {}
    for row in records:
        if not isinstance(row, dict) or row.get("type") != row_type:
            continue
        if status is not None and row.get("status") != status:
            continue
        key = row.get("dedup_key")
        if isinstance(key, str) and key and key not in out:
            out[key] = row
    return out


def _placed_intents(records: list[dict]) -> dict[str, dict]:
    """Map dedup_key -> placed intent row (first placed wins)."""
    return _first_by_key(records, "order_intent", status="placed")


def _fills(records: list[dict]) -> dict[str, dict]:
    """Map dedup_key -> first fill row (first wins)."""
    return _first_by_key(records, "fill")


def _fill_totals(records: list[dict]) -> dict[str, tuple[float, float, int]]:
    """dedup_key -> (total_shares, weighted_avg_price, first_fill_ts_ns).

    Under re-entry one key can carry fills from TWO orders (qwen review
    f3); position accounting must aggregate them. Idempotent against
    crash-replay duplicates: first fill row per order_id wins (rows
    without an order_id fall back to first-per-key).
    """
    seen_orders: set[str] = set()
    keyless_seen: set[str] = set()
    agg: dict[str, list[float]] = {}
    for row in records:
        if not isinstance(row, dict) or row.get("type") != "fill":
            continue
        key = row.get("dedup_key")
        if not isinstance(key, str) or not key:
            continue
        oid = row.get("order_id")
        if oid and str(oid) in seen_orders:
            continue
        if not oid and key in keyless_seen:
            continue
        shares = _shares(row)
        price = _price(row)
        # An invalid row must NOT consume its oid/key first-wins slot — a
        # malformed first row would suppress the real one forever (audit
        # M5; unreachable via current writers, kept correct for future
        # ones). Mark seen only after validation.
        if shares is None or shares <= 0 or price is None:
            continue
        if oid:
            seen_orders.add(str(oid))
        else:
            keyless_seen.add(key)
        ts = row.get("ts_ns")
        cur = agg.setdefault(key, [0.0, 0.0, ts if isinstance(ts, int) else 0])
        cur[0] += shares
        cur[1] += price * shares
    return {
        k: (s, cost / s, int(t)) for k, (s, cost, t) in agg.items() if s > 0
    }


def _settles(records: list[dict]) -> dict[str, dict]:
    """Map dedup_key -> the key's most TRUSTWORTHY settle row.

    First-wins among equal sources, but a signed-venue-truth row
    (``venue_resolution``) outranks an earlier zero-PnL manual-exit
    release (fills.py 08-27 finding f6: the release must never
    permanently block a late resolution from restating true PnL).
    Returns the same KEY SET either way, so open-position/exposure
    semantics are unchanged — only which row represents the settle.
    """
    fallback: dict[str, dict] = {}
    preferred: dict[str, dict] = {}
    for row in records:
        if not isinstance(row, dict) or row.get("type") != "settle":
            continue
        key = row.get("dedup_key")
        if not isinstance(key, str) or not key:
            continue
        if key not in fallback:
            fallback[key] = row
        # Explicit TRUSTED set (audit: preferring any source that merely
        # ISN'T the manual-exit sentinel was fail-open — an unknown future
        # release source would outrank real venue truth). Only signed venue
        # resolutions outrank a release row.
        if row.get("source") in TRUSTED_SETTLE_SOURCES:
            preferred.setdefault(key, row)
    return {k: preferred.get(k, v) for k, v in fallback.items()}


def _price(row: dict) -> float | None:
    price = row.get("price")
    if isinstance(price, (int, float)) and not isinstance(price, bool):
        return float(price)
    return None


def _shares(row: dict) -> float | None:
    shares = row.get("shares")
    if isinstance(shares, (int, float)) and not isinstance(shares, bool):
        return float(shares)
    return None


def open_positions(records: list[dict]) -> list[dict]:
    """Positions with a fill but no settle yet.

    A placed intent counts as open once a fill row references its
    dedup_key and no settle has. Fills without a matching placed intent
    are ignored; duplicate fills count once (first wins).

    Args:
        records: Ledger rows (see module docstring).

    Returns:
        One dict per open position: the intent row merged with fill
        price/shares (falling back to intent price/shares) and a
        ``fill_ts_ns`` key. Sorted by dedup_key for determinism.
    """
    placed = _placed_intents(records)
    totals = _fill_totals(records)
    settles = _settles(records)
    positions: list[dict] = []
    for key in sorted(set(placed) & set(totals) - set(settles)):
        intent = placed[key]
        shares, price, first_ts = totals[key]
        pos = dict(intent)
        pos["price"] = price
        pos["shares"] = shares
        pos["fill_ts_ns"] = first_ts
        positions.append(pos)
    return positions


def open_exposure(records: list[dict]) -> int:
    """Count of placed-but-unreleased ORDERS (resting orders + open positions).

    Exposure counting for the engine's max-open cap, per ORDER not per
    key — under re-entry (H-MAPENTRY1-REENTRY1) one dedup_key can carry
    two placed orders, and the first entry's clean cancel must not hide
    the second entry's live exposure (glm trade-path review 08-26,
    finding 2). An order is released by ITS OWN clean cancel (matched on
    order_id, ``status == "cancelled"`` only) unless its key has a fill
    row (partial fill before cancel = contracts held), or by its key
    settling. ``gone_before_cancel`` never releases: the venue no longer
    had the order when we pulled it — very likely a fill — so the slot
    stays counted until a settle (or reconcile) resolves it. Orders
    without an order_id (should not happen for status "placed") stay
    counted — fail closed. Derives from the ledger, so it survives
    restarts.

    Args:
        records: Ledger rows (see module docstring).

    Returns:
        Number of placed orders with no own clean cancel and no settle.
    """
    settled = set(_settles(records))
    filled_oids, filled_keys_keyless = fill_scopes(records)
    cancelled_orders = {
        str(row.get("order_id"))
        for row in records
        if isinstance(row, dict) and row.get("type") == "cancel"
        and row.get("status") == "cancelled" and row.get("order_id")
    }
    n = 0
    for row in records:
        if not isinstance(row, dict) or row.get("type") != "order_intent":
            continue
        if row.get("status") != "placed":
            continue
        key = row.get("dedup_key")
        if key in settled:
            continue
        oid = row.get("order_id")
        if (oid and str(oid) in cancelled_orders
                and str(oid) not in filled_oids
                and key not in filled_keys_keyless):
            continue
        n += 1
    return n


def realized_pnl(records: list[dict]) -> list[tuple[str, float, int]]:
    """Per-position realized PnL for every settled position.

    Maker BUY accounting: s shares bought at price p settles won for
    +(1-p)*s, lost for -p*s. Fill price/shares override the intent's
    when a fill row exists (partial fills and re-fills at a different
    price are the fill's business).

    Args:
        records: Ledger rows (see module docstring).

    Returns:
        Tuples (dedup_key, pnl, settle_ts_ns) sorted by dedup_key.
        Settles without a matching placed intent are ignored.
    """
    placed = _placed_intents(records)
    totals = _fill_totals(records)
    settles = _settles(records)
    out: list[tuple[str, float, int]] = []
    for key in sorted(set(placed) & set(settles)):
        srow = settles[key]
        # Manual-exit releases carry venue_pnl=None and won=None by design
        # (exposure release only, fills.py 08-27): they must not zero out a
        # position's PnL. A falsifiable preference, per repo discipline:
        # the signed-venue-truth source (source="venue_resolution") is
        # preferred WHEN PRESENT; if it never arrives the None-pnl release
        # remains an explicit null result with exactly 0 contribution. A
        # wrong released key can always be repaired from the same ledger
        # history that produced it.
        if srow.get("source") == MANUAL_EXIT_SOURCE:
            continue
        ts = srow.get("ts_ns")
        ts = ts if isinstance(ts, int) else 0
        # The venue's own realized number is the truth when present — the
        # maker formula misprices shorts (venue short PnL = price × qty,
        # observed 08-26) and mispairs multi-fill keys (qwen review f9).
        vp = srow.get("venue_pnl")
        if isinstance(vp, (int, float)) and not isinstance(vp, bool):
            out.append((key, round(float(vp), 6), ts))
            continue
        intent = placed[key]
        if key in totals:
            shares, price, _first = totals[key]
        else:
            price = _price(intent)
            shares = _shares(intent)
        if price is None or shares is None:
            continue
        won = srow.get("won")
        if not isinstance(won, bool):
            continue
        pnl = (1.0 - price) * shares if won else -price * shares
        out.append((key, round(pnl, 6), ts))
    return out


def _day_of(ts_ns: int) -> str:
    """UTC 'YYYYMMDD' for an epoch-ns timestamp."""
    return f"{datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=UTC):%Y%m%d}"


def daily_loss_usd(records: list[dict], day_utc: str) -> float:
    """Realized loss on one UTC day, floored at zero.

    Args:
        records: Ledger rows (see module docstring).
        day_utc: UTC day as 'YYYYMMDD'.

    Returns:
        max(0.0, -sum(pnl)) over settles whose ts_ns falls in that day.
    """
    total = 0.0
    for _key, pnl, ts_ns in realized_pnl(records):
        if _day_of(ts_ns) == day_utc:
            total += pnl
    return max(0.0, -total)


def summarize(records: list[dict]) -> dict:
    """One-line snapshot of the ledger.

    Args:
        records: Ledger rows (see module docstring).

    Returns:
        Dict with intent counts, open position count, and total
        realized PnL across all settles.
    """
    placed = _placed_intents(records)
    fills = _fills(records)
    settles = _settles(records)
    n_intents = 0
    for row in records:
        if isinstance(row, dict) and row.get("type") == "order_intent":
            n_intents += 1
    pnl_total = sum(pnl for _k, pnl, _t in realized_pnl(records))
    return {
        "intents": n_intents,
        "placed": len(placed),
        "filled": len(set(placed) & set(fills)),
        "settled": len(set(placed) & set(settles)),
        "open": len(open_positions(records)),
        "realized_pnl_total": round(pnl_total, 6),
    }
