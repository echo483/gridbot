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
US_TICK = 0.01  # Polymarket US orderPriceMinTickSize on every observed book


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
    # Polymarket US mapping (empty until a US market matcher attaches one;
    # the wire layer refuses to place without it — fail closed).
    market_slug: str = ""
    us_side: str = ""

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
            "market_slug": self.market_slug,
            "us_side": self.us_side,
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


US_BAND_GATE = (0.50, 0.80)


def us_band_gated(p_map: object) -> bool:
    """Executor risk policy (frozen 2026-08-26 pre-first-fire): place US map
    entries only while P_map is inside US_BAND_GATE.

    External diagnostic (cs2agent ADR-0038, our 32-series pinned holdout):
    the race aggregation over-forecasts above ~0.8 — entries at P_map>=0.8
    measured -8..-16 pts settled edge, 0.50-0.80 positive at every
    threshold. Policy layer only: the H-MAPENTRY1 recording population and
    frozen bar are untouched; gated rows are still ledgered for evidence.
    Returns True when placement must be blocked; missing p_map fails closed.
    """
    if not isinstance(p_map, (int, float)) or isinstance(p_map, bool):
        return True
    return not (US_BAND_GATE[0] <= float(p_map) <= US_BAND_GATE[1])


def build_us_intent(row: dict, stake_cap_usd: float) -> OrderIntent | None:
    """Build a Polymarket US maker intent from one QUALIFYING mapentry row.

    The row's frozen entry rule already held (side_ask <= p_map - margin);
    this converts it into a post-only resting order one US tick inside the
    taker price for OUR side, clamped to the frozen entry cap and the
    hypothesis ceiling — the clamps only ever LOWER the price, so the order
    always rests (never matches) and never pays above what the registration
    allows. Integer contracts (venue rule); below one contract -> None.

    Dedup is per (series, map, side, book): ONE entry per map book — later
    qualifying rounds for the same book never average in.
    """
    if row.get("type") != "mapentry" or not row.get("qualifies"):
        return None
    slug = str(row.get("market_slug") or "")
    us_side = row.get("us_side")
    side_ask = row.get("side_ask")
    entry_max = row.get("entry_price_max")
    if not slug or us_side not in ("long", "short"):
        return None
    if not isinstance(side_ask, (int, float)) or not isinstance(entry_max, (int, float)):
        return None
    hyp = str(row.get("hypothesis_id") or "")
    price = round(min(float(side_ask) - US_TICK, float(entry_max), entry_ceiling(hyp)), 4)
    if price < US_TICK:
        return None
    contracts = int(stake_cap_usd // price)
    if contracts < 1:
        return None
    dedup = ":".join(
        [hyp, str(row.get("series_id")), str(row.get("map_number")), str(us_side), slug]
    )
    return OrderIntent(
        hypothesis_id=hyp,
        series_id=str(row.get("series_id")),
        map_number=int(row.get("map_number") or 0),
        round_number=int(row.get("round_number") or 0),
        market_condition_id="",
        token_id="",
        team=str(row.get("team", "")),
        price=price,
        shares=float(contracts),
        stake_usd=round(price * contracts, 2),
        dedup_key=dedup,
        market_slug=slug,
        us_side=str(us_side),
    )
