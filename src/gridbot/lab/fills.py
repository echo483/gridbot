"""The fill model: does a resting order at our price actually get filled?

This is the single most important number in the system and the one this project
has got wrong in the most expensive way. ``H-MAPENTRY1``'s paper edge was +2.8pt
per contract; realized was -61pt. The gap was not the signal, it was *which
quotes fill*: the 41% that would have filled won 0.553 while the 59% that never
filled won 0.672, at the same price. An edge computed on the opportunity
population is therefore not a smaller version of the truth — it is a different
quantity, and it is the wrong one.

The model here is deliberately the simplest one that is honest:

**A resting order fills when the tape comes to it.** Our buy at ``p`` fills if a
later quote on the same slug, within the order's TTL, shows an ask at or below
``p``. That is "someone sold into our price". It is not a queue simulator: it
ignores queue position, so it is *optimistic* about getting filled at the touch
and *pessimistic* about nothing. Stating that direction matters more than
precision here — a simple honest model beats a precise wrong one, and the
alternative (assume every quote fills) is what produced the retracted numbers.

Two things this model does NOT do, on purpose:

- It does not fill an order before it was placed. The TTL window opens at the
  observation and closes ``ttl_s`` later; a quote from before our order existed
  cannot have traded against it.
- It does not invent depth. If the book is dead (no ask), nothing fills, and the
  row stays unfilled rather than being dropped — an unfilled row is evidence.

Ownership: this is the USER's artifact to own and refine (WORKBLOCKS: "backtest
core and fill model are USER"). What is here is the scaffold and the contract:
one SQL statement, one test with a hand-computed answer, and a documented
direction of bias.
"""

from __future__ import annotations

import duckdb

DEFAULT_TTL_S = 150.0  # the executor's resting TTL (engine auto-cancels at 150s)


def apply_touch_fill_model(
    con: duckdb.DuckDBPyConnection,
    *,
    ttl_s: float = DEFAULT_TTL_S,
    table: str = "scan_input",
) -> int:
    """Set ``filled`` on every row by the touch rule. Returns the filled count.

    Reads ``v_books`` (created by :mod:`gridbot.lab.joins`) and the ``market_slug``
    column written by the join. Rows whose slug is unknown stay unfilled.
    """
    if ttl_s <= 0:
        raise ValueError("ttl_s must be positive: an order that never expires is not modelled")
    con.execute(
        f"""
        UPDATE {table} AS s
        SET filled = EXISTS (
            SELECT 1 FROM v_books b
            WHERE b.slug = s.market_slug
              AND b.ts_ns > s.ts_ns
              AND b.ts_ns <= s.ts_ns + {int(ttl_s * 1e9)}
              AND (
                    (s.side = 'long'  AND b.ask IS NOT NULL AND b.ask <= s.price)
                 OR (s.side = 'short' AND b.bid IS NOT NULL AND (1.0 - b.bid) <= s.price)
              )
        )
        """
    )
    return int(con.execute(f"SELECT count(*) FROM {table} WHERE filled").fetchone()[0])


def fill_summary(con: duckdb.DuckDBPyConnection, *, table: str = "scan_input") -> dict:
    """Fill rate and the price distribution of fills vs misses.

    The second half is the diagnostic that matters: if filled rows sit at
    systematically worse prices than unfilled ones, the strategy is being
    adversely selected and no amount of signal quality fixes it.
    """
    row = con.execute(
        f"""
        SELECT count(*) AS n,
               count(*) FILTER (WHERE filled) AS filled,
               avg(price) FILTER (WHERE filled) AS px_filled,
               avg(price) FILTER (WHERE NOT filled) AS px_unfilled
        FROM {table}
        """
    ).fetchone()
    n, filled, px_f, px_u = row
    return {
        "n": int(n or 0),
        "filled": int(filled or 0),
        "fill_rate": (filled / n) if n else 0.0,
        "avg_price_filled": float(px_f) if px_f is not None else None,
        "avg_price_unfilled": float(px_u) if px_u is not None else None,
    }
