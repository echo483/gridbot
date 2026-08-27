# gridbot knowledge graph — design (approved: Approach A, 2026-08-27)

User-driven learning lane, co-developed. The user builds this largely
themselves with Claude as tutor/pair in a dedicated session; this spec is
the shared contract both sessions read.

## Goal and north star

The KG is gridbot's **agent context engine**: the queryable substrate that
makes a fresh agent (hermes lane, Claude session, script) competent about
this system without re-reading everything. North star: **hermes ultimately
runs the system autonomously** — orchestration already exists (kanban
daemon, crons, webhooks, lane preambles, learned skills); what agents lack
is situational knowledge. The KG closes that gap.

## Non-goals (v1)

- Never in the trade path (iron rail 1). Slow loop only.
- No LangGraph "operator brain" in v1 — hermes stays the runner; a
  supervisor graph is a possible phase-5+ once the KG is worth reading.
- Arming remains the user's own act, forever outside any automation.
- Not a general chat-memory store — facts about THIS system only.

## Architecture (three parts, one substrate)

1. **Store: Kuzu** (embedded graph DB, Cypher queries, Python API).
   Lives at `data/kg/graph.kuzu` — **gitignored**. What IS versioned:
   schema, pipelines, MCP server (`src/gridbot/kg/`).
2. **Ingest: LangGraph pipelines** (`src/gridbot/kg/pipelines/`) — the
   user's learning surface. Each pipeline is a small StateGraph:
   extract → resolve (dedupe/entity-match) → upsert → verify. Sources
   are repo artifacts (registry.jsonl, STATE.md, learnings docs,
   executor ledgers, gotchas). deepseek (dispatch `analysis`/
   `classification` lanes) may serve as a bulk-extractor node for
   unstructured text; structured sources parse deterministically.
3. **Interface: one stdio MCP server** (`src/gridbot/kg/mcp_server.py`,
   run via `uv run python -m gridbot.kg.mcp_server`). Mounted in BOTH
   runtimes: `hermes mcp add gridbot-kg --command ...` and Claude Code
   `.mcp.json`. Absorbs queue task #19.

## Decisions locked at approval

- **Write policy: pipelines write facts; agents propose.** The only MCP
  write tool is `propose_fact` → a staging node table. A pipeline
  validates and promotes proposals; nothing an agent says becomes a
  fact without passing validation. Keeps the graph clean as agent
  count grows.
- **Rebuildability contract:** `uv run python -m gridbot.kg.rebuild`
  reproduces the entire graph from repo sources at any commit. The DB
  file is disposable cache; the schema + pipelines are the artifact
  (moat #2: declarative, reproducible). CI-checkable: rebuild twice →
  identical node/edge counts.

## Ontology

Phase 1 spine (smallest real graph, from registry.jsonl):

- Nodes: `Hypothesis` (id, status, frozen_text_hash, registered_at),
  `Amendment`, `Verdict` (kind, n, date, evidence_ref).
- Edges: `AMENDS` (Amendment→Hypothesis), `DECIDES` (Verdict→Hypothesis),
  `SUPERSEDES` (Hypothesis→Hypothesis where a lane closed and reopened
  under a new mechanism claim).

Target ontology (phased in, not built upfront — YAGNI applies to each):
`Component` (engine, fills, ledger, capture…), `Gotcha` (with
`BITES` → Component), `Incident`, `QueueItem` (with `MOTIVATED_BY` →
Incident/Verdict), `TradeSession` (armed night, with per-key outcomes),
`Market`/`Series`, `RuntimeStatus` (daemons, caps, dry_run — refreshed,
not append-only), `Procedure` (learned skills, with `LEARNED_FROM` →
Incident). Every fact node carries `source_ref` (file+line or commit) —
provenance is non-negotiable; an unreferenced fact fails validation.

## MCP tools (v1 surface, read-heavy)

- `context_for(topic)` — the "what must I know before touching X" query:
  component + its gotchas + open queue items + relevant procedures.
- `hypothesis_status(id?)` — registry state, amendments, verdicts.
- `gotchas(component?)` — the bites list.
- `runtime_state()` — daemons/caps/dry_run snapshot node.
- `search(text)` — name/description match across nodes.
- `propose_fact(kind, payload, source_ref)` — staging only (see write
  policy).

## Phases (each = one user-driven session, with learning objective)

1. **Registry spine.** Kuzu schema + first LangGraph pipeline:
   registry.jsonl → Hypothesis/Amendment/Verdict + edges. Learn:
   StateGraph, nodes, typed state, conditional edges; Kuzu DDL + MERGE
   idempotency. Exit test: rebuild twice → identical counts; 3 Cypher
   queries answer real questions (open lanes? verdicts by kind? what
   amended H-MAPENTRY1?).
2. **MCP server.** FastMCP stdio server over the phase-1 graph
   (read tools only); mount in Claude Code and hermes; prove a hermes
   agent answers a registry question through it. Learn: MCP tool
   design, stdio transport.
3. **Widen ingest.** STATE.md sections, gotchas, learnings docs,
   TradeSession from ledgers; deepseek extractor node for unstructured
   text; `propose_fact` staging + promotion pipeline. Learn: entity
   resolution, LLM-in-the-loop extraction with validation.
4. **Autonomy uplift.** Lane preambles require KG consultation; kanban
   cards auto-attach `context_for` output; nightly rebuild cron.
   Measured exit: a fresh hermes agent completes a real card using KG
   context alone (no STATE.md paste).

## Testing & error handling

- Pipelines: unit tests per node function + a golden-graph test
  (fixture registry → exact node/edge counts). Ingest is fail-closed:
  a malformed source row is skipped AND reported in the pipeline
  summary — never silently, never a partial write of one fact.
- Rebuild is idempotent (MERGE semantics); interrupting it and re-running
  converges.
- MCP server: tool smoke tests against a fixture DB; read tools never
  raise into the caller (return typed "not found" instead).
- Suite stays green: `uv run pytest -q` covers `tests/test_kg_*.py`.

## Directory layout

```
src/gridbot/kg/
  schema.py          # DDL, versioned; the ontology in code
  rebuild.py         # python -m gridbot.kg.rebuild — full rebuild
  pipelines/
    registry.py      # phase 1
    state_docs.py    # phase 3
    ledger_sessions.py
  mcp_server.py      # phase 2
data/kg/graph.kuzu   # gitignored, disposable
tests/test_kg_*.py
```

## Rails

Slow loop only; never imported by executor/capture modules (enforce: no
`gridbot.kg` import outside kg/, scripts, tests). No secrets in the graph
ever (source_refs are paths, not contents of secret-bearing files — there
are none in-repo anyway; the scanner still applies). Capture continuity
outranks KG work. Dispatch lanes used for extraction obey agents/*.md
preambles. The user drives; Claude tutors — building ahead of the user in
this lane defeats its purpose.
