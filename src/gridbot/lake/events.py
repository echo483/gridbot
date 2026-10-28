"""Raw Parquet → the typed event tape every strategy and scan reads.

The raw layer keeps capture rows exactly as they arrived. This layer makes one
*typed, title-agnostic* projection of them so research code never parses nested
GRID JSON again. Two tables come out:

``events``
    One row per (series, snapshot, game) — both teams' observable state at that
    snapshot's receive time. This is the tape a strategy walks.

``round_results``
    The resolution oracle: winner per (series, map, round), fixed by the FIRST
    snapshot that observes the round resolved. A round whose resolution is never
    observed emits nothing — labels are never fabricated (corpus F6).

``map_results``
    Winner per (series, map) from ``gridbot.corpus.winners`` — the same
    first-observation, MR12-validated oracle every instrument already resolves
    against. It is not re-derived here: two map oracles would eventually
    disagree, and then two published numbers would too.

**Title-agnostic by design.** CS2 has CT/T sides that swap at halftime; Dota 2
has Radiant/Dire. Fixed ``ct_``/``t_`` columns would either break on Dota or
force a title check into every query. So teams go in two slots, ``a`` and ``b``,
each carrying an explicit ``*_side`` label. For CS2 the CT side is placed in slot
``a`` when it is resolvable, so ``WHERE a_side = 'ct'`` reproduces the historical
CT-framed population exactly. For any other title the slots are node order and
the side label says what it is.

Why Python flattening and not SQL over the nested structs: the nesting is four
levels deep (state → games → segments → teams → players) and every downstream
query would repeat that traversal. The expensive parse happens ONCE per ingest
and is then never repeated — which is the whole point of an ETL layer. The cost
is bounded: one pass per raw partition, and rebuilds skip partitions whose
content hash is unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from gridbot.corpus.winners import process_lines
from gridbot.lake.ingest import SERIES_STATE, bronze_glob
from gridbot.lake.paths import lake_dir as _lake_dir

# Stable Parquet schema. Append-only: new columns go on the end, never reorder or
# rename — a rename silently reinterprets every number derived from the tape.
EVENT_COLUMNS = (
    "series_id",
    "title",
    "ts_ns",
    "req_ns",
    "map_number",
    "map_started",
    "map_finished",
    "series_finished",
    "round_number",
    "rounds_played",
    "a_name",
    "a_side",
    "a_score",
    "a_alive",
    "a_money",
    "a_loadout",
    "a_awp",
    "a_hp",
    "b_name",
    "b_side",
    "b_score",
    "b_alive",
    "b_money",
    "b_loadout",
    "b_awp",
    "b_hp",
    "time_remaining",
    "num_games",
    "num_segments",
)

MAP_RESULT_COLUMNS = (
    "series_id",
    "map_number",
    "winner_name",
    "loser_name",
    "winner_score",
    "loser_score",
    "overtime",
    "first_seen_ts_ns",
)

RESULT_COLUMNS = (
    "series_id",
    "title",
    "map_number",
    "round_number",
    "winner_slot",
    "winner_side",
    "winner_name",
    "first_ts_ns",
)

_CS_SIDES = {
    "counter-terrorists": "ct",
    "terrorists": "t",
    "ct": "ct",
    "t": "t",
}
_A_SIDE_PREFERENCE = ("ct", "radiant", "dire")


def _sql_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _side_label(team: dict) -> str | None:
    raw = str(team.get("side") or "").strip().lower()
    if raw in _CS_SIDES:
        return _CS_SIDES[raw]
    return raw or None


def _team_stats(team: dict | None) -> dict:
    """Observable aggregates for one team node. Absent node → zeroed row."""
    if not isinstance(team, dict):
        return {
            "name": "",
            "side": None,
            "score": 0,
            "alive": 0,
            "money": 0,
            "loadout": 0,
            "awp": 0,
            "hp": 0,
        }
    players = team.get("players") or []
    alive_players = [p for p in players if isinstance(p, dict) and p.get("alive")]

    def _has_awp(player: dict) -> bool:
        items = (player.get("inventory") or {}).get("items") or []
        return any(
            "awp" in " ".join(str((it or {}).get("name") or "").lower().split())
            for it in items
            if isinstance(it, dict)
        )

    awp = sum(1 for p in alive_players if _has_awp(p))
    return {
        "name": str(team.get("name") or ""),
        "side": _side_label(team),
        "score": int(team.get("score") or 0),
        "alive": len(alive_players),
        "money": int(team.get("money") or 0),
        "loadout": int(team.get("loadoutValue") or 0),
        "awp": awp,
        "hp": sum(int(p.get("currentHealth") or 0) for p in alive_players),
    }


def _slots(game: dict) -> tuple[dict, dict]:
    """Place the two teams into slots ``a``/``b`` deterministically.

    CS2 first: a side label of ``ct`` goes to slot ``a`` so the historical
    CT-framed population is reproduced without a title check downstream. When
    neither team carries a resolvable side (Dota 2, or a transition snapshot),
    node order is used and stays stable for that snapshot.
    """
    teams = [t for t in game.get("teams") or [] if isinstance(t, dict)]
    if not teams:
        return _team_stats(None), _team_stats(None)
    for team in teams:
        if _side_label(team) == "ct":
            rest = [t for t in teams if t is not team]
            return _team_stats(team), _team_stats(rest[0] if rest else None)
    first, *rest = teams
    return _team_stats(first), _team_stats(rest[0] if rest else None)


def _round_winners_by_seq(game: dict) -> dict[int, tuple[str | None, str, str]]:
    """``seq → (side, name, slot)`` for each finished round with a resolvable winner.

    Winner = the first team node flagged ``won`` whose side label resolves. A
    round with an unresolvable winner leaves a HOLE rather than shifting every
    later round's label onto the wrong key — the F6 lesson from the corpus.
    """
    out: dict[int, tuple[str | None, str, str]] = {}
    for seg in game.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        if str(seg.get("type") or "").lower() != "round" or not seg.get("finished"):
            continue
        seq = int(seg.get("sequenceNumber") or 0)
        slot_a, _ = _slots(seg)
        for team in seg.get("teams") or []:
            if not isinstance(team, dict) or not team.get("won"):
                continue
            stats = _team_stats(team)
            if stats["side"] is None and not stats["name"]:
                continue
            slot = "a" if stats["name"] == slot_a["name"] else "b"
            out[seq] = (stats["side"], stats["name"], slot)
            break
    return out


def flatten_snapshot(rec: dict, title: str) -> tuple[list[dict], list[dict]]:
    """One raw ``ss`` record → ``(event_rows, result_rows)``.

    Returns empty lists for anything that is not a seriesState snapshot: error
    rows, heartbeats and discovery rows stay in the raw layer as evidence and do
    not become tape.
    """
    if rec.get("type") != "ss":
        return [], []
    state = (rec.get("data") or {}).get("seriesState")
    if not isinstance(state, dict):
        return [], []
    series_id = str(rec.get("series_id") or state.get("id") or "")
    if not series_id:
        return [], []
    ts_ns = int(rec.get("ts_ns") or 0)
    req_ns = int(rec["req_ns"]) if rec.get("req_ns") else None
    series_finished = bool(state.get("finished"))
    games = [g for g in state.get("games") or [] if isinstance(g, dict)]

    events: list[dict] = []
    results: list[dict] = []
    for game in games:
        map_number = int(game.get("sequenceNumber") or 0)
        winners = _round_winners_by_seq(game)
        for seq, (side, name, slot) in winners.items():
            results.append(
                {
                    "series_id": series_id,
                    "title": title,
                    "map_number": map_number,
                    "round_number": seq,
                    "winner_slot": slot,
                    "winner_side": side,
                    "winner_name": name,
                    "first_ts_ns": ts_ns,
                }
            )
        a, b = _slots(game)
        clock = game.get("clock") or {}
        events.append(
            {
                "series_id": series_id,
                "title": title,
                "ts_ns": ts_ns,
                "req_ns": req_ns,
                "map_number": map_number,
                "map_started": bool(game.get("started")),
                "map_finished": bool(game.get("finished")),
                "series_finished": series_finished,
                "round_number": len(winners) + 1,
                "rounds_played": a["score"] + b["score"],
                "a_name": a["name"],
                "a_side": a["side"],
                "a_score": a["score"],
                "a_alive": a["alive"],
                "a_money": a["money"],
                "a_loadout": a["loadout"],
                "a_awp": a["awp"],
                "a_hp": a["hp"],
                "b_name": b["name"],
                "b_side": b["side"],
                "b_score": b["score"],
                "b_alive": b["alive"],
                "b_money": b["money"],
                "b_loadout": b["loadout"],
                "b_awp": b["awp"],
                "b_hp": b["hp"],
                "time_remaining": float(clock.get("currentSeconds") or 0.0),
                "num_games": len(games),
                "num_segments": len(game.get("segments") or []),
            }
        )
    return events, results


def _title_of(rec: dict) -> str:
    return str(rec.get("title") or "").strip().lower() or "unknown"


@dataclass(frozen=True)
class EventBuildResult:
    partitions_read: int
    events: int
    results: int
    map_results: int = 0
    skipped: bool = False


def _has_events(out: Path) -> bool:
    return out.exists() and out.stat().st_size > 0


def build_events(*, base: Path | None = None, force: bool = False) -> EventBuildResult:
    """Rewrite the silver tables from every bronze ``grid.series_state`` day.

    A full rewrite, not an append: a day re-ingested after a torn line was fixed
    must not leave old rows behind. Lines are read in capture order (day, then
    receive time) so the first-observation oracles hold across the corpus.
    """
    root = base or _lake_dir()
    out_dir = root / "events"
    out_events = out_dir / "events.parquet"
    parts = sorted((root / "raw" / f"stream={SERIES_STATE}").glob("dt=*/part.parquet"))
    if not parts:
        return EventBuildResult(partitions_read=0, events=0, results=0, skipped=True)
    if _has_events(out_events) and not force:
        return EventBuildResult(partitions_read=0, events=0, results=0, skipped=True)

    con = duckdb.connect()
    try:
        lines = [
            row[0]
            for row in con.execute(
                f"SELECT raw FROM read_parquet({_sql_str(bronze_glob(SERIES_STATE, base))}) "
                "WHERE type = 'ss' ORDER BY _day, ts_ns"
            ).fetchall()
        ]
        seen: set[tuple[str, int, int]] = set()
        event_rows: list[dict] = []
        result_rows: list[dict] = []
        for line in lines:
            rec = json.loads(line)
            events, results = flatten_snapshot(rec, _title_of(rec))
            event_rows.extend(events)
            for r in results:
                key = (r["series_id"], r["map_number"], r["round_number"])
                if key in seen:
                    continue
                seen.add(key)
                result_rows.append(r)
        winners = process_lines(lines)["winners"]
        map_rows = [
            {
                "series_id": str(w["series_id"]),
                "map_number": int(w["map_number"]),
                "winner_name": w["winner"],
                "loser_name": w["loser"],
                "winner_score": w["winner_score"],
                "loser_score": w["loser_score"],
                "overtime": w["overtime"],
                "first_seen_ts_ns": w["first_seen_ts_ns"],
            }
            for w in winners.values()
        ]

        _write_rows(con, event_rows, EVENT_COLUMNS, out_events)
        _write_rows(con, result_rows, RESULT_COLUMNS, out_dir / "round_results.parquet")
        _write_rows(con, map_rows, MAP_RESULT_COLUMNS, out_dir / "map_results.parquet")
        return EventBuildResult(
            partitions_read=len(parts),
            events=len(event_rows),
            results=len(result_rows),
            map_results=len(map_rows),
        )
    finally:
        con.close()


def _write_rows(
    con: duckdb.DuckDBPyConnection, rows: list[dict], columns: tuple[str, ...], out: Path
) -> None:
    """Write typed rows to Parquet with an explicit, stable column set.

    Rows go through a temporary NDJSON file rather than a Python→Arrow bridge:
    it needs no extra dependency, it is the format the rest of the repo already
    speaks, and DuckDB's ``read_json_auto`` casts the inferred types to the
    stable schema in one pass. An empty tape still writes its schema, so a query
    against a quiet day returns zero rows instead of failing on a missing file.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.parquet")
    stage = out.with_suffix(".stage.jsonl")
    for path in (tmp, stage):
        if path.exists():
            path.unlink()

    with open(stage, "w", encoding="utf-8") as fh:
        # Missing keys → NULL; unknown keys are trimmed by the explicit
        # projection below. A widening raw file cannot break the tape.
        fh.writelines(
            json.dumps({c: row.get(c) for c in columns}, default=str) + "\n" for row in rows
        )
        if not rows:
            fh.write(json.dumps(dict.fromkeys(columns), default=str) + "\n")

    projection = ", ".join(columns)
    where = " WHERE 1=0" if not rows else ""
    con.execute(
        f"COPY (SELECT {projection} FROM read_json_auto({_sql_str(str(stage))}, "
        f"format='newline_delimited', union_by_name=true){where}) "
        f"TO {_sql_str(str(tmp))} (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    tmp.replace(out)
    stage.unlink(missing_ok=True)


def load_events(base: Path | None = None) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB with ``events`` and ``round_results`` registered."""
    root = base or _lake_dir()
    con = duckdb.connect()
    for name in ("events", "round_results", "map_results"):
        path = root / "events" / f"{name}.parquet"
        if path.exists():
            con.execute(
                f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet({_sql_str(str(path))})"
            )
    return con


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
