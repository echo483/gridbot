"""Maker-fill inference from the captured book stream (frozen accounting).

Registered in H-MODELENTRY1-CELLS: maker = post at the recorded best bid plus
one tick, fill inferred from the subsequently captured stream. Frozen
parameters: TICK = 0.01; a maker quote exists only when ``bid + TICK < ask``
at detection; it rests for at most FILL_WINDOW_S; it FILLS at the first later
captured event where a trade prints at or below the quote
(``last_trade_price <= price``) or the best ask crosses down onto it
(``ask <= price``). Deterministic and conservative — no partial fills, no
queue position modeling; any refinement requires a registry amendment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gridbot.instruments.books import _best
from gridbot.jsonl import iter_jsonl

TICK = 0.01
FILL_WINDOW_S = 180.0


@dataclass
class MakerQuote:
    key: str  # instance identity (caller-defined)
    token: str
    price: float
    after_ns: int
    deadline_ns: int
    filled_ns: int | None = None

    @property
    def filled(self) -> bool:
        return self.filled_ns is not None


def maker_quote(instance: dict, team_token: str | None) -> MakerQuote | None:
    """The frozen maker quote for an instance, or None when impossible."""
    if not team_token:
        return None
    top = (instance.get("books") or {}).get(team_token) or {}
    bid, ask = top.get("bid"), top.get("ask")
    if bid is None or ask is None:
        return None
    price = round(bid + TICK, 4)
    if price >= ask:
        return None  # spread too tight: joining the bid+tick would cross
    after = int(instance.get("grid_ts_ns") or instance.get("ts_ns") or 0)
    if not after:
        return None
    return MakerQuote(
        key=str(instance.get("row_key") or id(instance)),
        token=team_token,
        price=price,
        after_ns=after,
        deadline_ns=after + int(FILL_WINDOW_S * 1e9),
    )


def _events_for_fill(record: dict) -> list[tuple[str, float | None, float | None]]:
    """(token, trade_price, best_ask) tuples observable in one capture record."""
    out: list[tuple[str, float | None, float | None]] = []
    kind = record.get("type")
    if kind == "ws":
        raw = record.get("raw")
        events = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
        for event in events:
            if "price_changes" in event:
                for change in event.get("price_changes") or []:
                    token = str(change.get("asset_id") or "")
                    ask = change.get("best_ask")
                    if token:
                        out.append((token, None, float(ask) if ask is not None else None))
            elif event.get("event_type") == "last_trade_price" or "price" in event:
                token = str(event.get("asset_id") or "")
                price = event.get("price")
                if token and price is not None:
                    out.append((token, float(price), None))
            if "asks" in event:
                token = str(event.get("asset_id") or "")
                best_ask = _best(event.get("asks"), min)
                if token and best_ask is not None:
                    out.append((token, None, best_ask[0]))
    elif kind == "rest_books":
        for book in record.get("books") or []:
            token = str(book.get("asset_id") or "")
            best_ask = _best(book.get("asks"), min)
            if token and best_ask is not None:
                out.append((token, None, best_ask[0]))
    return out


def infer_fills(quotes: list[MakerQuote], records) -> None:
    """One pass over capture records (ts order): mark first-fill on each quote."""
    watchers: dict[str, list[MakerQuote]] = {}
    for quote in quotes:
        watchers.setdefault(quote.token, []).append(quote)
    if not watchers:
        return
    for record in records:
        ts = int(record.get("ts_ns") or 0)
        if not ts:
            continue
        for token, trade_price, best_ask in _events_for_fill(record):
            for quote in watchers.get(token, ()):
                if quote.filled or not quote.after_ns < ts <= quote.deadline_ns:
                    continue
                hit_trade = trade_price is not None and trade_price <= quote.price
                hit_cross = best_ask is not None and best_ask <= quote.price
                if hit_trade or hit_cross:
                    quote.filled_ns = ts


def iter_book_records(book_dir: Path):
    """All pm book records across day files, in file/time order."""
    for path in sorted(book_dir.glob("books-*.jsonl")):
        yield from iter_jsonl(path, dicts_only=False)
