"""Deep-analysis session on gridbot's Claude Managed Agent (slow loop, weekly).

Data plane: gathers fresh evidence (corpus, registry, verdict summary, model
metrics, capture QA), uploads it, and runs one budget-capped session of the
pre-created agent (scripts/setup_managed_agent.py) in its hermetic sandbox —
limited networking, no reachable hosts, no secrets inside. The agent's
deliverables (analysis.md + telegram.txt) are downloaded from session outputs,
archived under docs/analysis/managed/, and the digest goes to Telegram.

Iron rails: drafts only — no orders, no registry writes, dry_run untouched.

Run: uv run python -m gridbot.analysis.managed [--evidence-only]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import anthropic

from gridbot import config

IDS_FILE = config.DATA_DIR / "analysis" / "managed_ids.json"
OUT_ROOT = Path(__file__).resolve().parents[3] / "docs" / "analysis" / "managed"
REPO = Path(__file__).resolve().parents[3]
SESSION_BUDGET_CENTS = "500"  # $5.00 hard cap per session
WALL_CLOCK_LIMIT_S = 25 * 60

KICKOFF = """Evidence for this run is mounted under /workspace/evidence/ (some
sandboxes surface file mounts at /mnt/session/uploads/workspace/evidence/ —
check both):

- training_rounds.jsonl — the full training corpus, one JSON object per line.
  Columns: id; row_key "series:map:round" plus ":b<n>" suffix for time-bin
  n>0 (no suffix = bin 0); series_id; map_number; round_number; features
  (dict of 20 floats, stored alphabetically); label (1.0 = the CT-side team
  won the round); ct_team/t_team; provenance fields.
  BINDING DATA-INTEGRITY FILTER (registry CORPUS-F1-LEAKFILTER): before any
  aggregate, drop rows failing rounds_played + 1 == round_number checked
  against BOTH features.round_number and the top-level round_number (~13% of
  rows are post-round snapshots that encode their own label).
- registry.jsonl — the frozen experiment registry: falsified lanes stay
  closed; H-MANADV1 / H-ECONBUY1 / H-PISTOLCARRY1 / H-MODELENTRY1(+CELLS)
  are the open pre-registered hypotheses with frozen falsification bars.
- verdict.json — per-cell instrument progress from the live shadow engine
  (n, series spread, win rates with CIs, paper/maker PnL when quoted).
- model_metrics.json — the served model's pinned-holdout benchmark
  (roundwin-v1; leak-filtered; temporal 32-series holdout).
- capture_qa.txt — capture-health fingerprint at run time.

Do your standing job per the system instructions. Focus areas this run:
(a) recompute the headline corpus edges under the binding filter and flag any
drift from the registry's frozen anchors; (b) probe feature interactions the
registered hypotheses do NOT yet exploit (candidate DRAFTs); (c) sanity-check
the pinned benchmark's calibration by score/round segment; (d) read the
verdict progress and state exactly what evidence is still missing for each
frozen bar. Write /mnt/session/outputs/analysis.md and
/mnt/session/outputs/telegram.txt."""


def _run(cmd: list[str], timeout: int = 180) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=REPO, check=False)
    return r.stdout


def gather_evidence(scratch: Path) -> dict[str, Path]:
    scratch.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    corpus = config.DATA_DIR / "analysis" / "training_rounds.jsonl"
    if corpus.exists():
        files["training_rounds.jsonl"] = corpus
    registry = REPO / "docs" / "experiments" / "registry.jsonl"
    if registry.exists():
        files["registry.jsonl"] = registry
    metrics = config.DATA_DIR / "models" / "roundwin-v1" / "metrics.json"
    if metrics.exists():
        files["model_metrics.json"] = metrics
    verdict = scratch / "verdict.json"
    verdict.write_text(
        _run([sys.executable, "-m", "gridbot.instruments.verdict", "--json"]) or "[]"
    )
    files["verdict.json"] = verdict
    qa = scratch / "capture_qa.txt"
    qa.write_text(_run([sys.executable, "scripts/capture_qa.py"]))
    files["capture_qa.txt"] = qa
    return files


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--evidence-only", action="store_true", help="gather + list, no session")
    args = ap.parse_args()

    if not IDS_FILE.exists():
        raise SystemExit("no managed agent yet — run scripts/setup_managed_agent.py first")
    ids = json.loads(IDS_FILE.read_text())

    stamp = f"{datetime.now(UTC):%Y-%m-%d}"
    scratch = config.DATA_DIR / "analysis" / "managed-scratch"
    evidence = gather_evidence(scratch)
    print(f"evidence: {', '.join(f'{n} ({p.stat().st_size // 1024}KiB)' for n, p in evidence.items())}")
    if args.evidence_only:
        return

    client = anthropic.Anthropic(api_key=config.require("ANTHROPIC_API_KEY"))

    resources = []
    for name, path in evidence.items():
        uploaded = client.beta.files.upload(file=path)
        resources.append(
            {"type": "file", "file_id": uploaded.id, "mount_path": f"/workspace/evidence/{name}"}
        )
        print(f"uploaded {name} -> {uploaded.id}")

    session = client.beta.sessions.create(
        agent={"type": "agent", "id": ids["agent_id"], "version": ids["agent_version"]},
        environment_id=ids["environment_id"],
        title=f"gridbot deep analysis {stamp}",
        resources=resources,
        budget={"type": "limit", "max_list_cost": {"amount": SESSION_BUDGET_CENTS, "currency": "USD"}},
        initial_events=[{"type": "user.message", "content": [{"type": "text", "text": KICKOFF}]}],
    )
    print(f"session: {session.id} ({session.status})")
    print(f"trace: https://platform.claude.com/workspaces/default/sessions/{session.id}")

    deadline = time.monotonic() + WALL_CLOCK_LIMIT_S
    done_reason = "unknown"
    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        for event in stream:
            if time.monotonic() > deadline:
                done_reason = "wall_clock_limit"
                client.beta.sessions.events.send(
                    session_id=session.id, events=[{"type": "user.interrupt"}]
                )
                break
            if event.type == "agent.message":
                for block in event.content:
                    if getattr(block, "type", "") == "text":
                        print(f"[agent] {block.text[:300]}")
            elif event.type == "session.status_terminated":
                done_reason = "terminated"
                break
            elif event.type == "session.status_idle":
                stop = getattr(event, "stop_reason", None)
                stop_type = getattr(stop, "type", None)
                if stop_type != "requires_action":
                    done_reason = str(stop_type)
                    break
    print(f"session finished: {done_reason}")

    out_dir = OUT_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    telegram_text = ""
    for attempt in range(4):
        listed = list(
            client.beta.files.list(scope_id=session.id, betas=["managed-agents-2026-04-01"])
        )
        downloaded = 0
        for f in listed:
            try:
                content = client.beta.files.download(f.id)
            except anthropic.BadRequestError:
                continue  # uploaded evidence is session-scoped but not downloadable
            target = out_dir / Path(f.filename).name
            content.write_to_file(str(target))
            downloaded += 1
            print(f"output: {target}")
            if target.name == "telegram.txt":
                telegram_text = target.read_text().strip()[:900]
        if downloaded:
            break
        time.sleep(2 + attempt)
    else:
        print("no session outputs found")

    for _ in range(10):
        if client.beta.sessions.retrieve(session_id=session.id).status != "running":
            break
        time.sleep(1)
    client.beta.sessions.archive(session_id=session.id)

    if telegram_text:
        subprocess.run(
            ["/home/ops/.local/bin/hermes", "send", "-t", "telegram", "🔬 deep analysis: " + telegram_text],
            capture_output=True,
            timeout=30,
            check=False,
        )
        print("telegram digest sent")


if __name__ == "__main__":
    main()
