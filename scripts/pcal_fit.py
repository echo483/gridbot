#!/usr/bin/env python3
"""H-PCAL1: p_map recalibration overlay — fit + eval instruments.

Read-only overlay artifact (registry H-PCAL1). Fits a monotone isotonic
recalibration p_cal = g(p_map) on PRE-HOLDOUT resolved map entries and scores
raw p_map vs p_cal on a declared population:
  - design  = pre-holdout (the fit population; IN-SAMPLE / optimistic)
  - holdout = BOOKPIN1-pinned holdout series only
Verdicts on holdout are refused below 150 resolved observations (currently ~0).

Pure overlay: never touches the trade path, no network, stdlib only, seeded
deterministic. The registry bar is QUINTILE ECE (5 equal-count bins).

STATUS 2026-09-01: first real-data --fit REFUSED by the anchor guard (armed
band gap +0.049 on all-evaluations vs +0.196 on the qualifying subset — see
docs/analysis/2026-08-31-pcal-population-finding.md). The fit population
below is pending a user decision (qualifying-only vs discount-conditional)
before H-PCAL1 is registered; do not weaken the anchor to force a fit.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import random
import subprocess
import sys
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Bindings (registry H-MAPENTRY1-BOOKPIN1). Series whose FIRST mapping row has
# ts_ns >= this are HOLDOUT. 2026-08-31T19:53:46Z.
HOLDOUT_CUTOFF_NS = 1788206026000000000

ARMED_LO, ARMED_HI = 0.50, 0.80   # the band we trade
CLIP_LO, CLIP_HI = 0.001, 0.999   # calibrated-output clip
BOOT_REPS, BOOT_SEED = 1000, 18   # series-clustered bootstrap (deterministic)
HOLDOUT_MIN_OBS = 150             # refuse verdict numbers below this

MODEL_PATH = "data/models/pcal-v1.json"
ANALYSIS_DIR = "data/analysis"
SHADOW_GLOB = "data/shadow/mapentry-*.jsonl"
MAPPING_GLOB = "data/pmus/mappings-*.jsonl"
GRID_GLOB = "data/grid/series_state-*.jsonl"

# --fit sanity anchors (STOP-grade). Measured armed-band gap was +0.2129 on 434
# resolved maps; fit is pre-holdout, so expect roughly +0.15..+0.30 and a few
# hundred resolved maps.
ANCHOR_GAP_LO, ANCHOR_GAP_HI = 0.10, 0.45
ANCHOR_MIN_OBS = 100


def utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(x):
    """Coerce to float if numeric, else None (a row without numeric p_map is
    never used — calibration uses ALL rows with numeric p_map, book or no)."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Data loading


def load_jsonl(pattern: str):
    """Parse every line of every file matching pattern into dicts.

    An unparseable line (per spec a torn final line) is skipped, never
    guessed. Files that cannot be opened count as empty."""
    rows = []
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, "r") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            continue
    return rows


def read_oracle():
    """Run the grid_winners subprocess oracle; return {sid:map: {...}}.

    Monolithic: returns payload["winners"] so tests can monkeypatch it
    (no real subprocess in tests)."""
    cmd = [sys.executable, "scripts/grid_winners.py", *sorted(glob.glob(GRID_GLOB))]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"oracle grid_winners.py failed rc={proc.returncode}: "
            f"{proc.stderr[-400:]}"
        )
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError("oracle stdout not valid JSON") from exc
    return payload.get("winners", {})


def first_mapping_ts():
    """grid_series_id -> min ts_ns across data/pmus/mappings-*.jsonl."""
    best = {}
    for row in load_jsonl(MAPPING_GLOB):
        gid = row.get("grid_series_id")
        ts = row.get("ts_ns")
        if gid is None or ts is None or not isinstance(ts, int):
            continue
        if gid not in best or ts < best[gid]:
            best[gid] = ts
    return best


# ---------------------------------------------------------------------------
# Observation construction (first eval per series,map,round, resolved only)


def _realized_y(winner_row, team):
    """1/0 when `team` is the map's winner/loser, else None (unresolved).

    A presentation team matching neither side is never guessed."""
    if winner_row.get("winner") == team:
        return 1
    if winner_row.get("loser") == team:
        return 0
    return None


def build_observations(rows, winners):
    """Reduce eval rows to resolved first-evaluations.

    Returns:
      obs: list of {series_id, map_number, round_number, ts_ns, p_map, y}
           where y in {0,1} is realized win (truth oracle), first per
           (series_id, map_number, round_number) by ts_ns, numeric p_map,
           resolved maps only; UNRESOLVED maps are never guessed.
      n_unresolved: count of numeric-p_map rows excluded as unresolved or
           unmapped (also counted separately).
    """
    first = {}
    n_unresolved = 0
    for r in rows:
        p = _num(r.get("p_map"))
        sid = r.get("series_id")
        mn = r.get("map_number")
        ts = r.get("ts_ns")
        if p is None or sid is None or mn is None:
            continue
        if not isinstance(ts, int):
            # Cannot order for dedup — skip as an observation.
            continue
        w = winners.get(f"{sid}:{mn}")
        y = _realized_y(w, r.get("team")) if w else None
        if y is None:  # unmapped map, or team matching neither side
            n_unresolved += 1
            continue
        key = (sid, mn, r.get("round_number"))
        cur = first.get(key)
        if cur is None or ts < cur["ts_ns"]:
            first[key] = {
                "series_id": sid,
                "map_number": mn,
                "round_number": r.get("round_number"),
                "ts_ns": ts,
                "p_map": p,
                "y": y,
            }
    return list(first.values()), n_unresolved


def classify_holdout(series_ids, mapping_ts):
    """Split series ids by BOOKPIN1 holdout predicate.

    Returns (pre, hold, absent); a series with no mapping rows at all goes in
    `absent` and is EXCLUDED from both fit and eval populations: a
    yet-unmapped series may still receive its first mapping row at/after the
    cutoff and become holdout, so fitting on it would leak holdout data."""
    pre, hold, absent = set(), set(), set()
    for gid in series_ids:
        ts = mapping_ts.get(gid)
        if ts is None:
            absent.add(gid)
        elif ts >= HOLDOUT_CUTOFF_NS:
            hold.add(gid)
        else:
            pre.add(gid)
    return pre, hold, absent


# ---------------------------------------------------------------------------
# Isotonic regression (pooled-adjacent-violators, implemented directly)


def preaverage(obs):
    """Aggregate per distinct p_map: mean y and count (tie handling)."""
    agg = {}
    for o in obs:
        key = _num(o["p_map"])
        if key is None:
            continue
        cnt, s = agg.get(key, (0, 0.0))
        agg[key] = (cnt + 1, s + o["y"])
    xs = sorted(agg)
    ys = [agg[x][1] / agg[x][0] for x in xs]
    ws = [agg[x][0] for x in xs]
    return xs, ys, ws


def pav_increasing(xs, ys, weights):
    """Isotonic regression: monotone NON-DECREASING step function, stdlib only.

    Inputs must be pre-averaged per distinct x and sorted ascending. Repeatedly
    pools an adjacent pair that violates monotonicity (right value < left value)
    into its weighted mean. Returns (breakpoints_x, breakpoints_y); a block's
    breakpoint_x is its leftmost (smallest) x."""
    bx, by, bw = [], [], []
    for x, y, w in zip(xs, ys, weights):
        bx.append(x)
        by.append(y)
        bw.append(w)
        while len(by) >= 2 and by[-1] < by[-2]:
            _, yr, wr = bx.pop(), by.pop(), bw.pop()
            yl, wl = by[-1], bw[-1]
            wt = wl + wr
            by[-1] = (yl * wl + yr * wr) / wt  # bx[-1] stays the left x
            bw[-1] = wt
    return bx, by


def fit_isotonic(obs):
    """Fit g on observations -> (breakpoints_x, breakpoints_y), clipped."""
    xs, ys, ws = preaverage(obs)
    bx, by = pav_increasing(xs, ys, ws)
    by = [min(max(v, CLIP_LO), CLIP_HI) for v in by]
    return bx, by


def make_g(breakpoints_x, breakpoints_y):
    """Return g(x); rightmost breakpoint_x <= x, else the leftmost value.

    A monotone step function: between breakpoints g is flat at the lower
    breakpoint's value; below/above it clamps to the endpoint values."""
    bx = list(breakpoints_x)
    by = list(breakpoints_y)
    n = len(bx)

    def g(x):
        lo, hi = 0, n - 1
        idx = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if bx[mid] <= x:
                idx = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return by[idx]

    return g


# ---------------------------------------------------------------------------
# Scoring metrics


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _band_gap(pairs):
    """Armed-band gap over (forecast, y) pairs: mean forecast - realized win
    rate on rows whose forecast is inside the traded band; None if empty."""
    band = [(f, y) for f, y in pairs if ARMED_LO <= f <= ARMED_HI]
    if not band:
        return None
    return _mean([f for f, _ in band]) - _mean([y for _, y in band])


def metrics(fs, ys):
    """Brier, quintile ECE, armed-band gap, decile table for forecast/real."""
    n = len(fs)
    brier_ = _mean([(f - y) ** 2 for f, y in zip(fs, ys)]) if n else None

    # Quintile ECE: 5 equal-count bins ordered by forecast ascending.
    order = sorted(zip(fs, ys), key=lambda t: t[0])
    bin_size = -(-n // 5)  # ceil(n/5); may leave a short final bin
    ece = 0.0
    for b in range(5):
        chunk = order[b * bin_size:(b + 1) * bin_size]
        if not chunk:
            break
        bf = _mean([c[0] for c in chunk])
        byy = _mean([c[1] for c in chunk])
        ece += (len(chunk) / n) * abs(bf - byy)

    gap = _band_gap(zip(fs, ys))

    # Decile reliability table: 10 equal-width forecast bins.
    dec = []
    for b in range(10):
        lo, hi = b / 10.0, (b + 1) / 10.0
        if b == 9:  # top bin includes the 1.0 boundary
            label = "[0.9,1.0]"
            cells = [(f, y) for f, y in zip(fs, ys) if lo <= f <= 1.0]
        else:
            label = f"[{lo:.1f},{hi:.1f})"
            cells = [(f, y) for f, y in zip(fs, ys) if lo <= f < hi]
        dec.append({
            "bin": label,
            "n": len(cells),
            "mean_f": _mean([c[0] for c in cells]),
            "mean_y": _mean([c[1] for c in cells]),
        })
    return {"n": n, "brier": brier_, "quintile_ece": ece, "armed_gap": gap,
            "deciles": dec}


def series_clustered_bootstrap(obs, compute, reps=BOOT_REPS, seed=BOOT_SEED):
    """Bootstrap a per-series metric with 95% CI (resample series w/ repl.).

    Resamples the series index with replacement (len == #series), pools each
    chosen series' observations, and recomputes `compute` over the pooled
    sample. Returns (ci_lo, ci_hi) as 2.5% / 97.5% percentiles. Reps whose
    metric is undefined (e.g. an empty band) are dropped."""
    by = {}
    for o in obs:
        by.setdefault(o["series_id"], []).append(o)
    sids = sorted(by)
    rng = random.Random(seed)
    stats = []
    for _ in range(reps):
        pooled = []
        for _ in range(len(sids)):
            s = rng.choice(sids)
            pooled.extend(by[s])
        if not pooled:
            continue
        val = compute(pooled)
        if val is not None:
            stats.append(val)
    stats.sort()
    if not stats:
        return None, None
    last = len(stats) - 1
    idx_lo = max(0, min(round(reps * 0.025), last))
    idx_hi = max(0, min(round(reps * 0.975) - 1, last))
    return stats[idx_lo], stats[idx_hi]


# ---------------------------------------------------------------------------
# Scoring an evaluation population (raw vs calibrated)


def score_obs(obs, g):
    """Evaluate raw p_map vs p_cal on resolved observations.

    Returns a report dict; when g is None, scores raw p_map only."""

    def _brier_diff(sample):
        d = 0.0
        for o in sample:
            c = g(_num(o["p_map"]))
            d += (c - o["y"]) ** 2 - (o["p_map"] - o["y"]) ** 2
        return d / len(sample)

    def _raw_gap(sample):
        return _band_gap((o["p_map"], o["y"]) for o in sample)

    def _cal_gap(sample):
        return _band_gap((_num(g(o["p_map"])), o["y"]) for o in sample)

    report = {"n_obs": len(obs),
              "n_series": len({o["series_id"] for o in obs})}
    if len(obs) < HOLDOUT_MIN_OBS:
        return report

    fs_raw = [o["p_map"] for o in obs]
    ys = [o["y"] for o in obs]
    raw = metrics(fs_raw, ys)
    cal = metrics([_num(g(f)) for f in fs_raw], ys) if g is not None else None

    report["raw"] = raw
    if cal is not None:
        report["cal"] = cal
    report["brier_diff_cal_minus_raw"] = (
        (cal["brier"] - raw["brier"]) if (raw["brier"] is not None
                                          and cal["brier"] is not None) else None)
    report["brier_diff_ci95"] = series_clustered_bootstrap(obs, _brier_diff)
    report["raw_armed_gap_ci95"] = series_clustered_bootstrap(obs, _raw_gap)
    if cal is not None:
        report["cal_armed_gap_ci95"] = series_clustered_bootstrap(obs, _cal_gap)
    return report


# ---------------------------------------------------------------------------
# Artifact write + sha256


def artifact_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# CLI


def load_populations():
    """Shared fit/eval load: (obs, n_unresolved) plus the BOOKPIN1 split."""
    obs, n_unresolved = build_observations(load_jsonl(SHADOW_GLOB), read_oracle())
    pre, hold, absent = classify_holdout({o["series_id"] for o in obs}, first_mapping_ts())
    return obs, n_unresolved, pre, hold, absent


def cmd_fit(args):
    obs, n_unresolved, pre, _hold, _absent = load_populations()
    fit_obs = [o for o in obs if o["series_id"] in pre]

    # --- sanity anchors (STOP-grade) ---
    n = len(fit_obs)
    gap = _band_gap((o["p_map"], o["y"]) for o in fit_obs)
    print(f"[fit] pre-holdout resolved obs={n}, resolved-maps({n_unresolved} "
          f"unresolved skipped), buy-band raw gap={gap}")
    if n < ANCHOR_MIN_OBS or gap is None or not (ANCHOR_GAP_LO <= gap <= ANCHOR_GAP_HI):
        print("ANCHOR MISMATCH: pre-holdout population does not reproduce the "
              f"measured signal (n={n}, armed-band raw gap={gap}); NOT writing "
              "artifact.")
        sys.exit(1)

    bx, by = fit_isotonic(fit_obs)
    artifact = {
        "version": "pcal-v1",
        "fitted_utc": utcnow_iso(),
        "train_cutoff_ns": HOLDOUT_CUTOFF_NS,
        "n_obs": len(fit_obs),
        "n_series": len({o["series_id"] for o in fit_obs}),
        "source_files": sorted(glob.glob(SHADOW_GLOB)),
        "breakpoints_x": bx,
        "breakpoints_y": by,
    }
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    with open(MODEL_PATH, "w") as fh:
        json.dump(artifact, fh)
    print(f"[fit] pcal-v1 artifact written -> {MODEL_PATH}")
    print(f"[fit] artifact sha256: {artifact_sha256(MODEL_PATH)}")
    print(f"[fit] breakpoints: n={len(bx)} x_span=[{bx[0]},{bx[-1]}], "
          f"y_span=[{by[0]},{by[-1]}]")


def cmd_eval(args):
    holdout_mode = args.population == "holdout"

    with open(MODEL_PATH, "r") as fh:
        art = json.load(fh)
    g = make_g(art["breakpoints_x"], art["breakpoints_y"])

    obs, n_unresolved, pre, hold, absent = load_populations()
    pop = [o for o in obs if o["series_id"] in (hold if holdout_mode else pre)]

    report = {
        "mode": args.population,
        "label": ("HOLDOUT (BOOKPIN1)" if holdout_mode
                  else "IN-SAMPLE (train) — optimistic by construction"),
        "model_version": art.get("version"),
        "train_cutoff_ns": HOLDOUT_CUTOFF_NS,
        "n_series": len({o["series_id"] for o in pop}),
        "n_unresolved_skipped": n_unresolved,
        "absent_from_mappings_series": len(absent),
    }

    if holdout_mode and len(pop) < HOLDOUT_MIN_OBS:
        # Refuse verdict-style numbers on a thin holdout: report the accrual
        # count only. ~0 today; that is expected.
        report["verdict_ready"] = False
        report["accrual"] = {"resolved_holdout_observations": len(pop)}
    else:
        report["verdict_ready"] = True
        report["n_obs"] = len(pop)
        report.update(score_obs(pop, g))

    os.makedirs(ANALYSIS_DIR, exist_ok=True)
    stamp = utcnow_iso().replace(":", "").replace("-", "")
    out_path = os.path.join(ANALYSIS_DIR, f"pcal-{args.population}-{stamp}.json")
    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))
    print(f"\n[report file] {out_path}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pcal_fit",
                                     description="H-PCAL1 recalibration overlay.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pfit = sub.add_parser("fit", help="fit + write pcal-v1.json artifact")
    pfit.set_defaults(func=cmd_fit)

    pev = sub.add_parser("eval", help="score raw vs p_cal on a population")
    pev.add_argument("population", choices=["design", "holdout"])
    pev.set_defaults(func=cmd_eval)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
