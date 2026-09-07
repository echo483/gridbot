"""Assemble an order population and per-order observations for an offline
maker replay (H-MAPENTRY1-EXEC1).

Pure data plumbing: this module turns the recording mapentry rows into
:class:`Order` records and attaches each order's in-lifetime observations
(keyed from the postquote and pmus tape indexes). It does NO statistics, NO
fill simulation (that is :mod:`gridbot.analysis.exec_fills`, which owns the
``Obs`` record and the bracket rules) and no I/O beyond what the caller passes
in.

The output of every helper here is what :mod:`exec_fills` consumes: prices are
our-side cost, converted with :func:`our_side_quote` exactly the way the replay
strategist expects (a short already converts ``bid`` to ``1 - bid``, so the
bracket rules are side-agnostic).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from gridbot.analysis.exec_fills import Obs
from gridbot.instruments.mapentry_verdict import (
    has_two_sided_book,
    holdout_series,
    map_outcome,
    posted_price,
    provenance,
)

# (this module declared its own `US_TICK = 0.01` and never used it — removed
# 2026-09-07; the venue tick lives in instruments.fills.TICK)


@dataclass(frozen=True)
class Order:
    """One qualifying maker order at its detection time, kept for replay."""

    series_id: str
    map_number: int
    us_side: str
    slug: str
    t0_ns: int
    cost: float  # our-side posted cost = posted_price(row)
    team: str
    opponent: str
    p_map: float
    top_size: float | None  # displayed size on OUR opposing side at detection
    row: dict  # the source mapentry row (kept for reconciliation)


def heldout_orders(
    rows,
    first_mapping,
    hold_start_ns,
    series_target=None,
) -> list[Order]:
    """The replay population: one :class:`Order` per qualifying (series, map, side).

    Keeps rows that are DETECTOR provenance, qualifying, carrying a real
    two-sided book, priceable, with an int ``ts_ns`` at/after ``hold_start_ns``,
    whose series was first mapped at/after ``hold_start_ns`` (a series absent
    from ``first_mapping`` is excluded, never guessed). Dedupes to the EARLIEST
    ``ts_ns`` per (series, map, us_side). Output sorted by (series first-mapping
    ts, ``t0_ns``).
    """
    kept = []
    for row in rows:
        if provenance(row) != "DETECTOR" or row.get("qualifies") is not True:
            continue
        if not has_two_sided_book(row) or posted_price(row) is None:
            continue
        if not isinstance(row.get("ts_ns"), int) or row["ts_ns"] < hold_start_ns:
            continue
        sid = str(row.get("series_id"))
        fm = first_mapping.get(sid)
        if fm is None or fm < hold_start_ns:
            continue
        kept.append(row)

    if series_target is None:
        # EXEC1 has no series cap: every series first-mapped in the holdout belongs.
        chosen = {str(r["series_id"]) for r in kept}
    else:
        eligible = defaultdict(int)
        for r in kept:
            eligible[str(r["series_id"])] += 1  # one eligible count per row
        chosen = set(
            holdout_series(
                first_mapping, hold_start_ns, eligible, series_target=series_target
            )
        )

    best: dict[tuple, dict] = {}
    for row in sorted(kept, key=lambda r: r["ts_ns"]):
        key = (str(row["series_id"]), row.get("map_number"), row.get("us_side"))
        if key[0] in chosen:
            best.setdefault(key, row)

    orders: list[Order] = []
    for row in best.values():
        cost = posted_price(row)
        assert cost is not None  # kept only filters priceable rows
        orders.append(
            Order(
                series_id=str(row["series_id"]),
                map_number=row["map_number"],
                us_side=row["us_side"],
                slug=row["market_slug"],
                t0_ns=row["ts_ns"],
                cost=cost,
                team=row["team"],
                opponent=row["opponent"],
                p_map=row["p_map"],
                top_size=_top_size(row),
                row=row,
            )
        )
    orders.sort(
        key=lambda o: (first_mapping.get(str(o.series_id), o.t0_ns), o.t0_ns)
    )
    return orders


def _top_size(row: dict) -> float | None:
    """The displayed size on OUR opposing side at detection.

    LONG posts against the ask, so its displayed queue is ``ask_size``; SHORT
    posts against the bid, so its is ``bid_size``. A missing ``book`` (the
    two-sided price can come from row fields while the enriched book is a
    separate record) means no size is known.
    """
    book = row.get("book")
    if not isinstance(book, dict):
        return None
    if row.get("us_side") == "long":
        return book.get("ask_size")
    if row.get("us_side") == "short":
        return book.get("bid_size")
    return None


def our_side_quote(
    us_side, bid, ask, bid_size=None, ask_size=None
) -> tuple[float | None, float | None]:
    """Convert a raw quote to OUR-side cost, in the form exec_fills consumes.

    LONG -> ``(ask, ask_size)``; SHORT -> ``(1 - bid rounded to 4 dp, bid_size)``.
    Any None or <= 0 price is a dead/one-sided book -> ``(None, None)``.
    """
    if us_side == "long":
        if not _positive(ask):
            return None, None
        return float(ask), ask_size
    if us_side == "short":
        if not _positive(bid):
            return None, None
        return round(1.0 - float(bid), 4), bid_size
    return None, None


def our_side_bid(us_side, bid, ask) -> float | None:
    """Best bid on OUR leg: LONG -> the Yes bid; SHORT -> ``1 - yes_ask`` (4 dp).

    EXEC1-AMEND1's level-consumption channel: after we post at cost ``c`` we are
    the top of our leg's book (own_bid == c); a later own_bid < c means a taker
    consumed our level. None when the side is dead/one-sided.
    """
    if us_side == "long":
        return float(bid) if _positive(bid) else None
    if us_side == "short":
        return round(1.0 - float(ask), 4) if _positive(ask) else None
    return None


def _positive(v) -> bool:
    return isinstance(v, (int, float)) and v is not None and v > 0.0


def observations(
    order,
    postquote_by_src: dict[int, list[dict]],
    tape_by_slug: dict[str, list[dict]],
    ttl_ns: int = 150_000_000_000,
) -> list[Obs]:
    """An order's in-lifetime observations, merged and sorted by time.

    Postquote rows are keyed by ``src_ts_ns == order.t0_ns`` and restricted to
    ``us_side == order.us_side``; tape rows are keyed by ``order.slug``. The
    window is ``(t0_ns, t0_ns + ttl_ns]``. Postquote rows carry no sizes
    (``opp_size`` None); tape rows read ``req_ns`` as their time. Exact duplicate
    times are collapsed in favour of the tape row.
    """
    t0 = order.t0_ns
    end = t0 + ttl_ns

    # Postquote samples are keyed by the EVALUATION that spawned them
    # (src_ts_ns == mapentry.ts_ns). A held-out order's t0 IS that evaluation
    # time; a fixture order's t0 is the later intent time, so look the samples
    # up by the source row's ts_ns first and fall back to t0 (review 2026-09-04:
    # keying by t0 alone left all 48 real orders with zero observations).
    src_key = order.row.get("ts_ns") if isinstance(order.row, dict) else None
    pq_rows = postquote_by_src.get(src_key, ()) if isinstance(src_key, int) else ()
    if not pq_rows:
        pq_rows = postquote_by_src.get(t0, ())

    items: list[tuple[int, Obs, bool]] = []  # t_ns, obs, is_tape
    for pq in pq_rows:
        if pq.get("us_side") != order.us_side:
            continue
        t = pq.get("ts_ns")
        if not isinstance(t, int) or not (t0 < t <= end):
            continue
        price, size = our_side_quote(
            order.us_side,
            pq.get("bid"),
            pq.get("ask"),
            pq.get("bid_size"),
            pq.get("ask_size"),
        )
        own = our_side_bid(order.us_side, pq.get("bid"), pq.get("ask"))
        items.append((t, Obs(t_ns=t, opp_ask=price, opp_size=size, own_bid=own), False))

    for tape in tape_by_slug.get(order.slug, ()):
        t = tape.get("req_ns")
        if not isinstance(t, int) or not (t0 < t <= end):
            continue
        price, size = our_side_quote(
            order.us_side,
            tape.get("bid"),
            tape.get("ask"),
            tape.get("bid_size"),
            tape.get("ask_size"),
        )
        own = our_side_bid(order.us_side, tape.get("bid"), tape.get("ask"))
        items.append((t, Obs(t_ns=t, opp_ask=price, opp_size=size, own_bid=own), True))

    # sort by time; within an equal time the tape row (is_tape True) sorts last
    # and wins the dedupe.
    items.sort(key=lambda x: (x[0], x[2]))
    out: list[Obs] = []
    for t, obs, _is_tape in items:
        if out and out[-1].t_ns == t:
            out[-1] = obs
        else:
            out.append(obs)
    return out


def index_postquotes(rows) -> dict[int, list[dict]]:
    """Postquote rows by ``src_ts_ns`` (the join key with a mapentry order)."""
    out: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("type") != "postquote":
            continue
        key = r.get("src_ts_ns")
        if isinstance(key, int):
            out[key].append(r)
    return out


def index_tape(rows) -> dict[str, list[dict]]:
    """pmus BBO rows by ``slug``, each list sorted by ``req_ns``."""
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("type") != "pmus_bbo":
            continue
        slug = r.get("slug")
        if slug is not None:
            out[str(slug)].append(r)
    return {k: sorted(v, key=lambda r: r.get("req_ns") or 0) for k, v in out.items()}


def y_side(order, winners) -> float | None:
    """The order's settlement for OUR position: 1.0 if the row's ``team`` won the map.

    Row semantics (mapentry recorder): ``team`` is the team WE back on both
    sides — a SHORT row's ``team`` is the No-leg team (the market's Yes team is
    its ``opponent``), ``p_map`` is P(team wins) and ``side_ask``/``posted_price``
    are already the No-leg cost. So the outcome is ``map_outcome`` for both
    sides; inverting it for shorts would double-orient (review 2026-09-04).
    An unresolved map stays None.
    """
    return map_outcome(order.row, winners)
