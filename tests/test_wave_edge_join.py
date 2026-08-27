"""Tests for scripts.wave_edge_join (waves x settled winners edge join).

Analysis only (slow loop, iron rail 1): locks in the join semantics (team
recovery by exact identity, verdict-file-only outcomes) and the bucket metrics
(headline settled_book_edge over the n_book subset).  No network, no keyring,
no fixtures on disk — every input is a synthetic dict built inline; file-based
behaviour is exercised through tmp_path writers.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure the project root is on sys.path so `scripts` is importable (mirror
# the existing test_wave_detect.py).
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts import wave_edge_join as wej


# ── synthetic-input builders ────────────────────────────────────────────────

def mk_wave(ts_ns: object = 100, series_id="s1", map_number=1, rise=0.40,
            p_map=0.60, book_ask=None, **kw) -> dict:
    """A wave as wave_detect.py emits it (ts_ns intentionally untyped-tight:
    one test feeds the string "100" to prove exact-identity matching)."""
    w = {
        "series_id": series_id,
        "map_number": map_number,
        "us_side": "None",           # stringified null key, as seen upstream
        "market_slug": "None",
        "round_number": 11,
        "p_map": p_map,
        "prev_p_map": p_map - rise,
        "rise": rise,
        "book_ask": book_ask,
        "book_vs_model": None if book_ask is None else round(book_ask - p_map, 6),
        "ts_ns": ts_ns,
    }
    w.update(kw)
    return w


def mk_entry(series_id="s1", map_number=1, ts_ns=100, team="Alpha",
             opponent="Beta") -> dict:
    """A mapentry JSONL row carrying the team name for identity recovery."""
    return {
        "type": "mapentry",
        "series_id": series_id,
        "map_number": map_number,
        "round_number": 11,
        "team": team,
        "opponent": opponent,
        "a": 5,
        "b": 0,
        "q_round": 0.55,
        "p_map": 0.60,
        "market_slug": None,
        "us_side": None,
        "side_ask": None,
        "ts_ns": ts_ns,
    }


def mk_verdict(winner="Alpha", loser="Beta", winner_score=13,
               loser_score=4, overtime=False, series_id="s1",
               map_number=1) -> dict:
    """A grid_winners verdict record."""
    return {
        "series_id": series_id,
        "map_number": map_number,
        "winner": winner,
        "loser": loser,
        "winner_score": winner_score,
        "loser_score": loser_score,
        "overtime": overtime,
    }


def index_for(entries: list[dict]) -> dict:
    return wej.build_team_index(entries)


def winners_for(verdicts: list[dict], series_id="s1", map_number=1) -> dict[str, dict]:
    """Bundle verdict records under their '<series>:<map>' lookup keys."""
    d = {}
    for v in verdicts:
        sid = v.get("series_id", series_id)
        mn = v.get("map_number", map_number)
        d[f"{sid}:{mn}"] = v
    return d


# ── (a) won and lost waves ─────────────────────────────────────────────────

def test_won_and_lost_waves():
    waves = [
        mk_wave(ts_ns=100, series_id="s1", rise=0.20, p_map=0.55),   # Alpha wins
        mk_wave(ts_ns=200, series_id="s2", rise=0.45, p_map=0.62),   # Alpha loses
        mk_wave(ts_ns=300, series_id="s3", rise=0.10, p_map=0.70),   # Alpha wins
    ]
    entries = [
        mk_entry("s1", 1, 100, team="Alpha"),
        mk_entry("s2", 1, 200, team="Alpha"),
        mk_entry("s3", 1, 300, team="Alpha"),
    ]
    winners = winners_for([
        mk_verdict(series_id="s1", winner="Alpha", loser="Beta"),   # won
        mk_verdict(series_id="s2", winner="Gamma", loser="Alpha"),  # lost
        mk_verdict(series_id="s3", winner="Alpha", loser="Delta"),  # won
    ])
    rows, excluded = wej.join_waves(waves, index_for(entries), winners)

    assert excluded == []
    assert len(rows) == 3
    won = {r["ts_ns"]: r["won"] for r in rows}
    assert won[100] == 1.0   # team == winner
    assert won[200] == 0.0   # team == loser
    assert won[300] == 1.0
    # joined-row shape carries the metric fields verbatim
    r1 = next(r for r in rows if r["ts_ns"] == 100)
    assert set(r1) == {"series_id", "map_number", "team", "ts_ns", "rise",
                       "p_map", "book_ask", "won", "overtime"}
    assert r1["team"] == "Alpha" and r1["overtime"] is False


# ── (b) exclusion reasons: no_team_match / no_verdict / team_not_in_verdict ─

def test_no_team_match_excluded():
    waves = [mk_wave(ts_ns=100)]
    entries = [mk_entry("s1", 1, 999, team="Alpha")]  # ts_ns mismatch -> no hit
    rows, excluded = wej.join_waves(
        waves, index_for(entries), winners_for([mk_verdict()])
    )
    assert rows == []
    assert [e["reason"] for e in excluded] == ["no_team_match"]
    assert excluded[0]["ts_ns"] == 100


def test_first_matching_mapentry_row_supplies_team():
    # Two identical-identity rows: the FIRST one wins.
    waves = [mk_wave(ts_ns=100)]
    entries = [
        mk_entry("s1", 1, 100, team="Alpha"),
        mk_entry("s1", 1, 100, team="Zeta"),  # duplicate identity, later row
    ]
    rows, excluded = wej.join_waves(
        waves, index_for(entries), winners_for([mk_verdict(winner="Alpha")])
    )
    assert excluded == [] and len(rows) == 1
    assert rows[0]["team"] == "Alpha"


def test_no_verdict_when_key_missing():
    waves = [mk_wave(ts_ns=100, series_id="sx")]
    entries = [mk_entry("sx", 1, 100, team="Alpha")]
    rows, excluded = wej.join_waves(  # winners dict has no "sx:1"
        waves, index_for(entries), {}
    )
    assert rows == []
    assert [e["reason"] for e in excluded] == ["no_verdict"]


def test_team_not_in_verdict_never_guessed():
    # Wave's team matches NEITHER winner nor loser -> excluded, no score or
    # ordering inference.  Even a same-team-everywhere mapping stays out.
    waves = [mk_wave(ts_ns=100)]
    entries = [mk_entry("s1", 1, 100, team="Lambda")]
    rows, excluded = wej.join_waves(
        waves, index_for(entries),
        winners_for([mk_verdict(winner="Alpha", loser="Beta")]),
    )
    assert rows == []
    assert [e["reason"] for e in excluded] == ["team_not_in_verdict"]


# ── (c) rise bucketing at the split boundary ───────────────────────────────

def test_split_boundary_rise_equals_split_goes_hi():
    assert wej.bucket_label(rise=0.30, rise_split=0.30) == "hi"
    assert wej.bucket_label(rise=0.2999, rise_split=0.30) == "lo"
    assert wej.bucket_label(rise=0.31, rise_split=0.30) == "hi"
    # a custom --rise-split moves the boundary itself
    assert wej.bucket_label(rise=0.45, rise_split=0.45) == "hi"

    waves = [
        mk_wave(ts_ns=100, rise=0.29),
        mk_wave(ts_ns=200, series_id="s2", rise=0.30),  # exactly at the split
        mk_wave(ts_ns=300, series_id="s3", rise=0.50),
    ]
    entries = [mk_entry("s1", 1, 100), mk_entry("s2", 1, 200),
               mk_entry("s3", 1, 300)]
    winners = winners_for([
        mk_verdict(series_id="s1", winner="Alpha", loser="Beta"),
        mk_verdict(series_id="s2", winner="Alpha", loser="Beta"),
        mk_verdict(series_id="s3", winner="Alpha", loser="Beta"),
    ])
    rows, excluded = wej.join_waves(waves, index_for(entries), winners)
    assert excluded == []
    buckets = wej.compute_buckets(rows, rise_split=0.30)
    lo_ts = {r["ts_ns"] for r in rows if wej.bucket_label(r["rise"], 0.30) == "lo"}
    assert lo_ts == {100}
    assert buckets["lo"]["n"] == 1
    assert buckets["hi"]["n"] == 2   # the boundary rise landed in hi
    assert buckets["all"]["n"] == 3


# ── (d) book vs no-book waves: metrics over the n_book subset only ─────────

def test_book_metrics_cover_only_numeric_asks():
    waves = [
        # n=4; won = [1, 0, 0, 1] -> win_rate 0.5. n_book=2 (s1, s3):
        # mean_book_vs_model=(0.50+0.80)/2 - 0.60 = 0.05,
        # settled_book_edge=((1-0.50)+(0-0.80))/2 = -0.15 <- per-$1 headline
        mk_wave(ts_ns=100, rise=0.40, p_map=0.60, book_ask=0.50),  # buys win
        mk_wave(ts_ns=200, series_id="s2", rise=0.35, p_map=0.60), # no ask, lose
        mk_wave(ts_ns=300, series_id="s3", rise=0.50, p_map=0.60,
                book_ask=0.80),                                    # buys lose
        mk_wave(ts_ns=400, series_id="s4", rise=0.25, p_map=0.60,
                book_ask=None),                                    # explicit null
    ]
    entries = [mk_entry(f"s{i}", 1, ts) for i, ts in ((1, 100), (2, 200),
                                                      (3, 300), (4, 400))]
    winners = winners_for([
        mk_verdict(series_id="s1", winner="Alpha", loser="Beta"),
        mk_verdict(series_id="s2", winner="Gamma", loser="Alpha"),
        mk_verdict(series_id="s3", winner="Gamma", loser="Alpha"),
        mk_verdict(series_id="s4", winner="Alpha", loser="Beta"),
    ])
    rows, excluded = wej.join_waves(waves, index_for(entries), winners)
    assert excluded == [] and len(rows) == 4

    b = wej.compute_buckets(rows, rise_split=0.30)["all"]
    assert b["n"] == 4
    assert b["n_book"] == 2                      # only priced waves counted
    assert b["win_rate"] == pytest.approx(0.5)   # full-bucket denominator
    assert b["mean_p_map"] == pytest.approx(0.6)
    assert b["calib_gap"] == pytest.approx(-0.1)
    assert b["brier"] == pytest.approx((0.16 + 0.36 + 0.36 + 0.16) / 4)
    assert b["mean_book_vs_model"] == pytest.approx(0.05)
    assert b["settled_book_edge"] == pytest.approx(-0.15)


# ── (e) empty buckets report n=0 and null metrics (no division by zero) ────

def test_empty_bucket_null_metrics():
    empty = wej.compute_buckets([], rise_split=0.30)
    for label in ("lo", "hi", "all"):
        b = empty[label]
        assert b["n"] == 0
        for k in ("win_rate", "mean_p_map", "calib_gap", "brier",
                  "mean_book_vs_model", "settled_book_edge"):
            assert b[k] is None, f"{label}.{k}"
        assert b["n_book"] == 0

    # A bucket can be non-empty overall yet have zero priced waves: the book
    # metrics go null while the model metrics stay populated.
    waves = [mk_wave(ts_ns=100)]  # single unpriced wave lands in "hi"
    entries = [mk_entry("s1", 1, 100)]
    rows, _ = wej.join_waves(waves, index_for(entries), {})
    b_lo = wej.compute_buckets(rows, rise_split=0.30)["lo"]
    assert b_lo["n"] == 0
    assert b_lo["settled_book_edge"] is None
    assert b_lo["mean_p_map"] is None


def test_single_bucket_metrics():
    waves = [mk_wave(ts_ns=100, rise=0.10, p_map=0.55),
             mk_wave(ts_ns=200, series_id="s2", rise=0.15, p_map=0.65)]
    entries = [mk_entry("s1", 1, 100), mk_entry("s2", 1, 200)]
    winners = winners_for([
        mk_verdict(series_id="s1", winner="Alpha", loser="Beta"),
        mk_verdict(series_id="s2", winner="Gamma", loser="Alpha"),
    ])
    rows, _ = wej.join_waves(waves, index_for(entries), winners)
    b = wej.compute_buckets(rows, rise_split=0.30)["lo"]
    assert b["n"] == 2
    assert b["win_rate"] == pytest.approx(0.5)
    assert b["mean_p_map"] == pytest.approx(0.6)
    assert b["calib_gap"] == pytest.approx(-0.1)
    assert b["brier"] == pytest.approx((0.2025 + 0.4225) / 2)
    assert b["n_book"] == 0
    assert b["mean_book_vs_model"] is None
    assert b["settled_book_edge"] is None


# ── (f) overtime winner carries through the join ───────────────────────────

def test_overtime_winner():
    waves = [mk_wave(ts_ns=100)]
    entries = [mk_entry("s1", 1, 100, team="Alpha")]
    winners = winners_for([
        mk_verdict(winner="Alpha", loser="Beta", winner_score=16,
                   loser_score=14, overtime=True),
    ])
    rows, excluded = wej.join_waves(waves, index_for(entries), winners)
    assert excluded == [] and len(rows) == 1
    assert rows[0]["won"] == 1.0 and rows[0]["overtime"] is True


# ── defensive handling: malformed rows are skipped + counted, never crash ──

def test_malformed_inputs_are_skipped_and_counted(tmp_path):
    bad_mapentry = tmp_path / "bad.jsonl"
    bad_mapentry.write_text(
        '{"type": "mapentry", "series_id": "s1", "map_number": 1, '
        '"ts_ns": 100, "team": "Alpha"}\n'
        '{"type": "mapentry" oops\n'          # invalid JSON line
        '[1, 2, 3]\n'                          # valid JSON, wrong shape
        '\n'                                   # blank line
        '{"type": "ss", "data": {}}\n',        # unrelated capture row
        encoding="utf-8",
    )
    entries, skipped = wej.load_mapentry(str(bad_mapentry))
    assert len(entries) == 2      # good mapentry row + the ss row both parse
    assert len(skipped) == 2      # decode error + non-dict line (reason strings)
    idx = index_for(entries)
    assert idx == {("s1", 1, 100): "Alpha"}

    bad_waves = tmp_path / "waves.json"
    bad_waves.write_text('{"waves": [{"ts_ns": 100}, 7], "sequences": 1}',
                         encoding="utf-8")
    waves, wskipped = wej.load_waves(str(bad_waves))
    assert len(waves) == 1 and wskipped == 1

    bad_winners = tmp_path / "winners.json"
    bad_winners.write_text('{"winners": {"s1:1": 42}}', encoding="utf-8")
    winners, wskipped = wej.load_winners(str(bad_winners))
    assert winners == {} and wskipped == 1

    structurally_bad = tmp_path / "nope.json"
    structurally_bad.write_text('{"nope": true}', encoding="utf-8")
    with pytest.raises(ValueError):
        wej.load_waves(str(structurally_bad))
    with pytest.raises(ValueError):
        wej.load_winners(str(structurally_bad))

    # A non-dict entry among the waves is excluded without crashing.
    rows, excluded = wej.join_waves(["garbage"], {}, {})
    assert rows == [] and [e["reason"] for e in excluded] == ["malformed_wave"]
    # Numeric fields that refuse to coerce take the malformed path too.
    rows, excluded = wej.join_waves(
        [{"series_id": "s1", "map_number": 1, "ts_ns": 100, "p_map": "x",
          "rise": 0.3}],
        {("s1", 1, 100): "Alpha"}, {},
    )
    assert rows == []
    assert [e["reason"] for e in excluded] == ["malformed_wave"]


def test_corrupt_wave_rows_get_reason_code():
    # No-p_map / no-rise waves route to malformed_wave rather than crashing.
    key_ok = {("s1", 1, 100): "Alpha"}
    rows, excluded = wej.join_waves(
        [{"series_id": "s1", "map_number": 1, "ts_ns": 100}],
        key_ok, {},
    )
    assert rows == [] and [e["reason"] for e in excluded] == ["malformed_wave"]


# ── exact-identity team matching: stringified nulls cannot create hits ─────

def test_us_side_none_stringification_cannot_shadow_join():
    # The identity triple ignores us_side entirely: an upstream "None"-as-string
    # cannot broaden or shadow the match. Exact type mismatches still miss.
    waves = [
        mk_wave(ts_ns=100),                                  # int match
        mk_wave(ts_ns="100", series_id="s2"),                # str vs int miss
    ]
    entries = [mk_entry("s1", 1, 100, team="Alpha"),
               mk_entry("s2", 1, 101, team="Alpha")]
    winners = winners_for([mk_verdict(series_id="s1", winner="Alpha",
                                      loser="Beta")])
    rows, excluded = wej.join_waves(waves, index_for(entries), winners)
    assert [r["series_id"] for r in rows] == ["s1"]
    assert [e["reason"] for e in excluded] == ["no_team_match"]


# ── main() IO contract: stdout JSON, stderr summary, --out parity ──────────

def _write_inputs(tmp_path):
    waves_file = tmp_path / "waves.json"
    waves_file.write_text(json.dumps({
        "waves": [
            mk_wave(ts_ns=100, rise=0.29, p_map=0.55),
            mk_wave(ts_ns=200, series_id="s2", rise=0.30, p_map=0.62,
                    book_ask=0.45),
        ],
        "sequences": 2,
        "wave_rate": 1.0,
    }), encoding="utf-8")

    mapentry_file = tmp_path / "mapentry.jsonl"
    mapentry_file.write_text(
        json.dumps(mk_entry("s1", 1, 100, team="Alpha")) + "\n"
        + json.dumps(mk_entry("s2", 1, 200, team="Alpha")) + "\n",
        encoding="utf-8",
    )

    winners_file = tmp_path / "winners.json"
    winners_file.write_text(json.dumps({
        "winners": winners_for([
            mk_verdict(series_id="s1", winner="Alpha", loser="Beta"),
            mk_verdict(series_id="s2", winner="Gamma", loser="Alpha"),
        ]),
        "excluded": [],
        "contradictions": 0,
    }), encoding="utf-8")
    return str(waves_file), str(mapentry_file), str(winners_file)


def _run_cli(tmp_path):
    waves_path, mapentry_path, winners_path = _write_inputs(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-X", "utf8",
         str(_project_root / "scripts" / "wave_edge_join.py"),
         waves_path, mapentry_path, winners_path],
        capture_output=True, text=True, timeout=120,
    )
    return proc, waves_path, mapentry_path, winners_path


def test_cli_stdout_json_and_stderr_summary(tmp_path):
    proc, *_ = _run_cli(tmp_path)
    assert proc.returncode == 0
    doc = json.loads(proc.stdout)
    assert set(doc) == {"buckets", "rows", "excluded", "params", "_warnings"}
    assert set(doc["buckets"]) == {"lo", "hi", "all"}
    assert doc["params"]["rise_split"] == 0.30
    assert [r["won"] for r in doc["rows"]] == [1.0, 0.0]
    all_b = doc["buckets"]["all"]
    assert all_b["n"] == 2 and all_b["n_book"] == 1
    # The stderr summary is ONE line carrying all/lo/hi with settled_book_edge.
    err_lines = [ln for ln in proc.stderr.strip().splitlines() if ln.startswith(
        "wave_edge_join:")]
    assert len(err_lines) == 1
    summary = err_lines[0]
    for token in ("joined=", "excluded=", "all: ", "lo: ", "hi: ",
                  "settled_book_edge"):
        assert token in summary


def test_cli_out_file_matches_stdout(tmp_path):
    out_file = tmp_path / "join_out.json"
    proc, waves_path, mapentry_path, winners_path = _run_cli(tmp_path)
    assert proc.returncode == 0

    run2 = subprocess.run(
        [sys.executable, "-X", "utf8",
         str(_project_root / "scripts" / "wave_edge_join.py"),
         waves_path, mapentry_path, winners_path, "--out", str(out_file)],
        capture_output=True, text=True, timeout=120,
    )
    assert run2.returncode == 0
    assert json.loads(out_file.read_text(encoding="utf-8")) == \
        json.loads(run2.stdout)
