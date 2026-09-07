"""Pin venue constants that are declared in more than one module.

The dead-code audit (2026-09-07) found `US_TICK`/`TICK` = 0.01 declared
independently in FOUR modules, `0.90` as both `CEILING` and
`DEFAULT_ENTRY_CEILING`, and `ENTRY_MARGIN` = 0.04 in two. Every value agreed —
but nothing enforced that, and they are venue facts that must never diverge
silently: a tick mismatch between the module that PRICES an order and the module
that SCORES it produces a quiet off-by-one-tick bias in every number downstream.

`US_TICK` has since been CENTRALISED on `instruments.fills.TICK`. The original
version of this file claimed that would need "a `config` import in five modules,
two of them in the trade path"; the altitude review disproved it. `intents.py`
already imported `TICK` from `fills` five lines above its own redundant
`US_TICK = 0.01`; `exec_population.py` declared one and never used it at all;
and `mapentry_verdict.py` could import from `fills` directly, since `fills` does
not import it back. Net: zero new `config` imports, no live capture daemon
touched, four declarations reduced to one.

`CEILING`/`ENTRY_MARGIN` are still genuinely independent — `mapentry.py` and
`mapentry_verdict.py` have no existing import path between them — so those two
remain pinned here rather than centralised. If they are ever unified, delete the
matching test below.
"""

from __future__ import annotations

from gridbot.executor import intents
from gridbot.instruments import fills, mapentry, mapentry_verdict


def test_us_tick_is_centralised_on_one_declaration():
    """Every alias must be the SAME object as the canonical constant, not merely
    an equal literal — an equal-literal check would pass again the moment someone
    re-introduces a local `US_TICK = 0.01`.
    """
    assert intents.US_TICK is fills.TICK
    assert mapentry_verdict.US_TICK is fills.TICK
    # Pinned to the venue's observed orderPriceMinTickSize, so a coordinated
    # wrong edit still fails rather than agreeing with itself.
    assert fills.TICK == 0.01


def test_entry_ceiling_agrees_across_both_declarations():
    ceilings = {
        "instruments.mapentry_verdict.CEILING": mapentry_verdict.CEILING,
        "executor.intents.DEFAULT_ENTRY_CEILING": intents.DEFAULT_ENTRY_CEILING,
    }
    assert len(set(ceilings.values())) == 1, f"entry ceiling diverged: {ceilings}"
    assert set(ceilings.values()) == {0.90}, ceilings


def test_entry_margin_agrees_across_both_declarations():
    margins = {
        "instruments.mapentry.ENTRY_MARGIN": mapentry.ENTRY_MARGIN,
        "instruments.mapentry_verdict.ENTRY_MARGIN": mapentry_verdict.ENTRY_MARGIN,
    }
    assert len(set(margins.values())) == 1, f"ENTRY_MARGIN diverged: {margins}"
    assert set(margins.values()) == {0.04}, margins
