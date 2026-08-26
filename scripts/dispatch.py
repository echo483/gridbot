"""Task-routed agent dispatch (slow loop ONLY — iron rail 1).

One routing table for every Hermes/OpenRouter delegation so dispatches
stay managed and cohesive: pick the model by TASK KIND, never ad-hoc.
Roster set with the user 2026-08-26 (afternoon session):

  programming      deepseek/deepseek-v4-flash-0731   bulk code + tests ("#1s rn")
  classification   deepseek/deepseek-v4-flash-0731   labeling/extraction/bulk transforms
  draft            stealth/ox-alpha                  non-sensitive drafts, docs, summaries (free)
  review           anthropic/claude-opus-5           adversarial review, hard reasoning
  review2          z-ai/glm-5.3                      second reviewer (caught 403f62a BLOCKER)

Rules encoded here, not re-decided per call:
- --safe-mode always; prompts must be self-contained (paste data in;
  agents get no repo access guarantees).
- NOTHING sensitive to ox-alpha; secrets to NO agent, ever.
- hermes -z transcripts mirror to the user's Telegram — batch dispatches,
  don't spam.
- Every agent-authored file/finding is reviewed by Claude before it
  lands; engine/config diffs get an independent `review` pass before
  committing.

Run: uv run python scripts/dispatch.py <kind> <prompt-file> [--out FILE]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROUTES: dict[str, tuple[str, str | None]] = {
    # kind -> (model, provider-override or None)
    "programming": ("deepseek/deepseek-v4-flash-0731", None),
    "classification": ("deepseek/deepseek-v4-flash-0731", None),
    "draft": ("stealth/ox-alpha", None),
    "review": ("anthropic/claude-opus-5", "openrouter"),
    "review2": ("z-ai/glm-5.3", "openrouter"),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("kind", choices=sorted(ROUTES))
    ap.add_argument("prompt_file", help="file with the full self-contained prompt")
    ap.add_argument("--out", help="write agent output here (default stdout)")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    model, provider = ROUTES[args.kind]
    prompt = Path(args.prompt_file).read_text()
    cmd = ["hermes", "-m", model]
    if provider:
        cmd += ["--provider", provider]
    cmd += ["-z", prompt, "--safe-mode"]
    print(f"dispatch[{args.kind}] -> {model}", file=sys.stderr)
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
    out = res.stdout
    if args.out:
        Path(args.out).write_text(out)
        print(f"wrote {args.out} ({len(out)} bytes)", file=sys.stderr)
    else:
        print(out)
    if res.returncode != 0:
        print(res.stderr[-500:], file=sys.stderr)
    return res.returncode


if __name__ == "__main__":
    raise SystemExit(main())
