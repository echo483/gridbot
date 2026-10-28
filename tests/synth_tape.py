"""Synthetic GRID capture writer — the fixture that needs no API key.

Every test and every demo of the lake runs on tapes built here, in a ``tmp_path``
or a scratch directory. Nothing in this module talks to GRID, reads a secret, or
touches the corpus: a series is stated, the writer emits the same NDJSON the live
capture emits, and the pipeline under test cannot tell the difference. That is
what makes the ETL testable without a key and verifiable before the T7 is
mounted.

The shapes mirror the real capture contract (``capture/grid_poller.py`` for
``ss`` rows, ``gridbot.corpus.rows`` for how a game is read), so a test that
passes here is testing the production parse, not a convenience format.

Kept OUT of ``src/gridbot`` on purpose: fixture code must never be importable by
the capture or executor paths.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


def _players(alive: int, awp: bool, full_hp: int = 100) -> list[dict]:
    out = []
    for i in range(5):
        items = [{"name": "AWP"}] if (awp and i == 0) else [{"name": "AK-47"}]
        out.append(
            {
                "alive": i < alive,
                "currentHealth": full_hp if i < alive else 0,
                "inventory": {"items": items},
            }
        )
    return out


def _team(
    name: str,
    side: str,
    score: int,
    alive: int,
    money: int,
    loadout: int,
    *,
    awp: bool = False,
) -> dict:
    return {
        "__typename": "Cs2Team",
        "name": name,
        "side": side,
        "score": score,
        "money": money,
        "loadoutValue": loadout,
        "players": _players(alive, awp),
    }


def _won_teams(
    teams: list[dict], winner: str | None, *, keep_stats: bool = False
) -> list[dict]:
    """Team nodes with GRID's ``won`` flag. Segment nodes carry only name/side/won;
    game nodes (``keep_stats``) keep score, money and players as well."""
    if keep_stats:
        return [{**t, "won": t["name"] == winner} for t in teams]
    return [
        {"__typename": "Cs2Team", "name": t["name"], "side": t["side"], "won": t["name"] == winner}
        for t in teams
    ]


def _sides(swapped: bool) -> tuple[str, str]:
    """(side of the series' first-half CT team, side of the other team)."""
    if swapped:
        return "terrorists", "counter-terrorists"
    return "counter-terrorists", "terrorists"


@dataclass
class Round:
    """One in-progress round observation, plus the rounds already resolved."""

    ct_name: str
    t_name: str
    ct_score: int
    t_score: int
    ct_alive: int = 5
    t_alive: int = 5
    ct_money: int = 8000
    t_money: int = 8000
    ct_loadout: int = 14_000
    t_loadout: int = 14_000
    ct_awp: bool = False
    t_awp: bool = False
    seconds: float = 64.0


@dataclass
class Series:
    """A series to write: its map 1 round sequence and per-round observations."""

    series_id: str
    ct_name: str = "AUR"
    t_name: str = "NAVI"
    title: str = "cs2"
    #: Winner per resolved round, in order. ``None`` leaves the round unresolved
    #: (its observation still emits, its label never fabricates).
    winners: list[str | None] = field(default_factory=list)
    #: One observation per *in-progress* round, in order.
    rounds: list[Round] = field(default_factory=list)
    #: Resolved-round count after which the two teams swap sides (CS2 halftime
    #: is 12). ``None`` keeps sides fixed.
    halftime: int | None = None

    def state(self, upto: int | None = None) -> dict:
        """The seriesState after ``upto`` resolved rounds, round ``upto`` in progress.

        ``upto`` defaults to every resolved round — the last observation.
        """
        resolved = len(self.winners) if upto is None else upto
        ct_score = sum(1 for w in self.winners[:resolved] if w == self.ct_name)
        t_score = sum(1 for w in self.winners[:resolved] if w == self.t_name)
        obs = self.rounds[min(resolved, len(self.rounds) - 1)] if self.rounds else Round(
            self.ct_name, self.t_name, ct_score, t_score
        )
        first_side, second_side = _sides(self.halftime is not None and resolved >= self.halftime)
        # The map is over once a team has 13 and leads (regulation; the synthetic
        # tape never goes to overtime). GRID then flags the game finished and
        # marks the winning team node ``won``, which is what the oracle reads.
        lead = max(ct_score, t_score)
        map_over = resolved == len(self.winners) and lead >= 13 and ct_score != t_score
        game_winner = (self.ct_name if ct_score > t_score else self.t_name) if map_over else None
        segs = []
        def _seg_sides(round_no: int) -> tuple[str, str]:
            return _sides(self.halftime is not None and round_no > self.halftime)

        for i, w in enumerate(self.winners[:resolved], start=1):
            sa, sb = _seg_sides(i)
            segs.append(
                {
                    "type": "round",
                    "sequenceNumber": i,
                    "finished": True,
                    "teams": _won_teams(
                        [
                            _team(self.ct_name, sa, 0, 5, 8000, 14_000),
                            _team(self.t_name, sb, 0, 5, 8000, 14_000),
                        ],
                        w,
                    ),
                }
            )
        sa, sb = _seg_sides(resolved + 1)
        segs.append(
            {
                "type": "round",
                "sequenceNumber": resolved + 1,
                "finished": False,
                "teams": _won_teams(
                    [
                        _team(self.ct_name, sa, 0, 5, 8000, 14_000),
                        _team(self.t_name, sb, 0, 5, 8000, 14_000),
                    ],
                    None,
                ),
            }
        )
        return {
            "id": self.series_id,
            "finished": False,
            "games": [
                {
                    "sequenceNumber": 1,
                    "started": True,
                    "finished": map_over,
                    "clock": {"currentSeconds": obs.seconds},
                    "segments": segs,
                    "teams": _won_teams([
                        _team(
                            self.ct_name,
                            first_side,
                            ct_score,
                            obs.ct_alive,
                            obs.ct_money,
                            obs.ct_loadout,
                            awp=obs.ct_awp,
                        ),
                        _team(
                            self.t_name,
                            second_side,
                            t_score,
                            obs.t_alive,
                            obs.t_money,
                            obs.t_loadout,
                            awp=obs.t_awp,
                        ),
                    ], game_winner, keep_stats=True),
                }
            ],
        }


def ss_row(series: Series, upto: int | None, ts_ns: int) -> dict:
    return {
        "type": "ss",
        "title": series.title,
        "series_id": series.series_id,
        "ts_ns": ts_ns,
        "req_ns": ts_ns - 1_000_000,
        "data": {"seriesState": series.state(upto)},
    }


def write_day(
    directory: Path,
    series: list[Series],
    *,
    day: str = "20261028",
    stream: str = "series_state",
    step_s: float = 6.0,
    torn_tail: bool = False,
    base_ts_ns: int | None = None,
) -> Path:
    """Write one capture day file. Returns its path.

    Each series contributes one ``ss`` row per resolved-round boundary, in
    capture order, so the tape's first-observation oracle has something to fix.
    ``torn_tail`` appends a half-written final line — the append-only format's
    normal failure mode, which the ingest must count and drop.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stream}-{day}.jsonl"
    ts = base_ts_ns if base_ts_ns is not None else time.time_ns() - 3_600_000_000_000
    rows: list[dict] = []
    for s in series:
        n = len(s.winners)
        for i in range(n + 1):
            rows.append(ss_row(s, i, ts + int(i * step_s * 1e9)))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "discovery", "title": "cs2", "ts_ns": ts}) + "\n")
        fh.writelines(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
        if torn_tail:
            fh.write('{"type":"ss","title":"cs2","series_id":"')
    return path


def default_series(series_id: str = "999", n_rounds: int = 3) -> Series:
    """A small deterministic series: CT wins rounds 1 and 3, T wins round 2."""
    winners: list[str | None] = list(["AUR", "NAVI", "AUR"][:n_rounds])
    rounds = [
        Round("AUR", "NAVI", 0, 0, ct_alive=5, t_alive=3, t_money=3000, t_loadout=6000),
        Round("AUR", "NAVI", 1, 1, ct_alive=2, t_alive=5, ct_money=2500, ct_loadout=5000),
        Round("AUR", "NAVI", 2, 1, ct_alive=4, t_alive=1),
    ][:n_rounds]
    return Series(series_id=series_id, winners=winners, rounds=rounds)


def write_book_day(
    directory: Path,
    quotes: list[dict],
    *,
    day: str = "20261028",
    stream: str = "books",
) -> Path:
    """Write a PMUS BBO tape day file — the same shape ``capture/pmus_books.py`` writes.

    ``quotes`` are ``{"slug", "series_id", "map_number", "bid", "ask", "ts_ns"}``.
    A bid or ask of 0 is written as 0 (the venue's dead-book convention); the
    lake turns it into NULL, which is the honest reading.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stream}-{day}.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for q in quotes:
            row = {"type": "pmus_bbo", "kind": "map", "heartbeat": False, **q}
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    return path


def won_map(
    series_id: str, *, winner: str = "AUR", rounds: int = 13, halftime: int | None = None
) -> Series:
    """A series whose map 1 finishes at ``rounds``-0 — a resolved map outcome.

    ``halftime=12`` swaps sides after round 12, as CS2 does: the winner starts
    CT and finishes T, so it changes tape slot mid-map.
    """
    losers = "NAVI" if winner == "AUR" else "AUR"
    return Series(
        series_id=series_id,
        ct_name=winner,
        t_name=losers,
        winners=[winner] * rounds,
        rounds=[Round(winner, losers, s, 0) for s in range(rounds)],
        halftime=halftime,
    )
