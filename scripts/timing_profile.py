"""Timing profile of the signal→quote→outcome path (STATE queue 11).

Read-only over local day files. For every placed (and dry_run) intent:
- signal latency: GRID observation (grid_ts_ns on the mapentry row) →
  intent ledger write (ts_ns) — how fast the model reacts;
- quote lifetime: placed → cancel row age_s (TTL calibration truth);
- post-quote drift: oriented mid moves at +30/60/120s from the postquote
  sampler for the same book/side — did the book come TOWARD our quote
  (we were early = good) or run away (we were late/adverse)?

Run: uv run python scripts/timing_profile.py [YYYYMMDD]
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


REJECTED = "ORDER_STATE_REJECTED"


def venue_states(intents: list[dict], settle_docs: list[dict]) -> dict[str, str | None]:
    """order_id -> venue state per NOTE7 precedence (STATE queue 15).

    Precedence for an order_id: settle_truth venue.state (any settle_truth
    doc; a repeated order_id resolves to the later doc) > a cancel/fill_check
    row's venue_state > a fill row (a fill implies the order rested) > None
    (UNKNOWN). None is a real third bucket, never silently treated as rested
    or rejected. order_id keys are compared/kept as str (TRAP D3).
    """
    states: dict[str, str | None] = {}
    for doc in settle_docs:  # later doc wins for a repeated order_id
        for o in doc.get("orders", []):
            vs = (o.get("venue") or {}).get("state")
            oid = o.get("order_id")
            if oid is not None and vs:
                states[str(oid)] = vs
    for r in intents:  # cancel / fill_check rows carry venue_state
        vs = r.get("venue_state")
        oid = r.get("order_id")
        if oid is not None and vs:
            states.setdefault(str(oid), vs)
    for r in intents:  # a fill implies the order rested (TRAP D1)
        if r.get("type") == "fill" and r.get("order_id") is not None:
            states.setdefault(str(r["order_id"]), "ORDER_STATE_FILLED")
    return states


def rows_both_days(directory: Path, stem: str, day: str) -> list[dict]:
    """Day plus day-1 rows, oldest first (day files partition at UTC midnight)."""
    cur = date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:8]}")
    prev = f"{cur - timedelta(days=1):%Y%m%d}"
    out = []
    for d in (prev, day):
        out.extend(rows(directory / f"{stem}-{d}.jsonl"))
    return out


def main() -> None:
    explicit = len(sys.argv) > 1
    day = sys.argv[1] if explicit else f"{datetime.now(UTC):%Y%m%d}"
    # Cross-midnight sessions split rows across day files (mapentry row
    # pre-midnight, its intent post-midnight), so an unattended no-arg run
    # must join across the boundary; an explicit day stays single-file.
    if explicit:
        intents = rows(DATA / "executor" / f"intents-{day}.jsonl")
        mapentry = rows(DATA / "shadow" / f"mapentry-{day}.jsonl")
        postquote = rows(DATA / "shadow" / f"postquote-{day}.jsonl")
    else:
        intents = rows_both_days(DATA / "executor", "intents", day)
        mapentry = rows_both_days(DATA / "shadow", "mapentry", day)
        postquote = rows_both_days(DATA / "shadow", "postquote", day)

    me_by_key: dict[tuple, dict] = {}
    for r in mapentry:
        k = (r.get("series_id"), r.get("map_number"), r.get("round_number"),
             r.get("us_side"), r.get("market_slug"))
        me_by_key.setdefault(k, r)

    cancels = {str(r.get("order_id")): r for r in intents if r.get("type") == "cancel"}

    print(f"== timing profile {day} ==")
    sig_lat, ages = [], []
    for r in intents:
        if r.get("type") != "order_intent" or r.get("status") not in ("placed", "dry_run"):
            continue
        k = (r.get("series_id"), r.get("map_number"), r.get("round_number"),
             r.get("us_side"), r.get("market_slug"))
        me = me_by_key.get(k)
        lat_ms = None
        if me and isinstance(me.get("grid_ts_ns"), int) and isinstance(r.get("ts_ns"), int):
            lat_ms = (r["ts_ns"] - me["grid_ts_ns"]) / 1e6
            if r.get("status") == "placed":
                sig_lat.append(lat_ms)
        cxl = cancels.get(str(r.get("order_id")))
        if cxl and isinstance(cxl.get("age_s"), (int, float)):
            ages.append(cxl["age_s"])
        if r.get("status") == "placed":
            print(f"  {str(r.get('market_slug'))[-32:]:>32} {r.get('us_side'):>5} "
                  f"@{r.get('price')} sig→ledger {lat_ms and round(lat_ms) or '?'}ms "
                  f"ttl {cxl and cxl.get('age_s') or '—'}s cancel={cxl and cxl.get('status')}")

    if sig_lat:
        print(f"\nsignal→ledger latency ms (placed, n={len(sig_lat)}): "
              f"min {min(sig_lat):.0f} | median {statistics.median(sig_lat):.0f} | "
              f"max {max(sig_lat):.0f}")
    if ages:
        print(f"quote lifetime s (n={len(ages)}): all {sorted({round(a) for a in ages})}")

    # postquote drift, split by side, oriented (+ = book moved in our favor)
    moves: dict[tuple[str, int], list[float]] = {}
    for r in postquote:
        m = r.get("oriented_move")
        off = r.get("dt_s")
        side = r.get("us_side")
        if isinstance(m, (int, float)) and isinstance(off, (int, float)):
            moves.setdefault((str(side), int(off)), []).append(float(m))
    if moves:
        print("\npost-quote oriented drift (mean, + = toward our position):")
        for side, off in sorted(moves):
            vals = moves[(side, off)]
            print(f"  {side:>5} +{off:>3}s: {statistics.mean(vals):+.4f} (n={len(vals)})")

    # ---- rested re-cut (STATE queue 15 / H-MAPENTRY1-NOTE7) ----
    # Every SHORT on the armed nights was sent at the wrong venue price and
    # 15/24 were venue-REJECTED while the ledger said "placed". A "placed"
    # denominator overstates what actually rested; quote lifetimes of rejected
    # orders are TTL artifacts. Re-cut real-order statistics on RESTED orders
    # only (venue state known and not REJECTED). dry_run intents never rest.
    settle_docs = []
    for p in sorted((DATA / "executor").glob("settle_truth-*.json")):
        try:
            with p.open() as fh:
                settle_docs.append(json.load(fh))
        except (OSError, json.JSONDecodeError):
            continue
    vs = venue_states(intents, settle_docs)

    placed = [r for r in intents
              if r.get("type") == "order_intent" and r.get("status") == "placed"]
    placed_oids = {str(r.get("order_id")) for r in placed if r.get("order_id")}
    filled_oids = {str(r["order_id"]) for r in intents
                   if r.get("type") == "fill" and r.get("order_id")
                   and str(r["order_id"]) in placed_oids}

    def _rested_stats(orders: list[dict]) -> dict:
        rested = rejected = unknown = filled = 0
        ages: list[float] = []
        lat: list[float] = []
        for r in orders:
            oid = str(r.get("order_id"))
            state = vs.get(oid)
            if state is None:
                unknown += 1
            elif state == REJECTED:
                rejected += 1
            else:
                rested += 1
                cxl = cancels.get(oid)
                if cxl and isinstance(cxl.get("age_s"), (int, float)):
                    ages.append(cxl["age_s"])
                me = me_by_key.get((r.get("series_id"), r.get("map_number"),
                                    r.get("round_number"), r.get("us_side"),
                                    r.get("market_slug")))
                if me and isinstance(me.get("grid_ts_ns"), int) and isinstance(r.get("ts_ns"), int):
                    lat.append((r["ts_ns"] - me["grid_ts_ns"]) / 1e6)
            if oid in filled_oids:
                filled += 1
        return {"rested": rested, "rejected": rejected, "unknown": unknown,
                "filled": filled, "ages": ages, "lat": lat}

    def _print_block(label: str, nb: dict) -> None:
        fill_rate = (f"{nb['filled'] / nb['rested']:.1%}" if nb["rested"] else "—")
        print(f"  {label}: placed {nb['placed']} | rested {nb['rested']} | "
              f"rejected {nb['rejected']} | unknown {nb['unknown']} | "
              f"filled {nb['filled']} | fill@rested {fill_rate}")
        if nb["ages"]:
            print(f"    quote lifetime s among rested (n={len(nb['ages'])}): "
                  f"{sorted({round(a) for a in nb['ages']})} "
                  f"median {statistics.median(nb['ages']):.1f}")
        if nb["lat"]:
            print(f"    signal→ledger latency ms (placed & rested, n={len(nb['lat'])}): "
                  f"min {min(nb['lat']):.0f} | median {statistics.median(nb['lat']):.0f} | "
                  f"max {max(nb['lat']):.0f}")

    def _base(orders: list[dict]) -> dict:
        nb = _rested_stats(orders)
        nb["placed"] = len(orders)
        return nb

    print("\nrested re-cut (real-order stats on RESTED only):")
    if not placed:
        print("  no placed intents for this day — placed 0 | rested 0 | "
              "rejected 0 | unknown 0 | filled 0")
        return
    _print_block("overall", _base(placed))
    for side in ("long", "short"):
        side_orders = [r for r in placed if r.get("us_side") == side]
        if side_orders:
            _print_block(side, _base(side_orders))


if __name__ == "__main__":
    main()
