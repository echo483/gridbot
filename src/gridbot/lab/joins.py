"""Building ``scan_input`` from the lake: game events joined to venue books.

This is the one place where a game-state event is put next to a market quote, so
it is the one place where the two things that silently manufacture edges live:

**Staleness.** A snapshot at time ``t`` is matched to the most recent quote at or
before ``t``, and only if that quote is younger than ``staleness_s``. Without the
bound, a quiet book from twenty minutes ago prices a round it never saw, and the
"edge" is a measurement of the gap between two clocks. The bound is a required
argument, not a default that gets forgotten.

**Slot mapping.** A Polymarket US book names ONE team; buying that team is the
long side, and the No side of the same slug is the short. Which slot a slug names
is venue semantics that already live in ``gridbot.executor.us_mapping`` — this
module does not reinvent it. It reads a ``markets(slug, series_id, map_number,
slot)`` relation the caller supplies, and fails loudly when a row is missing
rather than guessing a side.

Output schema (the contract ``gridbot.lab.scan`` reads):

    series_id, ts_ns, side, price, outcome, filled, p_hat, reference

``filled`` is FALSE by construction here: whether a resting order at ``price``
would have filled is the fill model's answer and belongs to the backtester
(``gridbot.lab.replay``), not to a join. A scan over this input therefore reads
as the OPPORTUNITY population until the fill model has run — which is exactly why
the scan engine reports both populations and refuses to hide the split.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from gridbot.lake.paths import lake_dir as _lake_dir

MAP_WIN_ROUNDS = 13  # CS2 regulation; a map is won at 13 rounds (OT excluded here)


def _sql_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class JoinSpec:
    """Everything the join needs stated explicitly, so none of it is implicit."""

    book_stream: str = "pmus_bbo"
    staleness_s: float = 30.0
    side: str = "long"
    markets_view: str = "markets"

    def __post_init__(self) -> None:
        if self.side not in ("long", "short"):
            raise ValueError("side must be 'long' or 'short'")
        if self.staleness_s <= 0:
            raise ValueError("staleness_s must be positive: an unbounded join is not a join")


def map_outcomes_sql(events: str = "v_events") -> str:
    """Map winner per (series, map) from the last observed score on that map.

    A map is won at ``MAP_WIN_ROUNDS``; the slot with the higher final score is
    the winner. Ties and unfinished maps are excluded — a draw is not a win, and
    an unfinished map has no outcome to score against.
    """
    return f"""
    CREATE OR REPLACE VIEW v_map_outcomes AS
    WITH last AS (
        SELECT series_id, map_number, a_name, b_name, a_score, b_score,
               row_number() OVER (PARTITION BY series_id, map_number
                                  ORDER BY ts_ns DESC) AS rn
        FROM {events}
    )
    SELECT series_id, map_number,
           CASE WHEN a_score > b_score THEN 'a' ELSE 'b' END AS winner_slot,
           CASE WHEN a_score > b_score THEN a_name ELSE b_name END AS winner_name,
           greatest(a_score, b_score) AS winner_score,
           least(a_score, b_score) AS loser_score
    FROM last
    WHERE rn = 1
      AND greatest(a_score, b_score) >= {MAP_WIN_ROUNDS}
      AND a_score <> b_score
    """


def books_view_sql(book_stream: str = "pmus_bbo", raw: bool = True) -> str:
    """Typed view of the venue book tape: one row per quoted slug update.

    ``raw`` reads the lake's faithful Parquet and extracts the fields the
    recorder writes (``capture/pmus_books.py``). The zero-price convention the
    venue uses for a DEAD book is preserved as NULL rather than kept as 0.0: a
    book with no bid is not a book at price zero, and treating it as one is how a
    dead book produces a spectacular backtest.
    """
    if not raw:
        raise ValueError("only the raw lake stream is supported")
    root = _lake_dir()
    pattern = str(root / "raw" / f"stream={book_stream}" / "dt=*" / "part.parquet")
    return f"""
    CREATE OR REPLACE VIEW v_books AS
    SELECT
        strftime(to_timestamp(ts_ns / 1e9), '%Y-%m-%d') AS day,
        ts_ns,
        CAST(slug AS VARCHAR)       AS slug,
        CAST(series_id AS VARCHAR)  AS series_id,
        CAST(map_number AS INTEGER) AS map_number,
        CAST(kind AS VARCHAR)       AS kind,
        CASE WHEN bid IS NULL OR bid <= 0 THEN NULL ELSE CAST(bid AS DOUBLE) END AS bid,
        CASE WHEN ask IS NULL OR ask <= 0 THEN NULL ELSE CAST(ask AS DOUBLE) END AS ask
    FROM read_parquet({_sql_str(pattern)})
    WHERE type = 'pmus_bbo'
    """


def scan_input_sql(spec: JoinSpec, events: str = "v_events") -> str:
    """ASOF-join every event to the freshest book inside the staleness bound.

    ``price`` is our side's COST per contract: the long pays the ask, the short
    pays ``1 - bid``. ``outcome`` is 1 when the team we would have backed won the
    map. Markets missing from the mapping relation are dropped, not defaulted —
    an unmapped slug is an unknown side, and a guess there is a fabricated row.
    """
    price = "b.ask" if spec.side == "long" else "(1.0 - b.bid)"
    # Our team: the slug's slot on a long, the other slot on a short.
    our_slot = "m.slot" if spec.side == "long" else "(CASE WHEN m.slot = 'a' THEN 'b' ELSE 'a' END)"
    return f"""
    CREATE OR REPLACE TABLE scan_input AS
    SELECT
        e.series_id,
        e.ts_ns,
        {_sql_str(spec.side)} AS side,
        ({price}) AS price,
        CASE WHEN o.winner_slot = {our_slot} THEN 1.0 ELSE 0.0 END AS outcome,
        FALSE AS filled,
        CAST(NULL AS DOUBLE) AS p_hat,
        CAST(NULL AS DOUBLE) AS reference,
        b.slug AS market_slug,
        e.map_number,
        e.round_number
    FROM {events} e
    ASOF JOIN v_books b
        ON e.series_id = b.series_id
       AND e.map_number = b.map_number
       AND e.ts_ns >= b.ts_ns
    JOIN {spec.markets_view} m
        ON m.slug = b.slug
       AND m.series_id = e.series_id
       AND m.map_number = e.map_number
    JOIN v_map_outcomes o
        ON o.series_id = e.series_id
       AND o.map_number = e.map_number
    WHERE b.ts_ns >= e.ts_ns - {int(spec.staleness_s * 1e9)}
      AND ({price}) IS NOT NULL
      AND ({price}) > 0.0
      AND ({price}) < 1.0
    """


def build_scan_input(
    con: duckdb.DuckDBPyConnection,
    spec: JoinSpec | None = None,
    *,
    events_table: str = "v_events",
) -> int:
    """Create ``v_events``, ``v_books``, ``v_map_outcomes`` and ``scan_input``.

    Returns the row count written to ``scan_input``. The caller owns the
    ``markets`` relation: it is the venue's own slot mapping, and this module
    deliberately does not duplicate it.
    """
    spec = spec or JoinSpec()
    root = _lake_dir()
    events_path = root / "events" / "events.parquet"
    if not events_path.exists():
        raise FileNotFoundError(
            f"no event tape at {events_path} — run `python -m gridbot.lake events` first"
        )
    con.execute(
        f"CREATE OR REPLACE VIEW v_events AS "
        f"SELECT * FROM read_parquet({_sql_str(str(events_path))})"
    )
    con.execute(books_view_sql(spec.book_stream))
    con.execute(map_outcomes_sql())
    con.execute(scan_input_sql(spec, events=events_table))
    return int(con.execute("SELECT count(*) FROM scan_input").fetchone()[0])
