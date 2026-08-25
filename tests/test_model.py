"""Model lane: leak-filtered dataset loader + training smoke (docs/MODEL.md)."""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pytest

from gridbot.corpus.rows import FEATURE_NAMES
from gridbot.model.dataset import load_dataset
from gridbot.model.train import split_series

REAL_DATA = Path(__file__).resolve().parents[1] / "data" / "analysis" / "training_rounds.jsonl"


def corpus_row(
    rid: int,
    series_id: str = "100",
    round_number: int = 4,
    rounds_played: float | None = None,
    feature_round: float | None = None,
    drop: tuple[str, ...] = (),
    feature_values: dict[str, float] | None = None,
    label: float = 1.0,
) -> dict:
    features = {name: 0.0 for name in FEATURE_NAMES}
    features.update(feature_values or {})
    features["rounds_played"] = float(
        rounds_played if rounds_played is not None else round_number - 1
    )
    features["round_number"] = float(
        feature_round if feature_round is not None else round_number
    )
    for name in drop:
        del features[name]
    return {
        "id": rid,
        "row_key": f"{series_id}:1:{round_number}",
        "series_id": series_id,
        "map_number": 1,
        "round_number": round_number,
        "features": features,
        "label": label,
        "ct_team": "CT",
        "t_team": "T",
        "captured_at": f"2026-07-01T00:00:{rid:02d}+00:00",
        "source_file": "synthetic.jsonl",
    }


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    # sort_keys mirrors the real corpus, whose feature keys are alphabetical.
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    return path


def test_leak_filter_and_missing_keys(tmp_path: Path) -> None:
    rows = [
        corpus_row(1, series_id="A"),  # coherent → kept
        corpus_row(2, series_id="B", rounds_played=4),  # post-round scoreboard → leak-dropped
        corpus_row(3, series_id="C", feature_round=5),  # feature/top mismatch → leak-dropped
        corpus_row(4, series_id="D", drop=("hp_alive_diff",)),  # missing key → dropped
        corpus_row(5, series_id="E", rounds_played=4, drop=("awp_alive_diff",)),  # both defects
    ]
    x, y, groups, meta = load_dataset(write_jsonl(tmp_path / "rows.jsonl", rows))
    assert meta.total_rows == 5
    assert meta.kept_rows == 1
    assert meta.leak_dropped == 3  # rows 2, 3, 5
    assert meta.missing_key_rows == 2  # rows 4, 5
    assert meta.missing_key_dropped == 1  # row 4 only; row 5 already leak-dropped
    assert x.shape == (1, len(FEATURE_NAMES))
    assert y.tolist() == [1.0]
    assert groups == ["A"]
    assert meta.row_keys == ["A:1:4"]


def test_feature_order_matches_contract(tmp_path: Path) -> None:
    values = {name: float(i) for i, name in enumerate(FEATURE_NAMES)}
    values["rounds_played"], values["round_number"] = 3.0, 4.0  # keep the row coherent
    row = corpus_row(1, feature_values=values)
    x, _, _, meta = load_dataset(write_jsonl(tmp_path / "rows.jsonl", [row]))
    assert meta.kept_rows == 1
    # JSON stores the dict alphabetically; X must follow FEATURE_NAMES instead.
    assert x[0].tolist() == [values[name] for name in FEATURE_NAMES]


def test_split_series_partitions_and_honors_pin() -> None:
    ordered = [str(i) for i in range(10)]
    train, valid, holdout = split_series(ordered, None, seed=42)
    assert holdout == ordered[-2:]  # last 20% of series, temporal
    assert sorted(train + valid + holdout) == sorted(ordered)
    assert not set(train) & set(valid)
    train2, valid2, holdout2 = split_series(ordered, ["3", "7"], seed=42)
    assert holdout2 == ["3", "7"]
    assert not {"3", "7"} & set(train2 + valid2)


@pytest.mark.skipif(not REAL_DATA.exists(), reason="real corpus not present")
def test_train_smoke_on_real_subsample() -> None:
    x, y, groups, meta = load_dataset(REAL_DATA)
    assert meta.kept_rows == len(x) == len(y) == len(groups)
    assert meta.leak_dropped > 0
    booster = lgb.train(
        {"objective": "binary", "num_leaves": 31, "learning_rate": 0.05, "verbosity": -1,
         "seed": 7},
        lgb.Dataset(x[:500], label=y[:500], feature_name=list(FEATURE_NAMES)),
        num_boost_round=20,
    )
    assert booster.num_trees() == 20
    p = booster.predict(x[:500])
    assert np.all((p > 0.0) & (p < 1.0))
