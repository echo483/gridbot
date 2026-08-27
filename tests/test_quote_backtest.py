"""Tests for scripts.quote_backtest (Queue-13 quote-rule A/B backtest).

Analysis only (slow loop, iron rail 1): locks in the two quote formulas, the
first-qualifying-row dedup, the optimistic would-fill model (TTL bounds,
book-path touch), verdict-file-only settlement, per-band splits, and the
malformed-row-never-crashes contract.  No network, no keyring, no fixtures on
disk — every input is a synthetic dict built inline; file-based behaviour is
exercised through tmp_path writers.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from typing import Any

import pytest

# Ensure the project root is on sys.path so `scripts` is importable (mirror
# the existing test_wave_edge_join.py).
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts import quote_backtest as qb


TTL_NS = 150_000_000_000


# ── synthetic-input builders ────────────────────────────────────────────────

def mk_row(ts_ns: Any = 1_000, series_id: Any = "s1", map_number: int = 1,
           team: Any = "Alpha", opponent: Any = "Beta", p_map: Any = 0.70,
           q_round: Any = 0.60, slug: Any = "astatc-cs2-map1",
           us_side: Any = "long", bid: Any = 0.55, ask: Any = 0.58,
           side_ask: Any = 0.61, entry_price_max: Any = 0.66,
           qualifies: bool = True, skip_reason: Any = None,
           rtype: Any = "mapentry", **kw) -> dict:
    """A qualifying mapentry row in the captured shape (all fields present).

    Field annotations are Any on purpose: several tests deliberately feed
    malformed values (str garbage, None, unhashable containers, giant ints)
    through this builder to prove the defensive gates.
    """
    row = {
        "type": rtype,
        "series_id": series_id,
        "map_number": map_number,
        "round_number": 4,
        "team": team,
        "opponent": opponent,
        "q_round": q_round,
        "p_map": p_map,
        "market_slug": slug,
        "us_side": us_side,
        "bid": bid,
        "ask": ask,
        "side_ask": side_ask,
        "entry_price_max": entry_price_max,
        "qualifies": qualifies,
        "skip_reason": skip_reason,
        "ts_ns": ts_ns,
    }
    row.update(kw)
    return row


def winners_for(pairs: list[tuple[str, int, str, str]]) -> dict[str, dict]:
    """Bundle (series_id, map_number, winner, loser) verdicts under
    '<series>:<map>' keys, the grid_winners.py --out shape."""
    return {
        f"{sid}:{mn}": {"winner": w, "loser": l, "winner_score": 13,
                        "loser_score": 4, "overtime": False}
        for sid, mn, w, l in pairs
    }


# ── quote formulas ──────────────────────────────────────────────────────────

class TestQuoteFormulas:
    def test_quote_a_min_of_ask_minus_tick_and_cap(self):
        assert qb.quote_a(0.61, 0.7373) == pytest.approx(0.60)
        assert qb.quote_a(0.61, 0.59) == pytest.approx(0.59)   # cap binds
        assert qb.quote_a(0.60, 0.99) == pytest.approx(0.59)   # tick binds

    def test_quote_b_bid_plus_tick_when_inside(self):
        # quote_b >= quote_a always (max over bid+tick of the min() term);
        # on a normal book bid+tick < ask-tick, so b == a == 0.60.
        assert qb.quote_b(0.61, 0.7373, 0.55) == pytest.approx(0.60)

    def test_quote_b_cap_below_bid_tick_goes_verbatim(self):
        # min(cap 0.53, ask-tick 0.60) = 0.53 < bid+tick 0.56: the formula
        # is applied verbatim -> 0.56 (above the cap; the registry, not this
        # tool, judges that).
        assert qb.quote_b(0.61, 0.53, 0.55) == pytest.approx(0.56)

    def test_quote_b_bid_floors_when_book_is_one_tick_wide(self):
        # bid+tick = 0.60 > min(cap, ask-tick = 0.60)... equal case -> 0.60
        assert qb.quote_b(0.61, 0.7373, 0.59) == pytest.approx(0.60)
        # bid 0.60 -> bid+tick 0.61 > ask-tick 0.60: formula verbatim -> 0.61
        assert qb.quote_b(0.61, 0.7373, 0.60) == pytest.approx(0.61)

    def test_quote_b_uses_cap_when_it_binds(self):
        # min(cap 0.57, ask-tick 0.60) = 0.57 > bid+tick 0.50 -> 0.57
        assert qb.quote_b(0.61, 0.57, 0.49) == pytest.approx(0.57)

    def test_quote_none_on_missing_or_nonnumeric_inputs(self):
        assert qb.quote_a(None, 0.5) is None
        assert qb.quote_b(0.61, None, 0.55) is None
        assert qb.quote_b(0.61, 0.7373, None) is None   # bid is required
        assert qb.quote_for_rule("zz", 0.61, 0.5, 0.5) is None

    def test_quote_outside_open_interval_is_skip(self):
        assert qb.quote_a(-0.01, 0.5) is None    # min(-0.02, 0.5) <= 0
        assert qb.quote_a(0.005, 0.2) is None    # min(-0.005, 0.2) <= 0
        assert qb.quote_a(1.0, 1.2) == 0.90      # engine entry ceiling binds
        assert qb.quote_a(1.5, 1.2) == 0.90      # ceiling clamps into range, as the engine does
        # b at the exact lower boundary (bid+tick = 0.01) stays valid, even
        # when the min() term itself went negative — max() rescues it:
        # min(0.2, 0.005-0.01) = -0.005; max(0.0+0.01, -0.005) = 0.01.
        assert qb.quote_b(0.02, 0.2, 0.0) == 0.01
        assert qb.quote_b(0.005, 0.2, 0.0) == 0.01
        assert qb.quote_b(1.5, 1.2, 1.0) is None    # both terms >= 1

    def test_bool_inputs_are_rejected_by_population_gate(self):
        # bools masquerading as numerics fail the population gate in
        # build_evaluations (via the _safe_float/_is_int chain).
        rows = [mk_row(side_ask=True, entry_price_max=0.5)]
        evals, counts = qb.build_evaluations(rows)
        assert evals == []
        assert counts["unusable_population_rows"] == 1

    def test_decimal_string_inputs_coerce(self):
        rows = [mk_row(side_ask="0.61", bid="0.55",
                       entry_price_max="0.7373")]
        evals, _ = qb.build_evaluations(rows)
        assert evals[0]["quotes"]["a"] == pytest.approx(0.60)
        assert evals[0]["quotes"]["b"] == pytest.approx(0.60)


# ── (a) fill vs no-fill when the book path touches only one rule ───────────

class TestFillVsNoFill:
    def _two_rule_scenario(self):
        """One evaluation whose cap pins rule a strictly below rule b, so a
        book-path touch can fill b without reaching a.

        side_ask 0.61, bid 0.55, cap 0.55: quote_a = min(0.60, 0.55, 0.90)
        = 0.55, quote_b = max(0.56, min(0.55, 0.60)) = 0.56.  The only later
        row shows side_ask = 0.56: on-grid, one full tick above quote_a — it
        fills b and cannot fill a (the half-tick epsilon can never bridge a
        whole tick).  Note b >= a is a formula identity, so the only
        reachable single-touch asymmetry is b-fills/a-doesn't; the mirror
        direction is asserted impossible in test_quote_b_is_never_below_quote_a.
        """
        rows = [
            mk_row(ts_ns=1_000, side_ask=0.61, entry_price_max=0.55,
                   bid=0.55),
            mk_row(ts_ns=2_000, side_ask=0.56, qualifies=False,
                   skip_reason="entry_rule_failed"),
        ]
        return rows

    def test_bid_plus_tick_fills_ask_minus_tick_does_not(self):
        rows = self._two_rule_scenario()
        evals, counts = qb.build_evaluations(rows)
        assert counts["dedup_collisions"] == 0
        assert len(evals) == 1
        assert evals[0]["quotes"]["a"] == pytest.approx(0.55)
        assert evals[0]["quotes"]["b"] == pytest.approx(0.56)
        qb.evaluate_fills(evals, qb.build_path_index(rows), TTL_NS)
        assert evals[0]["fills"]["b"] is True    # 0.56 <= 0.56
        assert evals[0]["fills"]["a"] is False   # 0.56 > 0.55 + half-tick

    def test_quote_b_is_never_below_quote_a(self):
        # Formula identity: b = max(a-term, bid+tick) >= a-term = a's quote.
        # The brief's "bid+tick fills, ask-tick doesn't" direction cannot
        # arise under these two formulas on identical inputs.
        for sa, cap, bid in ((0.61, 0.7373, 0.55), (0.55, 0.60, 0.40),
                             (0.90, 0.65, 0.64), (0.50, 0.49, 0.10)):
            qa = qb.quote_a(sa, cap)
            qb_ = qb.quote_b(sa, cap, bid)
            if qa is None:
                assert qb_ is None
            else:
                assert qb_ is not None and qb_ >= qa

    def test_identical_book_touch_fills_both(self):
        # Without the cap clamp, a single touch fills both rules equally.
        rows = [
            mk_row(ts_ns=1_000, side_ask=0.61, entry_price_max=0.7373,
                   bid=0.55),
            mk_row(ts_ns=2_000, side_ask=0.57, qualifies=False,
                   skip_reason="entry_rule_failed"),
        ]
        evals, _ = qb.build_evaluations(rows)
        qb.evaluate_fills(evals, qb.build_path_index(rows), TTL_NS)
        assert evals[0]["fills"]["a"] is True
        assert evals[0]["fills"]["b"] is True

    def test_reverse_scenario_touches_only_bid_tick_level(self):
        # Book path falls exactly to b's resting level (bid+tick) without
        # reaching a's ask-tick level, all on the 0.01 grid: side_ask 0.56,
        # cap 0.56, bid 0.55 -> quote_a = min(0.55, 0.56, 0.90) = 0.55,
        # quote_b = max(0.56, min(0.56, 0.55)) = 0.56; touch at 0.56.
        rows = [
            mk_row(ts_ns=1_000, side_ask=0.56, entry_price_max=0.56,
                   bid=0.55),
            mk_row(ts_ns=2_000, side_ask=0.56, qualifies=False,
                   skip_reason="entry_rule_failed"),
        ]
        evals, _ = qb.build_evaluations(rows)
        assert evals[0]["quotes"]["a"] == pytest.approx(0.55)
        assert evals[0]["quotes"]["b"] == pytest.approx(0.56)
        qb.evaluate_fills(evals, qb.build_path_index(rows), TTL_NS)
        assert evals[0]["fills"]["b"] is True    # 0.56 <= 0.56 (at level)
        assert evals[0]["fills"]["a"] is False   # 0.56 > 0.55 + half-tick

    def test_no_later_rows_never_fills(self):
        rows = [mk_row(ts_ns=1_000)]
        evals, _ = qb.build_evaluations(rows)
        qb.evaluate_fills(evals, qb.build_path_index(rows), TTL_NS)
        assert evals[0]["fills"] == {"a": False, "b": False}

    def test_path_index_ignores_nonqualifying_gate_only_for_dedup(self):
        # The book path uses EVERY row (qualifying or not); the dedup
        # population uses only qualifying rows.
        rows = [
            mk_row(ts_ns=1_000),
            mk_row(ts_ns=2_000, side_ask=0.30, qualifies=False,
                   skip_reason="entry_rule_failed"),
        ]
        evals, _ = qb.build_evaluations(rows)
        assert len(evals) == 1                    # second row not an evaluation
        qb.evaluate_fills(evals, qb.build_path_index(rows), TTL_NS)
        assert evals[0]["fills"]["a"] is True     # but it IS book evidence

    def test_path_requires_same_slug_and_side(self):
        rows = [
            mk_row(ts_ns=1_000, slug="m1", us_side="long"),
            mk_row(ts_ns=2_000, slug="m2", us_side="long", side_ask=0.30,
                   qualifies=False, skip_reason="entry_rule_failed"),
            mk_row(ts_ns=3_000, slug="m1", us_side="short", side_ask=0.30,
                   qualifies=False, skip_reason="entry_rule_failed"),
        ]
        evals, _ = qb.build_evaluations(rows)
        qb.evaluate_fills(evals, qb.build_path_index(rows), TTL_NS)
        assert evals[0]["fills"]["a"] is False


# ── (b) TTL boundary: touch at exactly T+150s counts, after doesn't ────────

class TestTtlBoundary:
    def _eval_with_touch_at(self, touch_ts: int):
        rows = [
            mk_row(ts_ns=1_000),
            mk_row(ts_ns=touch_ts, side_ask=0.30, qualifies=False,
                   skip_reason="entry_rule_failed"),
        ]
        evals, _ = qb.build_evaluations(rows)
        qb.evaluate_fills(evals, qb.build_path_index(rows), TTL_NS)
        return evals[0]

    def test_touch_at_exact_ttl_counts(self):
        # 1_000 + 150e9 ns exactly: inclusive upper bound.
        assert self._eval_with_touch_at(1_000 + TTL_NS)["fills"]["a"] is True

    def test_touch_after_ttl_does_not_count(self):
        assert self._eval_with_touch_at(1_001 + TTL_NS)["fills"]["a"] is False

    def test_touch_before_placement_does_not_count(self):
        # Strict lower bound: the row AT T (even with a passing ask) cannot
        # fill itself, and neither can anything earlier.
        rows = [
            mk_row(ts_ns=2_000, side_ask=0.30, qualifies=False,
                   skip_reason="entry_rule_failed"),
            mk_row(ts_ns=3_000),                      # the evaluation
        ]
        evals, _ = qb.build_evaluations(rows)
        assert evals[0]["ts_ns"] == 3_000
        qb.evaluate_fills(evals, qb.build_path_index(rows), TTL_NS)
        assert evals[0]["fills"]["a"] is False

    def test_custom_ttl_s_parameter_flows_through(self, tmp_path):
        write_jsonl(tmp_path, "m.jsonl", [
            mk_row(ts_ns=1_000),
            mk_row(ts_ns=1_000 + 50_000_000_000, side_ask=0.30,
                   qualifies=False, skip_reason="entry_rule_failed"),
        ])
        winners_file = write_winners(tmp_path, winners_for([]))
        base = [sys.executable, "-X", "utf8", str(_project_root / "scripts" /
                                                  "quote_backtest.py"),
                str(tmp_path / "m.jsonl"), "--winners", winners_file]
        out_default = json.loads(subprocess.run(
            base, capture_output=True, text=True, timeout=120).stdout)
        assert out_default["per_rule"]["a"]["would_fill"] == 1
        out_short = json.loads(subprocess.run(
            base + ["--ttl-s", "10"], capture_output=True, text=True,
            timeout=120).stdout)
        assert out_short["per_rule"]["a"]["would_fill"] == 0
        assert out_short["params"]["ttl_s"] == 10.0


# ── (c) first-qualifying-only dedup ─────────────────────────────────────────

class TestFirstQualifyingDedup:
    def test_first_row_becomes_the_evaluation(self):
        rows = [
            mk_row(ts_ns=1_000, side_ask=0.61, bid=0.55),
            mk_row(ts_ns=2_000, side_ask=0.90, bid=0.85, p_map=0.95),  # later
        ]
        evals, counts = qb.build_evaluations(rows)
        assert len(evals) == 1
        assert counts["dedup_collisions"] == 1
        assert evals[0]["ts_ns"] == 1_000
        assert evals[0]["quotes"]["a"] == pytest.approx(0.60)  # FIRST row's
        assert evals[0]["p_map"] == pytest.approx(0.70)

    def test_key_includes_slug_and_side(self):
        rows = [
            mk_row(ts_ns=1_000, slug="m1", us_side="long"),
            mk_row(ts_ns=2_000, slug="m1", us_side="short"),
            mk_row(ts_ns=3_000, slug="m2", us_side="long"),
        ]
        # same (series, map) but different slug/side -> three evaluations
        evals, counts = qb.build_evaluations(rows)
        assert counts["dedup_collisions"] == 0
        assert len(evals) == 3

    def test_nonqualifying_rows_do_not_consume_the_key(self):
        # A NON-qualifying row on a fresh key cannot have traded, so it must
        # not block a later qualifying row on that key.
        rows = [
            mk_row(ts_ns=1_000, qualifies=False,
                   skip_reason="entry_rule_failed"),
            mk_row(ts_ns=2_000),
        ]
        evals, counts = qb.build_evaluations(rows)
        assert len(evals) == 1
        assert counts["dedup_collisions"] == 0
        assert evals[0]["ts_ns"] == 2_000

    def test_unusable_row_does_not_consume_the_key(self):
        rows = [
            mk_row(ts_ns=1.5),                 # float ts_ns -> unusable
            mk_row(ts_ns=2_000),               # must still become the evaluation
        ]
        evals, counts = qb.build_evaluations(rows)
        assert counts["unusable_population_rows"] == 1
        assert len(evals) == 1 and evals[0]["ts_ns"] == 2_000


# ── (d) missing verdict excluded from edge but counted in fill rate ────────

class TestVerdictHandling:
    def _scenario(self):
        # Two fillable evaluations: one settled (Alpha wins), one missing
        # from the winners file entirely.  Both count in fill rate; edge
        # uses only the settled one.
        rows = [
            mk_row(ts_ns=1_000, series_id="s1", slug="m1",
                   side_ask=0.60, entry_price_max=0.60, bid=0.50),
            mk_row(ts_ns=2_000, series_id="s2", slug="m2",
                   side_ask=0.60, entry_price_max=0.60, bid=0.50),
            mk_row(ts_ns=3_000, side_ask=0.30, slug="m1",
                   qualifies=False, skip_reason="entry_rule_failed"),
            mk_row(ts_ns=4_000, side_ask=0.30, slug="m2",
                   qualifies=False, skip_reason="entry_rule_failed"),
        ]
        winners = winners_for([("s1", 1, "Alpha", "Beta")])   # s2 missing
        return rows, winners

    def test_missing_verdict_counts_fill_not_edge(self):
        rows, winners = self._scenario()
        evals, _ = qb.build_evaluations(rows)
        attach_counts = qb.attach_verdicts(evals, winners)
        assert attach_counts == {"no_verdict_key": 1, "team_in_neither": 0}
        assert evals[0]["won"] == 1.0 and evals[1]["won"] is None
        qb.evaluate_fills(evals, qb.build_path_index(rows), TTL_NS)
        m = qb.rule_metrics(evals, "a")
        assert m["n_quoted"] == 2
        assert m["would_fill"] == 2 and m["fill_rate"] == 1.0
        assert m["n_settled_fills"] == 1
        # quote_a = min(0.60-0.01, 0.60) = 0.59; won=1 -> edge = 1 - 0.59
        assert m["settled_edge_per_fill"] == pytest.approx(1.0 - 0.59)
        assert m["edge_x_fillrate"] == pytest.approx(0.41 * 1.0)

    def test_team_in_neither_excluded_from_edge_never_guessed(self):
        rows = [mk_row(ts_ns=1_000, team="Lambda")]
        evals, _ = qb.build_evaluations(rows)
        counts = qb.attach_verdicts(
            evals, winners_for([("s1", 1, "Alpha", "Beta")]))
        assert counts == {"no_verdict_key": 0, "team_in_neither": 1}
        assert evals[0]["won"] is None

    def test_non_dict_verdict_entry_is_no_verdict(self):
        rows = [mk_row(ts_ns=1_000)]
        evals, _ = qb.build_evaluations(rows)
        counts = qb.attach_verdicts(evals, {"s1:1": 42})
        assert counts["no_verdict_key"] == 1
        assert evals[0]["won"] is None

    def test_excluded_from_edge_but_in_fill_rate_in_full_pipeline(self):
        rows, winners = self._scenario()
        evals, _ = qb.build_evaluations(rows)
        qb.attach_verdicts(evals, winners)
        qb.evaluate_fills(evals, qb.build_path_index(rows), TTL_NS)
        per_rule = qb.compute_per_rule(evals)
        a = per_rule["a"]
        assert a["would_fill"] == 2
        assert a["n_settled_fills"] == 1
        assert a["settled_edge_per_fill"] == pytest.approx(0.41)


# ── (e) shoulder vs core band split ─────────────────────────────────────────

class TestBandSplit:
    def _banded_scenario(self):
        rows = [
            # core (p_map 0.70): quote_a 0.59, fills, Alpha wins -> +0.41
            mk_row(ts_ns=1_000, p_map=0.70, side_ask=0.60,
                   entry_price_max=0.60, bid=0.50, series_id="s1",
                   slug="m1"),
            # shoulder (p_map 0.85): quote_a 0.59, fills, Alpha loses -> -0.59
            mk_row(ts_ns=2_000, p_map=0.85, side_ask=0.60,
                   entry_price_max=0.60, bid=0.50, series_id="s2",
                   slug="m2"),
            # core (p_map 0.50 boundary): never fills -> nothing settled
            mk_row(ts_ns=3_000, p_map=0.50, side_ask=0.60,
                   entry_price_max=0.60, bid=0.50, series_id="s3",
                   slug="m3"),
            # shoulders at the exact band edges land outside core
            mk_row(ts_ns=4_000, p_map=0.80, side_ask=0.60,
                   entry_price_max=0.60, bid=0.50, series_id="s4",
                   slug="m4"),
        ]
        winners = winners_for([
            ("s1", 1, "Alpha", "Beta"),
            ("s2", 1, "Beta", "Alpha"),
            ("s3", 1, "Alpha", "Beta"),
            ("s4", 1, "Beta", "Alpha"),
        ])
        return rows, winners

    def test_band_membership_inclusive_core(self):
        assert qb.band_label(0.50) == "core"
        assert qb.band_label(0.80) == "core"
        assert qb.band_label(0.4999) == "shoulder"
        assert qb.band_label(0.8001) == "shoulder"
        assert qb.band_label(0.65) == "core"
        assert qb.band_label(None) is None
        assert qb.band_label(float("nan")) is None  # garbage, not a shoulder

    def test_per_band_metrics_split(self):
        rows, winners = self._banded_scenario()
        evals, _ = qb.build_evaluations(rows)
        qb.attach_verdicts(evals, winners)
        # Book paths: m1/m2 touched at 0.30 later; m3/m4 untouched.
        touch_rows = [
            mk_row(ts_ns=100_000, slug="m1", side_ask=0.30, qualifies=False,
                   skip_reason="entry_rule_failed"),
            mk_row(ts_ns=100_000, slug="m2", side_ask=0.30, qualifies=False,
                   skip_reason="entry_rule_failed"),
        ]
        rows.extend(touch_rows)
        qb.evaluate_fills(evals, qb.build_path_index(rows), TTL_NS)
        per_rule = qb.compute_per_rule(evals)

        a = per_rule["a"]
        assert a["core"]["n_quoted"] == 3        # p 0.70, 0.50, 0.80 (edges in)
        assert a["shoulder"]["n_quoted"] == 1    # p 0.85 only
        assert a["core"]["would_fill"] == 1
        assert a["shoulder"]["would_fill"] == 1
        assert a["core"]["settled_edge_per_fill"] == pytest.approx(0.41)
        assert a["shoulder"]["settled_edge_per_fill"] == pytest.approx(-0.59)

    def test_band_unknown_counts_excluded_and_stays_in_headline(self):
        rows, winners = self._banded_scenario()
        rows[2]["p_map"] = "garbage"             # core row loses its band
        evals, _ = qb.build_evaluations(rows)
        counts = qb.attach_verdicts(evals, winners)
        assert counts["no_verdict_key"] == 0
        per_rule = qb.compute_per_rule(evals)
        a = per_rule["a"]
        assert a["core"]["n_quoted"] == 2        # p 0.70 + 0.80 left in core
        assert a["shoulder"]["n_quoted"] == 1    # 0.85
        assert a["n_quoted"] == 4                # headline keeps everything


# ── (f) malformed rows counted, never crash ─────────────────────────────────

class TestMalformedHandling:
    def test_garbage_jsonl_lines_skipped_and_counted(self, tmp_path):
        path = tmp_path / "bad.jsonl"
        path.write_text(
            json.dumps(mk_row()) + "\n"
            + '{"type": "mapentry" oops\n'          # invalid JSON
            + "[1, 2, 3]\n"                          # valid JSON, wrong shape
            + "\n"                                   # blank line
            + "# a comment line\n"                   # annotation, silent
            + '{"type": "ss", "data": {}}\n',        # unrelated capture row
            encoding="utf-8")
        rows, problems = qb.load_mapentry(str(path))
        assert len(rows) == 2     # good row + the ss row
        assert len(problems) == 2 # decode error + non-object line
        evals, counts = qb.build_evaluations(rows)
        assert len(evals) == 1    # only the qualifying row
        assert counts["unusable_population_rows"] == 0

    def test_unhashable_and_huge_values_route_to_unusable(self):
        rows = [
            mk_row(slug=["not", "hashable"]),
            mk_row(series_id={"dict": "slug"}),
            mk_row(ts_ns=10 ** 400),             # huge int: float() overflows
            mk_row(team=None),
            mk_row(series_id=None),
        ]
        evals, counts = qb.build_evaluations(rows)
        assert evals == []
        assert counts["unusable_population_rows"] == 5

    def test_giant_float_fields_do_not_crash_metrics(self):
        rows = [
            mk_row(ts_ns=1_000, side_ask=0.60, entry_price_max=0.60,
                   bid=0.50, p_map=0.70, series_id="s1", slug="m1"),
            mk_row(ts_ns=2_000, side_ask=0.30, slug="m1", qualifies=False,
                   skip_reason="entry_rule_failed"),
            # A later row on the SAME path with a giant ts/side_ask: must be
            # ignored by the path index, not raise OverflowError.
            mk_row(ts_ns=10 ** 400, side_ask=0.01, slug="m1", qualifies=False,
                   skip_reason="entry_rule_failed"),
        ]
        evals, _ = qb.build_evaluations(rows)
        qb.evaluate_fills(evals, qb.build_path_index(rows), TTL_NS)
        assert evals[0]["fills"]["a"] is True   # 0.30 touch inside TTL
        m = qb.rule_metrics(evals, "a")
        assert m["would_fill"] == 1

    def test_bool_and_string_garbage_in_numerics(self):
        rows = [
            mk_row(side_ask="not-a-number"),
            mk_row(side_ask=float("nan")),
            mk_row(side_ask=float("inf")),
        ]
        evals, counts = qb.build_evaluations(rows)
        assert evals == []
        assert counts["unusable_population_rows"] == 3

    def test_empty_population_null_metrics(self):
        evals, counts = qb.build_evaluations([])
        assert evals == []
        per_rule = qb.compute_per_rule(evals)
        for rule in ("a", "b"):
            m = per_rule[rule]
            assert m["n_quoted"] == 0
            assert m["n_skipped"] == 0
            for key in ("fill_rate", "settled_edge_per_fill", "mean_quote",
                        "edge_x_fillrate"):
                assert m[key] is None, f"{rule}.{key}"
            for band in ("core", "shoulder"):
                assert per_rule[rule][band]["n_quoted"] == 0
                assert per_rule[rule][band]["fill_rate"] is None

    def test_all_skipped_rule_population_null_metrics(self):
        # entry_price_max missing -> both rules skip; metrics stay null.
        rows = [mk_row(entry_price_max=None, bid=None)]
        evals, _ = qb.build_evaluations(rows)
        qb.evaluate_fills(evals, qb.build_path_index(rows), TTL_NS)
        per_rule = qb.compute_per_rule(evals)
        for rule in ("a", "b"):
            m = per_rule[rule]
            assert m["n_quoted"] == 0 and m["n_skipped"] == 1
            assert m["would_fill"] == 0
            assert m["fill_rate"] is None
            assert m["edge_x_fillrate"] is None


# ── CLI contract: stdout JSON, one-line stderr, --out parity ────────────────

def write_jsonl(tmp_path, name, rows):
    path = tmp_path / name
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return str(path)


def write_winners(tmp_path, winners_doc):
    path = tmp_path / "winners.json"
    path.write_text(json.dumps({"winners": winners_doc, "excluded": [],
                                "contradictions": 0}), encoding="utf-8")
    return str(path)


def _cli_rows():
    """s1 fills (won, edge 0.41 on a), s2 fills (lost, -0.59 on a)."""
    rows = [
        mk_row(ts_ns=1_000, series_id="s1", slug="m1", side_ask=0.60,
               entry_price_max=0.60, bid=0.50, p_map=0.70),
        mk_row(ts_ns=2_000, series_id="s2", slug="m2", side_ask=0.60,
               entry_price_max=0.60, bid=0.50, p_map=0.85),
        mk_row(ts_ns=10_000, slug="m1", side_ask=0.30, qualifies=False,
               skip_reason="entry_rule_failed"),
        mk_row(ts_ns=10_000, slug="m2", side_ask=0.30, qualifies=False,
               skip_reason="entry_rule_failed"),
    ]
    return rows


class TestCli:
    def _run(self, tmp_path, extra=()):
        mapentry = write_jsonl(tmp_path, "m.jsonl", _cli_rows())
        winners = write_winners(tmp_path, winners_for([
            ("s1", 1, "Alpha", "Beta"),
            ("s2", 1, "Beta", "Alpha"),
        ]))
        proc = subprocess.run(
            [sys.executable, "-X", "utf8",
             str(_project_root / "scripts" / "quote_backtest.py"),
             mapentry, "--winners", winners, *extra],
            capture_output=True, text=True, timeout=120)
        return proc

    def test_stdout_json_shape_and_numbers(self, tmp_path):
        proc = self._run(tmp_path)
        assert proc.returncode == 0
        doc = json.loads(proc.stdout)
        assert set(doc) == {"per_rule", "n_evaluations", "excluded",
                            "caveats", "params"}
        assert doc["n_evaluations"] == 2
        a = doc["per_rule"]["a"]
        assert set(a) >= {"n_quoted", "n_skipped", "would_fill", "fill_rate",
                          "n_settled_fills", "settled_edge_per_fill",
                          "mean_quote", "edge_x_fillrate", "core",
                          "shoulder"}
        assert a["n_quoted"] == 2 and a["would_fill"] == 2
        assert a["settled_edge_per_fill"] == pytest.approx((0.41 + -0.59) / 2)
        assert doc["per_rule"]["b"]["would_fill"] == 2
        # quotes: a = 0.59, b = max(0.51, 0.59) = 0.59 — same here; edge same
        assert doc["per_rule"]["b"]["settled_edge_per_fill"] == \
            pytest.approx((1 - 0.59 + 0 - 0.59) / 2)
        assert doc["excluded"]["no_verdict_key"] == 0
        assert doc["excluded"]["team_in_neither"] == 0
        assert len(doc["caveats"]) >= 2
        assert doc["params"]["ttl_s"] == 150.0
        assert doc["params"]["core_band"] == [0.50, 0.80]
        assert "min(side_ask - 0.01, entry_price_max" in \
            doc["params"]["rules"]["a"]  # incl. engine entry ceiling
        assert doc["params"]["mapentry_line_problems"] == 0

    def test_stderr_is_exactly_one_summary_line(self, tmp_path):
        proc = self._run(tmp_path)
        err_lines = [ln for ln in proc.stderr.strip().splitlines()
                     if ln.startswith("quote_backtest:")]
        assert len(err_lines) == 1
        for token in ("evals=", "a: ", "b: ", "core:", "shoulder:",
                      "exf=", "fill=", "edge="):
            assert token in err_lines[0]

    def test_out_file_refuses_overwrite_and_matches_stdout(self, tmp_path):
        out_file = tmp_path / "out.json"
        proc = self._run(tmp_path, extra=["--out", str(out_file)])
        assert proc.returncode == 0
        assert json.loads(out_file.read_text(encoding="utf-8")) == \
            json.loads(proc.stdout)
        proc2 = self._run(tmp_path, extra=["--out", str(out_file)])
        assert proc2.returncode == 1
        assert "already exists" in proc2.stderr

    def test_multiple_mapentry_files_and_missing_winners_key(self, tmp_path):
        rows = _cli_rows()
        f1 = write_jsonl(tmp_path, "d1.jsonl", rows[:2])
        f2 = write_jsonl(tmp_path, "d2.jsonl", rows[2:])
        winners = write_winners(tmp_path, winners_for([
            ("s1", 1, "Alpha", "Beta")]))   # s2 verdict missing
        proc = subprocess.run(
            [sys.executable, "-X", "utf8",
             str(_project_root / "scripts" / "quote_backtest.py"),
             f1, f2, "--winners", winners],
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0
        doc = json.loads(proc.stdout)
        a = doc["per_rule"]["a"]
        assert a["n_quoted"] == 2 and a["would_fill"] == 2
        assert a["n_settled_fills"] == 1
        assert doc["excluded"]["no_verdict_key"] == 1
        assert a["settled_edge_per_fill"] == pytest.approx(0.41)

    def test_empty_population_runs_clean(self, tmp_path):
        mapentry = write_jsonl(tmp_path, "m.jsonl", [
            mk_row(qualifies=False, skip_reason="entry_rule_failed"),
        ])
        winners = write_winners(tmp_path, {})
        proc = subprocess.run(
            [sys.executable, "-X", "utf8",
             str(_project_root / "scripts" / "quote_backtest.py"),
             mapentry, "--winners", winners],
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0
        doc = json.loads(proc.stdout)
        assert doc["n_evaluations"] == 0
        assert doc["per_rule"]["a"]["fill_rate"] is None
        assert doc["per_rule"]["a"]["edge_x_fillrate"] is None

    def test_bad_ttl_rejected(self, tmp_path):
        proc = self._run(tmp_path, extra=["--ttl-s", "nan"])
        assert proc.returncode == 2

    def test_missing_winners_file_fails_cleanly(self, tmp_path):
        mapentry = write_jsonl(tmp_path, "m.jsonl", [mk_row()])
        proc = subprocess.run(
            [sys.executable, "-X", "utf8",
             str(_project_root / "scripts" / "quote_backtest.py"),
             mapentry, "--winners", str(tmp_path / "nope.json")],
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 1
        assert "cannot load" in proc.stderr

    def test_structurally_bad_winners_file_raises_value_error_path(self, tmp_path):
        mapentry = write_jsonl(tmp_path, "m.jsonl", [mk_row()])
        winners = tmp_path / "winners.json"
        winners.write_text('{"nope": true}', encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-X", "utf8",
             str(_project_root / "scripts" / "quote_backtest.py"),
             mapentry, "--winners", str(winners)],
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 1
