#!/usr/bin/env python3
"""Cut the raw Polymarket WS firehose (`data/pm/books-*.jsonl`) into the two
datasets that actually carry information, and drop the rest.

WHY THIS EXISTS
---------------
`data/pm/` is ~390 GiB of `book`-channel WebSocket payloads. A sample census on
2026-10-18 over 400 MB of `books-20260927.jsonl` found:

    outer:  ws 615,826 | ws_subscribed 48 | rest_books 16
    inner:  price_change 614,677 | book 734 | last_trade_price 363 |

So 99.88% of the bytes are `price_change` events, and each one repeats the same
`market` + `best_bid` + `best_ask` on every level entry. The tape's *information*
is therefore much smaller than its size:

  1. LADDERS  — the full `book` / `rest_books` depth snapshots. These are the
     only observations of the book ladder (the PMUS tape is BBO-only, which is
     exactly what NOTE10 says a maker strategy cannot be validated on). Sparse,
     irreplaceable, and the highest-value thing on the disk.
  2. BBO      — a collapsed best-bid/best-ask change log at the event clock,
     deduplicated to state transitions. Same microstructure, no repeated state.

Everything else is dropped: subscribe ACKs, last_trade_price (the PMUS trades
tape is the real source), and price_change entries that do not move the BBO or
the level's own size.

OUTPUT (per daily file, gzip-jsonl, one line per record)
--------------------------------------------------------
  ladders/YYYYMMDD.jsonl.gz : {ts_ns, market, asset_id, tick_size,
                               last_trade_price, bids, asks, src}
                              bids/asks = [[price, size], ...] best-first
  bbo/YYYYMMDD.jsonl.gz     : {ts_ns, market, asset_id, best_bid, best_ask}

WHAT IS NOT KEPT, AND WHY (state the loss, do not hide it)
----------------------------------------------------------
- Subscribe/ACK frames, `last_trade_price` (the PMUS trades tape is the real
  trade source, and NOTE10 makes it mandatory), and `tick_size_change`.
- `price_change` entries that do not change an asset's (best_bid, best_ask).
  Those entries still move a *level's* size inside the tail of the book, so
  per-level replenishment dynamics away from the touch are NOT in this cut.
  The book state is recoverable at every ladder timestamp; between ladders only
  the touch is. If queue dynamics matter later, re-derive from a re-recorded
  tape on the always-on host — this file does not replace that.

Stdlib only (no pyarrow in the environment yet; WB1 adds the Parquet layer on
top of these files). gzip rather than zstd because the repo just closed its
dependency set and `backup_raw.py` already gzips day files.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

DATA = Path("/home/ops/gridbot/data/pm")
OUT = Path("/home/ops/gridbot/data/lake-cut")


def _ladder_from(raw: dict) -> dict | None:
    """A full snapshot, whichever shape it arrived in."""
    bids = raw.get("bids") or raw.get("buys")
    asks = raw.get("asks") or raw.get("sells")
    if not bids and not asks:
        return None
    def levels(side):
        out = []
        for lv in side or []:
            try:
                out.append([float(lv["price"]), float(lv["size"])])
            except (KeyError, TypeError, ValueError):
                continue
        return out
    b, a = levels(bids), levels(asks)
    if not b and not a:
        return None
    return {
        "ts_ns": raw.get("_ts_ns"),
        "market": raw.get("market"),
        "asset_id": raw.get("asset_id"),
        "tick_size": raw.get("tick_size"),
        "last_trade_price": raw.get("last_trade_price"),
        "bids": b,
        "asks": a,
    }


def cut_one(path: str) -> dict:
    """One day-file -> (ladders, bbo) gzip-jsonl. Returns a stats dict."""
    import gzip

    src = Path(path)
    day = src.stem.split("-")[-1]  # books-20260927 -> 20260927
    (OUT / "ladders").mkdir(parents=True, exist_ok=True)
    (OUT / "bbo").mkdir(parents=True, exist_ok=True)

    n_lad = n_bbo = n_lines = n_bad = 0
    last_bbo: dict[str, tuple] = {}
    t0 = time.time()

    with (
        open(src, "rb") as fin,
        gzip.open(OUT / "ladders" / f"{day}.jsonl.gz", "wb", compresslevel=6) as fl,
        gzip.open(OUT / "bbo" / f"{day}.jsonl.gz", "wb", compresslevel=6) as fb,
    ):
        wl, wb = fl, fb
        for line in fin:
            n_lines += 1
            try:
                rec = json.loads(line)
            except Exception:
                n_bad += 1
                continue
            if not isinstance(rec, dict):
                continue
            ts_ns = rec.get("ts_ns")
            typ = rec.get("type")
            raw = rec.get("raw")

            # --- full ladders: one record, or a batch of them ---
            if typ == "rest_books" and isinstance(raw, list):
                for r in raw:
                    if isinstance(r, dict):
                        r["_ts_ns"] = ts_ns
                        lad = _ladder_from(r)
                        if lad:
                            lad["src"] = "rest_books"
                            wl.write((json.dumps(lad, separators=(",", ":")) + "\n").encode())
                            n_lad += 1
                continue

            if not isinstance(raw, dict):
                continue

            if raw.get("event_type") == "book":
                raw["_ts_ns"] = ts_ns
                lad = _ladder_from(raw)
                if lad:
                    lad["src"] = "book"
                    wl.write((json.dumps(lad, separators=(",", ":")) + "\n").encode())
                    n_lad += 1
                continue

            if raw.get("event_type") == "price_change":
                market = raw.get("market")
                for pc in raw.get("price_changes") or ():
                    if not isinstance(pc, dict):
                        continue
                    aid = pc.get("asset_id")
                    if not aid:
                        continue
                    key = (pc.get("best_bid"), pc.get("best_ask"))
                    # collapse: only emit when this asset's BBO state changes
                    if last_bbo.get(aid) == key:
                        continue
                    last_bbo[aid] = key
                    row = {
                        "ts_ns": ts_ns,
                        "market": market,
                        "asset_id": aid,
                        "best_bid": pc.get("best_bid"),
                        "best_ask": pc.get("best_ask"),
                    }
                    wb.write((json.dumps(row, separators=(",", ":")) + "\n").encode())
                    n_bbo += 1

        wl.close()
        wb.close()

    el = time.time() - t0
    return {
        "day": day,
        "src_bytes": src.stat().st_size,
        "lines": n_lines,
        "bad": n_bad,
        "ladders": n_lad,
        "bbo": n_bbo,
        "lad_bytes": (OUT / "ladders" / f"{day}.jsonl.gz").stat().st_size,
        "bbo_bytes": (OUT / "bbo" / f"{day}.jsonl.gz").stat().st_size,
        "secs": round(el, 1),
    }


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--one":
        files = [args[1]]
    else:
        files = sorted(str(p) for p in DATA.glob("books-*.jsonl"))
    print(f"{len(files)} file(s), {len(files) and sum(os.path.getsize(f) for f in files)/1e9:.1f} GB in", flush=True)

    tot = {"src_bytes": 0, "lines": 0, "bad": 0, "ladders": 0, "bbo": 0,
           "lad_bytes": 0, "bbo_bytes": 0}
    t0 = time.time()
    with mp.Pool(processes=min(12, os.cpu_count() or 4)) as pool:
        for i, st in enumerate(pool.imap_unordered(cut_one, files), 1):
            for k in tot:
                tot[k] += st[k]
            print(
                f"[{i:3d}/{len(files)}] {st['day']} {st['src_bytes']/1e9:6.2f}GB "
                f"{st['secs']:6.1f}s lad={st['ladders']:6d} bbo={st['bbo']:8d} "
                f"-> {st['lad_bytes']/1e6:7.1f}MB+{st['bbo_bytes']/1e6:7.1f}MB",
                flush=True,
            )

    el = time.time() - t0
    out_b = tot["lad_bytes"] + tot["bbo_bytes"]
    print("\n=== TOTAL ===")
    print(f"source   {tot['src_bytes']/1e9:9.2f} GB")
    print(f"ladders  {tot['lad_bytes']/1e9:9.3f} GB  ({tot['ladders']:,} snapshots)")
    print(f"bbo      {tot['bbo_bytes']/1e9:9.3f} GB  ({tot['bbo']:,} state changes)")
    print(f"kept     {out_b/1e9:9.3f} GB  ratio {tot['src_bytes']/max(out_b,1):.0f}x")
    print(f"saving   {(tot['src_bytes']-out_b)/1e9:9.2f} GB")
    print(f"elapsed  {el/60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
