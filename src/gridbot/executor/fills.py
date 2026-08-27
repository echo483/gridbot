"""Venue fills and settlements → ledger truth rows (closes the S3 gap).

The first armed night proved fills happen with ZERO ledger evidence: a
post-only bid partially fills, the TTL cancel then succeeds on the
remainder, and the ledger shows a clean "cancelled" — while the account
holds contracts. This module turns venue order records and portfolio
activities into the ``fill`` and ``settle`` rows ledger.py already
documents, so exposure and the daily-loss cap bind on real numbers.

The venue can also go flat OUTSIDE this engine: the user sells a
filled position by hand, no ``POSITION_RESOLUTION`` ever names the
key, and it would consume an exposure slot forever (live failure
08-27). :func:`sync` appends a release settle row sourced
``manual_exit_position_closed`` — but ONLY when every proof lines up:

- the key's NEWEST fill has aged ``MANUAL_EXIT_MIN_FILL_AGE_NS`` (a
  re-entry fill resets the clock — first-fill timestamps would age an
  already-superseded entry while the re-entry races);
- no placed order of the key is still resting without terminal
  evidence — exposure is ORDER-scoped in ledger.py, so releasing the
  key would drop a live sibling bid from the reconcile view
  (code-review f4);
- a COMPLETE ``portfolio.positions()`` listing — dict shell or list
  shell accepted, ``positions`` and ``availablePositions`` both
  readable, nothing unattributable, no pagination tail — either names
  the key's market at size ZERO or cleanly enumerates every entry and
  misses it (code-review f1/f2: the only real capture ships
  ``positions`` as a mapping, and a held position may appear under
  ``availablePositions``);
- that market's per-market resolution query ran THIS PASS and came
  back EMPTY: a resolved, cap-starved, or transiently failing
  resolution stream defers the release instead of papering payable
  truth with a zero-PnL row (code-review f3).

A released key never becomes resolution-blind: it stays a settle
candidate so a late ``POSITION_RESOLUTION`` upgrades the ledger from
zero-PnL release to signed venue truth (code-review f6; the upgrade is
applied by ledger._settles preferring truth rows over releases).

Row construction is pure (testable without a client); :func:`sync` is
the one orchestrator the engine calls on its reconcile cadence. It never
raises into the engine and never writes a row it cannot defend: no
resolution truth → no settle row (fail closed).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime

log = logging.getLogger("gridbot.executor.fills")

# Venue order states after which cumQuantity can never change again.
TERMINAL_STATES = frozenset({
    "ORDER_STATE_FILLED",
    "ORDER_STATE_CANCELED",
    "ORDER_STATE_REJECTED",
    "ORDER_STATE_EXPIRED",
})

MAX_RETRIEVES_PER_PASS = 10
MAX_SLUG_FETCHES_PER_PASS = 8
ACTIVITY_PAGE_LIMIT = 50

# Settle-row source of the manual-exit release (shared with the ledger's
# truth-preference in ledger._settles — keep the literal in sync).
MANUAL_EXIT_SOURCE = "manual_exit_position_closed"

# A fill younger than this is still inside its own entry/re-entry window;
# positions truth pulled before it can be stale about it (review f5: the
# guard measures from the key's newest fill, and from DISCOVERY time —
# fill rows carry sync ts, so a late-discovered fill ages slowly, which
# only ever DELAYS a release).
MANUAL_EXIT_MIN_FILL_AGE_NS = 120_000_000_000

# How long a manual-exit release stays RESOLVABLE (its market keeps
# getting per-market resolution queries) after the release row lands.
# Markets in scope resolve within hours of game end; past this window a
# "late" resolution would mean ledger-level weirdness outside the
# engine's remit, while polling forever would accumulate released slugs
# across nights until they starved live keys under the per-pass cap.
RESOLUTION_RECHECK_NS = 24 * 3600 * 1_000_000_000

# Size spellings probed in a venue position, in priority order.
# "netPosition" is the field the REAL venue uses — verified 2026-08-27
# 13:10Z against a live HELD position (positions dict keyed by slug,
# entry {"netPosition": "5", "qtyBought": "5", ...}); the others are
# defensive spellings. Before this capture an unlisted spelling made a
# held position read UNPARSEABLE, which correctly deferred (fail-closed)
# but could never take the parsed-zero release path.
_SIZE_FIELDS = ("netPosition", "quantity", "size", "shares")


def _amount(a: object) -> float | None:
    """Float value of a venue Amount ``{"value": "0.53", "currency": "USD"}``."""
    if isinstance(a, dict):
        try:
            return float(a.get("value"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    return None


def _num(v: object) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def fill_row(intent_row: dict, order: dict, ts_ns: int) -> dict | None:
    """A ``fill`` row for a venue order with executed quantity, else None.

    Price prefers the venue's average execution price; a missing/zero
    avgPx falls back to the intent's limit price (post-only maker never
    fills worse than its limit). ``ts_ns`` is DISCOVERY time, not venue
    execution time — downstream age guards therefore run late, never
    early (conservative direction).
    """
    cum = _num(order.get("cumQuantity"))
    if cum <= 0:
        return None
    price = _amount(order.get("avgPx"))
    if price is None or price <= 0:
        price = intent_row.get("price")
    return {
        "type": "fill",
        "dedup_key": intent_row.get("dedup_key"),
        "order_id": str(order.get("id") or intent_row.get("order_id")),
        "market_slug": intent_row.get("market_slug"),
        "price": price,
        "shares": cum,
        "partial": cum < _num(order.get("quantity") or cum),
        "source": "venue_order",
        "ts_ns": ts_ns,
    }


def settle_row(intent_row: dict, resolution: dict, ts_ns: int) -> dict | None:
    """A ``settle`` row from a venue position resolution, else None.

    ``won`` derives from the realized-PnL delta of the resolution. For
    our BUY-only maker book that sign is decisive: a win realizes
    +(1-p)·s, a loss −p·s, with p strictly inside (0, 1). No delta →
    no row (fail closed; never guess a settlement).
    """
    before = resolution.get("beforePosition") or {}
    after = resolution.get("afterPosition") or {}
    rb = _amount(before.get("realized"))
    ra = _amount(after.get("realized"))
    if rb is None or ra is None:
        return None
    pnl = ra - rb
    # Venue resolution time when parseable, else sync time — a settle
    # bucketed into the wrong UTC day mis-binds the daily-loss cap
    # (qwen review f9).
    ts = _iso_ns(resolution.get("updateTime")) or ts_ns
    return {
        "type": "settle",
        "dedup_key": intent_row.get("dedup_key"),
        "market_slug": intent_row.get("market_slug"),
        "won": pnl > 0,
        # Zero delta = void/refund-at-cost, not a loss (code-review f8);
        # realized_pnl uses venue_pnl so the number stays honest either way.
        "voided": pnl == 0,
        "venue_pnl": round(pnl, 6),
        "source": "venue_resolution",
        "ts_ns": ts,
    }


def _iso_ns(s: object) -> int | None:
    """Epoch ns for an ISO-8601 string, else None."""
    if not isinstance(s, str) or not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1e9)
    except (ValueError, OverflowError):
        return None


def _order_ids(
    records: list[dict], row_type: str, status: str | None = None
) -> set[str]:
    """order_ids carried by rows of ``row_type`` (optionally status-filtered)."""
    return {
        str(row.get("order_id"))
        for row in records
        if isinstance(row, dict) and row.get("type") == row_type
        and (status is None or row.get("status") == status)
        and row.get("order_id")
    }


def _rows_of(records: list[dict], row_type: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in records:
        if isinstance(row, dict) and row.get("type") == row_type:
            key = row.get("dedup_key")
            if isinstance(key, str) and key and key not in out:
                out[key] = row
    return out


def fill_candidates(records: list[dict], checked: set[str]) -> list[dict]:
    """Placed intents whose order still needs a venue fill-state check.

    ORDER-scoped: skips order_ids that already have their own fill row or
    were confirmed terminal-with-no-fill. Key-scoped skipping made a
    re-entry sibling's fill invisible forever (qwen review f3).
    """
    filled_orders = _order_ids(records, "fill")
    out = []
    for row in records:
        if not isinstance(row, dict) or row.get("type") != "order_intent":
            continue
        if row.get("status") != "placed":
            continue
        oid = row.get("order_id")
        if not oid or str(oid) in checked or str(oid) in filled_orders:
            continue
        out.append(row)
    return out


def match_resolution(intents_by_slug: dict[str, list[dict]], resolution: dict) -> dict | None:
    """The unsettled filled intent a resolution belongs to, else None.

    Slug match only. The venue's ``side`` string is CONSTANT
    ("POSITION_RESOLUTION_SIDE_SHORT" on every 08-26 resolution
    regardless of our position side), so substring disambiguation would
    confidently attribute a LONG book's resolution to the SHORT intent
    (code-review f4). Both-sides-held on one slug is therefore always
    skipped — fail closed; netting is a queued design item.
    """
    slug = resolution.get("marketSlug")
    candidates = intents_by_slug.get(slug) or []
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        log.warning("resolution on %s spans %d held intents (both sides?) — "
                    "skipped, needs manual/netted settlement",
                    slug, len(candidates))
    return None


def fetch_resolutions(client, slug: str) -> list[dict] | None:
    """Position resolutions FOR ONE MARKET; None on error (retry later).

    Per-slug on purpose: a single newest-N global page can bury an old
    resolution behind unrelated account activity forever (code-review
    f7) — asking the venue per market makes every held position's
    settlement reachable.
    """
    try:
        resp = client.portfolio.activities({
            "limit": ACTIVITY_PAGE_LIMIT,
            "marketSlug": slug,
            "types": ["ACTIVITY_TYPE_POSITION_RESOLUTION"],
            "sortOrder": "SORT_ORDER_DESCENDING",
        })
    except Exception as exc:  # noqa: BLE001 — fills sync must never kill the engine
        log.warning("activities fetch failed for %s: %s", slug, type(exc).__name__)
        return None
    out = []
    for act in (resp or {}).get("activities") or []:
        if isinstance(act, dict) and act.get("type") == "ACTIVITY_TYPE_POSITION_RESOLUTION":
            res = act.get("positionResolution")
            if isinstance(res, dict):
                out.append(res)
    return out


def _fetch_resolutions_by_slug(
    client, slugs: set[str]
) -> dict[str, list[dict] | None]:
    """Per-market resolution queries for ONE pass, capped.

    Maps every REQUESTED slug to either its resolution list or None
    (query failed — or was never issued: beyond the per-pass cap the
    remaining slugs are simply absent, and callers treat absence as
    "no truth this pass", never as flat). Head-first under the cap is
    fine: max_open=2 bounds live slugs well below it, so beyond-cap
    pressure only arises from crash backlogs that drain a pass at a
    time.
    """
    out: dict[str, list[dict] | None] = {}
    for slug in sorted(slugs)[:MAX_SLUG_FETCHES_PER_PASS]:
        out[slug] = fetch_resolutions(client, slug)
    return out


def _pos_slug(position: object) -> str | None:
    """Market slug of a venue position under any known spelling."""
    if not isinstance(position, dict):
        return None
    for field in ("marketSlug", "market_slug", "slug"):
        val = position.get(field)
        if isinstance(val, str) and val:
            return val
    return None


def _row_sizes(position: object) -> list[float]:
    """Every parseable FINITE non-negative size across all known fields.

    Each present field contributes, so one garbage value can no longer
    hide a later valid spelling and contradictory spellings resolve
    toward HELD via the caller's max() (code-review f9 — the old
    first-parse-wins probe let ``{"quantity": "0", "size": 7}`` read as
    flat). NaN/infinite/negative readings are refused outright
    (code-review f8): ``float("nan") > 0`` is False, so NaN previously
    READ AS FLAT, and a signed size plausibly encodes a held short —
    neither may ever stand in for flatness.
    """
    if not isinstance(position, dict):
        return []
    vals: list[float] = []
    for field in _SIZE_FIELDS:
        if field not in position:
            continue
        try:
            v = float(position[field])
        except (TypeError, ValueError):
            continue
        if math.isfinite(v) and v >= 0:
            vals.append(v)
    return vals


def _container_entries(raw: object) -> list[tuple[str | None, object]] | None:
    """Normalise a venue collection to (slug-hint, entry) pairs.

    The real capture (data/executor/settle_truth-20260826.json) ships
    ``positions`` as a MAPPING ({slug: {...}}), not the list the spec
    assumed — iterating a dict yields slug STRINGS, which made the
    shipped pass a permanent no-op the moment the account held anything
    (code-review f1). Accept either shape: dict keys become slug hints
    for entries that lack a readable market identifier themselves.
    Anything else is an unusable shape → None (caller defers the pass).
    """
    if isinstance(raw, dict):
        return [(str(k) if k else None, v) for k, v in raw.items()]
    if isinstance(raw, list):
        return [(None, v) for v in raw]
    return None


def _positions_index(
    client, slugs: set[str]
) -> tuple[dict[str, list[float]], set[str], bool] | None:
    """Position truth for ``slugs`` from ONE positions fetch per pass.

    Shared across every candidate key so a multi-position night costs
    one call. Returns ``None`` on fetch error, an UNREADABLE RESPONSE
    (either collection absent or of an unknown shell type — no release
    can rest on a response we cannot even enumerate), or an INCOMPLETE
    listing (eof false / nextCursor present): on a paginated tail a
    candidate would look absent and be released while actually still
    held elsewhere in the page sequence — fail closed instead.

    Both ``positions`` and ``availablePositions`` are absorbed: nothing
    in the repo establishes which array carries a live esports position
    (the only capture was taken flat, both empty), so releasing on
    ``positions``-absence alone faked flatness against a position parked
    in the sibling array (code-review f2). An entry with no readable
    market identifier compromises the WHOLE enumeration — that junk row
    could be the held position we cannot name — and disables
    absence-based releases below.

    Otherwise ``(sizes, ambiguous, fully_attributed)``:

    - ``sizes[slug]`` lists every parseable size seen for the slug, any
      field, any row; callers resolve toward HELD with max();
    - ``ambiguous``: the slug is NAMED somewhere but carries no usable
      size evidence (missing/garbage/NaN/negative across all spelled
      fields, in either array) — callers defer that slug's release;
    - ``fully_attributed`` is False when at least one entry carried no
      readable slug (then slug-absence proves nothing).
    """
    try:
        resp = client.portfolio.positions()
    except Exception as exc:  # noqa: BLE001 — fills sync must never kill the engine
        log.warning("portfolio.positions failed: %s", type(exc).__name__)
        return None
    resp = resp or {}
    if resp.get("eof") is False or resp.get("nextCursor"):
        log.info("positions listing incomplete (pagination) — manual-exit "
                 "pass deferred")
        return None
    entries = _container_entries(resp.get("positions"))
    available = _container_entries(resp.get("availablePositions"))
    if entries is None or available is None:
        # Unknown shell / dropped collection: we cannot even enumerate
        # what the venue sent, so nothing can be proved flat (f2).
        log.warning("portfolio.positions shape unreadable (positions=%r "
                    "availablePositions=%r) — manual-exit pass deferred",
                    type(resp.get("positions")).__name__,
                    type(resp.get("availablePositions")).__name__)
        return None
    sizes: dict[str, list[float]] = {}
    ambiguous: set[str] = set()
    fully_attributed = True

    def absorb(hint: str | None, item: object) -> None:
        nonlocal fully_attributed
        if isinstance(item, dict):
            slug = _pos_slug(item) or hint
            if not slug:
                fully_attributed = False
                return
            vals = _row_sizes(item)
            if vals:
                sizes.setdefault(slug, []).extend(vals)
            else:
                # Named but unreadable bookkeeping: not flat, not held,
                # just unknown — blocks that slug's release.
                ambiguous.add(slug)
        else:
            if hint:
                ambiguous.add(hint)
            else:
                fully_attributed = False

    for hint, item in entries:
        absorb(hint, item)
    for hint, item in available:
        absorb(hint, item)
    keep = slugs
    return (
        {s: v for s, v in sizes.items() if s in keep},
        {s for s in ambiguous if s in keep},
        fully_attributed,
    )


def _placed_intents(records: list[dict]) -> dict[str, dict]:
    """Map dedup_key -> first placed intent row (first placed wins)."""
    out: dict[str, dict] = {}
    for row in records:
        if (isinstance(row, dict) and row.get("type") == "order_intent"
                and row.get("status") == "placed"
                and isinstance(row.get("dedup_key"), str)
                and row["dedup_key"] not in out):
            out[row["dedup_key"]] = row
    return out


def _resolvable_pools(
    records: list[dict], fresh_fills: list[dict], now_ns: int
) -> dict[str, dict[str, list[dict]]]:
    """Slug-partitioned settle candidates for one pass.

    "fresh": keys with a fill and NO settle row at all. "released":
    keys whose only settle is the zero-PnL manual-exit release — kept
    RESOLVABLE so a late ``POSITION_RESOLUTION`` still lands (the
    release must never permanently eat payable truth, code-review f6;
    ledger._settles prefers the truth row once it exists). Release
    freshness bounds the recheck: past ``RESOLUTION_RECHECK_NS`` the
    released slug stops being re-queried (markets in scope resolve in
    hours; unbounded polling would accumulate stale slugs across nights
    and starve live keys under the per-pass cap).

    Matching never mixes the pools: released keys join a slug's match
    targets only when it has no fresh candidates, so a stale release
    cannot manufacture two-sided ambiguity that refuses a fresh
    sibling's legitimate settle.
    """
    all_rows = records + fresh_fills
    fills_by_key = _rows_of(all_rows, "fill")
    truth_settled: set[str] = set()
    released_at: dict[str, int] = {}
    for row in records:
        if not (isinstance(row, dict) and row.get("type") == "settle"):
            continue
        key = row.get("dedup_key")
        if not (isinstance(key, str) and key):
            continue
        if row.get("source") == MANUAL_EXIT_SOURCE:
            # Anchor the recheck window to the RELEASE row's own timestamp —
            # anchoring to the first placed intent (audit F7) expired keys
            # released >24h after entry the moment they were released, so a
            # late resolution could never upgrade the zero-PnL row.
            ts = row.get("ts_ns")
            released_at[key] = ts if isinstance(ts, int) else 0
        else:
            truth_settled.add(key)
    released_keys = set(released_at)
    placed = _placed_intents(records)
    pools: dict[str, dict[str, list[dict]]] = {}
    for key in set(fills_by_key) & set(placed) - truth_settled:
        bucket = "released" if key in released_keys else "fresh"
        slug = placed[key].get("market_slug")
        if not (isinstance(slug, str) and slug):
            continue
        rel_ts = released_at.get(key, 0)
        if (bucket == "released" and rel_ts
                and now_ns - rel_ts > RESOLUTION_RECHECK_NS):
            continue  # release too old to keep re-queried — see RESOLUTION_RECHECK_NS
        pools.setdefault(slug, {"fresh": [], "released": []})[bucket].append(
            placed[key])
    return pools


def _newest_fill_ts(records: list[dict], fresh_fills: list[dict]) -> dict[str, int]:
    """dedup_key -> NEWEST fill ts_ns for the key.

    First-wins fill maps carry the OLDEST fill; the manual-exit age
    guard must age the key's latest fill or a re-entry filled a moment
    ago inherits its superseded ancestor's age and releases inside the
    race window the guard exists to prevent (code-review f5).
    """
    out: dict[str, int] = {}
    for row in records + fresh_fills:
        if not (isinstance(row, dict) and row.get("type") == "fill"):
            continue
        key = row.get("dedup_key")
        ts = row.get("ts_ns")
        if not (isinstance(key, str) and key and isinstance(ts, int)):
            continue
        prev = out.get(key)
        if prev is None or ts > prev:
            out[key] = ts
    return out


def _release_candidates(
    records: list[dict], fresh_fills: list[dict], checked: set[str], now_ns: int
) -> list[dict]:
    """Filled, never-settled, fully-aged keys that MAY be released.

    Cheap ledger-only guards run HERE, before any network load; venue
    truth (positions listing, resolution queries) is checked per slug
    afterwards in :func:`_sync_manual_exits`.

    Skips keys that already hold ANY settle row (including a prior
    manual-exit release — the release is idempotent, not repeatable).

    Also skips keys with a RESTING order: exposure is order-scoped
    (ledger.open_exposure / reconcile.open_ledger_orders release every
    order under a settled key), so releasing a key while one of its
    placed orders still lacks terminal evidence would drop a live
    sibling bid from the reconcile view (code-review f4). Terminal
    evidence = the order's own fill row, a clean-cancel (or
    venue-terminal corrective) row of its own, or confirmation in
    ``checked`` from this session's venue order polls.
    """
    placed_by_key: dict[str, list[dict]] = {}
    for row in records:
        if (isinstance(row, dict) and row.get("type") == "order_intent"
                and row.get("status") == "placed"
                and isinstance(row.get("dedup_key"), str)):
            placed_by_key.setdefault(row["dedup_key"], []).append(row)
    latest_fill_ts = _newest_fill_ts(records, fresh_fills)
    settled_keys = set(_rows_of(records, "settle"))

    terminal_oids = _order_ids(records, "fill")
    terminal_oids |= _order_ids(records, "cancel", status="cancelled")
    terminal_oids |= checked

    # Slugs whose UNSETTLED filled keys span BOTH us_sides: slug-level
    # flatness cannot prove either side flat (audit F2 — the venue may
    # net 5 YES + 5 NO to netPosition 0 while both positions are held;
    # releasing both would free capacity on live inventory and orphan
    # both PnLs). Defer every candidate on such slugs until at least
    # one side gains real terminal truth.
    sides_by_slug: dict[str, set[str]] = {}
    for key, rows in placed_by_key.items():
        if key in settled_keys or key not in latest_fill_ts:
            continue
        first = rows[0]
        slug, side = first.get("market_slug"), first.get("us_side")
        if isinstance(slug, str) and isinstance(side, str):
            sides_by_slug.setdefault(slug, set()).add(side)
    two_sided = {s for s, sides in sides_by_slug.items() if len(sides) > 1}

    out = []
    for key in sorted(placed_by_key):
        if key in settled_keys:
            continue
        ts = latest_fill_ts.get(key)
        if ts is None or now_ns - ts < MANUAL_EXIT_MIN_FILL_AGE_NS:
            continue
        if placed_by_key[key][0].get("market_slug") in two_sided:
            continue  # two-sided slug: flatness is unprovable per side
        resting = any(
            not oid or str(oid) not in terminal_oids
            for row in placed_by_key[key]
            for oid in [row.get("order_id")]
        )
        if resting:
            # Some placed order of this key is still resting (or has an
            # unidentifiable order_id): a release here would drop that
            # live order from the order-scoped exposure/reconcile view.
            continue
        out.append(placed_by_key[key][0])
    return out


def _sync_manual_exits(
    records: list[dict],
    fresh_fills: list[dict],
    release_rows: list[dict],
    fetched: dict[str, list[dict] | None],
    client,
    checked: set[str],
    now_ns: int,
) -> list[dict]:
    """Release settle rows for filled keys the venue reports FLAT.

    Live failure 08-27, STATE.md item 11c: the user sells a filled
    position on the venue UI; no ``POSITION_RESOLUTION`` ever names the
    key, so without this pass its slot leaks forever.

    A slug is released ONLY when every proof lines up this pass:

    - its per-market resolution query SUCCEEDED and returned EMPTY —
      resolution OBSERVED or rate-capped or raising (map value None)
      defers the release; the market's truth channel comes before the
      flatness shortcut (code-review f3 — absence from positions also
      describes a resolved-and-paid position, and its −$ would be lost
      forever behind the zero-PnL row);
    - the positions listing is complete and attributable, and the slug
      is either listed at a resolved-to HELD-safe zero (max parsed size
      across all rows/fields/spellings is exactly 0 — a parseable zero
      outranks ambiguous sibling rows, positive sizes outrank anything)
      or entirely absent from a fully-attributed enumeration;
    - any unreadable/NaN/negative size on the slug defers it
      (code-review f8 — ambiguity must never read as flat);
    - no candidate key still rests a placed order (order-scoped
      exposure survives the release with its reconcile anchor intact,
      code-review f4).

    ``venue_pnl`` stays None: our hand-off sells execute on the venue UI
    under order refs this ledger never issued, so summing "sell
    proceeds minus fill cost" would require GUESSING which TRADE rows
    are ours (the activities payload self-attributes via aggressor/
    passive ids, but only for order refs we recognize — a UI sale's ref
    is un-linkable from ledger state). realized_pnl contributes 0 for a
    None-pnl settle (ledger contract) — conservative for the daily-loss
    cap — and a late real resolution still upgrades the key (f6).
    """
    # Same-pass precedence: a resolution settle written moments ago in
    # THIS pass removes the key from release candidacy here. Earlier-pass
    # settles (truth or release) were already excluded by
    # _release_candidates' settled-keys gate.
    released_this_pass = {
        row.get("dedup_key")
        for row in release_rows
        if isinstance(row, dict)
        and row.get("type") == "settle"
        and row.get("source") == MANUAL_EXIT_SOURCE
    }
    candidates = [
        row for row in _release_candidates(records, fresh_fills, checked, now_ns)
        if row.get("dedup_key") not in released_this_pass
    ]
    if not candidates:
        return []
    by_slug: dict[str, list[dict]] = {}
    for intent in candidates:
        slug = str(intent["market_slug"])
        by_slug.setdefault(slug, []).append(intent)

    positions = _positions_index(client, set(by_slug))
    if positions is None:
        return []  # transient error / unreadable / incomplete — retry next pass
    sizes, ambiguous, fully_attributed = positions

    new_rows: list[dict] = []
    for slug, intents in sorted(by_slug.items()):
        fetched_list = fetched.get(slug)
        if fetched_list is None:
            # Query failed or starved under the pass cap: no resolution
            # truth for this slug this pass — the flatness shortcut
            # waits (code-review f3).
            continue
        if fetched_list:
            # The market shows resolution ACTIVITY. Matching (and, when
            # ambiguous, refusing) happens in the settle pass; the
            # release never rides on top of a live truth channel.
            continue
        if ambiguous and slug not in sizes:
            # Named on the listing but unreadable: never release (f8).
            continue
        if slug in sizes:
            if max(sizes[slug]) > 0:
                # Explicitly still held (larger of any contradictory
                # readings): never release.
                continue
            # Listed at (resolved-to-) zero → proven flat. A parseable
            # zero outranks ambiguous sibling rows; positives above
            # already overrode everything.
        elif not fully_attributed:
            # Absent from the listing while the enumeration is
            # compromised (unattributable entries present): that junk
            # entry could be THIS held position — absence proves
            # nothing, defer to a cleaner listing.
            continue
        # else absent from a fully-attributed complete listing → flat.
        for intent in intents:
            new_rows.append({
                "type": "settle",
                "dedup_key": intent.get("dedup_key"),
                "market_slug": slug,
                "won": None,
                "venue_pnl": None,
                "voided": False,
                "source": MANUAL_EXIT_SOURCE,
                "ts_ns": now_ns,
            })
            log.warning("MANUAL-EXIT release recorded: %s %s gone from "
                        "venue positions (sold outside the engine)",
                        slug, intent.get("dedup_key"))
    return new_rows


def _sync_fills(
    records: list[dict], client, checked: set[str], now_ns: int
) -> list[dict]:
    """Fill (or corrective-cancel) rows for placed orders now terminal on venue.

    Mutates ``checked`` with order_ids proven terminal so they are never
    re-fetched. At most MAX_RETRIEVES_PER_PASS order retrieves per pass.
    """
    new_rows: list[dict] = []

    # Orders that already have a venue-confirmed clean cancel of their own —
    # those need no corrective release. Everything ELSE that turns out
    # terminal-with-zero-fill gets one: gone_before_cancel, hand-cancelled,
    # venue-auto-cancelled at resolution, crash orphans (code-review f5 —
    # gating on gone rows alone leaked slots across sessions).
    clean_cancelled = _order_ids(records, "cancel", status="cancelled")

    retrieves = 0
    for intent in fill_candidates(records, checked):
        if retrieves >= MAX_RETRIEVES_PER_PASS:
            break
        oid = str(intent.get("order_id"))
        retrieves += 1
        try:
            resp = client.orders.retrieve(oid)
        except Exception as exc:  # noqa: BLE001
            log.warning("order retrieve %s failed: %s", oid, type(exc).__name__)
            continue
        order = (resp or {}).get("order") or {}
        terminal = order.get("state") in TERMINAL_STATES
        if not terminal:
            # A live order's cumQuantity can still grow; writing a fill row
            # now would freeze an understated quantity forever (ledger fills
            # are first-wins — glm review 08-26, finding 4). The TTL cancel
            # makes every order terminal within minutes; wait for that.
            if _num(order.get("cumQuantity")) > 0:
                log.info("partial fill in progress on %s (%s) — recording at "
                         "terminal state", intent.get("market_slug"), oid)
            continue
        row = fill_row(intent, order, now_ns)
        if row:
            new_rows.append(row)
            log.warning("FILL recorded from venue: %s %s x%s @%s%s",
                        intent.get("market_slug"), intent.get("us_side"),
                        row["shares"], row["price"],
                        " (partial)" if row["partial"] else "")
        elif oid not in clean_cancelled:
            # Terminal with ZERO executed and no clean-cancel row of its
            # own (gone_before_cancel, hand-cancel, venue auto-cancel,
            # crash orphan): without this corrective row the exposure slot
            # leaks forever (glm f8 + code-review f5). A clean-cancel row
            # releases it via the normal ledger semantics.
            new_rows.append({
                "type": "cancel", "order_id": oid,
                "dedup_key": intent.get("dedup_key"),
                "market_slug": intent.get("market_slug"),
                "status": "cancelled", "source": "venue_terminal_no_fill",
                "ts_ns": now_ns,
            })
            log.warning("order %s was gone_before_cancel but venue shows "
                        "terminal with no fill — exposure released", oid)
        checked.add(oid)
    return new_rows


def _write_resolution_settles(
    pools: dict[str, dict[str, list[dict]]],
    fetched: dict[str, list[dict] | None],
    now_ns: int,
) -> list[dict]:
    """Venue-resolution settle rows for one pass's resolution queries.

    Released (manual-exit) keys remain match targets — resolution truth
    upgrades them, it is never blocked by the release row (f6). A slug
    with NO queryable resolutions this pass (failed/starved: value None,
    or absent from the map) simply contributes nothing and is retried
    when its turn comes again. A resolution naming an ALREADY-released
    key suppresses any same-pass release rows for it via ``fresh``.
    """
    new_rows: list[dict] = []
    done: set[str] = set()
    for slug in sorted(fetched):
        fetched_list = fetched[slug]
        if not fetched_list:
            continue  # [] = queried-and-empty; None = failed (logged upstream)
        pool = pools.get(slug) or {"fresh": [], "released": []}
        targets = pool["fresh"] or pool["released"]
        if not targets:
            continue
        for res in fetched_list:
            intent = match_resolution({slug: targets}, res)
            if intent is None or intent.get("dedup_key") in done:
                continue
            row = settle_row(intent, res, now_ns)
            if row:
                done.add(str(intent.get("dedup_key")))
                new_rows.append(row)
                log.warning("SETTLE recorded from venue: %s won=%s pnl=%s",
                            row["market_slug"], row["won"], row["venue_pnl"])
    return new_rows


def sync(records: list[dict], client, checked: set[str], now_ns: int) -> list[dict]:
    """One fills/settles pass. Returns new ledger rows (caller appends).

    Mutates ``checked`` with order_ids proven terminal so they are never
    re-fetched. Bounded venue load: at most MAX_RETRIEVES_PER_PASS order
    retrieves, MAX_SLUG_FETCHES_PER_PASS per-market resolution queries,
    plus ONE portfolio.positions call per pass (manual-exit candidates
    are pruned by ledger-only guards before that call happens).

    Resolution truth outranks flatness everywhere: a manual-exit
    release issues only against a slug whose per-market resolution
    query succeeded AND came back empty this pass, and a released key
    keeps being resolution-checked (within RESOLUTION_RECHECK_NS of its
    release) so late venue truth replaces its zero-PnL release in the
    ledger — the upgrade lands via the falsifiable-preference hook this
    module exposes as ``resolution_upgrade_wanted``.
    """
    fill_rows = _sync_fills(records, client, checked, now_ns)
    pools = _resolvable_pools(records, fill_rows, now_ns)
    fetched = _fetch_resolutions_by_slug(client, set(pools))
    settle_rows = _write_resolution_settles(pools, fetched, now_ns)
    release_rows = _sync_manual_exits(
        records, fill_rows, settle_rows, fetched, client, checked, now_ns)
    return fill_rows + settle_rows + release_rows
