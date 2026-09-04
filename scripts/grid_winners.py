#!/usr/bin/env python3
"""First-observation map-winner oracle over GRID series-state capture files.

For each (series_id, game sequenceNumber) the verdict comes from the FIRST
observation (in file order across the given time-ordered files) where that game
shows finished == true and exactly one team has won == true.  Later
contradicting observations never change the verdict; they are counted and
reported.  Finals are validated against CS2 MR12 terminal-score rules (see
VALIDATION) and games whose terminal state is ambiguous are excluded.

Since the game is indexed by (series_id, map_number) rather than by a numeric
game id, the map of a "best-of-3" contest never spills into the next series --
keys are namespaced per series.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = ["main", "process_lines"]

# CS2 MR12 overtime targets step +3 per OT period from the regulation target.
REGULATION_TARGET = 13


def _target_after(loser_score: int) -> int:
    """Smallest terminal target that can follow a given loser score.

    Regulation ends when a team reaches 13 with the loser at most 11 (so a
    13-12 line is impossible: at 12-12 the board goes to overtime).  Each
    overtime period adds 3, so the achievable (winner, loser) bands are
    13:{0..11}, 16:{12..14}, 19:{15..17}, 22:{18..20}, ... .
    """
    if loser_score < REGULATION_TARGET - 1:  # loser < 12 -> regulation
        return REGULATION_TARGET
    period = (loser_score - (REGULATION_TARGET - 1)) // 3 + 1
    return REGULATION_TARGET + 3 * period


def _final_reason(winner_score, loser_score):
    """Return the CSV2 MR12 terminal-score violation reason, or None if valid.

    Valid final iff winner > loser, winner >= 13, and winner >= the smallest
    target in {13,16,19,22,...} strictly greater than loser.  This correctly
    rejects 13-12 (impossible -> target becomes 16) and accepts 16-14 OT.
    """
    try:
        ws = int(winner_score)
        ls = int(loser_score)
    except (TypeError, ValueError):
        return f"impossible_score:{winner_score}-{loser_score}"
    target = _target_after(ls)
    if ws < REGULATION_TARGET or ws <= ls or ws < target:
        return f"impossible_score:{ws}-{ls}"
    return None


def process_lines(lines):
    """Reduce capture lines (any iterable of raw JSON lines) to a result dict."""
    verdicts: dict[tuple, dict] = {}  # (series_id, seq) -> first-obs verdict
    saw_finished: set = set()          # (series_id, seq) seen with finished==true
    contrad = 0
    lines_read = 0
    series_seen: set = set()

    for line in lines:
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(obj, dict) or obj.get("type") != "ss":
            continue
        lines_read += 1
        series_id = obj.get("series_id")
        ts_ns = obj.get("ts_ns")
        if series_id is not None:
            series_seen.add(series_id)
        state = (obj.get("data") or {}).get("seriesState")
        if not isinstance(state, dict):
            continue
        for game in state.get("games") or []:
            if not isinstance(game, dict) or not game.get("finished"):
                continue
            seq = game.get("sequenceNumber")
            if seq is None:
                continue
            key = (series_id, seq)
            saw_finished.add(key)
            teams = game.get("teams") or []
            won = [t for t in teams if isinstance(t, dict) and t.get("won")]
            if len(won) != 1:
                # Zero or two winners: no first-observation verdict available
                # from this line.  seen as finished; resolved later if a
                # definitive line appears, otherwise excluded as ambiguous.
                continue
            winner = won[0]
            loser = next((t for t in teams if t is not winner), None)
            wname = winner.get("name")
            if wname is None:
                continue
            wscore = winner.get("score")
            lscore = loser.get("score") if isinstance(loser, dict) else None
            if key in verdicts:
                prev = verdicts[key]
                if (
                    prev["winner"] != wname
                    or prev["winner_score"] != wscore
                    or prev["loser_score"] != lscore
                ):
                    contrad += 1
            else:
                lname = loser.get("name") if isinstance(loser, dict) else None
                verdicts[key] = {
                    "series_id": series_id,
                    "map_number": seq,
                    "winner": wname,
                    "loser": lname,
                    "winner_score": wscore,
                    "loser_score": lscore,
                    "first_seen_ts_ns": ts_ns,
                }

    excluded = []
    # Finished but never resolved definitively -> ambiguous winner.
    for key in saw_finished:
        if key in verdicts:
            continue
        excluded.append(
            {"series_id": key[0], "map_number": key[1], "reason": "ambiguous_winner"}
        )
    # Resolved finals that violate CS2 MR12 terminal-score rules.
    winners = {}
    for rec in verdicts.values():
        reason = _final_reason(rec["winner_score"], rec["loser_score"])
        if reason is not None:
            excluded.append(
                {
                    "series_id": rec["series_id"],
                    "map_number": rec["map_number"],
                    "reason": reason,
                }
            )
            continue
        out = dict(rec)
        out["overtime"] = rec["winner_score"] > REGULATION_TARGET
        winners[f"{rec['series_id']}:{rec['map_number']}"] = out

    excluded.sort(key=lambda e: (str(e["series_id"]), e["map_number"], e["reason"]))
    return {
        "winners": winners,
        "excluded": excluded,
        "contradictions": contrad,
        "series_seen": len(series_seen),
        "lines_read": lines_read,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="First-observation map-winner oracle over GRID series-state JSONL."
    )
    ap.add_argument("files", nargs="+", help="time-ordered JSONL capture files")
    ap.add_argument("--out", help="also write the JSON result to this file")
    args = ap.parse_args(argv)

    lines = []
    for path in args.files:
        try:
            with open(path) as fh:
                lines.extend(fh)
        except OSError as exc:
            print(f"grid_winners: cannot read {path}: {exc}", file=sys.stderr)
            return 1

    result = process_lines(lines)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        try:
            Path(args.out).write_text(text)
        except OSError as exc:
            print(f"grid_winners: cannot write {args.out}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())