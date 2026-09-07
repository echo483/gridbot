"""Pin venue constants that are declared in more than one module.

The dead-code audit (2026-09-07) found `US_TICK`/`TICK` = 0.01 declared
independently in FOUR modules, `0.90` as both `CEILING` and
`DEFAULT_ENTRY_CEILING`, and `ENTRY_MARGIN` = 0.04 in two. Every value agreed —
but nothing enforced that, and they are venue facts that must never diverge
silently: a tick mismatch between the module that PRICES an order and the module
that SCORES it produces a quiet off-by-one-tick bias in every number downstream.

Centralising them would mean adding a `config` import to five modules, two of
them in the trade path. That refactor is worth doing deliberately; this file is
the cheap half of it — it costs no production change and fails loudly the moment
the declarations drift. If they are ever centralised, delete this file.
"""

from __future__ import annotations

from gridbot.analysis import exec_population
from gridbot.executor import intents
from gridbot.instruments import fills, mapentry, mapentry_verdict


def test_us_tick_agrees_across_every_module_that_declares_it():
    ticks = {
        "instruments.fills.TICK": fills.TICK,
        "analysis.exec_population.US_TICK": exec_population.US_TICK,
        "instruments.mapentry_verdict.US_TICK": mapentry_verdict.US_TICK,
        "executor.intents.US_TICK": intents.US_TICK,
    }
    assert len(set(ticks.values())) == 1, f"US_TICK diverged: {ticks}"
    # Pinned to the venue's observed orderPriceMinTickSize, not to whatever the
    # modules happen to say — so a coordinated wrong edit still fails.
    assert set(ticks.values()) == {0.01}, ticks


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
