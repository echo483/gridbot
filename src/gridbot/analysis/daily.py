"""Daily Claude analysis: deterministic evidence in, drafted insight out.

Slow loop ONLY (iron rail 1): this layer reads the registry, verdict summary,
shadow counts, model metrics and capture QA, asks Claude for an analyst read,
writes docs/analysis/YYYY-MM-DD.md and sends a short Telegram digest via
hermes. It never touches orders, registry.jsonl, dry_run, or any runtime
state — its output is a DRAFT for the human and the registry process.

Run: uv run python -m gridbot.analysis.daily [--dry-run] [--max-tokens N]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from gridbot import config

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5"
# Appended to a note whose SECOND attempt still hit max_tokens: a visibly
# truncated note beats one that reads as complete (four nightly notes,
# 08-29..09-02, were cut mid-sentence with no marker).
TRUNCATION_MARKER = "[TRUNCATED: analysis stopped at max_tokens twice — read as partial]"
REPO = Path(__file__).resolve().parents[3]

SYSTEM = """You are gridbot's slow-loop quant research analyst. gridbot trades esports
prediction markets on GRID data with an experiment-factory discipline: every
strategy is a frozen pre-registered hypothesis; verdicts come only from
held-out capture; dry_run until a pre-registered PASS; you never place orders
and never edit the registry — your output is a draft read for the human.

You receive today's deterministic evidence (registry tail, per-cell verdict
progress, shadow instance counts, model benchmark, capture QA). Write:
1. A line starting exactly with 'TELEGRAM:' followed by a <=700-character
   digest (plain text, no markdown) — the single most decision-relevant read
   of the day: evidence movement, anomalies, and the one next action.
2. A line 'REPORT:' followed by a markdown report (<=600 words): evidence
   review, calibration/drift concerns, hypothesis-by-hypothesis read,
   candidate ideas clearly labeled DRAFT (never as registered).
Ground every claim in the numbers provided; say 'no new evidence' when true."""


def _run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=REPO, check=False)
        return r.stdout.strip() or r.stderr.strip()[:400]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"(unavailable: {exc})"


def gather_evidence() -> str:
    registry = REPO / "docs/experiments/registry.jsonl"
    tail = registry.read_text().splitlines()[-8:] if registry.exists() else []
    metrics_path = REPO / "data/models/roundwin-v1/metrics.json"
    metrics = metrics_path.read_text() if metrics_path.exists() else "(no model)"
    from gridbot.instruments.verdict import compact_summary

    parts = [
        "## Registry tail (frozen hypotheses)",
        *[line[:600] for line in tail],
        "## Verdict progress (per cell, real instances only)",
        *compact_summary(),
        "## Model benchmark (pinned holdout)",
        metrics[:1500],
        "## Capture QA fingerprint",
        _run([sys.executable, "scripts/capture_qa.py"]),
        "## UTC now",
        datetime.now(UTC).isoformat(timespec="seconds"),
    ]
    return "\n".join(parts)


def ask_claude(evidence: str, max_tokens: int) -> str:
    key = config.require("ANTHROPIC_API_KEY")
    model = os.environ.get("GRIDBOT_ANALYSIS_MODEL", DEFAULT_MODEL)

    def call(budget: int) -> tuple[str, str | None]:
        resp = httpx.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": budget,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": evidence}],
            },
            timeout=120.0,
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"anthropic API HTTP {resp.status_code}")
        data = resp.json()
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return text, data.get("stop_reason")

    text, stop = call(max_tokens)
    if stop == "max_tokens":
        # Claude 5 models think by default and thinking counts against
        # max_tokens: long evidence can consume the whole budget before any
        # text block appears (observed 2026-08-25: 200 OK, 0 text chars) —
        # or, worse, after a PARTIAL text block, which 805ec73's empty-only
        # retry wrote out as a complete-looking note (docs/analysis
        # 2026-08-29..09-02 all end mid-sentence). Retry on the stop reason,
        # not on emptiness.
        text, stop = call(max_tokens * 3)
    if not text:
        raise RuntimeError(f"empty analysis output (stop_reason={stop})")
    if stop == "max_tokens":
        text = f"{text}\n{TRUNCATION_MARKER}"
    return text


def split_output(text: str) -> tuple[str, str]:
    telegram, report = "", text
    if "TELEGRAM:" in text:
        after = text.split("TELEGRAM:", 1)[1]
        if "REPORT:" in after:
            telegram, report = after.split("REPORT:", 1)
        else:
            telegram = after
    return telegram.strip()[:900], report.strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print only; no file, no telegram")
    ap.add_argument("--max-tokens", type=int, default=3000)
    args = ap.parse_args()

    text = ask_claude(gather_evidence(), args.max_tokens)
    telegram, report = split_output(text)
    if args.dry_run:
        print("--- TELEGRAM ---\n" + telegram + "\n--- REPORT ---\n" + report[:1500])
        return
    out_dir = REPO / "docs/analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{datetime.now(UTC):%Y-%m-%d}.md"
    out.write_text(f"# gridbot daily analysis — {datetime.now(UTC):%Y-%m-%d}\n\n{report}\n")
    if telegram:
        _run(["/home/ops/.local/bin/hermes", "send", "-t", "telegram", "🧭 " + telegram])
    print(f"wrote {out} ({len(report)} chars); telegram {'sent' if telegram else 'empty'}")


if __name__ == "__main__":
    main()
