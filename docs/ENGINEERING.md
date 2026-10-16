# Engineering principles

Binding rules for this repository, plus the standards that keep it readable at
size. Read this before changing anything under `src/`, `scripts/`, or `deploy/`.

Orientation order: **this file → `docs/STATE.md` (what is proven, what is dead,
what runs) → `docs/WORKBLOCKS.md` (the live plan) → `docs/experiments/registry.jsonl`
(the frozen hypotheses) → `docs/MAP.md` (the floor plan).**

## The rules (merge-blocking)

1. **The trade path is deterministic.** Pricing comes from a frozen, calibrated
   statistical model. No network call, no model inference, and no external
   service sits between a signal and an order. Anything that needs to reason is
   a slow-loop research task, and slow-loop output is a *draft* — it never
   places or cancels an order, and it never reads a raw secret.
2. **Pre-registration discipline.** Every strategy idea becomes a frozen
   hypothesis in `docs/experiments/registry.jsonl` *before* its instrument runs:
   the claim, the population, the falsification bar. Verdicts come from held-out
   capture data. A falsified lane stays closed absent a new mechanism claim.
   Late registration is post-hoc selection.
3. **`dry_run=True` until a pre-registered PASS.** The executor never arms
   itself. Going live is a deliberate human act, re-checked every engine cycle,
   with the runbook in `docs/LIVE_FIRE.md`.
4. **Capture continuity outranks refactors.** The corpus is the asset; every
   number in `docs/analysis/` is derived from it. Never break a running
   recorder to land a change.
5. **One account per platform.** Scaling axes are titles × venues × capital.
6. **Secrets live in the OS keyring only**, service `gridbot`, written by
   `scripts/setup_secrets.py`. Never in files, code, logs, or commits — see
   `SECURITY.md`. The pre-commit scanner enforces this at the repo boundary:
   `git config core.hooksPath .githooks`.
7. **No runtime dependency on an agent framework.** Development may use
   whichever tools help, but nothing that *runs* may require an LLM platform,
   an agent CLI, or a chat transport. Notifications and alerts go through
   `gridbot.notify`, whose transport is configuration.

## Layers

The codebase is split by plane so the future Rust fire path has a clean seam.
Dependencies point inward; a lower plane never imports a higher one.

| plane | packages | rule |
|---|---|---|
| **data** | `capture/`, `corpus/` | I/O only. Talks to GRID and the venue, writes day files. No strategy, no sizing. |
| **research** | `scorecard/`, `instruments/`, `model/`, `sizing/` | measurement and estimation. Pure or file-reading; never places orders. |
| **execution** | `executor/` | order lifecycle, gates, ledger, reconciliation. The only place a wire call happens. |
| **shared** | `config`, `jsonl`, `ratelimit`, `graphql`, `redaction`, `notify` | cross-cutting primitives, no domain knowledge. |
| **evidence** | `scripts/` | frozen measurement instruments. Their outputs are cited by the registry — read them, rerun them, don't rewrite them. |
| **ops** | `deploy/`, `scripts/` operator tools | keeping the machine alive. |

**Where Rust goes.** The fire path becomes `rust/` alongside `src/`, with the
Python executor as the reference semantics. The seam is the order-intent
boundary in `executor/`: everything upstream of it is data + signal, everything
downstream is wire. Rust must reproduce the reference bit-for-bit on the same
tape before it is trusted.

## Standards

**Dependencies.** Minimal, declared in `pyproject.toml`, locked in `uv.lock`.
Every new dependency needs a reason that survives the question "what breaks if
this is not here". No framework for something a function can do.

**Comments explain WHY.** What the code does is the code's job. A comment earns
its place by recording a decision, a constraint, or a trap that is not visible
in the source: a venue's real behaviour, a wire-convention trap, why a naive
approach is wrong. Never narrate a dated incident in code — that belongs in
`docs/analysis/` with the number attached.

**Numbers carry their uncertainty.** Anything measured over captured data goes
through `gridbot.scorecard` and returns a `Score`: value, 95% interval, `n`, and
the count of independent *series*. Rows inside a series are correlated; an
interval that resamples rows will be wrong and will look convincing. If a
number cannot state its `n_series`, it is not a number.

**Tests are hermetic.** No network, no OS keyring, no live D-Bus, nothing
written outside `tmp_path`. A test that needs the world is a test that will fail
for the wrong reason. The suite must be runnable and green on a fresh clone
except where it deliberately fails loudly to signal absent real data.

**Docs have an owner and a lifecycle.**
- `docs/analysis/` is immutable evidence. Superseded numbers get a new dated
  document that cites the old one; the old one is never edited.
- `docs/STATE.md` accumulates dated addenda. It is the narrative, not a scratchpad.
- `docs/WORKBLOCKS.md` is the only live plan.
- `README.md` carries the status line and the verified-baseline block. If you
  run the commands and get different numbers, fixing it is part of the change.

**Naming.** Venue-side and our-side prices are different quantities. `side_ask`
is our cost; `venue_px` is the raw venue number. Keep that distinction in every
name; the two have been conflated before and it cost an armed night.

## A note on process

Some of this repository was built and reviewed by parallel automated lanes
running against a pre-registration workflow, which is why the evidence docs are
densely dated and why several mechanisms carry a recorded falsification. That is
a **process** fact, not a runtime one: none of it is required to build, test, or
run the system, and no part of the trade path depends on it. The discipline it
produced is the valuable part — frozen bars, held-out verdicts, and clustered
intervals — and it is preserved here as engineering practice.
