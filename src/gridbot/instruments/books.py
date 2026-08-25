"""Best bid/ask tracking from recorded Polymarket book messages.

price_change events carry best_bid/best_ask per asset directly; book events
and REST snapshots carry full ladders (best bid = highest priced bid with
size, best ask = lowest priced ask with size).
"""

from __future__ import annotations


def _best(levels: list | None, pick) -> tuple[float, float] | None:
    priced = [
        (float(level["price"]), float(level.get("size") or 0))
        for level in levels or []
        if float(level.get("size") or 0) > 0
    ]
    if not priced:
        return None
    return pick(priced, key=lambda pair: pair[0])


class BookTops:
    """Latest top-of-book per asset id, fed from capture records."""

    def __init__(self) -> None:
        self.tops: dict[str, dict] = {}

    def top(self, asset_id: str) -> dict | None:
        return self.tops.get(asset_id)

    def update(self, record: dict) -> None:
        """Consume one capture record (type ws / rest_books / rest_book)."""
        ts = record.get("ts_ns")
        kind = record.get("type")
        if kind == "ws":
            raw = record.get("raw")
            events = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
            for event in events:
                self._event(event, ts)
        elif kind == "rest_books":
            for book in record.get("books") or []:
                self._book(book, ts)
        elif kind == "rest_book":
            book = dict(record.get("book") or {})
            book.setdefault("asset_id", record.get("token_id"))
            self._book(book, ts)

    def _event(self, event: dict, ts) -> None:
        if "price_changes" in event:
            for change in event.get("price_changes") or []:
                asset_id = str(change.get("asset_id") or "")
                if not asset_id:
                    continue
                entry = self.tops.setdefault(asset_id, {})
                if change.get("best_bid") is not None:
                    entry["bid"] = float(change["best_bid"])
                if change.get("best_ask") is not None:
                    entry["ask"] = float(change["best_ask"])
                entry["ts_ns"] = ts
        elif "bids" in event or "asks" in event:
            self._book(event, ts)

    def _book(self, book: dict, ts) -> None:
        asset_id = str(book.get("asset_id") or "")
        if not asset_id:
            return
        bid = _best(book.get("bids"), max)
        ask = _best(book.get("asks"), min)
        entry = self.tops.setdefault(asset_id, {})
        if bid is not None:
            entry["bid"], entry["bid_size"] = bid
        if ask is not None:
            entry["ask"], entry["ask_size"] = ask
        entry["ts_ns"] = ts
