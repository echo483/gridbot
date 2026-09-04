#!/usr/bin/env python3
"""Flow-feature study: do book-flow signals predict price moves p_map misses?

Question (follows docs/analysis/2026-08-28-priced-in-test.md licensed action 3):
p_map uses GRID game-state only; PMUS books reprice on flow pressure we don't
ingest. Would simple book-flow features computed AT eval time add predictive
signal for the NEXT price move beyond p_map?

Data reality (deterministic, offline):
  * data/pmus/books-*.jsonl does not exist. PMUS book snapshots live in
    data/pmus/watch-*.jsonl as `pmus_bbo` records (slug, bestBid/bestAsk,
    bidDepth/askDepth, sharesTraded) — sparse (~333 records on 08-27, none on
    08-28), used for the depth-imbalance feature only.
  * The dense quote series is data/shadow/postquote-*.jsonl: every row carries
    (slug, us_side, src_ts_ns, bid, ask, mid0) sampled AT eval time. Trailing
    snapshots of the same (slug, us_side) book form the flow history.
  * Outcomes are the postquote rows themselves: oriented_move at 30/60/120s.
  * p_map baseline is joined from data/shadow/mapentry-*.jsonl via
    (series_id, map_number, dedup_extra), latest row at or before the sample.

Features (all computed ONLY from information available at sample time):
  mom120     mid now vs mid <=120s earlier (points, oriented)
  mom60      mid now vs mid <=60s earlier (points, oriented)
  vel        mom120 / gap_seconds (points/s)
  spread_ch  change in (ask-bid) vs previous sample (points)
  spread     current spread (points)
  gap_s      staleness of the previous sample
  imb        pmus_bbo depth imbalance (bidDepth-askDepth)/(bidDepth+askDepth)
             where a pmus_bbo snapshot for the slug exists (08-27 only)

Metrics per horizon (30/60/120s): Pearson corr with oriented move, partial
corr controlling for the p_map fair-value baseline (= incremental value),
AUC for predicting a positive oriented move, and mean oriented drift in the
feature's top-vs-bottom tercile to test H-PRICEDIN1's licensing bar
(mean oriented drift > +1.0pt at 120s).

Analysis-only: reads capture data, prints tables, writes nothing.
Run: uv run python scripts/flow_features_study.py
"""
from __future__ import annotations

import json
import math
from collections import defaultdict

DAYS = ("20260827", "20260828")
HORIZONS = (30.0, 60.0, 120.0)
FEATURES = ("mom120", "mom60", "vel", "spread_ch", "spread", "gap_s", "imb")


def load(path):
    out = []
    try:
        with open(path) as fh:
            for line in fh:
                if line.strip():
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except FileNotFoundError:
        pass
    return out


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return float("nan")
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def partial_corr(xs, ys, zs):
    """Pearson corr(x, y) controlling for z, via residualization."""
    n = len(xs)
    if n < 4:
        return float("nan")
    mz = sum(zs) / n

    def resid(vs):
        mv = sum(vs) / n
        bz = sum((v - mv) * (z - mz) for v, z in zip(vs, zs))
        zz = sum((z - mz) ** 2 for z in zs)
        b = bz / zz if zz > 0 else 0.0
        return [v - (mv + b * (z - mz)) for v, z in zip(vs, zs)]

    return pearson(resid(xs), resid(ys))


def auc(scores, labels):
    """Mann-Whitney AUC that score ranks a positive label above a negative."""
    pos = [s for s, l in zip(scores, labels) if l > 0]
    neg = [s for s, l in zip(scores, labels) if l <= 0]
    if not pos or not neg:
        return float("nan")
    wins = ties = 0
    for p in pos:
        for q in neg:
            if p > q:
                wins += 1
            elif p == q:
                ties += 1
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def build():
    """One flat row per (postquote sample, horizon) with features + outcome."""
    rows = []
    for day in DAYS:
        pq = [r for r in load(f"data/shadow/postquote-{day}.jsonl")
              if r.get("type") == "postquote" and r.get("mid0") is not None]
        me = [r for r in load(f"data/shadow/mapentry-{day}.jsonl")
              if r.get("type") == "mapentry" and r.get("p_map") is not None]
        bbo = [r for r in load(f"data/pmus/watch-{day}.jsonl")
               if r.get("type") == "pmus_bbo" and r.get("bbo")]

        # p_map baseline: latest mapentry p_map at/before the sample, joined on
        # (series_id, map_number, dedup_extra) — same join as priced_in_test.
        me_ix = defaultdict(list)
        for r in me:
            key = (str(r.get("series_id")), r.get("map_number"),
                   str(r.get("dedup_extra")))
            me_ix[key].append((r.get("ts_ns") or 0, r))
        for v in me_ix.values():
            v.sort()

        # pmus_bbo history per slug (sparse; depth-imbalance feature only)
        bbo_ix = defaultdict(list)
        for r in bbo:
            md = (r["bbo"].get("marketData") or {})
            bd, ad = md.get("bidDepth"), md.get("askDepth")
            if bd is None or ad is None:
                continue
            try:
                imb = (float(bd) - float(ad)) / max(float(bd) + float(ad), 1e-9)
            except (TypeError, ValueError):
                continue
            bbo_ix[r.get("slug")].append((r.get("ts_ns") or 0, imb))
        for v in bbo_ix.values():
            v.sort()

        # quote history per (slug, us_side) from the postquote rows themselves
        hist = defaultdict(list)
        for r in pq:
            hist[(r["slug"], r.get("us_side"))].append(r)
        for v in hist.values():
            v.sort(key=lambda r: r.get("src_ts_ns") or 0)

        # outcome lookup: (slug, us_side, src_ts) -> {dt_s: oriented_move}
        pq_ix = defaultdict(dict)
        for r in pq:
            pq_ix[(r["slug"], r.get("us_side"), r.get("src_ts_ns"))][
                r.get("dt_s")] = r.get("oriented_move")

        for key, series in hist.items():
            slug = key[0]
            for i, r in enumerate(series):
                t = r.get("src_ts_ns") or 0
                spread = (r.get("ask") or 0) - (r.get("bid") or 0)
                feats = {"spread": spread * 100.0, "gap_s": None,
                         "mom120": None, "mom60": None, "vel": None,
                         "spread_ch": None, "imb": None}
                # latest strictly-prior sample within 120s
                prev = None
                for j in range(i - 1, -1, -1):
                    gap = (t - (series[j].get("src_ts_ns") or 0)) / 1e9
                    if 0 < gap <= 120.0:
                        prev = series[j]
                        feats["gap_s"] = gap
                        break
                if prev is not None:
                    mom = ((r["mid0"] - prev.get("mid0", 0)) * 100.0
                           if r.get("us_side") == "long"
                           else (prev.get("mid0", 0) - r["mid0"]) * 100.0)
                    feats["mom120"] = mom
                    feats["vel"] = mom / max(feats["gap_s"], 1e-9)
                    feats["spread_ch"] = (
                        spread - ((prev.get("ask") or 0) - (prev.get("bid") or 0))
                    ) * 100.0
                    gap60 = (t - (prev.get("src_ts_ns") or 0)) / 1e9
                    if gap60 <= 60.0:
                        feats["mom60"] = mom
                for bt, imb in reversed(bbo_ix.get(slug, ())):
                    if bt <= t:
                        feats["imb"] = imb
                        break

                # outcome: oriented_move at each horizon for THIS sample
                fair = None
                mkey = (str(r.get("series_id")), r.get("map_number"),
                        str(r.get("dedup_extra")))
                cands = [(mts, m) for mts, m in me_ix.get(mkey, ())
                         if mts <= t]
                if cands:
                    m = cands[-1][1]
                    p = m.get("p_map")
                    if isinstance(p, (int, float)):
                        fair = p if r.get("us_side") == "long" else 1.0 - p

                for h in HORIZONS:
                    mv = pq_ix.get((r["slug"], r.get("us_side"), t), {}).get(h)
                    if mv is None:
                        continue
                    rows.append({
                        "day": day, "slug": r["slug"], "h": h,
                        "move": mv * 100.0,  # points
                        "fair": fair,
                        **feats,
                    })
    return rows


def tercile_drift(rows, feat):
    """Mean drift in bottom vs top tercile of a feature (licensing view)."""
    xs = [r[feat] for r in rows if r.get(feat) is not None]
    if len(xs) < 9:
        return None
    xs = sorted(xs)
    lo = xs[len(xs) // 3]
    hi = xs[2 * len(xs) // 3]
    bot = [r["move"] for r in rows
           if r.get(feat) is not None and r[feat] <= lo]
    top = [r["move"] for r in rows
           if r.get(feat) is not None and r[feat] >= hi]
    return (sum(bot) / len(bot), len(bot),
            sum(top) / len(top), len(top))


def main():
    rows = build()
    print("=== Book-flow feature study (08-27 + 08-28) ===")
    print("target = oriented_move (points) at 30/60/120s; "
          "baseline = p_map fair joined from mapentry\n")

    for h in HORIZONS:
        sub = [r for r in rows if r["h"] == h and r["fair"] is not None]
        print(f"--- horizon {h:.0f}s  (n={len(sub)} joined with p_map) ---")
        ys = [r["move"] for r in sub]
        zs = [r["fair"] for r in sub]
        base = pearson(zs, ys)
        base_auc = auc(zs, [1.0 if y > 0 else 0.0 for y in ys])
        print(f"{'feature':10s} {'n':>5s} {'corr':>7s} {'partial|fair':>12s} "
              f"{'AUC':>6s} {'base AUC':>8s} {'bot-terc':>9s} {'top-terc':>9s}")
        print(f"{'p_map':10s} {len(sub):>5d} {base:>+7.3f} {'—':>12s} "
              f"{base_auc:>6.3f} {base_auc:>8.3f} {'—':>9s} {'—':>9s}")
        for f in FEATURES:
            ss = [r for r in sub if r.get(f) is not None]
            if len(ss) < 30:
                print(f"{f:10s} {len(ss):>5d}  (insufficient n)")
                continue
            xs = [r[f] for r in ss]
            yy = [r["move"] for r in ss]
            zz = [r["fair"] for r in ss]
            c = pearson(xs, yy)
            pc = partial_corr(xs, yy, zz)
            a = auc(xs, [1.0 if y > 0 else 0.0 for y in yy])
            td = tercile_drift(ss, f)
            if td:
                print(f"{f:10s} {len(ss):>5d} {c:>+7.3f} {pc:>+12.3f} "
                      f"{a:>6.3f} {base_auc:>8.3f} "
                      f"{td[0]:>+8.2f}pt {td[2]:>+8.2f}pt  (n={td[1]}/{td[3]})")
            else:
                print(f"{f:10s} {len(ss):>5d} {c:>+7.3f} {pc:>+12.3f} {a:>6.3f}")
        print()

    # licensing view: does conditioning on flow clear +1.0pt/120s?
    print("=== H-PRICEDIN1 licensing view (bar: mean oriented drift > +1.0pt at 120s) ===")
    sub = [r for r in rows if r["h"] == 120.0 and r["fair"] is not None]
    allm = sum(r["move"] for r in sub) / max(len(sub), 1)
    print(f"unconditioned 120s drift: {allm:+.2f}pt (n={len(sub)})")
    for f in FEATURES:
        td = tercile_drift([r for r in sub if r.get(f) is not None], f)
        if td:
            better = max(td[0], td[2])
            which = "bottom" if td[0] > td[2] else "top"
            verdict = "CLEARS" if better > 1.0 else "below bar"
            print(f"{f:10s}: {which} tercile {better:+.2f}pt (n={td[1] if which=='bottom' else td[3]}) -> {verdict}")
        else:
            print(f"{f:10s}: insufficient n")

    print("\nNotes:")
    print(" * imb comes from pmus_bbo depth (08-27 only, sparse) — n is small.")
    print(" * flow history is reconstructed from prior postquote quotes of the")
    print("   same (slug, us_side) book; rows without a prior sample within")
    print("   120s carry only spread/imb.")


if __name__ == "__main__":
    main()
