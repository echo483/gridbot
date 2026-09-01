"""Tests for the consolidated NDJSON reader (src/gridbot/jsonl.py).

Covers the reader's own semantics plus the intended dataset.py behavior
change (H-JSONL1): a torn line is skipped and its count surfaced, never
silent.
"""

from __future__ import annotations

import json

from gridbot.corpus.rows import FEATURE_NAMES
from gridbot.jsonl import SkippedCounter, iter_jsonl
from gridbot.model.dataset import load_dataset


def _write(tmp_path, name, lines):
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_blank_lines_skipped(tmp_path):
    p = _write(tmp_path, "a.jsonl", ['{"x": 1}', "", "   ", '{"x": 2}'])
    assert list(iter_jsonl(p)) == [{"x": 1}, {"x": 2}]


def test_torn_line_skipped(tmp_path):
    p = _write(
        tmp_path,
        "a.jsonl",
        ['{"a": 1}', '{"a": 2', '{"a": 3}'],  # torn final line of a day file
    )
    assert list(iter_jsonl(p)) == [{"a": 1}, {"a": 3}]


def test_non_dict_rows_skipped_when_dicts_only(tmp_path):
    p = _write(tmp_path, "a.jsonl", ['[1, 2]', '"str"', '{"ok": true}', "7"])
    assert list(iter_jsonl(p)) == [{"ok": True}]
    assert list(iter_jsonl(p, dicts_only=False)) == [[1, 2], "str", {"ok": True}, 7]


def test_missing_file_yields_nothing(tmp_path):
    assert list(iter_jsonl(tmp_path / "nope.jsonl")) == []


def test_skipped_counter(tmp_path):
    p = _write(tmp_path, "a.jsonl", ['{"a": 1}', "not json", "", "[0]"])
    skipped = SkippedCounter()
    rows = list(iter_jsonl(p, skipped=skipped))
    assert rows == [{"a": 1}]
    assert skipped.count == 2  # torn line + non-dict row; blank lines free


def test_list_counter_also_works(tmp_path):
    p = _write(tmp_path, "a.jsonl", ["garbage", '{"a": 1}'])

    class _List:
        def __init__(self):
            self.n = 0

        def update(self, k=1):
            self.n += k

    counter = _List()
    assert list(iter_jsonl(p, skipped=counter)) == [{"a": 1}]
    assert counter.n == 1


# --- dataset.py behavior change (H-JSONL1): torn line skipped + surfaced ----


def _corpus_row(round_number=3):
    features = {name: 0.0 for name in FEATURE_NAMES}
    features["rounds_played"] = round_number - 1
    features["round_number"] = float(round_number)
    return {
        "features": features,
        "label": 1.0,
        "series_id": "s1",
        "round_number": float(round_number),
        "row_key": f"rk{round_number}",
        "captured_at": "2026-09-01T00:00:00Z",
    }


def test_load_dataset_torn_line_skipped_and_reported(tmp_path, capsys):
    p = tmp_path / "training_rounds.jsonl"
    rows = [_corpus_row(1), _corpus_row(2)]
    p.write_text(
        json.dumps(rows[0]) + "\n"
        + '{"features": {"rounds_played"'  # torn line
        + "\n" + json.dumps(rows[1]) + "\n",
        encoding="utf-8",
    )
    X, _y, _groups, meta = load_dataset(p)
    assert meta.total_rows == 2
    assert meta.kept_rows == 2
    assert X.shape[0] == 2
    out = capsys.readouterr().out
    assert "skipped 1" in out
    assert "training_rounds.jsonl" in out


def test_load_dataset_clean_file_no_skip_report(tmp_path, capsys):
    p = tmp_path / "training_rounds.jsonl"
    p.write_text(json.dumps(_corpus_row(1)) + "\n", encoding="utf-8")
    load_dataset(p)
    assert capsys.readouterr().out == ""
