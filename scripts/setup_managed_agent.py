"""One-time setup: create gridbot's Claude Managed Agent + hermetic environment.

Control plane only — run once, store the IDs, never call create in the run
path (gridbot.analysis.managed reads the stored IDs). The environment uses
LIMITED networking with no allowed hosts: the sandbox can install packages
but reach nothing else, so evidence goes in as mounted files and only drafted
analysis comes out. No gridbot secret ever enters the sandbox.

Run: uv run python scripts/setup_managed_agent.py [--force]
"""

from __future__ import annotations

import argparse
import json

import anthropic

from gridbot import config

IDS_FILE = config.DATA_DIR / "analysis" / "managed_ids.json"

SYSTEM = """You are gridbot's deep-analysis quant researcher (slow loop only). gridbot
trades esports prediction markets on GRID data under an experiment-factory
discipline: every strategy is a frozen pre-registered hypothesis in a
registry; verdicts come only from held-out capture; dry_run until a
pre-registered PASS. You never place orders, never edit the registry, and
never handle credentials — your output is DRAFT analysis for the human and
the registry process, nothing more.

Each session mounts evidence files under /workspace/evidence/ and the kickoff
message describes their schemas and the current hypothesis states. Work
strictly from these files: compute real numbers with Python (pip install what
you need, e.g. pandas), apply any data-integrity filters the kickoff names
BEFORE trusting aggregate statistics, verify surprising findings by
recomputing them a second way, and quantify everything with group sizes.

Deliverables — write BOTH files to /mnt/session/outputs/:
1. analysis.md — the full drafted report: evidence review, calibration and
   drift checks against the pinned benchmark, per-hypothesis read (distance
   to each frozen falsification bar), anomalies with computed evidence, and
   ranked candidate ideas each explicitly labeled DRAFT with a mechanism
   claim and a falsification sketch. Ground every claim in numbers you
   computed this session; say "no new evidence" when that is the truth.
2. telegram.txt — a plain-text digest, 700 characters maximum, of the single
   most decision-relevant finding and the one next action."""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="recreate even if IDs exist")
    args = ap.parse_args()

    if IDS_FILE.exists() and not args.force:
        print(f"already set up: {IDS_FILE}\n{IDS_FILE.read_text()}")
        return

    client = anthropic.Anthropic(api_key=config.require("ANTHROPIC_API_KEY"))

    environment = client.beta.environments.create(
        name="gridbot-analysis",
        description="Hermetic analysis sandbox: package installs only, no other egress.",
        config={
            "type": "cloud",
            "networking": {"type": "limited", "allow_package_managers": True},
        },
    )
    print(f"environment: {environment.id}")

    agent = client.beta.agents.create(
        name="gridbot-analyst",
        description="Slow-loop deep analysis over mounted gridbot evidence; drafts only.",
        model={"id": "claude-opus-5", "effort": "high"},
        system=SYSTEM,
        tools=[
            {
                "type": "agent_toolset_20260401",
                "default_config": {"enabled": True},
                "configs": [
                    {"name": "web_fetch", "enabled": False},
                    {"name": "web_search", "enabled": False},
                ],
            }
        ],
    )
    print(f"agent: {agent.id} v{agent.version}")

    IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    IDS_FILE.write_text(
        json.dumps(
            {
                "environment_id": environment.id,
                "agent_id": agent.id,
                "agent_version": agent.version,
            },
            indent=1,
        )
    )
    print(f"stored: {IDS_FILE}")


if __name__ == "__main__":
    main()
