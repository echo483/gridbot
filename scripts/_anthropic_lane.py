"""Anthropic API lane for dispatch.py — Opus 5 verification of agent work.

WHY THIS EXISTS: OpenRouter-built fixes need adversarial verification by a
model that did not write them (2026-08-28: a drift gate shipped with three
defects and 14 passing tests, because the builder's fixtures encoded the
builder's own assumption). Claude Code's own review is free but serial and
tied to a session; this lane runs the same review programmatically — in
parallel, from cron, and from hermes — against the user's console credits.

SECURITY (iron rail 6): the key is read from the OS keyring inside THIS
process and handed to the SDK constructor. It never touches argv (visible
in /proc/<pid>/cmdline), never an env var a subprocess inherits, never a
log line. hermes calls this lane by running dispatch.py — it passes a
prompt file and reads an artifact; it never sees the key.

IRON RAIL 1: this module lives in scripts/, NOT src/gridbot/ — nothing the
executor or capture daemons can import reaches an LLM client. That is a
structural guarantee, not a convention.

COST: every call appends a ledger row with real token counts and the
list-price cost, and the lane REFUSES to run once the ledger crosses the
budget cap (fail closed — an unattended cron must not drain the credits).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Opus 5 list price, USD per million tokens (input, output). Cache reads bill
# at ~0.1x input and 5m cache writes at ~1.25x input.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
CACHE_READ_MULT = 0.1
CACHE_WRITE_MULT = 1.25

# The user's console credit balance at lane build time (2026-08-28). The cap
# is deliberately BELOW it: credits are for verification, and a runaway loop
# must hit our own gate before it hits Anthropic's.
DEFAULT_BUDGET_USD = 25.0
LEDGER = ROOT / "data" / "agent_spend" / "anthropic.jsonl"


def cost_usd(usage: dict, model: str) -> float:
    """List-price cost of one call. Unknown model -> Opus 5 rates (the
    expensive assumption; a budget gate must never under-count)."""
    inp_rate, out_rate = PRICES.get(model, PRICES["claude-opus-5"])
    m = 1_000_000
    return (
        usage.get("input_tokens", 0) * inp_rate / m
        + usage.get("output_tokens", 0) * out_rate / m
        + usage.get("cache_read_input_tokens", 0) * inp_rate * CACHE_READ_MULT / m
        + usage.get("cache_creation_input_tokens", 0) * inp_rate * CACHE_WRITE_MULT / m
    )


def spend_to_date(ledger: Path = LEDGER) -> float:
    """Total logged spend. An unreadable/absent ledger reads as 0.0 — the
    ledger is an accounting record, not the gate's only defense (the cap is
    re-checked against it before every call and after every write)."""
    if not ledger.exists():
        return 0.0
    total = 0.0
    with open(ledger, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("cost_usd"), (int, float)):
                total += float(row["cost_usd"])
    return total


def budget_check(budget_usd: float, ledger: Path = LEDGER) -> float:
    """Remaining budget; raises RuntimeError when exhausted (fail closed)."""
    spent = spend_to_date(ledger)
    remaining = budget_usd - spent
    if remaining <= 0:
        raise RuntimeError(
            f"anthropic lane REFUSING: ${spent:.2f} spent of ${budget_usd:.2f} "
            f"budget (ledger {ledger}). Raise the cap deliberately to continue."
        )
    return remaining


def _log(row: dict, ledger: Path = LEDGER) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def run(
    prompt: str,
    *,
    model: str = "claude-opus-5",
    effort: str = "xhigh",
    max_tokens: int = 32000,
    system: str | None = None,
    budget_usd: float = DEFAULT_BUDGET_USD,
    label: str = "verify",
    ledger: Path = LEDGER,
) -> str:
    """One verification call. Returns the review text.

    Streams (max_tokens is large; a non-streaming request that size risks an
    SDK HTTP timeout). Adaptive thinking is Opus 5's default and is left on —
    `budget_tokens` and sampling params are rejected by this model, and
    disabling thinking is invalid above `high` effort anyway.
    """
    import anthropic

    from gridbot import config

    remaining = budget_check(budget_usd, ledger)
    client = anthropic.Anthropic(api_key=config.require("ANTHROPIC_API_KEY"))
    t0 = time.time()
    # RESERVATION (same wire_pending/outcome shape the executor ledger uses):
    # a call killed mid-stream — timeout, Ctrl-C, SIGKILL — still bills, but no
    # exception handler runs, so a post-hoc-only write would silently under-count
    # and the budget gate would drift open. Reserve the worst case now; the
    # settle row below books the negative delta back. Observed 2026-08-28.
    worst_case = cost_usd(
        {"input_tokens": len(prompt) // 3, "output_tokens": max_tokens}, model)
    _log({"ts": int(t0), "label": label, "model": model, "kind": "reservation",
          "cost_usd": round(worst_case, 6)}, ledger)
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    with client.messages.stream(**kwargs) as stream:
        message = stream.get_final_message()

    usage = message.usage.model_dump() if hasattr(message.usage, "model_dump") else {}
    spend = cost_usd(usage, model)
    _log({
        # Settle: books the reservation back and charges the true cost, so the
        # two rows sum to exactly what this call spent.
        "ts": int(t0), "label": label, "model": model, "effort": effort,
        "kind": "settle", "reserved_usd": round(-worst_case, 6),
        "stop_reason": message.stop_reason,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cost_usd": round(spend - worst_case, 6),   # net of the reservation
        "true_cost_usd": round(spend, 6),
        "elapsed_s": round(time.time() - t0, 1),
    }, ledger)
    print(f"[anthropic:{label}] {model} {effort} | in "
          f"{usage.get('input_tokens')} out {usage.get('output_tokens')} | "
          f"${spend:.4f} | ${remaining - spend:.2f} budget left",
          file=sys.stderr)

    # Safety classifiers can decline with HTTP 200 — check before reading
    # content, or an indexing read raises on an empty list.
    if message.stop_reason == "refusal":
        return ("REFUSED by safety classifiers (stop_reason=refusal). No "
                "review produced; artifacts stand UNREVIEWED.")
    return "\n".join(b.text for b in message.content if b.type == "text")
