# Spec — capture v2 on the homelab, and the data path into the lake

**Date:** 2026-10-28 · **Status:** DRAFT, decisions D1–D8 answered 2026-10-28 (D3 deliberately left open).
**Parent:** `docs/specs/2026-10-28-buildout-spec.md`, milestones M1 (capture + deploy)
and M2 (lake). **Baseline:** tag `baseline-2026-10-28`.

## 1. Why, in one paragraph

The corpus is the asset, and every past capture loss came from the host, never from
the code: three desktop-keyring failures, one KDE idle suspend (10.8 h), and one
power-off. Capture therefore moves to an always-on Proxmox VE host (the M720q). On the
way, the file format gains what the research side keeps needing and can't recover
afterwards: a sequence number per row, a receive timestamp per row, heartbeat rows, and
immutable closed files. With those, a gap is *measured* instead of inferred, and the lake
being built on `research/lab-v1` (the parallel session) can ingest without guessing.

## 2. Decisions (operator answers, 2026-10-28)

| # | decision | answer | why / what is left |
|---|---|---|---|
| D1 | Proxmox guest type | **Debian 13 server VM, minimal install** (2 vCPU, 4 GB, ~100 GB disk) | Low overhead, and a plain Linux box to learn on: its own kernel, chrony, systemd, journald. Backs up and restores as one unit (vzdump). An LXC is lighter but adds idmap and systemd-in-container quirks to the learning curve for no gain at this load. |
| D2 | where the corpus lives | **M720q internal SSD (256 GB) now**; the 2.5" bay next; 3.5" WD drives in the homelab for bulk later. **T7 = overflow and portability**, never the hot path | Size the VM disk to leave Proxmox headroom. The T7's USB bridge drops TRIM and a USB disconnect would be an outage. Bulk storage changes the mount, not the design. |
| D3 | how the secret reaches the service | **open, decide on PVE.** Requirement: no daemon on the read path (the keyring failure class) | Candidates: systemd `LoadCredential=` from a root-only file inside the VM (recommended; `gridbot.config` already reads `$CREDENTIALS_DIRECTORY`), or an encrypted credential (`systemd-creds encrypt`, bound to the VM's machine key). Both are plain files, read once per lookup, with no unlock step. Verify the permission check against systemd's credential directory before choosing. |
| D4 | deploy | **agreed:** checkout a tag + `uv sync --frozen`, run `.venv/bin/python` | No `uv run` at runtime: a corrupt uv cache (seen today: 6,808 zero-byte files) must not be able to stop capture from restarting. |
| D5 | process topology | **agreed: split per source** so a drop is attributable | Which venue data is worth capturing at all is § 2a. |
| D6 | file rotation | **agreed:** hourly files, `.open` → closed + sha256 | The lake ingests closed files only. |
| D7 | Polymarket international (`pm/`) | **drop from capture v2**, with one consequence to accept | It is not a venue we trade. Its one remaining consumer is the shadow round lanes (`instruments/shadow.py`, `verdict.py`: H-MANADV1, H-ECONBUY1, H-PISTOLCARRY1, the H-MODELENTRY1 round cells), which price on international **round** books. PMUS lists no round books, so those verdicts could never become a trade; dropping the stream stops new evidence for them. H-MAPENTRY1 is unaffected (its books come from PMUS). C2 must also drop the `pm/` freshness check in `health_check.py`. The v1 files stay readable as `pm.books` / `pm.catalog`. |
| D8 | external dead-man switch | **yes; the operator builds it** (deferred to tonight, a learning item) | Spec for it is in § 4 (the "alert path down" row) and § 8 (C4 is now split). |

### 2a. Which venue data is signal

Capture what a registered or plausible hypothesis can consume, nothing else:

| stream | keep? | used by |
|---|---|---|
| `grid.series_state` | **yes, essential** | the event tape, round/map results, the information lead |
| `grid.discovery` | **yes** | knowing a series was live: separates "quiet" from "capture down" |
| `pmus.books` (BBO, change-only + heartbeat) | **yes, essential** | prices, the as-of join, the touch fill model, the timing chain |
| PMUS trades / depth | **investigate** | the fill model's biggest blind spot is that a BBO poll cannot see trades or queue depth. Check what the public gateway exposes before adding a stream |
| `pm.books`, `pm.catalog` (international CLOB) | **drop** (D7) | none |
| executor ledger | already separate (`data/executor/`) | settlement truth, live fill statistics |

## 3. Record contract v2 (what every capture row carries)

```
{"v":2, "stream":"grid.series_state", "run":"<uuid4 per process start>",
 "seq":1234, "t_recv_ns":..., "t_mono_ns":..., "t_req_ns":...,   # t_req only for polls
 "host":"gridbot-vm", "data":{...verbatim payload...}}
```

- **`stream`** is `<source>.<kind>` and globally unique: `grid.series_state`,
  `grid.discovery`, `pmus.books`, `exec.ledger`. These are the names the lake already
  derives from v1 paths (`<dir>.<stem>`), so v1 and v2 rows land in the same stream.
- **`seq`** is monotonic per `(run, stream)` with no gaps. A missing `seq` is a lost row,
  a repeated one is a duplicate, and a new `run` is a restart. This is the only way to
  tell "no live matches" apart from "the writer died".
- **`t_recv_ns`** is wall clock taken when the bytes arrived, not when the row was
  written. `t_mono_ns` is `CLOCK_MONOTONIC`, used for latency spans inside one process
  (immune to clock steps). Every latency number in the buildout spec is computed from
  these two fields.
- **Heartbeat rows:** `{"stream":"<s>.hb", "seq":..., "rows_since":n, "last_seq":...,
  "clock_offset_ms":<from chrony>, "rss_mb":...}` every 10 s per writer. Uptime is then
  the fraction of 10 s slots that have a heartbeat, computed in SQL.
- **Payload is verbatim.** No parsing or interpretation at capture (unchanged rule).
- **Layout:** `raw/<stream>/dt=YYYY-MM-DD/hh=HH/<run>-<first_seq>.jsonl[.open]` + `.sha256`.
- **Durability:** line-buffered writes (as today), `fsync` every 5 s and on close. A power
  cut loses at most 5 s per stream. An `flock` on the stream directory stops two
  processes from writing the same stream. During migration both hosts could run, and
  `run` + `host` dedupes them in the lake.
- **v1 stays readable.** The T7 corpus is v1 (no `seq` or `run`). The lake reads both
  and tags rows `v=1`. v1 gaps can only be inferred from timestamps, and the reports
  say so.

## 4. Failure modes

Every row has a detector, and the detector itself is tested by a drill (§ 5). A detector
that has never fired is not known to work.

| failure | detection | mitigation |
|---|---|---|
| power loss / host reboot | external dead-man ping stops; `run` changes; heartbeat slot gap | units `WantedBy=multi-user.target`, VM `onboot=1`; fsync bounds loss to 5 s |
| process crash / OOM | `run` changes; systemd `Restart=always` count in health | restart in 15 s; `MemoryMax=` so a leak restarts one stream, not the VM |
| **silent stall** (socket open, no messages: the PM websocket's known mode) | per-stream `last t_recv` age against a per-stream bound, *only while discovery says something is live* | watchdog restarts the unit; `WatchdogSec=` + `sd_notify` from the writer loop |
| ISP / DNS outage | all streams stale at once + external ping missing | nothing to fix locally; the gap is recorded as an outage, not as quiet markets |
| GRID 401/403 (key revoked or expired) | error rows typed `auth` | alert immediately; no retry storm (backoff to 5 min) |
| GRID 429 / rate budget | error rows typed `rate` | existing `ratelimit.py` budget; heartbeat reports the budget used |
| GRID / venue schema change | lake gate: key-column null share jumps | capture is unaffected (verbatim); fix the typed layer and rebuild silver |
| secrets file missing or unsafe | `gridbot.config` fails closed → `capture-grid` refuses to start | alert carries the reason (never a value); PM/PMUS keep running (no key needed) |
| disk full | heartbeat `df` field; health alert at 80 % | hourly files + compression of closed days to zstd; the VM disk holds months (sized in § 6) |
| clock step / drift | `clock_offset_ms` in heartbeats | chrony in the guest; latency spans use `t_mono_ns` |
| dependency / uv cache corruption | deploy smoke test fails before switch-over | frozen venv per tag; runtime never resolves; roll back = previous tag |
| vzdump backup pausing I/O | heartbeat slot gap at backup time | schedule outside live-match hours; measure the stall in the soak, don't assume it |
| two writers on one stream | `flock` refuses the second | dedupe in lake by `(host, run, seq)` |
| hour/day rollover bug | gate: every closed hour has a successor file or an explicit gap | rollover covered by a unit test with an injected clock |
| alert path down | external dead-man is independent of `gridbot.notify` | — |

## 5. Migration and acceptance

1. **Provision (USER, with commands from AI):** create the VM, install Debian 13 + chrony,
   create the `gridbot` user, create the credential file (`0600 root`, read through
   `LoadCredential=`), and verify it with `python -m gridbot.config --check` inside the
   unit. This is where the review's open point gets tested: does the systemd credential
   file pass the permission check?
2. **Deploy (AI script, USER runs):** clone the tag, `uv sync --frozen`, install units,
   start one `--once` smoke per stream.
3. **Soak, 72 h unattended, with injected drills.** Each drill must show up in the
   lake's gap report, at the right time, as the right kind:
   - `kill -9` on each unit
   - VM reboot
   - pulling the network for 5 min
   - removing the GRID key line
   - a `SIGSTOP` on the PM writer (silent stall)
4. **Acceptance (numbers with n, from heartbeat rows):**
   - Heartbeat-slot uptime is ≥ 99.5 % per stream, excluding the drill windows. Uptime
     is published with its slot count.
   - Every drill is detected, and recovery time after each restart drill is ≤ 2 min.
   - `kill -9` loses ≤ 1 row per writer, and the observed `seq` gaps equal the rows lost.
   - Zero duplicate `(run, seq)`.
5. **Then:** the T7 corpus is copied (read-only) onto the VM disk as `raw/v1/` and becomes
   lake input, and the T7 becomes the nightly backup target (restic, so backups are
   versioned and verifiable).

## 6. How the data is used: raw → lake → research

This is the contract between capture (this spec) and the lake/lab work on
`research/lab-v1`. Lake, scan engine and report are that session's; their rules belong here
because capture has to produce what they need.

| layer | form | rule |
|---|---|---|
| **raw** | JSONL v1/v2, immutable closed files | the source of truth; never edited; backed up |
| **bronze** | Parquet `stream=/dt=`, one partition per closed file set | faithful copy + `_src_file`, `_sha256`; row count reconciled against parseable lines (lab already does this) |
| **silver** | typed tables: `events`, `round_results`, `books_bbo`, `capture_health` | the only place nested JSON is parsed; rebuildable from bronze in one command |
| **gold** | research sets: `scan_input`, model training rows, holdouts | built by versioned SQL + a config hash recorded with every result |

Rules that keep backtests honest:

- **Point-in-time.** A feature or quote may be used only if its `t_recv_ns` is at or
  before the decision time. GRID's own event timestamps are never used for this, because
  they record when something happened, not when we knew.
- **Bounded as-of joins.** A quote older than the staleness bound is excluded, not carried
  forward. The lab's `JoinSpec` already requires the bound.
- **Holdouts are by time *and* series.** The registry fixes the holdout boundary before
  the data exists.
- **Capture health is data.** `capture_health` (from heartbeats) is joined to every
  result, so a window where we weren't recording can't show up as a window with no edge.
- **Model training rows move into gold.** `corpus.ingest` (local JSONL today) becomes a
  gold view built from silver, so the model and the backtester read the same tape. This
  comes later and must not change the 20-feature contract or the leak filter.

Size check, to be confirmed by the T7 inventory: a ~100 GB VM disk assumes raw stays
under ~100 MB/day compressed. If the inventory says otherwise, D1's disk grows; the
design does not change.

## 7. Findings in the parallel lake/lab work

All resolved on `main` on 2026-10-28. The list, with the tests that pin each fix, is in
`docs/specs/2026-10-28-research-platform-spec.md` § 8. The two that touch capture
directly: streams are `<dir>.<stem>` (so the stream names in § 3 already match v1),
and bronze stores each line losslessly, so a new capture field reaches the lake
without a schema change.

## 8. Workstreams (dispatch plan)

| id | work | owner | depends on |
|---|---|---|---|
| C1 | `capture/writer.py` v2: envelope, `seq`, hourly `.open`→closed rotation, fsync, flock, heartbeat; injected-clock tests | AI | — |
| C2 | split the supervisor into `capture-grid` / `capture-pm` / `capture-pmus`; `sd_notify` watchdog; per-stream stall bounds | AI | C1 |
| C3 | system units (hardening: `ProtectSystem=strict`, `ReadWritePaths=`, `MemoryMax=`, `LoadCredential=`), `deploy/install.sh`, frozen-venv runner | AI | C2 |
| C4a | health v2: freshness from heartbeat rows, disk/clock fields | AI | C1 |
| C4b | external dead-man watchdog | **USER** (tonight; AI reviews) | C1 for the heartbeat it reads |
| C5 | lake adapter: v2 envelope, ingest closed files only, `capture_health` silver table (stream naming and lossless bronze are done) | AI | C1 |
| C6 | VM provisioning + credential file + chrony | **USER** (AI writes the runbook) | D1–D4 |
| C7 | soak + drills + acceptance numbers; gate thresholds | **USER** | C3, C4, C6 |
| C8 | T7 read-only inventory → copy to `raw/v1/` → backup target | **USER** | C6 |

C1–C4 can be built and fully tested on the laptop before the VM exists (hermetic tests,
`--once` smokes against the live public APIs).
