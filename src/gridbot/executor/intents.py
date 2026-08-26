"""Pure intent construction: instrument instance → capped maker order intent.

Mirrors the FROZEN maker accounting in gridbot.instruments.fills: quote at
bid + one tick, only when that stays below the ask. Entry ceilings per
hypothesis match the verdict analyzer (ENTRY_ASK) so live behavior and the
shadow falsification bars measure the same trade.

Pure functions only — no I/O, no clocks, no network. The engine supplies
records and timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass

from gridbot.instruments.fills import TICK
from gridbot.instruments.market_match import team_outcome_token
from gridbot.instruments.verdict import ENTRY_ASK

DEFAULT_ENTRY_CEILING = 0.90  # H-MODELENTRY1 cells: never chase past this


@dataclass(frozen=True)
class OrderIntent:
    """One capped BUY-side maker intent derived from a single instance."""

    hypothesis_id: str
    series_id: str
    map_number: int
    round_number: int
    market_condition_id: str
    token_id: str
    team: str
    price: float
    shares: float
    stake_usd: float
    dedup_key: str

    def record(self) -> dict:
        """JSON-serializable form for the intent ledger."""
        return {
            "type": "order_intent",
            "hypothesis_id": self.hypothesis_id,
            "series_id": self.series_id,
            "map_number": self.map_number,
            "round_number": self.round_number,
            "market_condition_id": self.market_condition_id,
            "token_id": self.token_id,
            "team": self.team,
            "price": round(self.price, 4),
            "shares": round(self.shares, 2),
            "stake_usd": round(self.stake_usd, 2),
            "dedup_key": self.dedup_key,
        }


def entry_ceiling(hypothesis_id: str) -> float:
    """Max acceptable quote for a hypothesis (verdict ENTRY_ASK parity)."""
    for key, ceiling in ENTRY_ASK.items():
        if hypothesis_id.startswith(key):
            return ceiling
    return DEFAULT_ENTRY_CEILING


def build_intent(instance: dict, stake_cap_usd: float) -> OrderIntent | None:
    """Build a maker intent from one instance, or None when not quotable.

    None when: no matched round market, no unambiguous team token, no
    two-sided top-of-book, quote would cross the ask, or quote exceeds the
    hypothesis entry ceiling. Book tops are the flat BookTops shape:
    {"bid": float, "ask": float, ...} keyed by clob token id.
    """
    market = instance.get("market")
    if not isinstance(market, dict) or not market.get("condition_id"):
        return None
    token = team_outcome_token(market, str(instance.get("team", "")))
    if not token:
        return None
    top = (instance.get("books") or {}).get(token) or {}
    bid, ask = top.get("bid"), top.get("ask")
    if not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)):
        return None
    quote = round(float(bid) + TICK, 4)
    if quote >= float(ask) or quote <= 0:
        return None
    hyp = str(instance.get("hypothesis_id", ""))
    if quote > entry_ceiling(hyp):
        return None
    shares = round(stake_cap_usd / quote, 2)
    dedup = ":".join(
        [
            hyp,
            str(instance.get("series_id")),
            str(instance.get("map_number")),
            str(instance.get("round_number")),
            str(instance.get("dedup_extra", "")),
        ]
    )
    return OrderIntent(
        hypothesis_id=hyp,
        series_id=str(instance.get("series_id")),
        map_number=int(instance.get("map_number") or 0),
        round_number=int(instance.get("round_number") or 0),
        market_condition_id=str(market["condition_id"]),
        token_id=token,
        team=str(instance.get("team", "")),
        price=quote,
        shares=shares,
        stake_usd=round(quote * shares, 2),
        dedup_key=dedup,
    )
