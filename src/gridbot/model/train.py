"""Train roundwin-v1: LightGBM round-win classifier + Platt calibration.

Run as ``uv run python -m gridbot.model.train`` from the repo root. Splits are
by SERIES only (rows from one series never straddle a split): the last 20% of
series by earliest ``captured_at`` are the pinned temporal HOLDOUT, and a
seeded random 15% of the remaining series are the validation fold used for
early stopping and calibration. If ``holdout_series.json`` already exists in
the output directory it is reused verbatim — the benchmark contract in
docs/MODEL.md pins that list across retrains. All reported metrics are
holdout-only.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
from scipy.special import expit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score

from gridbot.corpus.rows import FEATURE_NAMES
from gridbot.model.dataset import DatasetMeta, load_dataset

DEFAULT_DATA = Path("data/analysis/training_rounds.jsonl")
DEFAULT_OUT = Path("data/models/roundwin-v1")
SEED = 42
HOLDOUT_FRACTION = 0.20
VALID_FRACTION = 0.15
MAX_TREES = 2000
EARLY_STOPPING_ROUNDS = 50

LGB_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "force_row_wise": True,
    "deterministic": True,
    "verbosity": -1,
}


def series_by_earliest_capture(groups: list[str], captured_at: list[str]) -> list[str]:
    """Unique series ids sorted by earliest captured_at (ties by id)."""
    earliest: dict[str, datetime] = {}
    for sid, stamp in zip(groups, captured_at, strict=True):
        moment = datetime.fromisoformat(stamp)
        if sid not in earliest or moment < earliest[sid]:
            earliest[sid] = moment
    return sorted(earliest, key=lambda sid: (earliest[sid], sid))


def split_series(
    ordered: list[str], pinned_holdout: list[str] | None, seed: int
) -> tuple[list[str], list[str], list[str]]:
    """(train, valid, holdout) series lists; holdout honors the pin if given."""
    if pinned_holdout is not None:
        pinned = set(pinned_holdout)
        absent = pinned - set(ordered)
        if absent:
            raise SystemExit(
                f"pinned holdout series missing from dataset: {sorted(absent)}"
            )
        holdout = list(pinned_holdout)
        remaining = [sid for sid in ordered if sid not in pinned]
    else:
        n_holdout = max(1, round(len(ordered) * HOLDOUT_FRACTION))
        holdout = ordered[-n_holdout:]
        remaining = ordered[:-n_holdout]
    n_valid = max(1, round(len(remaining) * VALID_FRACTION))
    valid = sorted(random.Random(seed).sample(remaining, n_valid))
    valid_set = set(valid)
    train = [sid for sid in remaining if sid not in valid_set]
    return train, valid, holdout


def ece_10bin(y: np.ndarray, p: np.ndarray) -> float:
    """Expected calibration error over 10 equal-width probability bins."""
    bins = np.minimum((p * 10).astype(int), 9)
    ece = 0.0
    for b in range(10):
        mask = bins == b
        if mask.any():
            ece += (mask.sum() / len(p)) * abs(float(p[mask].mean() - y[mask].mean()))
    return float(ece)


def fit_platt(margins: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Platt (a, b) fit on raw log-odds margins: p = sigmoid(a * margin + b)."""
    clf = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000)
    clf.fit(margins.reshape(-1, 1), y.astype(int))
    return float(clf.coef_[0][0]), float(clf.intercept_[0])


def apply_platt(margins: np.ndarray, a: float, b: float) -> np.ndarray:
    return expit(a * margins + b)


def holdout_metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    """Brier, 10-bin ECE, AUC, log loss for one probability vector."""
    return {
        "brier": float(np.mean((p - y) ** 2)),
        "ece_10bin": ece_10bin(y, p),
        "auc": float(roc_auc_score(y, p)),
        "log_loss": float(log_loss(y, p)),
    }


def _mask(groups: list[str], wanted: list[str]) -> np.ndarray:
    wanted_set = set(wanted)
    return np.fromiter((sid in wanted_set for sid in groups), dtype=bool, count=len(groups))


def train(data_path: Path, out_dir: Path, seed: int) -> dict:
    """Full pipeline: load → split → fit → calibrate → score → save artifacts."""
    x, y, groups, meta = load_dataset(data_path)
    ordered = series_by_earliest_capture(groups, meta.captured_at)

    pin_path = out_dir / "holdout_series.json"
    pinned = None
    if pin_path.exists():
        pinned = json.loads(pin_path.read_text(encoding="utf-8"))["series"]
        print(f"reusing pinned holdout: {len(pinned)} series from {pin_path}")
    train_series, valid_series, holdout_series = split_series(ordered, pinned, seed)

    masks = {
        "train": _mask(groups, train_series),
        "valid": _mask(groups, valid_series),
        "holdout": _mask(groups, holdout_series),
    }
    feature_names = list(FEATURE_NAMES)
    dtrain = lgb.Dataset(x[masks["train"]], label=y[masks["train"]], feature_name=feature_names)
    dvalid = lgb.Dataset(
        x[masks["valid"]], label=y[masks["valid"]], feature_name=feature_names, reference=dtrain
    )
    booster = lgb.train(
        {**LGB_PARAMS, "seed": seed},
        dtrain,
        num_boost_round=MAX_TREES,
        valid_sets=[dvalid],
        valid_names=["valid"],
        callbacks=[
            lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
            lgb.log_evaluation(period=200),
        ],
    )
    best_iteration = booster.best_iteration

    valid_margin = booster.predict(x[masks["valid"]], num_iteration=best_iteration, raw_score=True)
    hold_margin = booster.predict(x[masks["holdout"]], num_iteration=best_iteration, raw_score=True)
    a, b = fit_platt(np.asarray(valid_margin), y[masks["valid"]])

    y_hold = y[masks["holdout"]]
    raw = holdout_metrics(y_hold, expit(np.asarray(hold_margin)))
    calibrated = holdout_metrics(y_hold, apply_platt(np.asarray(hold_margin), a, b))

    out_dir.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out_dir / "model.txt"), num_iteration=best_iteration)
    now = datetime.now(UTC).isoformat()
    (out_dir / "calibration.json").write_text(
        json.dumps(
            {"method": "platt", "a": a, "b": b, "input": "lightgbm raw score (log-odds)"},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if pinned is None:
        pin_path.write_text(
            json.dumps(
                {
                    "pinned_at": now,
                    "rule": "docs/MODEL.md benchmark contract — never edit, re-pin = new version",
                    "n_series": len(holdout_series),
                    "series": holdout_series,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    (out_dir / "feature_names.json").write_text(
        json.dumps(feature_names, indent=2) + "\n", encoding="utf-8"
    )
    metrics = {
        "trained_at": now,
        "data_path": str(data_path),
        "seed": seed,
        "dataset": meta.counts(),
        "split": {
            name: {"series": len(series), "rows": int(masks[name].sum())}
            for name, series in (
                ("train", train_series),
                ("valid", valid_series),
                ("holdout", holdout_series),
            )
        },
        "params": {**LGB_PARAMS, "seed": seed, "max_trees": MAX_TREES,
                   "early_stopping_rounds": EARLY_STOPPING_ROUNDS},
        "best_iteration": best_iteration,
        "holdout_metrics": {"raw": raw, "calibrated": calibrated},
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    _print_summary(meta, metrics)
    return metrics


def _print_summary(meta: DatasetMeta, metrics: dict) -> None:
    counts = meta.counts()
    split = metrics["split"]
    print("\nroundwin-v1 training summary")
    print(
        f"dataset: total={counts['total_rows']} kept={counts['kept_rows']} "
        f"leak_dropped={counts['leak_dropped']} missing_key_rows={counts['missing_key_rows']} "
        f"(of which {counts['missing_key_dropped']} dropped beyond the leak filter)"
    )
    for name in ("train", "valid", "holdout"):
        print(f"{name:>8}: {split[name]['series']:>4} series  {split[name]['rows']:>6} rows")
    print(f"best_iteration: {metrics['best_iteration']}")
    print(f"\nholdout metrics (n={split['holdout']['rows']}):")
    print(f"  {'metric':<10} {'raw':>10} {'calibrated':>12}")
    for key in ("brier", "ece_10bin", "auc", "log_loss"):
        raw_v = metrics["holdout_metrics"]["raw"][key]
        cal_v = metrics["holdout_metrics"]["calibrated"][key]
        print(f"  {key:<10} {raw_v:>10.4f} {cal_v:>12.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    train(args.data, args.out_dir, args.seed)


if __name__ == "__main__":
    main()
