"""H-POSTSHOCKFADE1: post-shock fade backtest (analysis only, describe-only).

█ STATUS 2026-09-03: RECONCILED 2026-09-03 to the amended brief; funnel
matches the spec-literal reference. █
The 2026-09-01 WIP state is superseded: the AMENDMENT 2026-09-03 block atop
docs/superpowers/specs/2026-09-01-postshock-fade-design.md voids the old
108/70/32 anchors and pins the SPEC-LITERAL funnel
(docs/analysis/2026-09-01-postshock-anchor-trace.md §3) as the reference.
This file now implements that funnel stage-for-stage: every §1.2 clause-1
population gate (prev_p_map >= 0.40, not ended_by_restart, market_slug
present, shock-row book alive) applies to BOTH arms (§1.3 UP mirror, pinned
2026-09-03), clause 1 reads the shock row ITSELF (a live quote after the
shock row never rescues a dead one), and clause 3 is the NEXT live quote
strictly after the shock row within 45s.  Per-gate counters print in the
anchor-trace format.  Still STRICTLY DESCRIBE-ONLY: no registry edit, no
src/, no orders.

Operationalizes the frozen design brief
docs/superpowers/specs/2026-09-01-postshock-fade-design.md EXACTLY — every
constant, gate, dedup rule, cutoff, and statistic is pinned there.  No tuning,
no substitution.  STRICTLY DESCRIBE-ONLY: no registry edit, no src/, no orders.

Subcommands:
  entries (default) — candidate SHORT-side fade of DOWN shocks + UP control
                      arm, resolved via the frozen grid_winners.py oracle,
                      --design (default) / --holdout with the §1.5 floors.
  spreads           — the §2 mechanism-C spread-widening discriminator
                      (describe-only; cannot arm anything).

POPULATION / SHOCKS (§1.1, reused from scripts/postshock_lag.py verbatim):
  data/shadow/mapentry-*.jsonl, ALL type=="mapentry" rows (slate rows
  INCLUDED — postshock_lag sequences on numeric p_map only).  Sequences keyed
  (input file, series_id, map_number, team) ordered by ts_ns.  SHOCK at row t
  when |p_map(t) - p_map(prev)| >= 0.25 (DEFAULT_SHOCK); window 300s
  (DEFAULT_WINDOW_S); converge 0.05 (DEFAULT_CONVERGE).  One active window per
  sequence; a new shock RESTARTS it (ended_by_restart) — restart-flagged
  shocks are EXCLUDED from the entry population.

ENTRY RULE (§1.2, frozen):
  - DOWN shocks, prev_p_map >= 0.40 (fallen-favorite filter, DEVIATION 1),
    not ended_by_restart, market_slug present, shock-row book alive
    (bid and ask numeric, > 0, ask > bid).
  - STALENESS GATE: shock-row bid - p_map >= 0.10.
  - PERSISTENCE: the next same-sequence live quote within 45s must show
    bid - p_map >= 0.05, else no entry (slot not consumed).
  - Taker entry price = 1 - bid at the shock row (short the favorite = buy
    the No leg; mapentry.py side_ask convention).  Maker leg (describe-only)
    posts one tick inside the taker price with the 150s TTL fill proxy and
    the measured -6.5pt fill-selection cost flagged; the taker leg carries
    the bar.
  - Offset cap +30s: any fill/mark later than shock+30s is outside the
    mechanism and excluded from edge accounting.
  - Settlement cutoff: quotes at/after the oracle's map-terminal observation
    (first_seen_ts_ns) for (series_id, map_number) are excluded.
  - Dedup: ONE entry per (series_id, map_number, market_slug), earliest
    qualifying shock wins.

NEGATIVE CONTROL (§1.3): UP shocks mirrored — LONG the riser at ask, gate
  p_map - ask >= 0.10, persistence p_map - ask >= 0.05 within 45s, same
  dedup/settlement/offsets.  Expected ~no edge.

RESOLUTION (§1.4): scripts/grid_winners.py first-observation map-winner oracle
  (subprocess, pure consumer).  WON iff the bet wins: candidate (short the
  shocked team) wins iff the shocked team LOST the map; control (long the
  riser) wins iff the riser WON.  Unresolved maps excluded and counted.
  Stats: per-arm win rate, mean settled edge, series-clustered bootstrap
  (resample series_id, 1000 reps, seed 17; NEVER row-pooled).

HOLDOUT (§1.5, BOOKPIN1): series first mapped at/after 1788206026000000000 ns
  (2026-08-31T19:53:46Z) per data/pmus/mappings-*.jsonl.  --design = pre-
  holdout only (IN-SAMPLE DESIGN EVIDENCE).  --holdout refuses to print
  metrics below 40 resolved DEDUPED candidate entries AND 15 distinct holdout
  series -> INCONCLUSIVE_STARVED with accrual counts.

Run: uv run python scripts/postshock_fade.py [--design|--holdout] | spreads
"""

from __future__ import annotations

import argparse
import bisect
import collections
import datetime
import glob
import json
import math
import random
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from postshock_lag import (
    DEFAULT_CONVERGE,
    DEFAULT_SHOCK,
    DEFAULT_WINDOW_S,
    NS_PER_S,
    _seq_key,
    detect,
    load_data,
)

DATA = ROOT / "data"

# §1.2 frozen gates
FALLEN_FAVORITE_MIN = 0.40   # prev_p_map >= 0.40 (DOWN arm, DEVIATION 1)
STALENESS_GATE = 0.10        # |gap| at shock row >= 0.10
PERSIST_WINDOW_S = 45        # next same-sequence live quote within 45s
PERSIST_GATE = 0.05          # persistence gap >= 0.05
OFFSET_CAP_S = 30            # fills/marks later than +30s are outside the mechanism
MAKER_TICK = 0.01            # maker posts one tick better than the taker price
MAKER_TTL_S = 150            # standing MAKER_TTL_S (NOTE6 fill proxy window)

# §1.4 statistics
BOOTSTRAP_N = 1000
BOOT_SEED = 17               # CROSSIN1 continuity

# §1.5 holdout (BOOKPIN1)
HOLDOUT_CUTOFF_NS = 1788206026000000000  # 2026-08-31T19:53:46Z
HOLDOUT_MIN_CANDIDATE_ENTRIES = 40
HOLDOUT_MIN_SERIES = 15

# §2 spread discriminator
PRE_WINDOW_S = 300
GAP_WINDOW_S = 30

IN_SAMPLE_LABEL = "IN-SAMPLE DESIGN EVIDENCE — not a verdict"


# --------------------------------------------------------------------------
# loading / shock detection


def load_mapentry_rows(pattern: str | None = None):
    """(rows, load_stats) via postshock_lag.load_data (per-file provenance)."""
    pattern = pattern or str(DATA / "shadow" / "mapentry-*.jsonl")
    return load_data(sorted(glob.glob(pattern)))


def run_detect(rows: list[dict]) -> dict:
    """Shock detection with the pinned postshock_lag defaults."""
    return detect(rows, DEFAULT_SHOCK, DEFAULT_WINDOW_S, DEFAULT_CONVERGE)


def sequences_by_key(rows: list[dict]) -> SeqIndex:
    """Index rows by postshock_lag's exact sequence key, ts-sorted.

    detect() records do not carry the per-file provenance tag, so a record
    whose (series_id, map_number, team) has rows in more than one input file
    cannot name its sequence directly.  SeqIndex resolves it data-driven:
    the shock's ts_ns must appear as a row ts in exactly one candidate file's
    sequence (p_map breaks a residual tie).  Sequences are never bridged
    across files — the per-file partition is postshock_lag's own.
    """
    return SeqIndex(rows)


class SeqIndex:
    """Per-file-partitioned sequence index with rec->rows resolution."""

    def __init__(self, rows: list[dict]):
        seqs: dict[tuple, list[dict]] = collections.defaultdict(list)
        for r in rows:
            if not isinstance(r, dict):
                continue
            ts = r.get("ts_ns")
            if not isinstance(ts, int) or isinstance(ts, bool):
                continue
            seqs[_seq_key(r)].append(r)
        self.by_key: dict[tuple, list[dict]] = {}
        self.ts_by_key: dict[tuple, list[int]] = {}
        self.tags_by_identity: dict[tuple, set] = collections.defaultdict(set)
        for key, seq in seqs.items():
            seq.sort(key=lambda r: r["ts_ns"])  # stable: ties keep file order
            self.by_key[key] = seq
            self.ts_by_key[key] = [r["ts_ns"] for r in seq]
            self.tags_by_identity[key[1:]].add(key[0])
        self.n_ambiguous_resolved = 0
        self.n_ambiguous_unresolved = 0

    def for_rec(self, rec: dict) -> tuple[list[dict], str]:
        """(sequence rows for a detect() record, status)."""
        sid, mn, team = rec.get("series_id"), rec.get("map_number"), rec.get("team")
        tags = self.tags_by_identity.get((sid, mn, team), set())
        if not tags:
            return [], "no_sequence"
        if len(tags) == 1:
            return self.by_key[(next(iter(tags)), sid, mn, team)], "unique"
        # Same (series, map, team) rows exist under multiple input files: pin
        # the file by the shock's own ts_ns (unique per physical row).
        ts = rec["shock_ts_ns"]
        hits = []
        for tag in sorted(tags, key=repr):
            key = (tag, sid, mn, team)
            tl = self.ts_by_key[key]
            i = bisect.bisect_left(tl, ts)
            if i < len(tl) and tl[i] == ts:
                hits.append(key)
        if len(hits) == 1:
            self.n_ambiguous_resolved += 1
            return self.by_key[hits[0]], "resolved_by_ts"
        if len(hits) > 1:
            # Tie-break on the record's own p_map (6dp-rounded by detect()).
            hits = [k for k in hits
                    if any(_finite(r.get("p_map")) == rec.get("p_map")
                           for r in self.by_key[k] if r["ts_ns"] == ts)]
            if len(hits) == 1:
                self.n_ambiguous_resolved += 1
                return self.by_key[hits[0]], "resolved_by_p_map"
            self.n_ambiguous_unresolved += 1
            return [], "ambiguous_unresolved"
        # No candidate file carries the exact shock ts (capture hole rewrite):
        # fall back to the earliest-tag sequence sharing the identity.
        self.n_ambiguous_unresolved += 1
        key = (min(tags, key=repr), sid, mn, team)
        return self.by_key[key], "fallback_first_tag"


def _finite(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def book_top(row: dict) -> tuple[float, float] | None:
    """Valid two-sided LONG-side book top (bid, ask) or None (dead book).

    §1.2 gate 1: bid and ask numeric, > 0, ask > bid (strictly); zero prices
    are dead-book placeholders per mapentry.py parse_bbo.
    """
    bid, ask = _finite(row.get("bid")), _finite(row.get("ask"))
    if bid is None or ask is None:
        return None
    if not (bid > 0.0 and ask > 0.0 and ask > bid):
        return None
    return bid, ask


def arm_gap(arm: str, bid: float, ask: float, p_map: float) -> float:
    """Stale-top gap for the arm's orientation.

    DOWN (candidate, short the shocked team): the book still prices the
    favorite high -> gap = bid - p_map.  UP (control, long the riser):
    the book lags below -> gap = p_map - ask.
    """
    return (bid - p_map) if arm == "down" else (p_map - ask)


def taker_price(arm: str, bid: float, ask: float) -> float:
    """Executable taker price.  DOWN: short the favorite = 1 - bid (buy No).
    UP: long the riser = ask.  mapentry.py side_ask convention."""
    return round((1.0 - bid) if arm == "down" else ask, 6)


def find_shock_row(seq: list[dict], rec: dict) -> dict | None:
    """The shock row itself: ts_ns match within its sequence.

    Ties (two rows sharing a ts_ns) resolve to the row whose p_map is
    closest to the record's (records carry p_map rounded to 6dp).
    """
    ts = rec["shock_ts_ns"]
    cands = [r for r in seq
             if r.get("ts_ns") == ts and _finite(r.get("p_map")) is not None]
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    p = rec["p_map"]
    return min(cands, key=lambda r: abs((_finite(r.get("p_map")) or 0.0) - p))


# --------------------------------------------------------------------------
# entry simulation (§1.2 / §1.3)


def _persistence_ok(
    arm: str,
    seq: list[dict],
    ts0: int,
    terminal: int | None,
) -> bool:
    """§1.2 clause 3 (amendment wording, 2026-09-03): persistence = the NEXT
    same-sequence live quote STRICTLY AFTER the shock row, within 45s of the
    shock ts_ns, showing gap >= 0.05.  Non-live rows are skipped inside the
    window (they are not quotes); the first live quote decides — any-vs-next
    was measured immaterial at this cadence, but the text pins NEXT.  Quotes
    at/after the map-terminal observation are settlement-informed and
    invisible (§1.2 clause 5).  No live quote in window => no entry.
    """
    hi = ts0 + PERSIST_WINDOW_S * NS_PER_S
    for r in seq:
        if r["ts_ns"] <= ts0:
            continue
        if r["ts_ns"] > hi:
            break
        if terminal is not None and r["ts_ns"] >= terminal:
            continue
        top = book_top(r)
        p = _finite(r.get("p_map"))
        if top is None or p is None:
            continue  # not a live quote; keep scanning within 45s
        return arm_gap(arm, top[0], top[1], p) >= PERSIST_GATE
    return False


def simulate_entries(
    shocks: list[dict],
    seq_index: SeqIndex,
    terminal_by_map: dict[tuple, int] | None = None,
) -> tuple[list[dict], dict, dict]:
    """Spec-literal entry funnel (§1.2/§1.3, AMENDMENT 2026-09-03).

    Stage-for-stage the reference funnel of
    docs/analysis/2026-09-01-postshock-anchor-trace.md §3, applied per arm
    in the reference gate ORDER: shocks -> priced (n_book_obs > 0) ->
    prev_p_map >= 0.40 -> not ended_by_restart -> market_slug present ->
    shock-row book alive (the shock row ITSELF: bid and ask numeric, > 0,
    ask > bid — a live quote after the shock row never rescues a dead one)
    -> staleness gate >= 0.10 on the shock row -> persistence (the NEXT live
    quote strictly after the shock row within 45s, gap >= 0.05) -> dedup
    (ONE entry per (series_id, map_number, market_slug), earliest qualifying
    shock wins; persistence-failed shocks consume nothing).

    Amendment 2026-09-03: every clause-1 population gate mirrors onto the UP
    control arm (§1.3), including prev_p_map >= 0.40 — the old DOWN-only
    reading is void.

    Returns (entries, funnel, seq_res_counts).  ``funnel[arm]`` carries the
    per-stage counts under the anchor-trace labels plus diagnostics
    (shock_row_not_found / after_terminal / dedup_slots_lost).
    ``seq_res_counts`` reports how SeqIndex pinned each slugged record to its
    per-file sequence rows.

    ``terminal_by_map`` maps (series_id, map_number) -> the oracle's first
    terminal observation ts_ns (§1.2 clause 5 / crossin F1 pin): entries at
    or after it, and persistence quotes at/after it, are settlement-informed
    and excluded.

    Entries carry ``_seq_key`` (the postshock_lag sequence key parts) for the
    describe-only maker-leg join.
    """
    funnel: dict[str, dict] = {}
    entries: list[dict] = []
    res_counts: collections.Counter = collections.Counter()
    for arm in ("down", "up"):
        arm_shocks = [r for r in shocks if r["direction"] == arm]
        priced = [r for r in arm_shocks if r["n_book_obs"] > 0]
        # prev_p_map >= 0.40 on BOTH arms (§1.1 DEVIATION 1 mirrored onto
        # the UP population by §1.3, pinned 2026-09-03).  Missing prev -> -1
        # sentinel, as in the reference funnel.
        prev_ok = [
            r for r in priced
            if (_finite(r.get("prev_p_map")) or -1.0) >= FALLEN_FAVORITE_MIN]
        no_res = [r for r in prev_ok if not r["ended_by_restart"]]
        slugged = [r for r in no_res if r.get("market_slug")]
        alive: list[tuple] = []
        n_row_missing = 0
        for rec in slugged:
            seq, status = seq_index.for_rec(rec)
            res_counts[f"seq_{status}"] += 1
            sr = find_shock_row(seq, rec) if seq else None
            if sr is None:
                n_row_missing += 1
                continue
            top = book_top(sr)
            p = _finite(sr.get("p_map"))
            if top is None or p is None:
                continue  # dead book ON THE SHOCK ROW: no rescue, no entry
            alive.append((rec, seq, sr, top[0], top[1], p))
        gated = [t for t in alive
                 if arm_gap(arm, t[3], t[4], t[5]) >= STALENESS_GATE]
        persisted: list[dict] = []
        n_after_terminal = 0
        for rec, seq, sr, bid, ask, p in gated:
            ts0 = rec["shock_ts_ns"]
            terminal = (terminal_by_map or {}).get(
                (str(rec["series_id"]), rec["map_number"]))
            if terminal is not None and ts0 >= terminal:
                n_after_terminal += 1
                continue
            if not _persistence_ok(arm, seq, ts0, terminal):
                continue  # failed persistence does NOT consume the dedup slot
            entry_px = taker_price(arm, bid, ask)
            persisted.append({
                "arm": arm,
                "series_id": str(rec["series_id"]),
                "map_number": rec["map_number"],
                "team": rec["team"],
                "opponent": sr.get("opponent"),
                "market_slug": str(rec["market_slug"]),
                "shock_ts_ns": ts0,
                "p_map": round(p, 6),
                "prev_p_map": rec["prev_p_map"],
                "delta": rec["delta"],
                "bid": bid,
                "ask": ask,
                "entry_price": entry_px,
                "maker_post_price": round(entry_px - MAKER_TICK, 6),
                "staleness_gap": round(arm_gap(arm, bid, ask, p), 6),
                "_seq_key": list(_seq_key(sr)),
            })
        # Dedup per arm (the reference funnel dedups within each arm): ONE
        # entry per (series_id, map_number, market_slug), earliest shock wins.
        seen: dict[tuple, dict] = {}
        for cand in sorted(persisted,
                           key=lambda x: (x["shock_ts_ns"], x["series_id"])):
            key = (cand["series_id"], cand["map_number"], cand["market_slug"])
            seen.setdefault(key, cand)
        arm_entries = sorted(seen.values(), key=lambda x: x["shock_ts_ns"])
        funnel[arm] = {
            "shocks": len(arm_shocks),
            "priced": len(priced),
            "prev>=0.40": len(prev_ok),
            "no-restart": len(no_res),
            "slug": len(slugged),
            "alive": len(alive),
            "gate": len(gated),
            "persist-raw": len(persisted),
            "deduped": len(seen),
            "series": len({e["series_id"] for e in arm_entries}),
            "shock_row_not_found": n_row_missing,
            "after_terminal": n_after_terminal,
            "dedup_slots_lost": len(persisted) - len(seen),
        }
        entries.extend(arm_entries)
    entries.sort(key=lambda x: x["shock_ts_ns"])
    return entries, funnel, dict(res_counts)


# --------------------------------------------------------------------------
# resolution (frozen oracle, §1.4)


def load_map_winners(state_pattern: str | None = None) -> tuple[dict, list[dict]]:
    """(winners, excluded) from the frozen map-winner oracle (pure consumer)."""
    state_pattern = state_pattern or str(DATA / "grid" / "series_state-*.jsonl")
    files = sorted(glob.glob(state_pattern))
    if not files:
        return {}, []
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "grid_winners.py"), *files],
        capture_output=True, text=True, check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"grid_winners failed: {res.stderr[-400:]}")
    payload = json.loads(res.stdout)
    return payload.get("winners", {}), payload.get("excluded", [])


def resolve(row: dict, winners: dict, excluded: list[dict]) -> tuple[bool | None, str]:
    """(won for the bet, status); None won => unresolved (never guessed).

    Candidate (short the shocked team): WON iff the shocked team LOST the map.
    Control (long the riser): WON iff the riser WON the map.
    """
    if row.get("map_number") is None:
        return None, "no_map_number"
    mkey = f"{row['series_id']}:{row['map_number']}"
    w = winners.get(mkey)
    if not w:
        for ex in excluded:
            if f"{ex.get('series_id')}:{ex.get('map_number')}" == mkey:
                return None, f"excluded:{ex.get('reason')}"
        return None, "no_map_winner_in_capture"
    team = row.get("team")
    if team == w.get("winner"):
        return (row["arm"] == "up"), "resolved"
    if team == w.get("loser"):
        return (row["arm"] == "down"), "resolved"
    return None, "team_unmatched_to_winner_or_loser"


# --------------------------------------------------------------------------
# maker fill proxy (describe-only, NOTE6)


def maker_fills(
    entry: dict,
    seq: list[dict],
    terminal: int | None,
) -> tuple[int | None, int | None]:
    """(fill_offset_s, fill_ts_ns) under the NOTE6 fill proxy.

    A fill is inferred at the FIRST later same-sequence snapshot within TTL
    150s showing our side's effective ask <= posted price.  DOWN arm (No
    leg): effective ask = 1 - bid_row.  UP arm (Yes leg): ask_row.  Rows
    at/after the terminal observation are excluded.  Returns (None, None)
    when never filled.
    """
    posted = entry["maker_post_price"]
    ts0 = entry["shock_ts_ns"]
    hi = ts0 + MAKER_TTL_S * NS_PER_S
    for r in seq:
        if r["ts_ns"] <= ts0:
            continue
        if r["ts_ns"] > hi:
            break
        if terminal is not None and r["ts_ns"] >= terminal:
            continue
        top = book_top(r)
        if top is None:
            continue
        eff = round((1.0 - top[0]) if entry["arm"] == "down" else top[1], 6)
        if eff <= posted:
            return round((r["ts_ns"] - ts0) / NS_PER_S, 6), r["ts_ns"]
    return None, None


def maker_summary(entries: list[dict], seq_index: SeqIndex,
                  terminal_by_map: dict[tuple, int]) -> dict:
    """Describe-only maker leg (§1.2): NOTE6 fill proxy within TTL 150s,
    edge accounting restricted to fills inside the +30s offset cap.  The
    measured -6.5pt fill-selection cost applies: filled EV ran BELOW
    unfilled EV at identical price — the maker leg can only look BETTER
    than reality; the taker leg carries the bar."""
    n_res = sum(1 for e in entries if e["won"] is not None)
    filled = filled_cap = 0
    edges_full: list[float] = []
    edges_cap: list[float] = []
    offsets: list[float] = []
    for e in entries:
        if e["won"] is None:
            continue
        seq, _status = seq_index.for_rec({
            "series_id": e["series_id"],
            "map_number": e["map_number"],
            "team": e["team"],
            "shock_ts_ns": e["shock_ts_ns"],
            "p_map": e["p_map"],
        })
        off_s, _ts = maker_fills(
            e, seq, terminal_by_map.get((e["series_id"], e["map_number"])))
        if off_s is None:
            continue
        filled += 1
        edges_full.append((1.0 if e["won"] else 0.0) - e["maker_post_price"])
        if off_s <= OFFSET_CAP_S:
            filled_cap += 1
            edges_cap.append((1.0 if e["won"] else 0.0) - e["maker_post_price"])
            offsets.append(off_s)
    return {
        "describe_only": True,
        "fill_selection_caveat": (
            "NOTE6: filled EV -3.5pt vs unfilled +7.5pt at identical price "
            "(-6.5pt fill-selection cost); the maker leg can only look "
            "BETTER than reality — the taker leg carries the bar"),
        "n_resolved": n_res,
        "n_filled": filled,
        "fill_rate": (filled / n_res) if n_res else None,
        "n_filled_within_offset_cap": filled_cap,
        "mean_settled_edge_filled": (
            sum(edges_full) / len(edges_full)) if edges_full else None,
        "mean_settled_edge_filled_within_cap": (
            sum(edges_cap) / len(edges_cap)) if edges_cap else None,
        "median_fill_offset_s": (
            statistics.median(offsets) if offsets else None),
    }


# --------------------------------------------------------------------------
# holdout split (§1.5, BOOKPIN1)


def series_first_mapped_ns(pattern: str | None = None) -> dict[str, int]:
    """min ts_ns per grid_series_id across data/pmus/mappings-*.jsonl."""
    pattern = pattern or str(DATA / "pmus" / "mappings-*.jsonl")
    first: dict[str, int] = {}
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid, ts = r.get("grid_series_id"), r.get("ts_ns")
                if sid is None or ts is None:
                    continue
                sid, ts = str(sid), int(ts)
                if sid not in first or ts < first[sid]:
                    first[sid] = ts
    return first


def split_holdout(
    rows: list[dict], first_mapped: dict[str, int]
) -> tuple[list[dict], list[dict]]:
    """(pre-holdout rows, holdout rows) on the BOOKPIN1 first-mapped cutoff.

    ``rows`` need a string ``series_id``; ``first_mapped`` is keyed by the
    str() of grid_series_id.
    """
    design, holdout = [], []
    for r in rows:
        fm = first_mapped.get(r["series_id"])
        (holdout if (fm is not None and fm >= HOLDOUT_CUTOFF_NS) else design).append(r)
    return design, holdout


# --------------------------------------------------------------------------
# statistics (§1.4 — series-clustered bootstrap, seed 17, never row-pooled)


def _cluster_bootstrap(
    by_series: dict[str, list[float]], rng_seed: int = BOOT_SEED
) -> tuple[float, float] | None:
    """95% percentile CI for the mean, resampling SERIES."""
    series = list(by_series)
    if len(series) < 3:
        return None
    rng = random.Random(rng_seed)
    vals = []
    for _ in range(BOOTSTRAP_N):
        sample: list[float] = []
        for _ in series:
            sample.extend(by_series[rng.choice(series)])
        if sample:
            vals.append(sum(sample) / len(sample))
    vals.sort()
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals))]


def edge_rows(entries: list[dict]) -> dict[str, list[float]]:
    """Resolved entries: per-series settled edges (won - entry_price)."""
    by_series: dict[str, list[float]] = collections.defaultdict(list)
    for e in entries:
        if e["won"] is not None:
            by_series[e["series_id"]].append(
                (1.0 if e["won"] else 0.0) - e["entry_price"])
    return by_series


def win_rows(entries: list[dict]) -> dict[str, list[float]]:
    """Resolved entries: per-series win indicators (1.0/0.0)."""
    by_series: dict[str, list[float]] = collections.defaultdict(list)
    for e in entries:
        if e["won"] is not None:
            by_series[e["series_id"]].append(1.0 if e["won"] else 0.0)
    return by_series


def summarize(entries: list[dict]) -> dict:
    resolved = [e for e in entries if e["won"] is not None]
    won = [e for e in resolved if e["won"]]
    by_edge, by_win = edge_rows(entries), win_rows(entries)
    n_obs = sum(len(v) for v in by_edge.values())
    mean_edge = (sum(x for v in by_edge.values() for x in v) / n_obs) if n_obs else None
    return {
        "n_entries": len(entries),
        "n_series": len({e["series_id"] for e in entries}),
        "n_resolved": len(resolved),
        "n_won": len(won),
        "n_lost": len(resolved) - len(won),
        "n_unresolved": len(entries) - len(resolved),
        "win_rate": (len(won) / len(resolved)) if resolved else None,
        "win_rate_ci": _cluster_bootstrap(by_win),
        "mean_settled_edge": mean_edge,
        "edge_ci": _cluster_bootstrap(by_edge),
        "median_entry_price": (
            statistics.median(e["entry_price"] for e in entries)
            if entries else None),
        "unres_reasons": dict(collections.Counter(
            e["res_status"] for e in entries if e["won"] is None)),
    }


# --------------------------------------------------------------------------
# entries subcommand


def run_entries(mode: str, mapentry_pattern: str | None = None) -> dict:
    rows, load_stats = load_mapentry_rows(mapentry_pattern)
    det = run_detect(rows)
    shocks = det["shocks"]
    seq_index = sequences_by_key(rows)

    first_mapped = {str(k): v for k, v in series_first_mapped_ns().items()}
    pool_shocks = [
        {**s, "series_id": str(s["series_id"])} for s in shocks]
    design_shocks, holdout_shocks = split_holdout(pool_shocks, first_mapped)
    pool = design_shocks if mode == "design" else holdout_shocks

    winners, excluded = load_map_winners()
    terminal_by_map = {
        (str(w["series_id"]), w["map_number"]): w["first_seen_ts_ns"]
        for w in winners.values() if w.get("first_seen_ts_ns") is not None
    }
    entries, funnel, seq_res = simulate_entries(
        pool, seq_index, terminal_by_map)
    for e in entries:
        e["won"], e["res_status"] = resolve(e, winners, excluded)
    arms = {a: summarize([e for e in entries if e["arm"] == a])
            for a in ("down", "up")}
    for a in ("down", "up"):
        arms[a]["maker"] = maker_summary(
            [e for e in entries if e["arm"] == a], seq_index, terminal_by_map)

    cand = [e for e in entries if e["arm"] == "down"]
    return {
        "hypothesis_id": "H-POSTSHOCKFADE1",
        "mode": mode,
        "label": IN_SAMPLE_LABEL if mode == "design" else "HOLDOUT ACCRUAL",
        "describe_only": True,
        "params": {
            "shock": DEFAULT_SHOCK, "window_s": DEFAULT_WINDOW_S,
            "converge": DEFAULT_CONVERGE,
            "fallen_favorite_min": FALLEN_FAVORITE_MIN,
            "staleness_gate": STALENESS_GATE,
            "persist_window_s": PERSIST_WINDOW_S,
            "persist_gate": PERSIST_GATE,
            "offset_cap_s": OFFSET_CAP_S, "maker_ttl_s": MAKER_TTL_S,
            "bootstrap_n": BOOTSTRAP_N, "bootstrap_seed": BOOT_SEED,
            "holdout_cutoff_ns": HOLDOUT_CUTOFF_NS,
        },
        "load_stats": load_stats,
        "shock_counts": {
            "n_shocks": len(shocks),
            "n_priced": sum(1 for s in shocks if s["n_book_obs"] > 0),
            "n_down_priced": sum(
                1 for s in shocks
                if s["direction"] == "down" and s["n_book_obs"] > 0),
            "n_down_priced_restarted": sum(
                1 for s in shocks if s["direction"] == "down"
                and s["n_book_obs"] > 0 and s["ended_by_restart"]),
        },
        "n_shocks_pool": len(pool),
        "n_shocks_design": len(design_shocks),
        "n_shocks_holdout": len(holdout_shocks),
        "funnel": funnel,
        "sequence_resolution": seq_res,
        "arms": arms,
        "n_map_winners": len(winners),
        "holdout_accrual": {
            "n_candidate_entries_total": arms["down"]["n_entries"],
            "n_candidate_entries_resolved": arms["down"]["n_resolved"],
            "n_candidate_series": len({e["series_id"] for e in cand}),
        },
    }


def fmt(x, dp=4):
    return "n/a" if x is None else f"{x:.{dp}f}"


def _cistr(ci):
    return "" if ci is None else f" [{fmt(ci[0], 3)}, {fmt(ci[1], 3)}]"


def arm_table(arms: dict) -> str:
    lines = []
    for name, label in (("down", "DOWN shocks — SHORT-side fade CANDIDATE"),
                        ("up", "UP shocks — LONG control")):
        s = arms.get(name)
        if not s:
            lines.append(f"  {label}: n/a")
            continue
        wr = s["win_rate"]
        wrstr = "n/a" if wr is None else f"{wr * 100:.1f}%"
        lines.append(
            f"  {label}\n"
            f"    entries {s['n_entries']} (series {s['n_series']})  "
            f"resolved {s['n_resolved']}  won {s['n_won']}  lost {s['n_lost']}  "
            f"unresolved {s['n_unresolved']}\n"
            f"    win% {wrstr}{_cistr(s['win_rate_ci'])}  "
            f"median entry px {fmt(s['median_entry_price'], 3)}  "
            f"mean settled edge {fmt(s['mean_settled_edge'])}"
            f"{_cistr(s['edge_ci'])}  (series-clustered, seed {BOOT_SEED})"
        )
        m = s.get("maker")
        if m:
            lines.append(
                f"    maker (describe-only): filled {m['n_filled']}/"
                f"{m['n_resolved']}  edge(all fills) "
                f"{fmt(m['mean_settled_edge_filled'])}  edge(within "
                f"+{OFFSET_CAP_S}s cap) "
                f"{fmt(m['mean_settled_edge_filled_within_cap'])}"
            )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# §2 spread-widening discriminator (describe-only)


def slug_spread_timeline(rows: list[dict]) -> dict[str, list[dict]]:
    """Per-slug chronological quote records: spread for two-sided live rows
    (bid, ask both > 0, ask > bid), plus per-row dead-side flags over ALL
    quote-bearing rows (for the one-sided death-rate caveat)."""
    tl: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        if not isinstance(r, dict):
            continue
        slug = r.get("market_slug")
        ts = r.get("ts_ns")
        if not slug or not isinstance(ts, int) or isinstance(ts, bool):
            continue
        bid, ask = _finite(r.get("bid")), _finite(r.get("ask"))
        rec = {
            "ts_ns": ts,
            "series_id": str(r.get("series_id")),
            "spread": None,
            "bid_dead": bid is None or bid <= 0,
            "ask_dead": ask is None or ask <= 0,
        }
        if bid is not None and ask is not None and bid > 0 and ask > 0 and ask > bid:
            rec["spread"] = round(ask - bid, 6)
        tl[str(slug)].append(rec)
    for recs in tl.values():
        recs.sort(key=lambda r: r["ts_ns"])
    return dict(tl)


def run_spreads(mapentry_pattern: str | None = None) -> dict:
    rows, load_stats = load_mapentry_rows(mapentry_pattern)
    det = run_detect(rows)
    tl = slug_spread_timeline(rows)

    # slug baseline: median spread over ALL its two-sided live quotes
    # corpus-wide (§2: the slug's own median, entire corpus)
    baseline: dict[str, float | None] = {}
    for slug, recs in tl.items():
        spreads = [r["spread"] for r in recs if r["spread"] is not None]
        baseline[slug] = round(statistics.median(spreads), 6) if spreads else None

    episodes: list[dict] = []
    skipped: collections.Counter = collections.Counter()
    for rec in det["shocks"]:
        if rec["direction"] != "down":
            continue
        slug = rec.get("market_slug")
        if not slug or rec["n_book_obs"] <= 0:
            skipped["no_slug_or_unpriced"] += 1
            continue
        slug = str(slug)
        base = baseline.get(slug)
        if base is None or base <= 0:
            skipped["no_live_baseline"] += 1
            continue
        t0 = rec["shock_ts_ns"]
        spreads_by = {"pre": [], "gap": [], "reconverge": []}
        dead_by = {"pre": [0, 0], "gap": [0, 0], "reconverge": [0, 0]}
        n_rows_by = {"pre": 0, "gap": 0, "reconverge": 0}
        for r in tl.get(slug, []):
            off_s = (r["ts_ns"] - t0) / NS_PER_S
            if -PRE_WINDOW_S <= off_s < 0:
                w = "pre"
            elif 0 <= off_s < GAP_WINDOW_S:
                w = "gap"
            elif GAP_WINDOW_S <= off_s <= DEFAULT_WINDOW_S:
                w = "reconverge"
            else:
                continue
            n_rows_by[w] += 1
            if r["spread"] is not None:
                spreads_by[w].append(r["spread"])
            if r["bid_dead"]:
                dead_by[w][0] += 1
            if r["ask_dead"]:
                dead_by[w][1] += 1
        if not spreads_by["gap"]:
            skipped["no_gap_window_quotes"] += 1
            continue

        def med(w, _sb=spreads_by):
            return (round(statistics.median(_sb[w]), 6)
                    if _sb[w] else None)

        med_gap, med_pre, med_rec = med("gap"), med("pre"), med("reconverge")
        episodes.append({
            "series_id": str(rec["series_id"]),
            "map_number": rec["map_number"],
            "market_slug": slug,
            "shock_ts_ns": t0,
            "ended_by_restart": rec["ended_by_restart"],
            "slug_baseline_median_spread": base,
            "median_spread_pre": med_pre,
            "median_spread_gap": med_gap,
            "median_spread_reconverge": med_rec,
            "ratio_gap": (round(med_gap / base, 6)
                          if med_gap is not None else None),
            "ratio_reconverge": (round(med_rec / base, 6)
                                 if med_rec is not None else None),
            "diff_gap_minus_pre": (round(med_gap - med_pre, 6)
                                   if med_gap is not None and med_pre is not None
                                   else None),
            "n_rows": n_rows_by,
            "one_sided_death": {
                w: {"n_rows": n_rows_by[w], "n_bid_dead": dead_by[w][0],
                    "n_ask_dead": dead_by[w][1],
                    "bid_dead_share": (round(dead_by[w][0] / n_rows_by[w], 6)
                                       if n_rows_by[w] else None),
                    "ask_dead_share": (round(dead_by[w][1] / n_rows_by[w], 6)
                                       if n_rows_by[w] else None)}
                for w in ("pre", "gap", "reconverge")},
        })

    def pooled(field):
        vals = [e[field] for e in episodes if e[field] is not None]
        return round(statistics.median(vals), 6) if vals else None

    def ci_for(field):
        by_series: dict[str, list[float]] = collections.defaultdict(list)
        for e in episodes:
            if e[field] is not None:
                by_series[e["series_id"]].append(e[field])
        return _cluster_bootstrap(by_series)

    ratios = [e["ratio_gap"] for e in episodes if e["ratio_gap"] is not None]
    recs_r = [e["ratio_reconverge"] for e in episodes
              if e["ratio_reconverge"] is not None]
    gap_med, rec_med = pooled("ratio_gap"), pooled("ratio_reconverge")
    diff_ci = ci_for("diff_gap_minus_pre")
    confirm = {
        "median_gap_ratio_above_1": bool(gap_med is not None and gap_med > 1),
        "median_reconverge_ratio_below_gap_ratio": bool(
            gap_med is not None and rec_med is not None and rec_med < gap_med),
        "gap_minus_pre_ci_excludes_zero_positive": bool(
            diff_ci is not None and diff_ci[0] > 0),
    }
    confirm["mechanism_C_pattern"] = all(confirm.values())
    confirm["null_pattern_belief_lag"] = bool(
        ratios and 0.95 <= statistics.median(ratios) <= 1.05
        and recs_r and 0.95 <= statistics.median(recs_r) <= 1.05)

    return {
        "hypothesis_id": "H-POSTSHOCKFADE1",
        "section": "2 — mechanism-C spread-widening discriminator",
        "describe_only": True,
        "cannot_arm": True,
        "params": {
            "shock": DEFAULT_SHOCK, "window_s": DEFAULT_WINDOW_S,
            "pre_window_s": PRE_WINDOW_S, "gap_window_s": GAP_WINDOW_S,
            "bootstrap_seed": BOOT_SEED,
        },
        "load_stats": load_stats,
        "n_shocks_total": len(det["shocks"]),
        "n_episodes": len(episodes),
        "skipped": dict(skipped),
        "pooled": {
            "median_gap_ratio": gap_med,
            "median_reconverge_ratio": rec_med,
            "gap_ratio_ci": ci_for("ratio_gap"),
            "reconverge_ratio_ci": ci_for("ratio_reconverge"),
            "median_diff_gap_minus_pre": pooled("diff_gap_minus_pre"),
            "diff_gap_minus_pre_ci": diff_ci,
            "n_series": len({e["series_id"] for e in episodes}),
        },
        "confirmation": confirm,
        "caveats": [
            ("Spread is only observable when BOTH sides are quoted; the test "
             "is conditional on the book being alive (CROSSIN1 F2 selection)."),
            ("A withdrawing MM may widen by pulling the BID — the side we "
             "would sell into; one-sided death rates per window are reported."),
            "Describe-only join: cannot arm anything (spec §2).",
        ],
        "episodes_sample": episodes[:20],
    }


# --------------------------------------------------------------------------
# CLI


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")
    ent = sub.add_parser("entries", help="fade backtest (default)")
    g = ent.add_mutually_exclusive_group()
    g.add_argument("--design", dest="mode", action="store_const",
                   const="design", default="design",
                   help="pre-holdout series only (default)")
    g.add_argument("--holdout", dest="mode", action="store_const",
                   const="holdout", help="holdout series only (accrual)")
    sub.add_parser("spreads", help="§2 spread-widening discriminator")
    args = ap.parse_args()
    cmd = args.cmd or "entries"

    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_json = DATA / "analysis" / f"postshockfade-{cmd}-{stamp}.json"

    if cmd == "spreads":
        res = run_spreads()
        out_json.parent.mkdir(parents=True, exist_ok=True)
        with open(out_json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2, sort_keys=True)
        p = res["pooled"]
        print("== H-POSTSHOCKFADE1 §2 spread-widening discriminator "
              "[DESCRIBE-ONLY, cannot arm] ==")
        print(f"DOWN-shock episodes with gap-window quotes: {res['n_episodes']} "
              f"(series {p['n_series']})  skipped: {res['skipped']}")
        print(f"median GAP spread ratio {fmt(p['median_gap_ratio'], 3)}"
              f"{_cistr(p['gap_ratio_ci'])}  "
              f"median RECONVERGE ratio {fmt(p['median_reconverge_ratio'], 3)}"
              f"{_cistr(p['reconverge_ratio_ci'])}")
        print(f"paired GAP-PRE median diff {fmt(p['median_diff_gap_minus_pre'], 4)}"
              f"{_cistr(p['diff_gap_minus_pre_ci'])}")
        c = res["confirmation"]
        print(f"mechanism-C pattern: {c['mechanism_C_pattern']}  "
              f"(gap>1: {c['median_gap_ratio_above_1']}  "
              f"retighten: {c['median_reconverge_ratio_below_gap_ratio']}  "
              f"diff-CI>0: {c['gap_minus_pre_ci_excludes_zero_positive']})  "
              f"null(belief-lag): {c['null_pattern_belief_lag']}")
        for cav in res["caveats"]:
            print(f"  caveat: {cav}")
        print(f"JSON: {out_json}")
        return 0

    res = run_entries(args.mode)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, sort_keys=True)

    sc, fn = res["shock_counts"], res["funnel"]
    print(f"== H-POSTSHOCKFADE1 post-shock fade [{res['label']}] ==")
    print(f"mode: {args.mode}")
    print(f"shocks: {sc['n_shocks']}  priced {sc['n_priced']}  "
          f"DOWN-priced {sc['n_down_priced']}  "
          f"(DOWN-priced ended_by_restart {sc['n_down_priced_restarted']})")
    print(f"shock pool: {res['n_shocks_pool']} "
          f"(design {res['n_shocks_design']} / holdout {res['n_shocks_holdout']})")
    print("spec-literal funnel (anchor-trace §3 format, per arm):")
    for arm, label in (("down", "DOWN candidate"), ("up", "UP control")):
        f = fn[arm]
        print(f"  [{label}] shocks {f['shocks']} priced {f['priced']} "
              f"prev>=0.40 {f['prev>=0.40']} no-restart {f['no-restart']} "
              f"slug {f['slug']} alive {f['alive']} gate {f['gate']} "
              f"persist-raw {f['persist-raw']} deduped {f['deduped']} "
              f"series {f['series']}")
        print(f"      (shock_row_not_found {f['shock_row_not_found']}  "
              f"after_terminal {f['after_terminal']}  "
              f"dedup_slots_lost {f['dedup_slots_lost']})")
    print(f"map-winner oracle: {res['n_map_winners']} resolved (series,map) keys")
    print()
    ha = res["holdout_accrual"]
    if args.mode == "holdout" and (
            ha["n_candidate_entries_resolved"] < HOLDOUT_MIN_CANDIDATE_ENTRIES
            or ha["n_candidate_series"] < HOLDOUT_MIN_SERIES):
        print(f"INCONCLUSIVE_STARVED: {ha['n_candidate_entries_resolved']} "
              f"resolved deduped candidate entries "
              f"(of {ha['n_candidate_entries_total']}) / "
              f"{ha['n_candidate_series']} candidate series — below the floor "
              f"({HOLDOUT_MIN_CANDIDATE_ENTRIES} resolved entries AND "
              f"{HOLDOUT_MIN_SERIES} series); metrics withheld "
              f"(full JSON written to {out_json}).")
        return 0
    print(arm_table(res["arms"]))
    print(f"\nJSON: {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
