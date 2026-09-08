"""Tests for scripts/evidence_qa.py — evidence-flow QA (no-LLM, deterministic).

The suite builds synthetic day tapes that reproduce the REAL baseline numbers
from the 2026-09-05/06 venue freeze (books ratio 77.3/5.8/47.2, postquote
degeneracy 16.1%/100%/57.4%, 0 gate-passing crossings on 09-05 and 09-06) and
asserts the script flags the collapse day while leaving the normal and
recovering days alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import evidence_qa as eq


def _distribute(total: int, n: int) -> list[int]:
    """Split `total` into `n` non-negative ints as evenly as possible."""
    base, rem = divmod(total, n)
    return [base + (1 if i < rem else 0) for i in range(n)]


def _books_rows(slugs_spec: list[tuple[str, str, int, int]]) -> list[dict]:
    """Build pmus_bbo rows from (slug, kind, nonhb, hb) tuples."""
    rows = []
    for slug, kind, nonhb, hb in slugs_spec:
        for _ in range(nonhb):
            rows.append({"type": "pmus_bbo", "slug": slug, "kind": kind,
                         "bid": 0.5, "ask": 0.6, "heartbeat": False})
        for _ in range(hb):
            rows.append({"type": "pmus_bbo", "slug": slug, "kind": kind,
                         "bid": None, "ask": None, "heartbeat": True})
    return rows


def _write_books(
    dir_: Path,
    day: str,
    slugs_spec: list[tuple[str, str, int, int]],
    span_s: float | None = None,
) -> None:
    """Write a day tape. ``span_s`` stamps ts_ns evenly across that many
    seconds, so a partially-covered day can be distinguished from a full one.
    """
    dir_.mkdir(parents=True, exist_ok=True)
    rows = _books_rows(slugs_spec)
    if span_s is not None and rows:
        step = span_s * 1e9 / max(len(rows) - 1, 1)
        for i, row in enumerate(rows):
            row["ts_ns"] = int(i * step)
    with open(dir_ / f"books-{day}.jsonl", "w") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in rows)


def _write_postquote(dir_: Path, day: str, zero: int, nonnull: int, nulls: int = 0) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    with open(dir_ / f"postquote-{day}.jsonl", "w") as fh:
        fh.writelines(json.dumps({"type": "postquote", "oriented_move": 0.0}) + "\n"
                      for _ in range(zero))
        fh.writelines(json.dumps({"type": "postquote", "oriented_move": 0.02}) + "\n"
                      for _ in range(nonnull - zero))
        fh.writelines(json.dumps({"type": "postquote", "oriented_move": None}) + "\n"
                      for _ in range(nulls))


def _write_intents(dir_: Path, day: str, passing: int, total: int) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    with open(dir_ / f"intents-{day}.jsonl", "w") as fh:
        fh.writelines(json.dumps({"type": "order_intent", "status": "dry_run"}) + "\n"
                      for _ in range(passing))
        fh.writelines(json.dumps({"type": "order_intent", "status": "skipped:no_drift_confirm"}) + "\n"
                      for _ in range(total - passing))


# --- Baseline fixture: the three nights the thresholds were calibrated on. ---
# 09-04 normal (51 slugs), 09-05 collapse (23 slugs), 09-06 recovering (100 slugs).
def _baseline_books(tmp_path: Path) -> None:
    pmus = tmp_path / "pmus"
    # 51 slugs: 19 match, 32 map; 3941 non-hb rows; 90 heartbeats/slug.
    spec = []
    for i, kind in enumerate(["match"] * 19 + ["map"] * 32):
        spec.append((f"slug{i}", kind, _distribute(3941, 51)[i], 90))
    _write_books(pmus, "20260904", spec)
    # 23 slugs: 9 match, 14 map; 134 non-hb rows; 90 heartbeats/slug.
    spec = []
    for i, kind in enumerate(["match"] * 9 + ["map"] * 14):
        spec.append((f"slug{i}", kind, _distribute(134, 23)[i], 90))
    _write_books(pmus, "20260905", spec)
    # 100 slugs: 34 match, 66 map; 4721 non-hb rows; 90 heartbeats/slug.
    spec = []
    for i, kind in enumerate(["match"] * 34 + ["map"] * 66):
        spec.append((f"slug{i}", kind, _distribute(4721, 100)[i], 90))
    _write_books(pmus, "20260906", spec)


def _baseline_postquote(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    _write_postquote(shadow, "20260904", zero=293, nonnull=1821, nulls=5)
    _write_postquote(shadow, "20260905", zero=916, nonnull=916)
    _write_postquote(shadow, "20260906", zero=1602, nonnull=2791)


def _baseline_intents(tmp_path: Path) -> None:
    ex = tmp_path / "executor"
    _write_intents(ex, "20260904", passing=7, total=516)
    _write_intents(ex, "20260905", passing=0, total=308)
    _write_intents(ex, "20260906", passing=0, total=921)


def _baseline(tmp_path: Path) -> None:
    _baseline_books(tmp_path)
    _baseline_postquote(tmp_path)
    _baseline_intents(tmp_path)


def _books_days(tmp_path: Path) -> list[dict]:
    return [eq.books_day(p) for p in sorted((tmp_path / "pmus").glob("books-*.jsonl"))]


class TestParse:
    def test_books_reproduces_baseline_ratios(self, tmp_path):
        _baseline_books(tmp_path)
        days = {d["day"]: d for d in _books_days(tmp_path)}
        assert round(days["20260904"]["ratio"], 1) == 77.3
        assert round(days["20260905"]["ratio"], 1) == 5.8
        assert round(days["20260906"]["ratio"], 1) == 47.2
        assert days["20260904"]["slate"] == 51
        assert days["20260905"]["slate"] == 23
        assert days["20260906"]["slate"] == 100

    def test_books_ignores_error_rows_and_non_bbo(self, tmp_path):
        pmus = tmp_path / "pmus"
        pmus.mkdir(parents=True)
        rows = _books_rows([("a", "match", 3, 2)])
        rows.append({"type": "pmus_bbo_error", "slug": "a", "heartbeat": False})
        rows.append({"type": "other", "slug": "a"})
        with open(pmus / "books-20260101.jsonl", "w") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in rows)
        d = eq.books_day(pmus / "books-20260101.jsonl")
        assert d["nonhb"] == 3
        assert d["hb"] == 2
        assert d["slate"] == 1

    def test_postquote_excludes_nulls_from_denominator(self, tmp_path):
        _baseline_postquote(tmp_path)
        d = eq.postquote_day(tmp_path / "shadow" / "postquote-20260904.jsonl")
        # 5 null rows must NOT be counted in the denominator.
        assert d["zero"] == 293
        assert d["nonnull"] == 1821

    def test_intents_counts_only_non_skipped(self, tmp_path):
        _baseline_intents(tmp_path)
        d = eq.intents_day(tmp_path / "executor" / "intents-20260904.jsonl")
        assert d["gate_passing"] == 7
        assert d["total"] == 516


class TestCheck1:
    def test_flags_collapse_day_venue_quiet(self, tmp_path):
        _baseline_books(tmp_path)
        days = _books_days(tmp_path)
        out = {d["day"]: d for d in eq.check_venue_book_change(days)}
        assert out["20260905"]["flag"] is True
        assert out["20260905"]["verdict"] == "venue_quiet"
        assert out["20260904"]["flag"] is False
        assert out["20260906"]["flag"] is False  # recovering, not flagged

    def test_capture_broke_when_heartbeats_collapse_too(self):
        days = [
            {"day": "a", "ratio": 60.0, "slate": 50, "hb_per_slug": 90.0, "per_kind": {}},
            {"day": "b", "ratio": 3.0, "slate": 20, "hb_per_slug": 90.0, "per_kind": {}},
            {"day": "c", "ratio": 3.0, "slate": 20, "hb_per_slug": 2.0, "per_kind": {}},
        ]
        out = {d["day"]: d for d in eq.check_venue_book_change(days)}
        assert out["b"]["flag"] is True
        assert out["b"]["verdict"] == "venue_quiet"
        assert out["c"]["flag"] is True
        assert out["c"]["verdict"] == "capture_broke"

    def test_partial_coverage_is_not_mistaken_for_a_quiet_venue(self):
        """The real 2026-09-05 incident: the recorder was DOWN for most of the
        day (tape spans 6.0h against 23.7h on 09-06), so non-heartbeat rows
        collapsed while heartbeats-per-slug stayed normal — indeed higher than
        baseline, because the hours it did run were densely covered. Judging on
        heartbeat density alone labels our own outage a venue freeze, which is
        the one confusion this check exists to prevent.
        """
        full = 20 * 3600.0
        days = [
            {"day": "a", "ratio": 60.0, "slate": 50, "hb_per_slug": 90.0,
             "span_s": full, "per_kind": {}},
            {"day": "b", "ratio": 60.0, "slate": 50, "hb_per_slug": 90.0,
             "span_s": full, "per_kind": {}},
            # collapsed rate, heartbeats DENSER than baseline, but only 6h observed
            {"day": "c", "ratio": 2.6, "slate": 45, "hb_per_slug": 143.9,
             "span_s": 6 * 3600.0, "per_kind": {}},
        ]
        out = {d["day"]: d for d in eq.check_venue_book_change(days)}
        assert out["c"]["flag"] is True
        assert out["c"]["verdict"] == "capture_broke"

    def test_quiet_venue_over_a_fully_covered_day_still_reads_venue_quiet(self):
        """Guard the other direction: full coverage + normal heartbeats +
        collapsed changes is a genuine venue freeze and must stay so labelled.
        """
        full = 20 * 3600.0
        days = [
            {"day": "a", "ratio": 60.0, "slate": 50, "hb_per_slug": 90.0,
             "span_s": full, "per_kind": {}},
            {"day": "b", "ratio": 2.6, "slate": 45, "hb_per_slug": 92.0,
             "span_s": full, "per_kind": {}},
        ]
        out = {d["day"]: d for d in eq.check_venue_book_change(days)}
        assert out["b"]["verdict"] == "venue_quiet"

    def test_span_is_measured_from_the_tape(self, tmp_path):
        _write_books(tmp_path, "20260904", [("s1", "match", 40, 60)], span_s=7200.0)
        day = eq.books_day(tmp_path / "books-20260904.jsonl")
        assert day["span_s"] == pytest.approx(7200.0, rel=1e-3)

    def test_missing_timestamps_leave_span_unknown_and_do_not_crash(self, tmp_path):
        _write_books(tmp_path, "20260904", [("s1", "match", 40, 60)])
        day = eq.books_day(tmp_path / "books-20260904.jsonl")
        assert day["span_s"] is None

    def test_a_historical_flag_does_not_keep_the_exit_code_red_forever(self):
        """`flagged` drives the systemd exit code, so it must reflect only
        RECENT days. 2026-09-05 is a real past incident that sits in the window
        permanently; if it kept the unit red, the alarm would be ignored within a
        week and would be worse than no alarm. The table still shows history —
        only the exit code is scoped.
        """
        old = {"day": "20260101", "flag": True, "verdict": "capture_broke"}
        recent = {"day": "20260110", "flag": False, "verdict": "ok"}
        assert eq.flagged_recently([old, recent], [], [], today="20260110") is False

    def test_a_recent_flag_does_turn_the_exit_code_red(self):
        old = {"day": "20260101", "flag": False, "verdict": "ok"}
        recent = {"day": "20260110", "flag": True, "verdict": "venue_quiet"}
        assert eq.flagged_recently([old, recent], [], [], today="20260110") is True

    def test_recent_window_covers_yesterday_not_just_today(self):
        """A collapse that starts overnight must not wait a day to alarm."""
        y = {"day": "20260109", "flag": True, "verdict": "venue_quiet"}
        assert eq.flagged_recently([y], [], [], today="20260110") is True

    def test_no_slate_day_never_flags(self):
        days = [
            {"day": "a", "ratio": 60.0, "slate": 50, "hb_per_slug": 90.0, "per_kind": {}},
            {"day": "b", "ratio": 0.0, "slate": 0, "hb_per_slug": 0.0, "per_kind": {}},
        ]
        out = {d["day"]: d for d in eq.check_venue_book_change(days)}
        assert out["b"]["flag"] is False
        assert out["b"]["verdict"] == "insufficient_baseline"

    def test_incomparable_slate_not_flagged(self):
        # A 9-slug day vs a 100-slug baseline is not comparable — no flag.
        days = [
            {"day": "a", "ratio": 60.0, "slate": 100, "hb_per_slug": 90.0, "per_kind": {}},
            {"day": "b", "ratio": 2.0, "slate": 9, "hb_per_slug": 90.0, "per_kind": {}},
        ]
        out = {d["day"]: d for d in eq.check_venue_book_change(days)}
        assert out["b"]["flag"] is False
        assert out["b"]["verdict"] == "insufficient_baseline"


class TestCheck2:
    def test_flags_degenerate_day_only(self, tmp_path):
        _baseline_postquote(tmp_path)
        days = [eq.postquote_day(p) for p in sorted((tmp_path / "shadow").glob("postquote-*.jsonl"))]
        out = {d["day"]: d for d in eq.check_postquote(days)}
        assert round(out["20260905"]["frac"], 3) == 1.0
        assert out["20260905"]["flag"] is True
        assert out["20260904"]["flag"] is False  # 16.1% normal
        assert out["20260906"]["flag"] is False  # 57.4% elevated but below 0.60

    def test_small_sample_never_flags(self):
        out = eq.check_postquote([{"day": "a", "zero": 5, "nonnull": 5}])[0]
        assert out["frac"] == 1.0
        assert out["flag"] is False  # n=5 < CHECK2_MIN_SAMPLES


class TestCheck3:
    def test_flags_two_consecutive_dead_crossing_days(self, tmp_path):
        _baseline_books(tmp_path)
        _baseline_intents(tmp_path)
        intents = [eq.intents_day(p) for p in sorted((tmp_path / "executor").glob("intents-*.jsonl"))]
        slate = {d["day"]: d["slate"] for d in _books_days(tmp_path)}
        out = {d["day"]: d for d in eq.check_gate_passing(intents, slate)}
        assert out["20260905"]["flag"] is False  # streak of 1
        assert out["20260906"]["flag"] is True   # streak of 2
        assert out["20260904"]["flag"] is False  # had crossings

    def test_dead_slate_does_not_false_positive(self):
        # 2 consecutive days of 0 crossings but a genuinely dead slate.
        intents = [
            {"day": "a", "gate_passing": 0},
            {"day": "b", "gate_passing": 0},
        ]
        slate = {"a": 0, "b": 1}  # both below CHECK3_MIN_SLATE
        out = {d["day"]: d for d in eq.check_gate_passing(intents, slate)}
        assert out["a"]["flag"] is False
        assert out["b"]["flag"] is False

    def test_crossings_break_the_streak(self):
        intents = [
            {"day": "a", "gate_passing": 0},
            {"day": "b", "gate_passing": 1},
            {"day": "c", "gate_passing": 0},
            {"day": "d", "gate_passing": 0},
        ]
        slate = {d: 20 for d in "abcd"}
        out = {d["day"]: d for d in eq.check_gate_passing(intents, slate)}
        assert out["b"]["flag"] is False
        assert out["d"]["flag"] is True


class TestTapeSilence:
    """Check 4: the backstop for a tape that stops speaking.

    Checks 1-3 are keyed on days that EXIST in their tape and the exit code is
    scoped to recent days, so a tape that stops being written has no day to flag.
    Before this check the watchdog read that silence as health.
    """

    def test_a_live_tape_does_not_flag(self):
        out = eq.check_tape_silence(
            {"t": [("20260905", 10), ("20260906", 10)]}, today="20260906")
        assert out[0]["flag"] is False
        assert out[0]["newest_day"] == "20260906"

    def test_one_day_of_grace_is_allowed(self):
        """Day files roll on UTC while the slate is diurnal, so a tape can be
        legitimately empty for the first hours of a new UTC day."""
        out = eq.check_tape_silence({"t": [("20260905", 10)]}, today="20260906")
        assert out[0]["flag"] is False

    def test_a_tape_that_stopped_flags(self):
        """THE REGRESSION: before check 4 this exact shape exited 0."""
        out = eq.check_tape_silence({"t": [("20260905", 10)]}, today="20260907")
        assert out[0]["flag"] is True

    def test_a_tape_that_never_spoke_flags(self):
        out = eq.check_tape_silence({"t": []}, today="20260907")
        assert out[0]["flag"] is True
        assert out[0]["newest_day"] is None

    def test_an_empty_file_is_silence_with_extra_steps(self):
        """A day row with zero rows must not count as speaking."""
        out = eq.check_tape_silence({"t": [("20260907", 0)]}, today="20260907")
        assert out[0]["flag"] is True

    def test_tapes_are_reported_independently(self):
        out = {t["tape"]: t for t in eq.check_tape_silence(
            {"live": [("20260907", 5)], "dead": [("20260901", 5)]},
            today="20260907")}
        assert out["live"]["flag"] is False
        assert out["dead"]["flag"] is True

    def test_silence_and_flagged_recently_share_one_cutoff(self):
        """The exit code and the backstop must never disagree on 'recent'."""
        cutoff = eq.check_tape_silence({"t": []}, today="20260907")[0]["cutoff"]
        assert cutoff == eq._recent_cutoff("20260907")


class TestEndToEnd:
    def test_reports_and_flagged(self, tmp_path, monkeypatch, capsys):
        _baseline(tmp_path)
        monkeypatch.setattr(eq.config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(sys, "argv", ["evidence_qa.py", "--today", "20260906"])
        with pytest.raises(SystemExit) as exc:
            eq.main()
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "FLAGGED (exit 1)" in out
        assert "venue_quiet" in out

    def test_json_reproduces_baseline_and_flags(self, tmp_path, monkeypatch, capsys):
        _baseline(tmp_path)
        monkeypatch.setattr(eq.config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(
            sys, "argv", ["evidence_qa.py", "--json", "--today", "20260906"])
        with pytest.raises(SystemExit) as exc:
            eq.main()
        assert exc.value.code == 1
        report = json.loads(capsys.readouterr().out)
        c1 = {d["day"]: d for d in report["checks"]["venue_book_change"]["days"]}
        assert c1["20260905"]["flag"] is True
        assert c1["20260905"]["verdict"] == "venue_quiet"
        assert c1["20260904"]["flag"] is False
        assert c1["20260906"]["flag"] is False
        c2 = {d["day"]: d for d in report["checks"]["postquote_degeneracy"]["days"]}
        assert c2["20260905"]["flag"] is True
        assert c2["20260904"]["flag"] is False
        assert c2["20260906"]["flag"] is False
        assert report["provisional"]["nights"] == eq.PROVISIONAL_NIGHTS
        assert report["flagged"] is True

    def test_all_clear_exits_zero(self, tmp_path, monkeypatch, capsys):
        # A single healthy day with no baseline to compare against is not flagged.
        _write_books(tmp_path / "pmus", "20260904", [("s", "match", 77, 90)])
        _write_postquote(tmp_path / "shadow", "20260904", zero=10, nonnull=100)
        _write_intents(tmp_path / "executor", "20260904", passing=3, total=50)
        monkeypatch.setattr(eq.config, "DATA_DIR", tmp_path)
        # Pinned to the fixture's own day: the tapes are live as of 20260904, so
        # check 4 must stay quiet. Pinning to a later day would (correctly) flag.
        monkeypatch.setattr(sys, "argv", ["evidence_qa.py", "--today", "20260904"])
        with pytest.raises(SystemExit) as exc:
            eq.main()
        assert exc.value.code == 0
        assert "all checks pass (exit 0)" in capsys.readouterr().out

    def test_a_dead_tape_turns_the_unit_red_end_to_end(self, tmp_path,
                                                       monkeypatch, capsys):
        """Regression: all three checks pass on every day present, and the unit
        must STILL go red because one tape stopped being written."""
        _write_books(tmp_path / "pmus", "20260906", [("s", "match", 77, 90)])
        _write_postquote(tmp_path / "shadow", "20260901", zero=10, nonnull=100)
        _write_intents(tmp_path / "executor", "20260906", passing=3, total=50)
        monkeypatch.setattr(eq.config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(
            sys, "argv", ["evidence_qa.py", "--json", "--today", "20260906"])
        with pytest.raises(SystemExit) as exc:
            eq.main()
        assert exc.value.code == 1
        report = json.loads(capsys.readouterr().out)
        tapes = {t["tape"]: t for t in report["checks"]["tape_silence"]["tapes"]}
        assert tapes["shadow/postquote"]["flag"] is True
        assert tapes["pmus/books"]["flag"] is False
        # No day-keyed check fired: silence is the ONLY reason the unit is red.
        assert all(not d["flag"]
                   for k in ("venue_book_change", "postquote_degeneracy",
                             "gate_passing")
                   for d in report["checks"][k]["days"])


class TestCaptureGaps:
    """Check 5: a tape that ran LATE, which check 4 cannot see.

    THE REGRESSION. On 2026-09-07 capture was down 04:31-11:47 PDT (7h16m, a
    host suspend). Every day file still existed with plenty of rows, so checks
    1-4 all went green through a 440-minute hole, and the missing markets were
    later mis-attributed to a query window being too narrow.
    """

    def _rows(self, *offsets_s, day="20260908"):
        base = 1788825600_000000000
        return [{"type": "discovery_sweep", "title": "cs2",
                 "ts_ns": base + int(o * 1e9)} for o in offsets_s]

    def test_regular_sweeps_do_not_flag(self):
        r = eq.sweep_gaps(self._rows(*[i * 900 for i in range(20)]))
        assert r["status"] == "measured" and r["flag"] is False
        assert r["n_sweeps"] == 20
        assert r["max_gap_s"] == 900.0

    def test_a_seven_hour_hole_flags(self):
        r = eq.sweep_gaps(self._rows(0, 900, 1800, 1800 + 7 * 3600, 1800 + 7 * 3600 + 900))
        assert r["flag"] is True
        assert r["max_gap_s"] == 7 * 3600.0

    def test_one_missed_sweep_is_jitter_not_an_outage(self):
        assert eq.sweep_gaps(self._rows(0, 900, 2700, 3600))["flag"] is False

    def test_the_threshold_is_three_missed_sweeps(self):
        assert eq.sweep_gaps(self._rows(0, 3 * 900))["flag"] is False
        assert eq.sweep_gaps(self._rows(0, 3 * 900 + 1))["flag"] is True

    def test_no_markers_is_PENDING_not_green(self):
        """The marker ships dormant (rail 4). A check that cannot run must say
        so rather than report health -- that is the whole defect class."""
        r = eq.sweep_gaps([{"type": "series", "id": "1", "ts_ns": 1}])
        assert r["status"] == "pending"
        assert r["max_gap_s"] is None
        assert r["n_sweeps"] == 0

    def test_pending_does_not_flag_but_is_visible(self):
        r = eq.sweep_gaps([])
        assert r["flag"] is False and r["status"] == "pending"

    def test_non_sweep_rows_are_ignored_for_timing(self):
        rows = self._rows(0, 900) + [{"type": "series", "id": "9",
                                      "ts_ns": 1788825600_000000000 + int(5e12)}]
        assert eq.sweep_gaps(rows)["n_sweeps"] == 2

    def test_a_single_sweep_has_no_gap_to_measure(self):
        r = eq.sweep_gaps(self._rows(0))
        assert r["status"] == "measured" and r["max_gap_s"] == 0.0 and r["flag"] is False

    def test_check_returns_one_row_per_day(self):
        out = eq.check_capture_gaps([("20260907", self._rows(0, 900)),
                                     ("20260908", [])])
        assert [d["day"] for d in out] == ["20260907", "20260908"]
        assert out[1]["status"] == "pending"

    def test_a_gap_turns_the_unit_red_end_to_end(self):
        rows = self._rows(0, 900, 900 + 8 * 3600)
        rep = eq.build_report([], [], [], today="20260908",
                              discovery_days=[("20260908", rows)])
        assert rep["flagged"] is True
        assert rep["checks"]["capture_gaps"]["days"][0]["flag"] is True


class TestPostquoteAttribution:
    """Check 2 must say WHY it flagged, not just that it did.

    A bare "72% degenerate" cannot distinguish a broken shadow instrument from
    one faithfully sampling books that never move. Measured on the two flagged
    days: every observation came from a frozen or wide book and not one from a
    live book — H-MAPENTRY1-NOTE13 surfacing through a second instrument.
    """

    def _day(self, per_slug, day="20260908"):
        zero = sum(v[0] for v in per_slug.values())
        nonnull = sum(v[1] for v in per_slug.values())
        return {"day": day, "zero": zero, "nonnull": nonnull, "per_slug": per_slug}

    def test_a_frozen_only_sample_is_attributed_entirely_to_dead_books(self):
        d = self._day({"a": [400, 400], "b": [516, 516]})
        out = eq.check_postquote([d], liveness={"20260908": {"a": "frozen", "b": "frozen"}})[0]
        assert out["flag"] is True
        assert out["by_book"]["frozen"] == {"zero": 916, "nonnull": 916}
        assert out["frac_from_dead_books"] == 1.0

    def test_a_mixed_sample_reports_the_mix(self):
        d = self._day({"a": [246, 246], "b": [55, 170]})
        out = eq.check_postquote([d], liveness={"20260908": {"a": "frozen", "b": "wide"}})[0]
        assert out["by_book"] == {"frozen": {"zero": 246, "nonnull": 246},
                                  "wide": {"zero": 55, "nonnull": 170}}
        assert out["frac_from_dead_books"] == 1.0

    def test_a_live_sample_is_not_attributed_to_dead_books(self):
        d = self._day({"a": [10, 100]})
        out = eq.check_postquote([d], liveness={"20260908": {"a": "live"}})[0]
        assert out["frac_from_dead_books"] == 0.0
        assert out["flag"] is False

    def test_a_slug_with_no_book_tape_is_named_not_silently_dropped(self):
        """A slug missing from an otherwise-populated book tape is its own
        bucket — dropping it would quietly shrink the denominator."""
        d = self._day({"known": [1, 10], "ghost": [5, 50]})
        out = eq.check_postquote([d], liveness={"20260908": {"known": "live"}})[0]
        assert out["by_book"]["no_book_tape"] == {"zero": 5, "nonnull": 50}
        assert out["by_book"]["live"] == {"zero": 1, "nonnull": 10}
        assert out["frac_from_dead_books"] == 0.0

    def test_no_book_data_at_all_means_no_attribution(self):
        """Attributing every slug to "no_book_tape" is noise wearing the shape
        of a diagnosis."""
        d = self._day({"ghost": [5, 50]})
        assert "by_book" not in eq.check_postquote([d], liveness={"20260908": {}})[0]

    def test_without_a_book_tape_the_check_still_works_unattributed(self):
        """Attribution is an enrichment; the check must not depend on it."""
        d = self._day({"a": [80, 100]})
        out = eq.check_postquote([d])[0]
        assert out["frac"] == pytest.approx(0.8)
        assert "by_book" not in out

    def test_the_flag_itself_is_unchanged_by_attribution(self):
        d = self._day({"a": [80, 100]})
        bare = eq.check_postquote([d])[0]
        rich = eq.check_postquote([d], liveness={"20260908": {"a": "frozen"}})[0]
        assert bare["flag"] == rich["flag"] is True

    def test_per_slug_is_not_leaked_into_the_report(self):
        """The report is read by humans and shipped as JSON; a per-slug map
        would bloat it without being read."""
        d = self._day({"a": [80, 100]})
        assert "per_slug" not in eq.check_postquote([d])[0]
