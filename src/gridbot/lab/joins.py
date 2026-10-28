"""Building ``scan_input`` from the lake: game events joined to venue books.

This is the one place where a game-state event is put next to a market quote, so
it is the one place where the things that silently manufacture edges live:

**Staleness.** A snapshot at time ``t`` is matched to the most recent quote at or
before ``t``, and only if that quote is younger than ``staleness_s``. Without the
bound, a quiet book from twenty minutes ago prices a round it never saw, and the
"edge" is a measurement of the gap between two clocks. The bound is required.

**Point in time.** Both sides are ordered by ``ts_ns``, the time the row reached
our disk. A quote we had not received yet cannot price a decision.

**Which team a book names.** A Polymarket US book names ONE team; buying it is
the long, the No side of the same slug is the short. The mapping is keyed by
**team name**, never by tape slot: the event tape puts the CT side in slot ``a``,
and sides swap at halftime, so a slot-keyed mapping would silently flip the
outcome of every second-half row. The caller supplies ``markets(slug,
series_id, map_number, team)`` (venue semantics live in
``gridbot.executor.us_mapping``); an unmapped slug is dropped, never guessed.

**Outcome.** From ``map_results``, the lake's copy of the one map-winner oracle
(``gridbot.corpus.winners``). Maps the oracle excluded (ambiguous, impossible
final score) have no outcome and produce no rows.

Output schema (the contract ``gridbot.lab.scan`` reads):

    series_id, ts_ns, side, price, outcome, filled, p_hat, reference,
    market_slug, map_number, round_number

``filled`` is FALSE here: whether a resting order at ``price`` would have filled
is the fill model's answer (``gridbot.lab.fills``), not a join's. Until it runs,
a scan reads the OPPORTUNITY population, and the scan engine reports both.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from gridbot.lake.ingest import PMUS_BOOKS, bronze_glob
from gridbot.lake.paths import lake_dir as _lake_dir

MARKETS_DDL = (
    "CREATE OR REPLACE TABLE markets("
    "slug VARCHAR, series_id VARCHAR, map_number INTEGER, team VARCHAR)"
)


def _sql_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class JoinSpec:
    """Everything the join needs stated explicitly, so none of it is implicit."""

    book_stream: str = PMUS_BOOKS
    staleness_s: float = 30.0
    side: str = "long"
    markets_view: str = "markets"

    def __post_init__(self) -> None:
        if self.side not in ("long", "short"):
            raise ValueError("side must be 'long' or 'short'")
        if self.staleness_s <= 0:
            raise ValueError("staleness_s must be positive: an unbounded join is not a join")


def books_view_sql(book_stream: str = PMUS_BOOKS) -> str:
    """Typed view of the venue book tape: one row per recorded BBO update.

    Extracts the fields ``capture/pmus_books.py`` writes (``type='pmus_bbo'``)
    from the lossless bronze line. The venue reports a DEAD book as price 0;
    that is NULL here, not 0.0 — a book with no bid is not a book at price zero,
    and treating it as one is how a dead book produces a spectacular backtest.
    """
    pattern = bronze_glob(book_stream)
    return f"""
    CREATE OR REPLACE VIEW v_books AS
    WITH t AS (
        SELECT
            ts_ns,
            json_extract_string(raw, '$.slug')                          AS slug,
            json_extract_string(raw, '$.series_id')                     AS series_id,
            TRY_CAST(json_extract(raw, '$.map_number') AS INTEGER)      AS map_number,
            json_extract_string(raw, '$.kind')                          AS kind,
            TRY_CAST(json_extract(raw, '$.bid') AS DOUBLE)              AS bid,
            TRY_CAST(json_extract(raw, '$.ask') AS DOUBLE)              AS ask
        FROM read_parquet({_sql_str(pattern)})
        WHERE type = 'pmus_bbo'
    )
    SELECT ts_ns, slug, series_id, map_number, kind,
           CASE WHEN bid IS NULL OR bid <= 0 THEN NULL ELSE bid END AS bid,
           CASE WHEN ask IS NULL OR ask <= 0 THEN NULL ELSE ask END AS ask
    FROM t
    """


def scan_input_sql(spec: JoinSpec, events: str = "v_events") -> str:
    """ASOF-join every event to the freshest book inside the staleness bound.

    ``price`` is our side's COST per contract: the long pays the ask, the short
    pays ``1 - bid``. ``outcome`` is 1 when the team we backed won the map.
    """
    price = "b.ask" if spec.side == "long" else "(1.0 - b.bid)"
    won = "o.winner_name = m.team" if spec.side == "long" else "o.winner_name <> m.team"
    return f"""
    CREATE OR REPLACE TABLE scan_input AS
    SELECT
        e.series_id,
        e.ts_ns,
        {_sql_str(spec.side)} AS side,
        ({price}) AS price,
        CASE WHEN {won} THEN 1.0 ELSE 0.0 END AS outcome,
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
    JOIN v_map_results o
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
    """Create ``v_events``, ``v_books``, ``v_map_results`` and ``scan_input``.

    Returns the row count written to ``scan_input``. The caller owns the
    ``markets`` relation (``MARKETS_DDL``): it is the venue's own mapping, and
    this module deliberately does not duplicate it.
    """
    spec = spec or JoinSpec()
    root = _lake_dir() / "events"
    for name in ("events", "map_results"):
        if not (root / f"{name}.parquet").exists():
            raise FileNotFoundError(
                f"no {name} at {root} — run `python -m gridbot.lake events` first"
            )
    con.execute(
        "CREATE OR REPLACE VIEW v_events AS "
        f"SELECT * FROM read_parquet({_sql_str(str(root / 'events.parquet'))})"
    )
    con.execute(
        "CREATE OR REPLACE VIEW v_map_results AS "
        f"SELECT * FROM read_parquet({_sql_str(str(root / 'map_results.parquet'))})"
    )
    con.execute(books_view_sql(spec.book_stream))
    con.execute(scan_input_sql(spec, events=events_table))
    return int(con.execute("SELECT count(*) FROM scan_input").fetchone()[0])
