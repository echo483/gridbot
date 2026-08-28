#!/usr/bin/env python3
"""
Minimal evaluation harness for empirical model selection.
Runs real tasks through candidate models; scores correctness, cost, latency.
Output: per-task-kind model rankings with confidence intervals.

Usage:
    uv run python scripts/eval_harness.py --task-kind programming
    uv run python scripts/eval_harness.py --all-kinds

Requirements:
    - tasks.jsonl: task corpus (see format below)
    - OpenRouter API key in environment (or direct API keys for bypass lanes)
    - ground_truth/ directory with verified answers for each task
"""

import argparse
import json
import time
from pathlib import Path
from statistics import median, quantiles
from typing import Dict, List, Tuple

# Task corpus format (tasks.jsonl):
# {"kind": "programming", "prompt": "...", "ground_truth": "hash_or_path"}
# {"kind": "review", "prompt": "...", "ground_truth": "hash_or_path"}
# ...

# Candidate models per kind (expand as needed)
CANDIDATES = {
    "programming": [
        "glm-5.3-flash",
        "deepseek-v4-flash",
        "qwen3-max-thinking",
    ],
    "review": [
        "claude-3-opus-5-sonnet",
        "glm-5.3",
        "qwen3-max-thinking",
    ],
    "fast": [
        "deepseek-v4-flash",
        "glm-5.3-flash",
        "nemotron",
    ],
    "analysis": [
        "qwen3-max-thinking",
        "glm-5.3",
        "claude-3-opus-5-sonnet",
    ],
    "reasoning": [
        "qwen3-max-thinking",
        "glm-5.3",
    ],
    "bulk": [
        "nemotron",
        "deepseek-v4-flash",
    ],
}

# Scoring weights (adjust based on your priorities)
LATENCY_WEIGHT = 0.0  # 0.0 = async (don't care); >0 = real-time sensitive
COST_WEIGHT = 1.0


def load_tasks(kind: str) -> List[Dict]:
    """Load tasks of given kind from tasks.jsonl."""
    tasks = []
    with open("tasks.jsonl") as f:
        for line in f:
            task = json.loads(line)
            if task["kind"] == kind:
                tasks.append(task)
    return tasks


def run_task(task: Dict, model: str) -> Tuple[bool, float, float]:
    """
    Run task through model; return (correctness, cost, latency).
    TODO: Implement actual dispatch + verification.
    Placeholder returns dummy data for now.
    """
    # TODO: dispatch to model via OpenRouter/direct API
    # TODO: apply mechanical verification (Section 3 of ROUTING_LAYER_ANALYSIS.md)
    # TODO: fetch cost from OpenRouter billing API or token counters
    start = time.time()
    # ... dispatch ...
    latency = time.time() - start
    correctness = True  # TODO: compare against ground_truth
    cost = 0.001  # TODO: real cost
    return correctness, cost, latency


def score_task(correctness: bool, cost: float, latency: float) -> float:
    """Utility score: correctness / (cost^COST_WEIGHT * latency^LATENCY_WEIGHT)."""
    if not correctness:
        return 0.0
    denominator = (cost**COST_WEIGHT) * (latency**LATENCY_WEIGHT)
    return 1.0 / denominator if denominator > 0 else 0.0


def evaluate_kind(kind: str, n_holdout: int = 2):
    """
    Evaluate all candidate models on tasks of given kind.
    Splits tasks into train (80%) and holdout (20%) for validation.
    """
    tasks = load_tasks(kind)
    if not tasks:
        print(f"No tasks found for kind '{kind}'")
        return

    # Temporal split: last n_holdout tasks are validation set
    train_tasks = tasks[:-n_holdout] if n_holdout > 0 else tasks
    holdout_tasks = tasks[-n_holdout:] if n_holdout > 0 else []

    print(f"\n{'='*60}")
    print(f"Evaluating kind: {kind}")
    print(f"Tasks: {len(train_tasks)} train, {len(holdout_tasks)} holdout")
    print(f"{'='*60}\n")

    results: Dict[str, List[float]] = {model: [] for model in CANDIDATES[kind]}

    # Train phase
    for task in train_tasks:
        print(f"Task: {task.get('prompt', '')[:60]}...")
        for model in CANDIDATES[kind]:
            correctness, cost, latency = run_task(task, model)
            score = score_task(correctness, cost, latency)
            results[model].append(score)
            status = "✓" if correctness else "✗"
            print(f"  {model:30s} {status} score={score:.3f} cost=${cost:.4f} latency={latency:.2f}s")
        print()

    # Summarize train results
    print(f"\n{'='*60}")
    print("TRAIN RESULTS (median utility ± IQR)")
    print(f"{'='*60}")
    rankings = []
    for model, scores in results.items():
        if not scores:
            continue
        med = median(scores)
        if len(scores) >= 4:
            q = quantiles(scores, n=4)
            q1, q3 = q[0], q[2]
            iqr = q3 - q1
        else:
            q1 = q3 = med
            iqr = 0.0
        rankings.append((med, iqr, model))
        print(f"{model:30s} median={med:.3f} ±{iqr:.3f} (n={len(scores)})")

    rankings.sort(reverse=True)
    print(f"\nRecommended default for '{kind}': {rankings[0][2]}")

    # Holdout validation (if any)
    if holdout_tasks:
        print(f"\n{'='*60}")
        print("HOLDOUT VALIDATION")
        print(f"{'='*60}")
        for task in holdout_tasks:
            print(f"Task: {task.get('prompt', '')[:60]}...")
            for model in CANDIDATES[kind]:
                correctness, cost, latency = run_task(task, model)
                score = score_task(correctness, cost, latency)
                status = "✓" if correctness else "✗"
                print(f"  {model:30s} {status} score={score:.3f}")
            print()

    print(f"\n{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Model evaluation harness")
    parser.add_argument("--task-kind", type=str, help="Evaluate specific task kind")
    parser.add_argument("--all-kinds", action="store_true", help="Evaluate all task kinds")
    parser.add_argument("--holdout", type=int, default=2, help="Number of holdout tasks per kind (default: 2)")
    args = parser.parse_args()

    if not Path("tasks.jsonl").exists():
        print("ERROR: tasks.jsonl not found")
        print("\nCreate tasks.jsonl with real tasks from your backlog, e.g.:")
        print("""
{"kind": "programming", "prompt": "Fix drift-test false positive in venue-truth reconciliation", "ground_truth": "hash_or_path"}
{"kind": "review", "prompt": "Review PR that adds SECONDSIDE1 gate", "ground_truth": "hash_or_path"}
""")
        return

    if args.all_kinds:
        for kind in CANDIDATES.keys():
            evaluate_kind(kind, n_holdout=args.holdout)
    elif args.task_kind:
        if args.task_kind not in CANDIDATES:
            print(f"Unknown task kind: {args.task_kind}")
            print(f"Available kinds: {list(CANDIDATES.keys())}")
            return
        evaluate_kind(args.task_kind, n_holdout=args.holdout)
    else:
        print("Specify --task-kind KIND or --all-kinds")
        print(f"Available kinds: {list(CANDIDATES.keys())}")


if __name__ == "__main__":
    main()
