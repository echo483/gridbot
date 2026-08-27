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

Agents as code: every lane has a version-controlled system preamble in
agents/<lane>.md (fast/search/classification share analysis.md). _run
prepends it to every prompt, so lane behavior — rails, output contract,
craft rules — is declarative and reproducible, not re-typed per dispatch.
Editing a lane's behavior = editing its file, in review like any code.

Run: uv run python scripts/dispatch.py <kind> <prompt-file> [--out FILE]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROUTES: dict[str, tuple[str, str | None, str | None]] = {
    # kind -> (model, provider-override or None)
    #
    # provider "openrouter" = tell hermes which GATEWAY to use (its native
    # resolution mis-routed glm/opus early on). provider None = NO --provider
    # flag: hermes resolves its own provider for that model — deliberate for
    # deepseek-0731, where hermes-native routing works and picks from the
    # widest/fastest provider pool (verified in logs 08-26). Don't pin it.
    #
    # ":floor" = OpenRouter price-sorted provider routing (passes through
    # hermes' OpenAI-compatible endpoint verbatim; smoke-tested 08-26).
    # User 08-26: z-ai serving via Novita AI / GMI Cloud is 50% off — :floor
    # lands on the discounters automatically.
    #
    # Opus 5 doctrine (user 08-26 eve): use it wherever it MATTERS — review
    # with hard reasoning first and foremost, light-but-important uses fine —
    # but SPARING, the API is expensive (Fable rides the Claude Code account).
    #
    # Judge agents by ARTIFACTS, not transcripts: the hermes -z transcript
    # channel renders as mojibake for several models while the files they
    # write are clean (verified twice: deepseek scorer 50/50 tests,
    # glm-5.3-flash wave_detect correct on review).
    #
    # (Hermes-side config, NOT ours to manage here: hermes default =
    # glm-5.3-flash, fallback = qwen3.8-max — user-confirmed 08-26 eve.)
    #
    # Third field = --reasoning level (hermes global default is only
    # "medium" — discovered 08-26 when builder outputs came back thin;
    # user: the default builder has "gotta be very very good"). Builders
    # think HIGH, the opus review thinks XHIGH (user: "review with hard
    # reasoning"), the speed lane stays LOW — reasoning tokens are
    # latency there, not quality.
    "programming": ("z-ai/glm-5.3-flash:floor", "openrouter", "high"),
    "draft": ("z-ai/glm-5.3-flash:floor", "openrouter", "medium"),
    "fast": ("deepseek/deepseek-v4-flash-0731", None, "low"),
    "classification": ("deepseek/deepseek-v4-flash-0731", None, "low"),
    "review": ("anthropic/claude-opus-5", "openrouter", "xhigh"),
    "review2": ("z-ai/glm-5.3:floor", "openrouter", "high"),
    # deepseek-0731 expansion (user 08-27: top of many categories, avoid
    # one-sidedness; its observed weakness is the hermes AGENT/tool
    # harness, not intelligence — so it gets hands-on-keyboard lanes,
    # never tool-driving cron jobs):
    "programming2": ("deepseek/deepseek-v4-flash-0731", None, "high"),  # full alternate builder — rotate real builds here, judge by artifacts
    "search": ("deepseek/deepseek-v4-flash-0731", None, "medium"),      # retrieval/digest/extract over pasted material — its #1 category per user
    "analysis": ("deepseek/deepseek-v4-flash-0731", None, "medium"),    # slow-loop data reads, log digestion, report drafts
}


AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"
# Lanes without their own preamble file share the analysis one (bulk
# data-work contract); every other kind maps to agents/<kind>.md.
_SHARED_PREAMBLE = {"fast": "analysis", "search": "analysis",
                    "classification": "analysis"}


def _preamble(kind: str) -> str:
    path = AGENTS_DIR / f"{_SHARED_PREAMBLE.get(kind, kind)}.md"
    if not path.exists():
        print(f"dispatch[{kind}]: no lane preamble at {path} — sending bare",
              file=sys.stderr)
        return ""
    return path.read_text().strip() + "\n\n---\n\nTASK:\n\n"


def _run(kind: str, prompt: str, timeout: int) -> tuple[int, str]:
    model, provider, reasoning = ROUTES[kind]
    prompt = _preamble(kind) + prompt
    cmd = ["hermes", "-m", model]
    if provider:
        cmd += ["--provider", provider]
    if reasoning:
        cmd += ["--reasoning", reasoning]
    cmd += ["-z", prompt, "--safe-mode"]
    print(f"dispatch[{kind}] -> {model}", file=sys.stderr)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, ""
    return res.returncode, res.stdout


def pair(spec_file: str, artifacts: list[str], timeout: int,
         reviewer: str = "review", builder: str = "programming") -> int:
    """A2A development relay (user directive 08-26): glm-5.3-flash BUILDS,
    the reviewer lane REVIEWS the actual artifacts, the builder REVISES
    from the review. Claude verifies before anything lands — the relay
    replaces neither tests nor the human-side review gate.

    Reviewer choice is a COST decision (user 08-26 late eve — opus eats
    credits fast, input tokens are the driver since review bundles carry
    the whole spec + artifacts):
    - reviewer="review" (opus xhigh): engine/config/trade-path code and
      anything that decides a registration. Worth it.
    - reviewer="review2" (glm-5.3:floor high): routine analysis scripts,
      connectivity checks, second passes. Also the automatic fallback
      when opus stalls (observed >25min hang via hermes once).
    Anything deferrable to Claude-account usage (Claude's own review or
    an account-side subagent) should go there instead of paid API."""
    spec = Path(spec_file).read_text()

    rc, _ = _run(builder, spec, timeout)
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
    rc, review = _run(reviewer, review_prompt, min(timeout, 1200))
    if rc != 0 or not review.strip():
        fallback = "review2" if reviewer != "review2" else "programming"
        print(f"pair: {reviewer} failed/stalled — falling back to {fallback}",
              file=sys.stderr)
        rc, review = _run(fallback, review_prompt, timeout)
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
    _run(builder, revise_prompt, timeout)
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
    ap.add_argument("--builder", choices=["programming", "programming2"],
                    default="programming",
                    help="pair mode builder lane: programming=glm-flash, "
                         "programming2=deepseek-0731 (alternate-builder rotation)")
    ap.add_argument("--reviewer", choices=["review", "review2"],
                    default="review",
                    help="pair mode reviewer lane: review=opus xhigh "
                         "(critical code), review2=glm-5.3 high (routine)")
    args = ap.parse_args()

    if args.kind == "pair":
        if not args.artifact:
            ap.error("pair mode needs at least one --artifact")
        return pair(args.prompt_file, args.artifact, args.timeout,
                    args.reviewer, args.builder)

    rc, out = _run(args.kind, Path(args.prompt_file).read_text(), args.timeout)
    if args.out:
        Path(args.out).write_text(out)
        print(f"wrote {args.out} ({len(out)} bytes)", file=sys.stderr)
    else:
        print(out)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
