"""Verdict analyzer: shadow instances + capture → progress vs the frozen bars.

Joins each recorded instance to its round's eventual resolution (winner oracle
over the series' captured snapshots, first observation wins) and to the book
state recorded at detection, then reports per hypothesis/cell: N, series
spread, resolution rate (Wilson CI), quote availability, and taker paper PnL
at the recorded ask with a by-series bootstrap CI. Deterministic, offline,
read-only. v1 accounting is taker-at-recorded-ask; maker fill inference from
the captured stream is the v2 layer the frozen bars ultimately require.

Run: uv run python -m gridbot.instruments.verdict [--replay] [--include-test]
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict

from gridbot import config
from gridbot.corpus import rows as r
from gridbot.instruments.market_match import _coverage, _tokens

# Frozen entry-price triggers from the registry texts (paper eligibility).
ENTRY_ASK = {"H-MANADV1": 0.85, "H-ECONBUY1": 0.78, "H-PISTOLCARRY1": 0.80}
BAR_N = {"H-MANADV1": 80, "H-ECONBUY1": 120, "H-PISTOLCARRY1": 100, "H-MODELENTRY1": 0}


def _iter_jsonl(path):
    with open(path) as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_instances(replay: bool, include_test: bool) -> list[dict]:
    prefix = "replay" if replay else "instances"
    out: list[dict] = []
    for path in sorted((config.DATA_DIR / "shadow").glob(f"{prefix}-*.jsonl")):
        for rec in _iter_jsonl(path):
            if rec.get("type") != "instance":
                continue
            if not include_test and rec.get("series_id") in config.GRID_TEST_SERIES_IDS:
                continue
            out.append(rec)
    return out


def build_oracles(series_ids: set[str]) -> dict[str, dict[tuple[int, int], str]]:
    """Per-series (map, round) → winning side, first observation wins."""
    oracles: dict[str, dict[tuple[int, int], str]] = {sid: {} for sid in series_ids}
    for path in sorted((config.DATA_DIR / "grid").glob("series_state-*.jsonl")):
        for rec in _iter_jsonl(path):
            if rec.get("type") != "ss":
                continue
            sid = str(rec.get("series_id"))
            if sid not in oracles:
                continue
            state = (rec.get("data") or {}).get("seriesState") or {}
            for game in state.get("games") or []:
                map_number = int(game.get("sequenceNumber") or 1)
                for i, side in enumerate(r.round_winners(game)):
                    oracles[sid].setdefault((map_number, i + 1), side)
    return oracles


def _team_ask(instance: dict) -> float | None:
    """Recorded ask for the instance's own team outcome, when identifiable."""
    market = instance.get("market") or {}
    books = instance.get("books") or {}
    outcomes = market.get("outcomes") or []
    tokens = market.get("clob_token_ids") or []
    for outcome, token in zip(outcomes, tokens, strict=False):
        if _coverage(instance.get("team") or "", _tokens(str(outcome))) >= 0.6:
            top = books.get(str(token)) or {}
            return top.get("ask")
    return None


def resolve_instances(
    instances: list[dict], oracles: dict[str, dict[tuple[int, int], str]]
) -> list[dict]:
    rows: list[dict] = []
    for inst in instances:
        oracle = oracles.get(inst["series_id"], {})
        winner = oracle.get((inst["map_number"], inst["round_number"]))
        ask = _team_ask(inst)
        rows.append(
            {
                **inst,
                "resolved": winner is not None,
                "won": (winner == inst.get("side")) if winner is not None else None,
                "team_ask": ask,
            }
        )
    return rows


def _cell(row: dict) -> str:
    hyp = row["hypothesis_id"]
    if hyp == "H-ECONBUY1":
        return f"{hyp}:{row.get('gap_bucket')}"
    if hyp == "H-MODELENTRY1":
        return f"{hyp}:thr{row.get('threshold')}"
    return hyp


def _wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    z = 1.96
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return center - half, center + half


def _bootstrap_by_series(pnls_by_series: dict[str, list[float]], iters: int = 2000):
    import random

    series = list(pnls_by_series)
    if not series:
        return None
    rng = random.Random(13)
    means = []
    for _ in range(iters):
        sample: list[float] = []
        for _ in series:
            sample.extend(pnls_by_series[rng.choice(series)])
        if sample:
            means.append(sum(sample) / len(sample))
    means.sort()
    return means[int(0.025 * len(means))], means[int(0.975 * len(means))]


def summarize(rows: list[dict]) -> list[dict]:
    cells: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        cells[_cell(row)].append(row)
    out = []
    for cell, cell_rows in sorted(cells.items()):
        hyp = cell_rows[0]["hypothesis_id"]
        resolved = [c for c in cell_rows if c["resolved"]]
        wins = sum(1 for c in resolved if c["won"])
        with_ask = [c for c in resolved if c["team_ask"] is not None]
        entry_cap = ENTRY_ASK.get(hyp)
        eligible = [
            c
            for c in with_ask
            if entry_cap is None or c["team_ask"] <= entry_cap
        ]
        pnls_by_series: dict[str, list[float]] = defaultdict(list)
        for c in eligible:
            pnls_by_series[c["series_id"]].append((1.0 if c["won"] else 0.0) - c["team_ask"])
        pnls = [p for ps in pnls_by_series.values() for p in ps]
        lo, hi = _wilson(wins, len(resolved))
        ci = _bootstrap_by_series(pnls_by_series) if pnls else None
        out.append(
            {
                "cell": cell,
                "n": len(cell_rows),
                "series": len({c["series_id"] for c in cell_rows}),
                "resolved": len(resolved),
                "win_rate": round(wins / len(resolved), 4) if resolved else None,
                "win_ci": (round(lo, 4), round(hi, 4)) if resolved else None,
                "quoted": len(with_ask),
                "paper_entries": len(pnls),
                "paper_mean_pnl": round(sum(pnls) / len(pnls), 4) if pnls else None,
                "paper_pnl_ci": (round(ci[0], 4), round(ci[1], 4)) if ci else None,
                "bar_n": BAR_N.get(hyp, 0),
            }
        )
    return out


def compact_summary() -> list[str]:
    """Short per-cell progress lines for the nightly report (real instances only)."""
    instances = load_instances(replay=False, include_test=False)
    if not instances:
        return ["verdict: no real instances yet"]
    oracles = build_oracles({i["series_id"] for i in instances})
    lines = []
    for s in summarize(resolve_instances(instances, oracles)):
        rate = "" if s["win_rate"] is None else f" win {s['win_rate']:.2f}"
        pnl = "" if s["paper_mean_pnl"] is None else f" pnl {s['paper_mean_pnl']:+.3f}×{s['paper_entries']}"
        bar = f"/{s['bar_n']}" if s["bar_n"] else ""
        lines.append(f"  {s['cell']}: {s['n']}{bar} n·{s['series']}ser{rate}{pnl}")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--replay", action="store_true", help="analyze replay-*.jsonl instead")
    ap.add_argument("--include-test", action="store_true", help="include GRID-TEST fixtures")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args()

    instances = load_instances(args.replay, args.include_test)
    if not instances:
        print("no instances recorded yet")
        return
    oracles = build_oracles({i["series_id"] for i in instances})
    summary = summarize(resolve_instances(instances, oracles))
    if args.json:
        print(json.dumps(summary, indent=1))
        return
    print(f"{'cell':34} {'n':>4} {'ser':>3} {'res':>4} {'win%':>6} {'winCI':>15} "
          f"{'quoted':>6} {'paper':>5} {'pnl':>7} {'pnlCI':>16} {'barN':>5}")
    for s in summary:
        print(
            f"{s['cell']:34} {s['n']:>4} {s['series']:>3} {s['resolved']:>4} "
            f"{('' if s['win_rate'] is None else format(s['win_rate'], '.3f')):>6} "
            f"{s['win_ci'] or ''!s:>15} {s['quoted']:>6} {s['paper_entries']:>5} "
            f"{('' if s['paper_mean_pnl'] is None else format(s['paper_mean_pnl'], '+.4f')):>7} "
            f"{s['paper_pnl_ci'] or ''!s:>16} {s['bar_n']:>5}"
        )


if __name__ == "__main__":
    main()
