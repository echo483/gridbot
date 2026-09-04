"""Calibration fixture for the H-MAPENTRY1-EXEC1 offline maker replay.

Pure data plumbing: turns the armed nights' order_intent rows (2026-08-26/27)
into replay :class:`Order` records plus ground-truth outcomes (``actual``), in
the shapes the runner consumes. No statistics, no fill simulation, no network.

Reuses, never reimplements:
- :class:`gridbot.analysis.exec_population.Order` (constructed with ``row`` = the
  joined mapentry row), and
- ``scripts/timing_profile.py: venue_states`` (imported via the same ``sys.path``
  idiom scripts/venue_pricing_cut.py uses: insert the repo's ``scripts`` dir).

Cost conversion (NOTE7 -- the verbatim-send bug on those nights): the engine sent
the our-side No price as the venue Yes price, so the venue's actual No cost was
``1 - price``. LONG cost is therefore the intent's ``price``; SHORT cost is
``round(1 - price, 4)``. ``t0_ns`` is the intent's ``ts_ns`` (the order time),
NOT the mapentry row's ``ts_ns``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from gridbot.analysis.exec_population import Order

# repo root = parents[3] (/home/ops/gridbot/src/gridbot/analysis/...)[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from timing_profile import venue_states

REJECTED = "ORDER_STATE_REJECTED"
FILLED = "ORDER_STATE_FILLED"


@dataclass(frozen=True)
class Actual:
    """Ground-truth outcome of one replayed order (keyed (series, map, side))."""

    rested: bool
    filled: bool
    t_fill_ns: int | None
    one_sided: bool
    state: str | None
    order_id: str


def _join_key(it: dict) -> tuple:
    return (
        str(it.get("series_id")),
        it.get("map_number"),
        it.get("round_number"),
        it.get("us_side"),
        it.get("market_slug"),
    )


def _index_rows(mapentry_rows) -> dict:
    """Mapentry rows by the five join keys; FIRST row wins (like timing_profile)."""
    index: dict = {}
    for r in mapentry_rows:
        index.setdefault(_join_key(r), r)
    return index


def _fill_ts(intents) -> dict:
    """order_id -> the fill row's ts_ns (first fill row wins)."""
    out: dict = {}
    for r in intents:
        if r.get("type") == "fill" and r.get("order_id") is not None:
            out.setdefault(str(r["order_id"]), r.get("ts_ns"))
    return out


def _cost(intent: dict) -> float:
    price = intent.get("price")
    if intent.get("us_side") == "short":
        return round(1.0 - price, 4)
    return price


def _top_size(row: dict) -> float | None:
    """Displayed size on OUR opposing side: LONG ask_size, SHORT bid_size."""
    book = row.get("book")
    if not isinstance(book, dict):
        return None
    if row.get("us_side") == "long":
        return book.get("ask_size")
    if row.get("us_side") == "short":
        return book.get("bid_size")
    return None


def _one_sided(row: dict) -> bool:
    """True if either bid or ask is None or <= 0 (dead/one-sided book)."""
    bid, ask = row.get("bid"), row.get("ask")
    return not (
        isinstance(bid, (int, float))
        and bid > 0
        and isinstance(ask, (int, float))
        and ask > 0
    )


def fixture_orders(intents, mapentry_rows, settle_docs):
    """Assemble replay Orders + ground-truth ``actual`` for all real placed orders.

    An ``order_intent`` becomes an Order only when ``status == "placed"`` with a
    non-null ``order_id``. Each Order is joined to the mapentry row on
    (series_id, map_number, round_number, us_side, market_slug); an intent with no
    joined row is dropped and counted ``unjoined``. Orders include EVERY joined
    placed order (rejected ones too -- the runner subtracts them via ``actual``);
    an order whose venue state is None is excluded from ``actual`` and counted
    ``unknown_state``.

    Returns ``(orders, actual, counts)`` with counts = {placed, joined, unjoined,
    unknown_state, rejected, rested, filled}.
    """
    states = venue_states(intents, settle_docs)
    row_index = _index_rows(mapentry_rows)
    fills = _fill_ts(intents)

    orders: list[Order] = []
    actual: dict[tuple, Actual] = {}
    counts = {
        "placed": 0,
        "joined": 0,
        "unjoined": 0,
        "unknown_state": 0,
        "rejected": 0,
        "rested": 0,
        "filled": 0,
    }

    for it in intents:
        if it.get("type") != "order_intent":
            continue
        if it.get("status") != "placed" or it.get("order_id") is None:
            continue
        counts["placed"] += 1
        row = row_index.get(_join_key(it))
        if row is None:
            counts["unjoined"] += 1
            continue
        counts["joined"] += 1

        oid = str(it["order_id"])
        order = Order(
            series_id=str(row["series_id"]),
            map_number=row["map_number"],
            us_side=row["us_side"],
            slug=row["market_slug"],
            t0_ns=it["ts_ns"],
            cost=_cost(it),
            team=row["team"],
            opponent=row["opponent"],
            p_map=row["p_map"],
            top_size=_top_size(row),
            row=row,
        )
        orders.append(order)

        st = states.get(oid)
        if st is None:
            counts["unknown_state"] += 1
            continue

        rested = st != REJECTED
        fill_ts = fills.get(oid)
        filled = fill_ts is not None or st == FILLED
        if st == REJECTED:
            counts["rejected"] += 1
        if rested:
            counts["rested"] += 1
        if filled:
            counts["filled"] += 1

        actual[(order.series_id, order.map_number, order.us_side)] = Actual(
            rested=rested,
            filled=filled,
            t_fill_ns=fill_ts,
            one_sided=_one_sided(row),
            state=st,
            order_id=oid,
        )

    return orders, actual, counts


def actual_dicts(actual) -> dict:
    """``actual`` -> plain dicts {rested, filled, t_fill_ns, one_sided} (runner input)."""
    return {
        k: {
            "rested": a.rested,
            "filled": a.filled,
            "t_fill_ns": a.t_fill_ns,
            "one_sided": a.one_sided,
        }
        for k, a in actual.items()
    }


def _jsonl_lines(path: Path) -> list[dict]:
    """Tolerant JSON-lines read: torn lines or a missing file are skipped."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def load_day_files(data_dir: Path, days: tuple[str, ...]) -> tuple[list, list, list]:
    """Read the day files, concatenated: (intents, mapentry rows, settle_docs).

    The ONLY function here that touches the filesystem. Reads
    ``executor/intents-{day}.jsonl`` and ``shadow/mapentry-{day}.jsonl`` tolerantly
    (torn final lines skipped), and ``executor/settle_truth-{day}.json`` only when
    it exists. Missing files contribute nothing.
    """
    intents: list = []
    rows: list = []
    settles: list = []
    for day in days:
        intents.extend(_jsonl_lines(data_dir / "executor" / f"intents-{day}.jsonl"))
        rows.extend(_jsonl_lines(data_dir / "shadow" / f"mapentry-{day}.jsonl"))
        sp = data_dir / "executor" / f"settle_truth-{day}.json"
        if sp.exists():
            settles.append(json.loads(sp.read_text()))
    return intents, rows, settles