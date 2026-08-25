"""Retrain promotion gate: candidate vs served roundwin model (fail-closed).

Run as ``uv run python scripts/promote_model.py --candidate data/models/<dir>``
from the repo root. Both the served model (``data/models/roundwin-v1``) and the
candidate directory must follow the train.py artifact layout (LightGBM
``model.txt`` + Platt ``calibration.json``, p = sigmoid(a*margin + b)). Both are
scored on EXACTLY the pinned holdout series from the served model's
``holdout_series.json`` (rows arrive via the leak-filtered ``load_dataset``).
This script is read-only apart from stdout: artifact swap and the registry
amendment are human acts, never performed here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np

from gridbot.model.dataset import load_dataset
from gridbot.model.train import apply_platt, holdout_metrics

DEFAULT_DATA = Path("data/analysis/training_rounds.jsonl")
SERVED_DIR = Path("data/models/roundwin-v1")
METRIC_KEYS = ("brier", "ece_10bin", "auc", "log_loss")


class ModelArtifacts:
    """One model directory: booster + Platt coefficients."""

    def __init__(self, path: Path) -> None:
        """Load and validate artifacts from ``path``.

        Raises:
            FileNotFoundError: if model.txt or calibration.json is absent.
        """
        model_path = path / "model.txt"
        calib_path = path / "calibration.json"
        for missing in (model_path, calib_path):
            if not missing.exists():
                raise FileNotFoundError(f"missing artifact: {missing}")
        self.path = path
        self.booster = lgb.Booster(model_file=str(model_path))
        calib = json.loads(calib_path.read_text(encoding="utf-8"))
        self.a = float(calib["a"])
        self.b = float(calib["b"])

    def probabilities(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(raw, calibrated) holdout probability vectors for feature matrix x."""
        margins = np.asarray(self.booster.predict(x, raw_score=True))
        calibrated = apply_platt(margins, self.a, self.b)
        return 1.0 / (1.0 + np.exp(-margins)), calibrated


def load_holdout(
    data_path: Path, holdout_series: list[str]
) -> tuple[np.ndarray, np.ndarray]:
    """Feature matrix and labels for the pinned holdout series only.

    Rows come from ``load_dataset`` so the corpus leak filter is binding.
    """
    x, y, groups, _meta = load_dataset(data_path)
    wanted = set(holdout_series)
    mask = np.fromiter((sid in wanted for sid in groups), dtype=bool, count=len(groups))
    n = int(mask.sum())
    if n == 0:
        raise SystemExit("no holdout rows matched the pinned series — refusing to compare")
    return x[mask], y[mask]


def compare_models(
    served: ModelArtifacts, candidate: ModelArtifacts, x: np.ndarray, y: np.ndarray
) -> tuple[dict[str, dict[str, dict[str, float]]], bool]:
    """Score both models on the same rows; return (metrics, promote_ok).

    promote_ok is True only when the candidate's CALIBRATED Brier is strictly
    lower than the served model's calibrated Brier.
    """
    metrics: dict[str, dict[str, dict[str, float]]] = {}
    for name, model in (("served", served), ("candidate", candidate)):
        raw_p, cal_p = model.probabilities(x)
        metrics[name] = {
            "raw": holdout_metrics(y, raw_p),
            "calibrated": holdout_metrics(y, cal_p),
        }
    promote_ok = metrics["candidate"]["calibrated"]["brier"] < metrics["served"][
        "calibrated"
    ]["brier"]
    return metrics, bool(promote_ok)


def print_report(metrics: dict, promote_ok: bool) -> None:
    """Human-readable comparison table plus the fail-closed verdict."""
    print("\nholdout comparison (n rows shared)")
    for flavor in ("raw", "calibrated"):
        print(f"\n  {flavor}")
        print(f"  {'metric':<10} {'served':>10} {'candidate':>12} {'delta':>10}")
        for key in METRIC_KEYS:
            s = metrics["served"][flavor][key]
            c = metrics["candidate"][flavor][key]
            print(f"  {key:<10} {s:>10.4f} {c:>12.4f} {c - s:>+10.4f}")
    print()
    if promote_ok:
        print("PROMOTE-ELIGIBLE (candidate calibrated Brier < served)")
        print("Manual steps — HUMAN acts, not performed by this script:")
        print("  1. Review metrics above; sanity-check candidate artifacts.")
        print("  2. Swap data/models/roundwin-v1 artifacts by hand (keep the old")
        print("     directory; the holdout pin must NEVER change).")
        print("  3. Amend docs/experiments/registry.jsonl with the promotion entry.")
    else:
        print("KEEP-OLD (fail-closed)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--served", type=Path, default=SERVED_DIR)
    args = parser.parse_args()

    pin_path = args.served / "holdout_series.json"
    if not pin_path.exists():
        raise SystemExit(f"served model holdout pin not found: {pin_path}")
    pinned = json.loads(pin_path.read_text(encoding="utf-8"))["series"]
    print(f"pinned holdout: {len(pinned)} series from {pin_path}")

    served = ModelArtifacts(args.served)
    candidate = ModelArtifacts(args.candidate)
    x, y = load_holdout(args.data, pinned)
    print(f"holdout rows (leak-filtered): {len(y)}")

    metrics, promote_ok = compare_models(served, candidate, x, y)
    print_report(metrics, promote_ok)


if __name__ == "__main__":
    main()
