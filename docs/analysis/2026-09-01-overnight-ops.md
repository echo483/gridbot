# Overnight ops handoff — 2026-09-01 (goodnight loop)

Session: Claude ops + hermes bot-team lanes, ~23:00 PT 08-31 → ~04:50 PT
09-01. 15 commits. Suite 740 green at close. Nothing armed; dry_run
untouched; capture active throughout.

## Landed (chronological, all reviewed before commit)

1. dispatch.py bot-mode: the desktop-app profile team (builder/reasoner/
   reviewer/critical) routes through the one dispatch table; SOUL snapshots
   in agents/profiles/; opus-critical pinned sparing.
2. Five-agent review of b5dd78b..47d6446: intents.py docstring rewritten
   (gates + retry semantics); SLATE inflation disclosed in the queue16 docs
   (47.5% of population / 54.9% of crossers); roundwin2 scope addendum
   (commit-msg "1088" bogus; REENTRY1/second-side/capacity/daily-loss not
   replayed); watchdog + engine behavioral findings reported.
3. H-PCAL1 instrument (deepseek text lane): fit REFUSED by its anchor guard
   on real data — **the 21pt armed-band overconfidence is a QUALIFYING-
   subset property (+0.196, n=399 in-band, 87 series) vs +0.049 broad**
   (docs/analysis/2026-08-31-pcal-population-finding.md). Row-2 amendment
   draft is stale; fit-population decision is the user's.
4. Amendment drafts (qwen reasoner): H-CROSSIN1 / H-PCAL1 / H-Q15FETCHPAUSE1
   registry rows + mechanism memo + method audit F1–F14; ops addendum flags
   Row 2 stale.
5. Pre-existing dirty state: style follow-up, analysis-agent daily notes
   (three truncated mid-note — slow-loop defect worth a look), managed
   08-31 report, learnings, gitignore hygiene (scheduled_tasks.lock
   untracked, scripts/_tmp_* ignored).
6. Keyring watchdog drill flags (--collection-label/--state-file) + LIVE
   DRILL: P0 PASS (real D-Bus parse verified), **P1 ABORT — the gcr
   prompter is DEAD on the box right now** (same posture 08-26 left).
   Drill P2–P8 wait on a fresh graphical login; until then the only unlock
   is `gnome-keyring-daemon --replace --unlock`.
7. H-CROSSIN1 instrument (glm builder): **the +16.2pt mid-priced
   fallen-favorite edge is NEGATIVE at executable ask with persistence**
   (−3.1pt CI [−12.4, +6.1], n=108, 78 series); below-band control −10.9pt.
   F1 terminal cutoff wired + verified empirically empty. Rail 2 verdict:
   the best-looking mispricing on disk was a pricing artifact.
8. Watchdog alert-path fixes (builder lane): locked+failed-send exits 3 and
   stays loud; recovered ping retries; state shape-guard fixed; summary
   suppression targeted. +11 tests.
9. Executor hardening: us_band_gated fails closed on garbage p_map
   (reproduced OverflowError would have crashed the engine cycle);
   roundwin2 book_history mirrors the engine's slug fallback. Replay 08-27
   byte-stable (677/211 skips), roundwin2 placed=44 unchanged.
10. H-POSTSHOCKFADE1 design brief (qwen reasoner): fully pinned instrument
    + frozen-bar draft + honestly adverse prior — full-corpus DOWN
    convergence is 25.2% @ 67s median (not 08-27's 44% @ 29s) and the
    staleness gate selects the non-converging tail.

## Parked / open (morning queue, in priority order)

1. **postshock_fade.py is WIP-UNVERIFIED** (banner in the file): builder hit
   its budget — no tests, anchors mismatch the reasoner's verified funnel
   (59/37 vs 70/32 entries), and BOTH arms print large positive edges
   (candidate +0.221 CI [0.080, 0.350]; control +0.158 — clause-(iv)
   violation). Ops checked: entries are NOT resolution-proximate (median
   ~1,800s to terminal). Either a gate bug or a real both-directions
   book-lag effect after ≥25pt shocks. FIX-DISPATCH first: reconcile
   anchors vs the reasoner's pipeline, add the spec'd tests, then read.
2. Keyring drill P2–P8 after the next graphical login
   (`uv run python scripts/_tmp_keyring_drill.py`, driver survives resume).
3. User decisions pending: H-PCAL1 fit population (qualifying-only vs
   discount-conditional); sign-off on the three amendment drafts (Row 2
   NOT as drafted); H-CROSSIN1 registration is now questionable given the
   design-mode collapse — register-to-kill on holdout, or drop.
4. BOOKPIN1 holdout: 40/40 series accrued, 0 played at 10:40Z — instances
   accrue during today's matches; run mapentry_verdict --holdout,
   crossin --holdout, pcal accrual counts tonight.
5. Footgun: scripts/roundwin2_poolb.py REGENERATES its committed analysis
   doc on every run (clobbered the review addendum once tonight; reverted).
   Improvement: stamped output path or --write-doc flag.
6. Analysis-agent daily notes truncate mid-sentence (three examples
   committed as captured) — slow-loop writer defect.
