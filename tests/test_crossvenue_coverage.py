"""Tests for the intl<->PMUS join-coverage report.

Hermetic: synthetic tapes under tmp_path, never the real data directory and
never the network.

The report exists so a DEGRADING join cannot masquerade as "no opportunities" —
so the tests that matter here are the ones about counting honestly: dedupe (the
catalog re-emits every market on each 5-minute sweep, the mappings file every 15
minutes), the refusal breakout (each reason is a different operational failure),
and an inverted pair being surfaced rather than buried.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import crossvenue_coverage as cc

DAY = "20260907"
TITLE = "Counter-Strike: G2 Ares vs Bushido Wildcats (BO3) - DACH CS Masters"
TEAMS = ["G2 Ares", "Bushido Wildcats"]


def _intl(cid: str, outcomes: list[str], *, question: str | None = None,
          title: str = TITLE, ts: int = 1) -> dict:
    return {
        "type": "catalog",
        "event_title": title,
        "question": title if question is None else question,
        "outcomes": outcomes,
        "condition_id": cid,
        "clob_token_ids": [f"{cid}-yes", f"{cid}-no"],
        "ts_ns": ts,
    }


def _mapping(sides: dict[str, str], *, sid: str = "2990189", ts: int = 1,
             teams: list[str] | None = None, kind: str = "match",
             slug: str = "aec-cs2-g2a-bw-2026-09-07") -> dict:
    return {
        "type": "grid_pmus_mapping",
        "ts_ns": ts,
        "grid_series_id": sid,
        "grid_teams": teams or TEAMS,
        "us_markets": {kind: {"market_slug": slug, "kind": kind, "sides": sides}},
    }


def _write(data_dir: Path, sub: str, stem: str, rows: list[dict]) -> None:
    d = data_dir / sub
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{stem}-{DAY}.jsonl", "w") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in rows)


def _day(data_dir: Path, intl: list[dict], mappings: list[dict]) -> dict:
    _write(data_dir, "pm", "catalog", intl)
    _write(data_dir, "pmus", "mappings", mappings)
    return cc.coverage_for_day(DAY, data_dir=data_dir)


ALIGNED = {"G2 Ares": "long", "Bushido Wildcats": "short"}
INVERTED = {"Bushido Wildcats": "long", "G2 Ares": "short"}


def test_joins_an_aligned_pair(tmp_path):
    r = _day(tmp_path, [_intl("0xa", TEAMS)], [_mapping(ALIGNED)])
    assert r["joined"] == 1
    assert r["joined_by_orientation"]["aligned"] == 1
    assert r["inverted"] == 0
    assert r["join_rate"] == 1.0


def test_an_inverted_pair_is_counted_and_surfaced(tmp_path):
    """A sign-inverted pair is the most dangerous thing this pipeline can hit —
    it does not look broken, it looks like a stable tradeable signal."""
    r = _day(tmp_path, [_intl("0xa", TEAMS)], [_mapping(INVERTED)])
    assert r["joined"] == 1
    assert r["inverted"] == 1
    assert r["joined_by_orientation"]["inverted"] == 1


def test_catalog_rows_are_deduped_per_market(tmp_path):
    """The catalog re-emits every market on each ~5min sweep. Without dedupe one
    market would be counted — and joined — once per sweep, inflating coverage."""
    repeats = [_intl("0xa", TEAMS, ts=t) for t in range(1, 6)]
    r = _day(tmp_path, repeats, [_mapping(ALIGNED)])
    assert r["intl_cs2_winner_markets"] == 1
    assert r["joined"] == 1


def test_mapping_snapshots_are_deduped_to_the_newest(tmp_path):
    """Later snapshots supersede earlier ones; only the newest is authoritative.
    Here the newest FLIPS the sides, so taking the stale one would report the
    wrong orientation — the exact error the join exists to prevent."""
    stale = _mapping(INVERTED, ts=1)
    fresh = _mapping(ALIGNED, ts=99)
    r = _day(tmp_path, [_intl("0xa", TEAMS)], [stale, fresh])
    assert r["pmus_mapped_series"] == 1
    assert r["joined_by_orientation"]["aligned"] == 1
    assert r["inverted"] == 0


def test_props_are_not_counted_as_winner_markets(tmp_path):
    """Games Total / Odd-Even books are ~80% of CS2 tokens and are untradeable;
    counting them would understate the join rate against a fake denominator."""
    rows = [
        _intl("0xa", TEAMS),
        _intl("0xb", ["Over", "Under"], question="Games Total: O/U 2.5"),
        _intl("0xc", ["Odd", "Even"], question="Map 1: Odd/Even Total Kills?"),
    ]
    r = _day(tmp_path, rows, [_mapping(ALIGNED)])
    assert r["intl_cs2_winner_markets"] == 1


def test_no_pmus_match_is_reported_as_a_refusal(tmp_path):
    other = _mapping(ALIGNED, teams=["Astralis", "Vitality"], sid="999")
    r = _day(tmp_path, [_intl("0xa", TEAMS)], [other])
    assert r["joined"] == 0
    assert r["refusals"]["no_pmus_match"] == 1


def test_incomplete_pmus_sides_are_reported_as_a_refusal(tmp_path):
    """A one-sided book carries no Yes team to orient against — refuse, never guess."""
    r = _day(tmp_path, [_intl("0xa", TEAMS)], [_mapping({"G2 Ares": "long"})])
    assert r["joined"] == 0
    assert r["refusals"]["pmus_sides_incomplete"] == 1


def test_every_reason_key_is_always_present(tmp_path):
    """A consumer reading a reason unconditionally must never KeyError."""
    r = _day(tmp_path, [_intl("0xa", TEAMS)], [_mapping(ALIGNED)])
    assert set(r["refusals"]) == set(cc.REASONS)


def test_a_missing_day_is_zero_not_a_crash(tmp_path):
    r = cc.coverage_for_day("20200101", data_dir=tmp_path)
    assert r["joined"] == 0
    assert r["intl_cs2_winner_markets"] == 0
    assert r["join_rate"] == 0.0
    assert r["files"] == {"catalog": False, "mappings": False}


def test_join_rate_does_not_divide_by_zero_on_an_empty_catalog(tmp_path):
    r = _day(tmp_path, [], [_mapping(ALIGNED)])
    assert r["join_rate"] == 0.0
