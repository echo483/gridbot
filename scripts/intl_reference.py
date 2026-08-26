"""Intl-venue reference sampler — read-only, no auth, no orders.

The marketplace `polymarket` skill queries the INTERNATIONAL Polymarket
(gamma/clob/data APIs, public read-only). gridbot trades Polymarket US, but
intl lists many of the same CS2 matches with far deeper books — so intl
prices serve as an independent REFERENCE ORACLE for live-small sessions:
when our armed engine posts on a thin PMUS map book, the deep intl map book
on the same match shows what a liquid market thought at the same moment.

What this records (data/intl/reference-YYYYMMDD.jsonl):
  intl_ref rows: ts, grid_series_id, grid_teams, pmus_event_slug,
  intl_event_slug, market slug/kind/map, intl prices (yes/no), bid/ask
  tops if available, volume. NOTHING here feeds the trade path (rail 1);
  it is analysis-side context only.

Join: GRID series (from today's PMUS mapping snapshot) -> intl event via
gamma public-search per team + token coverage (reuses us_mapping's matcher)
+ same-day date proximity. Coverage >= 0.6 AND start-date within 2 days
required — both, or the series is skipped and logged in the row.

Run: uv run python scripts/intl_reference.py [--once]
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime

import httpx

from gridbot import config
from gridbot.executor.us_mapping import _coverage, _tokens

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
COVERAGE_FLOOR = 0.6
DAY_WINDOW = 2.0  # intl grid_start may differ from intl startDate by <= 2 days


def _gamma(path: str, params: dict | None = None):
    r = httpx.get(f"{GAMMA}{path}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def today_mappings(day: str) -> list[dict]:
    """Deduped GRID series with PMUS markets from today's mapping snapshot."""
    path = config.DATA_DIR / "pmus" / f"mappings-{day}.jsonl"
    out: dict[str, dict] = {}
    if not path.exists():
        return []
    for line in path.open():
        m = json.loads(line)
        if m.get("us_markets") and m.get("grid_teams"):
            out[str(m["grid_series_id"])] = m
    return list(out.values())


def find_intl_event(teams: list[str], grid_start: str | None) -> dict | None:
    """Best intl event for a GRID series: per-team search, token coverage,
    same-day proximity. Returns the gamma event dict or None."""
    pool: dict[str, dict] = {}
    for t in teams:
        try:
            res = _gamma("/public-search", {"q": t})
        except Exception:
            continue
        for e in (res or {}).get("events") or []:
            if e.get("slug"):
                pool[str(e["slug"])] = e
    best = None
    for ev in pool.values():
        hay = _tokens(ev.get("title") or "")
        cov = sum(_coverage(t, hay) for t in teams) / len(teams)
        if cov < COVERAGE_FLOOR:
            continue
        # Date proximity on the MARKET-level gameStartTime when present
        # (event-level startDate is the umbrella/group start, not the match —
        # e.g. BLAST Porto group events open days before the actual game).
        ev_day = (ev.get("startDate") or "")[:10]
        for mk in ev.get("markets") or []:
            gst = (mk.get("gameStartTime") or "")[:10]
            if gst:
                ev_day = gst
                break
        gs_day = (grid_start or "")[:10]
        if gs_day and ev_day:
            try:
                dd = abs(
                    datetime.fromisoformat(ev_day).date()
                    - datetime.fromisoformat(gs_day).date()
                ).days
            except ValueError:
                dd = 999
            if dd > DAY_WINDOW:
                continue
        if best is None or cov > best[0]:
            best = (cov, ev)
    return best[1] if best else None


def market_kind_map(slug: str) -> str | None:
    """Intl CS2 slug -> (kind, map_number) we care about."""
    s = slug or ""
    if s.endswith(("-game1", "-map1")):
        return "map1"
    if s.endswith(("-game2", "-map2")):
        return "map2"
    if s.endswith(("-game3", "-map3")):
        return "map3"
    # bare event slug = match winner (BO3) unless it has a derivative suffix
    if any(x in s for x in ("total", "handicap", "round")):
        return None
    return "match"


def book_top(token_id: str) -> dict | None:
    """Best bid/ask from the public CLOB book for one token."""
    try:
        r = httpx.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=10)
        r.raise_for_status()
        b = r.json()
    except Exception:
        return None
    def _top(levels: list, is_bid: bool) -> dict | None:
        """Best bid = max price; best ask = min price (CLOB returns unsorted)."""
        if not levels:
            return None
        px = [(float(x["price"]), float(x["size"])) for x in levels]
        best_p = max(px) if is_bid else min(px)
        depth = sum(s for p, s in px if abs(p - best_p[0]) <= 0.05)
        return {"price": best_p[0], "size": best_p[1], "depth_5c": round(depth, 2)}

    return {"bid": _top(b.get("bids") or [], True), "ask": _top(b.get("asks") or [], False)}


def sample(day: str) -> int:
    out_dir = config.DATA_DIR / "intl"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"reference-{day}.jsonl"
    n = 0
    with path.open("a") as fh:
        for m in today_mappings(day):
            teams = m["grid_teams"]
            ev = find_intl_event(teams, m.get("grid_start"))
            row = {
                "type": "intl_ref",
                "ts_ns": time.time_ns(),
                "grid_series_id": m["grid_series_id"],
                "grid_teams": teams,
                "grid_start": m.get("grid_start"),
                "pmus_event_slug": (m.get("us_markets") or {}).get("match", {}).get("market_slug"),
            }
            if ev is None:
                row["intl_event_slug"] = None
                row["skip_reason"] = "no_intl_event"
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                n += 1
                continue
            row["intl_event_slug"] = ev.get("slug")
            row["intl_title"] = ev.get("title")
            row["intl_volume"] = ev.get("volume")
            for mk in ev.get("markets") or []:
                kind = market_kind_map(mk.get("slug") or "")
                if kind is None:
                    continue
                try:
                    prices = json.loads(mk.get("outcomePrices") or "[]")
                except json.JSONDecodeError:
                    prices = []
                try:
                    outcomes = json.loads(mk.get("outcomes") or "[]")
                except json.JSONDecodeError:
                    outcomes = []
                try:
                    tokens = json.loads(mk.get("clobTokenIds") or "[]")
                except json.JSONDecodeError:
                    tokens = []
                top = book_top(tokens[0]) if tokens else None
                row["markets"] = row.get("markets") or []
                row["markets"].append(
                    {
                        "slug": mk.get("slug"),
                        "kind": kind,
                        "outcomes": outcomes,
                        "prices": [float(p) for p in prices],
                        "book_top": top,
                        "volume": mk.get("volume"),
                        "liquidity": mk.get("liquidity"),
                    }
                )
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="single pass (timer mode)")
    args = ap.parse_args()
    day = f"{datetime.now(UTC):%Y%m%d}"
    n = sample(day)
    print(f"intl reference: {n} series sampled -> data/intl/reference-{day}.jsonl")
    if not args.once:
        print("(continuous mode not implemented — run via timer or cron)")


if __name__ == "__main__":
    main()
