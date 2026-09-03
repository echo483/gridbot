"""Pure intent construction: instrument instance → capped maker order intent.

Mirrors the FROZEN maker accounting in gridbot.instruments.fills: quote at
bid + one tick, only when that stays below the ask. Entry ceilings per
hypothesis match the verdict analyzer (ENTRY_ASK) so live behavior and the
shadow falsification bars measure the same trade.

Pure functions only — no I/O, no clocks, no network. The engine supplies
records and timestamps.
"""

from __future__ import annotations

import math
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


US_BAND_GATE = (0.50, 0.80)  # H-PRICEDIN1 revert (H-BAND1-AMEND2-REVERT)


def us_band_gated(p_map: object) -> bool:
    """Executor risk policy: place US map entries only while P_map is inside
    US_BAND_GATE.

    REVERTED (user directive 2026-08-28, H-PRICEDIN1 / H-BAND1-AMEND2-REVERT):
    back to the frozen 2026-08-26 (0.50, 0.80) from the 2026-08-27 test
    widening (0.40, 0.90). Evidence: docs/analysis/2026-08-28-priced-in-test.md
    — live fill win% 0.20-0.67 vs corpus anchors 0.83-0.85, and postquote
    oriented drift at +120s of only +0.5..+0.94pt against 10-30pt discounts
    paid; the drift cannot repay the discount. Entry-bucket licensing is now
    governed by the registered drift diagnostic (+1.0pt at 120s bar), not by
    band width. Reversible by changing this one constant.

    Policy layer only: the H-MAPENTRY1 recording population and frozen bar are
    untouched; gated rows are still ledgered for evidence. Returns True when
    placement must be blocked; missing p_map fails closed.
    """
    if not isinstance(p_map, (int, float)) or isinstance(p_map, bool):
        return True
    try:
        p = float(p_map)
    except (TypeError, ValueError, OverflowError):
        return True  # unconvertible — fail closed (gated)
    if not math.isfinite(p):
        return True  # nan/inf — fail closed
    return not (US_BAND_GATE[0] <= p <= US_BAND_GATE[1])


DRIFT_WINDOW = 3  # postquote samples the engine feeds in (most recent first kept)
# H-PRICEDIN1 (docs/experiments/registry.jsonl) froze the licensing bar at
# "+1.0pt oriented drift at 120s" and licensed NO discount bucket (measured:
# 10-20pt +0.94pt, 20-30pt +0.63pt, 30+ +0.50pt). 1.0pt == 0.01 in probability
# units. This gate is deliberately AT LEAST AS STRICT as that frozen bar.
DRIFT_MIN_CONFIRM = 0.01  # 1.0pt -- H-PRICEDIN1's frozen bar (was 0.005 = 0.5pt)
# The postquote sampler records EACH entry at three horizons (30/60/120s).
# H-PRICEDIN1 froze the diagnostic at 120s, so the gate reads that horizon
# ONLY — pooling all three would triple-count one observation as if it
# were three independent confirmations (review 2026-08-28).
DRIFT_HORIZON_S = 120.0
# Queue-16 slate-coverage rows: recording-only, never traded (they exist to
# fix the starvation denominator, not to widen H-MAPENTRY1's entry population).
SLATE_SRC_HYPOTHESIS_ID = "H-MAPENTRY1-SLATE"


def drift_confirmed(
    prior_postquote_moves: list[float],
    min_confirm: float = DRIFT_MIN_CONFIRM,
    min_samples: int = DRIFT_WINDOW,
) -> bool:
    """DRIFT-CONFIRMATION gate (priced-in analysis 2026-08-28, licensed
    action 2): place only when the market has STARTED moving our way.

    ``prior_postquote_moves`` are ORIENTED moves (postquote sampler, + =
    with our side) for the record's slug+side at the 120s horizon, most
    recent LAST. Placement requires at least ``min_samples`` usable samples
    (review F5: a partial window could otherwise confirm on ONE sample) and
    their MEAN >= ``min_confirm``.

    MEAN, not sum (2026-08-28 Opus review). H-PRICEDIN1 froze the bar at
    "+1.0pt oriented drift at 120s" — that is a PER-120s-SAMPLE drift.
    Summing the window let several small moves clear a bar meant for one
    sample: three 0.2pt moves summed to 0.6pt and CONFIRMED under the old
    ``sum >= 0.005`` rule, so the engine would place where the registry
    licenses nothing (pre-registration violation). Averaging keeps this gate
    at least as strict as the frozen bar, never looser.

    Missing/empty/partial data and non-finite values (review F2: json parses
    bare Infinity to a float; one inf could force-confirm) fail CLOSED —
    no drift evidence, no entry.
    """
    moves = [
        float(m)
        for m in prior_postquote_moves
        if isinstance(m, (int, float))
        and not isinstance(m, bool)
        and math.isfinite(m)
    ]
    if not moves or len(moves) < min_samples:
        return False
    return sum(moves) / len(moves) >= min_confirm


def build_us_intent(row: dict, stake_cap_usd: float) -> OrderIntent | None:
    """Build a Polymarket US maker intent from one QUALIFYING mapentry row.

    The row's frozen entry rule already held (side_ask <= p_map - margin);
    this converts it into a post-only resting order one US tick inside the
    taker price for OUR side, clamped to the frozen entry cap and the
    hypothesis ceiling — the clamps only ever LOWER the price, so the order
    always rests (never matches) and never pays above what the registration
    allows. Integer contracts (venue rule); below one contract -> None.

    ``price`` is OUR side's cost per contract for both sides (a short's
    side_ask is 1 - long_bid, so its cost is already in cost terms);
    ``stake_usd`` and the integer-contract sizing are therefore exact cash
    exposure. The venue's Yes-price convention is applied only in
    pmus.wire_price (pinned 2026-09-03).

    Pure conversion only — qualifying is necessary but not sufficient for
    placement. engine.Engine.handle still runs the band gate and the
    drift-confirmation gate (>=3 same-side 120s-horizon postquote samples,
    mean >= DRIFT_MIN_CONFIRM — H-MAPENTRY1-NOTE3's per-sample bar) before
    this intent may place. Neither gate consumes the dedup key on failure,
    so a drift-unconfirmed round retries: the next qualifying mapentry row
    for the same book calls this function again against a fresh drift check.

    Dedup is per (series, map, side, book) — the key is round-independent,
    so at most ONE entry is ever placed per map book. The engine consumes
    the key only on an actual placement/dry_run decision, never on a gate
    skip: later qualifying rounds either retry the same pending book or,
    once one has placed, are dropped without averaging in.
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
