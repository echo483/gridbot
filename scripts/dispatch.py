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
import hashlib
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
    # MODEL DIVERSIFICATION (user 08-28: "use qwen, use nemotron, don't stick
    # to defaults"). Slugs verified live against OpenRouter's model list on
    # 2026-08-28 — do NOT invent slugs: hermes' own fallback is pinned to
    # "qwen/qwen3.8-max", which does not appear in that list.
    # CAVEAT: a model's self-reported identity is NOT proof of routing —
    # nemotron answered "OK Hermes" in a smoke test, so hermes injects a
    # persona. The OpenRouter dashboard is the only ground truth for which
    # model actually served a call.
    "reasoning": ("qwen/qwen3-max-thinking", "openrouter", "high"),   # hard design/analysis: model specs, leakage hunts, method arguments
    "bulk": ("nvidia/nemotron-3-super-120b-a12b", "openrouter", "medium"),  # cheap high-volume passes (1M ctx, $0.4/Mtok out) — alternative to deepseek
    # VERIFICATION lane (user 08-28): the Claude console credits buy exactly
    # one thing — adversarial verification of what OpenRouter built, plus
    # method review of quant/backtest/sim harnesses. Provider "anthropic"
    # means this does NOT shell out to hermes: the model, effort and key are
    # pinned HERE, in git, so hermes' global default/fallback/MoA/cron model
    # config can never reach this lane (the config-drift failure mode).
    # It may propose production code; nothing it writes lands unreviewed.
    "verify": ("claude-opus-5", "anthropic", "xhigh"),
}


AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"
# Lanes without their own preamble file share the analysis one (bulk
# data-work contract); every other kind maps to agents/<kind>.md.
_SHARED_PREAMBLE = {"fast": "analysis", "search": "analysis",
                    "classification": "analysis", "bulk": "analysis",
                    "reasoning": "programming"}


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
    if provider == "anthropic":
        # Direct Claude API call: key read from the OS keyring inside the
        # process, never argv (visible in /proc/<pid>/cmdline), never env.
        from _anthropic_lane import run as _anthropic_run
        print(f"dispatch[{kind}] -> {model} (anthropic API)", file=sys.stderr)
        try:
            return 0, _anthropic_run(prompt, model=model, effort=reasoning or "high",
                                     label=kind)
        except Exception as exc:  # noqa: BLE001 — budget/API failures report, never crash a relay
            print(f"dispatch[{kind}] FAILED: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1, ""
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


def _digests(artifacts: list[str]) -> dict[str, str | None]:
    """sha256 per artifact, None when absent. `exists()` cannot tell an
    unchanged pre-existing file from one the builder actually wrote."""
    out: dict[str, str | None] = {}
    for a in artifacts:
        path = Path(a)
        out[a] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return out


def _write_trail(out: str | None, sections: list[tuple[str, str]]) -> None:
    """Persist the whole relay. The builder's TEXT is part of the evidence:
    every OpenRouter lane returns code as text (hermes -z --safe-mode cannot
    write to the repo), so discarding stdout discards the work product."""
    if not out:
        return
    body = "\n\n".join(f"=== {title} ===\n{text}" for title, text in sections if text)
    Path(out).write_text(body)
    print(f"pair: trail written to {out} ({len(body)} bytes)", file=sys.stderr)


def pair(spec_file: str, artifacts: list[str], timeout: int,
         reviewer: str = "review", builder: str = "programming",
         out: str | None = None) -> int:
    """A2A development relay (user directive 08-26): the builder lane BUILDS,
    the reviewer lane REVIEWS the actual artifacts, the builder REVISES from
    the review. Claude verifies before anything lands — the relay replaces
    neither tests nor the human-side review gate.

    Reviewer choice is a COST decision (user 08-26 late eve — opus eats
    credits fast, input tokens are the driver since review bundles carry
    the whole spec + artifacts):
    - reviewer="review" (opus xhigh): engine/config/trade-path code and
      anything that decides a registration. Worth it.
    - reviewer="review2" (glm-5.3:floor high): routine analysis scripts,
      connectivity checks, second passes. Also the automatic fallback
      when opus stalls (observed >25min hang via hermes once).
    Anything deferrable to Claude-account usage (Claude's own review or
    an account-side subagent) should go there instead of paid API.

    EVIDENCE CONTRACT (2026-08-30 postmortem): the builder's stdout is kept,
    artifacts are compared by DIGEST not existence, and --out receives the
    whole trail. A dispatch that produces nothing must say so loudly rather
    than return 0 with an untouched file.
    """
    spec = Path(spec_file).read_text()

    before = _digests(artifacts)
    _, build_text = _run(builder, spec, timeout)
    after = _digests(artifacts)

    missing = [a for a in artifacts if after[a] is None]
    if missing:
        print(f"pair: builder did not produce {missing}; aborting", file=sys.stderr)
        _write_trail(out, [("SPEC", spec), ("BUILDER OUTPUT (no files written)", build_text)])
        return 1

    unchanged = [a for a in artifacts if before[a] == after[a]]
    if unchanged:
        print(
            f"pair: WARNING artifacts UNCHANGED by the builder: {unchanged}. "
            "This lane returns code as TEXT and cannot write to the repo "
            "(hermes -z --safe-mode). Its output is preserved in the trail — "
            "review it there and apply by hand.",
            file=sys.stderr,
        )

    # Review what the builder ACTUALLY produced. When it wrote no files but
    # returned text, reviewing the untouched on-disk file reviews the
    # pre-build state — which is what happened on 2026-08-30 and wasted the
    # reviewer leg. Review the text instead.
    if unchanged == artifacts and build_text.strip():
        bundle = f"=== BUILDER OUTPUT (text; no files written) ===\n{build_text}"
    else:
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
        _write_trail(out, [("SPEC", spec), ("BUILDER OUTPUT", build_text)])
        return 1

    revise_prompt = (
        "You built the files below from the spec. An independent reviewer "
        "returned the findings that follow. Apply every BLOCKER/MAJOR (and "
        "MINORs you agree with) by rewriting the files IN PLACE at the same "
        "paths. If you reject a finding, say why in a code comment.\n\nSPEC:\n"
        + spec + "\n\nCURRENT FILES:\n" + bundle + "\n\nREVIEW FINDINGS:\n" + review
    )
    _, revise_text = _run(builder, revise_prompt, timeout)

    _write_trail(out, [
        ("BUILDER OUTPUT", build_text),
        ("REVIEW FINDINGS", review),
        ("BUILDER REVISION", revise_text),
    ])
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
    ap.add_argument("--builder",
                    choices=["programming", "programming2", "reasoning"],
                    default="programming",
                    help="pair mode builder lane: programming=glm-flash, "
                         "programming2=deepseek-0731 (alternate-builder rotation)")
    ap.add_argument("--reviewer", choices=["review", "review2", "verify"],
                    default="review",
                    help="pair mode reviewer lane: review=opus via hermes, "
                         "review2=glm-5.3 (routine), verify=Claude API "
                         "(console credits; trade-path + quant method)")
    args = ap.parse_args()

    if args.kind == "pair":
        if not args.artifact:
            ap.error("pair mode needs at least one --artifact")
        return pair(args.prompt_file, args.artifact, args.timeout,
                    args.reviewer, args.builder, args.out)

    rc, out = _run(args.kind, Path(args.prompt_file).read_text(), args.timeout)
    if args.out:
        Path(args.out).write_text(out)
        print(f"wrote {args.out} ({len(out)} bytes)", file=sys.stderr)
    else:
        print(out)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
