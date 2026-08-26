# Hermes → Claude Code coordination note — intl reference oracle (2026-08-26)

**Lane:** Hermes (this note + `scripts/intl_reference.py`, analysis-side only).
No `src/` files touched. Repo tests 261 green after the addition.

## What happened

User installed the marketplace `polymarket` skill (Hermes side). It queries
the **international** Polymarket via three public read-only APIs
(gamma-api / clob / data-api, zero auth). That is a DIFFERENT venue from
our Polymarket US stack — but read-only intl data is globally accessible,
and it turns out intl lists many of the same CS2 matches with far deeper
books.

## Verified tonight (2026-08-26)

- 11/15 of today's GRID↔PMUS-mapped series also have intl events, joined
  via the repo's own token-coverage matcher (`us_mapping._coverage`) +
  market-level `gameStartTime` proximity (event `startDate` is the
  umbrella/group start — BLAST Porto group events open days early; do NOT
  date-join on it).
- Liquidity asymmetry is large. AURORA-G2 map1: intl BBO 0.48/0.49 with
  ~24k shares within 5c vs PMUS 0.47/0.49 with ~110×103 top-of-book.
  Spirit-DENDELE, NAVI-M80, Meia Noite-ODDIK similar. Tier-2/3 intl map
  books are junk (0.05/0.95) — filter on spread, not on existence.
- Intl also carries derivatives we don't have on PMUS: round totals O/U,
  round handicaps, map handicap. Analysis-side only.

## What Hermes added (analysis lane, no trade-path contact)

`scripts/intl_reference.py` — one pass joins today's PMUS-mapped GRID
series to intl events and samples per-market prices + BBO tops
(`data/intl/reference-YYYYMMDD.jsonl`). NOT wired into any daemon or the
executor; nothing feeds the trade path (rail 1 intact). Intended use: run
it around tonight's armed session and post-session to compare our PMUS
maker entries against what the deep intl book was showing at the same
moment — an independent sanity oracle for fill quality and adverse
selection on the thin US books.

## Facts for the shared gotchas list

- The marketplace skill's endpoints hit intl ONLY; US slugs
  (`astatc-`/`aec-`) return empty there. US discovery stays on
  `gateway.polymarket.us` (`search.query` + time-boxed `events.list`).
- Intl CLOB `/book` returns **unsorted** level arrays: best bid = MAX
  price, best ask = MIN price. Don't trust `[0]`.
- Intl market slugs for the same match differ from US slugs entirely
  (`cs2-aur1-g2-2026-08-26-game1` vs `astatc-cs2-aur-g2-2026-08-26-map1`)
  — never assume a slug mapping, always join on teams+date.

## Open items for Claude's queue (Hermes will not touch src/)

1. If the intl reference proves useful tonight, the fills→settles writer
   (open item #1 in STATE.md) could also snapshot the intl BBO at fill
   time for the same market — one extra read in the settle row. Hermes
   defers entirely; flag if you want the reference file schema changed
   first.
2. Intl round-total/handicap books are a potential future calibration
   cross-check for the round model (q) — pre-register before any use.

— Hermes (glm-5.3), 2026-08-26 ~00:40 PT, pre-first-live-small-session
