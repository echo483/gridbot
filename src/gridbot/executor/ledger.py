"""Pure position + PnL accounting over the executor intent ledger.

Reads the append-only day files the engine writes (data/executor/
intents-YYYYMMDD.jsonl) and answers the questions the engine and the
reports need: which positions are still open, what settled and for how
much, what today's realized loss is.

Ledger row types understood here:

- ``order_intent`` — as written by :meth:`OrderIntent.record`, plus
  ``status`` ('dry_run' | 'placed' | 'rejected:...' | 'error:...' |
  'skipped_max_open'), ``ts_ns`` (epoch ns int), ``live`` (bool).
  Only ``status == 'placed'`` rows can open positions.
- ``fill`` (future) — ``{type, dedup_key, price, shares, ts_ns}``.
- ``settle`` (future) — ``{type, dedup_key, won, ts_ns}``.

Everything is pure over ``list[dict]``; :func:`load_records` does the
only I/O. Unknown row types and rows with missing fields are skipped —
this module never raises on ledger contents.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


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
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(row, dict):
                records.append(row)
    return records


def _placed_intents(records: list[dict]) -> dict[str, dict]:
    """Map dedup_key -> placed intent row (first placed wins)."""
    placed: dict[str, dict] = {}
    for row in records:
        if not isinstance(row, dict) or row.get("type") != "order_intent":
            continue
        if row.get("status") != "placed":
            continue
        key = row.get("dedup_key")
        if isinstance(key, str) and key and key not in placed:
            placed[key] = row
    return placed


def _fills(records: list[dict]) -> dict[str, dict]:
    """Map dedup_key -> first fill row (first wins)."""
    fills: dict[str, dict] = {}
    for row in records:
        if not isinstance(row, dict) or row.get("type") != "fill":
            continue
        key = row.get("dedup_key")
        if isinstance(key, str) and key and key not in fills:
            fills[key] = row
    return fills


def _settles(records: list[dict]) -> dict[str, dict]:
    """Map dedup_key -> first settle row (first wins)."""
    settles: dict[str, dict] = {}
    for row in records:
        if not isinstance(row, dict) or row.get("type") != "settle":
            continue
        key = row.get("dedup_key")
        if isinstance(key, str) and key and key not in settles:
            settles[key] = row
    return settles


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
    fills = _fills(records)
    settles = _settles(records)
    positions: list[dict] = []
    for key in sorted(set(placed) & set(fills) - set(settles)):
        intent = placed[key]
        fill = fills[key]
        price = _price(fill)
        shares = _shares(fill)
        pos = dict(intent)
        pos["price"] = price if price is not None else intent.get("price")
        pos["shares"] = shares if shares is not None else intent.get("shares")
        pos["fill_ts_ns"] = fill.get("ts_ns")
        positions.append(pos)
    return positions


def _clean_cancels(records: list[dict]) -> dict[str, dict]:
    """dedup_key -> first CLEAN cancel row (order pulled while unfilled).

    Only ``status == "cancelled"`` releases exposure. A
    ``gone_before_cancel`` row means the venue no longer had the order when
    we tried to pull it — very likely a fill — so the slot stays counted
    until a settle (or reconcile) resolves it. Conservative by design.
    """
    out: dict[str, dict] = {}
    for row in records:
        if not isinstance(row, dict) or row.get("type") != "cancel":
            continue
        if row.get("status") != "cancelled":
            continue
        key = row.get("dedup_key")
        if isinstance(key, str) and key and key not in out:
            out[key] = row
    return out


def open_exposure(records: list[dict]) -> int:
    """Count of placed-but-unreleased intents (resting orders + open positions).

    Exposure counting for the engine's max-open cap: a placed GTC order is
    exposure whether or not it has filled yet; a settle releases it, and so
    does a CLEAN unfilled cancel (the stale-maker TTL pass). Survives
    process restarts because it derives from the ledger.

    Args:
        records: Ledger rows (see module docstring).

    Returns:
        Number of dedup_keys with a placed intent and no settle/clean-cancel.
    """
    placed = set(_placed_intents(records))
    return len(placed - set(_settles(records)) - set(_clean_cancels(records)))


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
    fills = _fills(records)
    settles = _settles(records)
    out: list[tuple[str, float, int]] = []
    for key in sorted(set(placed) & set(settles)):
        intent = placed[key]
        source = fills.get(key, intent)
        price = _price(source)
        if price is None:
            price = _price(intent)
        shares = _shares(source)
        if shares is None:
            shares = _shares(intent)
        if price is None or shares is None:
            continue
        won = settles[key].get("won")
        if not isinstance(won, bool):
            continue
        pnl = (1.0 - price) * shares if won else -price * shares
        ts = settles[key].get("ts_ns")
        out.append((key, round(pnl, 6), ts if isinstance(ts, int) else 0))
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
