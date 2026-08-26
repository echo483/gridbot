"""Task-routed agent dispatch (slow loop ONLY — iron rail 1).

One routing table for every Hermes/OpenRouter delegation so dispatches
stay managed and cohesive: pick the model by TASK KIND, never ad-hoc.
Roster set with the user 2026-08-26 (afternoon session):

  programming      z-ai/glm-5.3-flash                DEFAULT generalist/builder (user 08-26:
                                                     "blowing others out of the water" — most
                                                     prominent model, use throughout)
  draft            z-ai/glm-5.3-flash                non-sensitive drafts, docs, summaries
  fast             deepseek/deepseek-v4-flash-0731   LATENCY-SENSITIVE tasks: best token/s,
                                                     lowest latency, widest provider range
  classification   deepseek/deepseek-v4-flash-0731   bulk labeling/extraction/transforms
  review           anthropic/claude-opus-5           adversarial review, hard reasoning
  review2          z-ai/glm-5.3                      the ONLY full-size 5.3 use (review bench
                                                     record earns it; everywhere else = flash)

Rules encoded here, not re-decided per call:
- --safe-mode always; prompts must be self-contained (paste data in;
  agents get no repo access guarantees). Prompts ride argv and are
  visible in /proc/<pid>/cmdline for the run — NEVER paste secrets or
  key material into a dispatch prompt (security review 08-26, LOW-2).
- NOTHING sensitive to the cheap builder tier; secrets to NO agent, ever.
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
    # User 08-26: glm-5.3-flash (released from stealth 'ox-alpha' the same
    # day) is the primary builder — cheap, use it hard.
    # Judge agents by ARTIFACTS, not transcripts: the hermes -z transcript
    # channel renders as mojibake for several models while the files they
    # write are clean (verified twice: deepseek scorer 50/50 tests,
    # glm-5.3-flash wave_detect correct on review).
    "programming": ("z-ai/glm-5.3-flash", "openrouter"),
    "draft": ("z-ai/glm-5.3-flash", "openrouter"),
    "fast": ("deepseek/deepseek-v4-flash-0731", None),
    "classification": ("deepseek/deepseek-v4-flash-0731", None),
    "review": ("anthropic/claude-opus-5", "openrouter"),
    "review2": ("z-ai/glm-5.3", "openrouter"),
    "programming2": ("deepseek/deepseek-v4-flash-0731", None),  # backup builder
}


def _run(kind: str, prompt: str, timeout: int) -> tuple[int, str]:
    model, provider = ROUTES[kind]
    cmd = ["hermes", "-m", model]
    if provider:
        cmd += ["--provider", provider]
    cmd += ["-z", prompt, "--safe-mode"]
    print(f"dispatch[{kind}] -> {model}", file=sys.stderr)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, ""
    return res.returncode, res.stdout


def pair(spec_file: str, artifacts: list[str], timeout: int) -> int:
    """A2A development relay (user directive 08-26): glm-5.3-flash BUILDS,
    Opus 5 REVIEWS the actual artifacts, the builder REVISES from the
    review. Claude verifies before anything lands — the relay replaces
    neither tests nor the human-side review gate. Opus stalls fall back
    to glm-5.3 (observed >25min hang via hermes once)."""
    spec = Path(spec_file).read_text()

    rc, _ = _run("programming", spec, timeout)
    missing = [a for a in artifacts if not Path(a).exists()]
    if missing:
        print(f"pair: builder did not produce {missing}; aborting", file=sys.stderr)
        return 1

    bundle = "\n\n".join(
        f"=== FILE {a} ===\n{Path(a).read_text()}" for a in artifacts
    )
    review_prompt = (
        "Adversarial review of code built by another agent from the spec "
        "below. Numbered findings with severity (BLOCKER/MAJOR/MINOR/NIT), "
        "cite lines, one-line verdict at the end. Spec:\n\n" + spec
        + "\n\nARTIFACTS:\n\n" + bundle
    )
    rc, review = _run("review", review_prompt, min(timeout, 1200))
    if rc != 0 or not review.strip():
        print("pair: opus review failed/stalled — falling back to review2",
              file=sys.stderr)
        rc, review = _run("review2", review_prompt, timeout)
    if not review.strip():
        print("pair: no review produced; artifacts stand UNREVIEWED", file=sys.stderr)
        return 1

    revise_prompt = (
        "You built the files below from the spec. An independent reviewer "
        "returned the findings that follow. Apply every BLOCKER/MAJOR (and "
        "MINORs you agree with) by rewriting the files IN PLACE at the same "
        "paths. If you reject a finding, say why in a code comment.\n\nSPEC:\n"
        + spec + "\n\nCURRENT FILES:\n" + bundle + "\n\nREVIEW FINDINGS:\n" + review
    )
    _run("programming", revise_prompt, timeout)
    print("=== REVIEW TRAIL ===")
    print(review)
    print("pair: relay complete — verify artifacts + run tests before landing",
          file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("kind", choices=[*sorted(ROUTES), "pair"])
    ap.add_argument("prompt_file", help="prompt file (for pair: the build spec)")
    ap.add_argument("--out", help="write agent output here (default stdout)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--artifact", action="append", default=[],
                    help="pair mode: expected artifact path (repeatable)")
    args = ap.parse_args()

    if args.kind == "pair":
        if not args.artifact:
            ap.error("pair mode needs at least one --artifact")
        return pair(args.prompt_file, args.artifact, args.timeout)

    rc, out = _run(args.kind, Path(args.prompt_file).read_text(), args.timeout)
    if args.out:
        Path(args.out).write_text(out)
        print(f"wrote {args.out} ({len(out)} bytes)", file=sys.stderr)
    else:
        print(out)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
