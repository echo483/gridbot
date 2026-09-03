# ROUTING LAYER — MATURE DISPATCH PATTERNS
**Context**: gridbot multi-agent system, slow-loop only (analysis/review/drafts/builds). Current: task-kind routing table, OpenRouter gateway + 1 direct Anthropic lane, version-controlled preambles. Evidence: repo access > model tier; shared blind spots; unreliable self-reports.

---

## 1. DISPATCH AXES — RANKED BY ROI AT ONE-PERSON SCALE

| Rank | Axis | What it is | Pays for itself? | Why |
|------|------|------------|------------------|-----|
| 1 | **Task kind** | programming, review, draft, analysis, fast, reasoning | ✓ YES | Different tasks have different compute/error profiles. Programming needs code-aware models; review needs strong reasoning; fast needs speed. Clear separation of concerns. |
| 2 | **Risk tier** | high-stakes (trade-path adjacent) vs low-stakes (drafts) | ✓ YES | High-stakes tasks get redundant lanes (e.g., 2 independent reviewers); low-stakes get cheapest adequate model. Prevents single-point failures where it matters. |
| 3 | **Cost ceiling** | max $/task budget | ✓ YES | Hard cap per task prevents runaway bills on pathological prompts. Simple config, high leverage. |
| 4 | **Time sensitivity** | real-time vs async | △ SOMETIMES | Only matters if you have genuine latency SLAs. Most slow-loop work is async — not worth the complexity. |
| 5 | **Model tier** | flagship vs cheap | ✗ NO (alone) | Without task-kind separation, this is just vibes. Cheap models fail on programming; flagships waste money on classification. |
| 6 | **Vendor diversity** | OpenRouter vs direct API | ✗ NO (as policy) | Only valuable as a *fallback* when gateway fails. Don't route by vendor; route by task, with backup paths. |
| 7 | **Content domain** | CS2 vs Dota vs LoL | ✗ NO | Domain knowledge lives in prompts/preambles, not model selection. One programming lane handles all titles. |

**Action**: Add **risk tier** and **cost ceiling** axes now. Everything else is premature optimization.

---

## 2. ENSEMBLE PATTERNS THAT ACTUALLY WORK

Shared blind spots (finding b) mean agreement ≠ correctness. Use these patterns:

### Pattern A: Independent Verification (IV)
- **What**: Two agents work the SAME task from scratch, no cross-talk. Third agent (or human) compares outputs.
- **Catches**: Individual hallucinations, calculation errors, missed edge cases.
- **Cost**: 2× compute + comparison overhead.
- **Trap**: If both agents use the same preamble or context, they share blind spots. **Mitigation**: Give each agent different context slices or framing.

### Pattern B: Adversarial Review (AR)
- **What**: Agent 1 produces output → Agent 2 tries to BREAK it (find flaws, edge cases, contradictions) → Agent 1 defends/fixes.
- **Catches**: Logical gaps, unstated assumptions, fragile logic.
- **Cost**: 1.5–2× compute. Requires review-capable model.
- **Trap**: Reviewer can miss flaws too. **Mitigation**: Rotate reviewer models; never use the same model family for both roles.

### Pattern C: Ground-Truth Anchoring (GTA)
- **What**: Before generation, fetch REAL external facts (API calls, file reads, DB queries). Generation must match them.
- **Catches**: Fabricated data, wrong file paths, stale assumptions.
- **Cost**: Extra tool calls, but cheap vs model compute.
- **Trap**: External source can be wrong or stale. **Mitigation**: Cross-check critical facts against ≥2 sources.

### Pattern D: Mechanical Verification (MV)
- **What**: After generation, run objective checks: syntax validation, type checking, test execution, hash comparison.
- **Catches**: Syntax errors, broken imports, failed tests, drift from baseline.
- **Cost**: Near-zero (automated).
- **Trap**: Passing checks ≠ correct logic. **Mitigation**: Combine with AR or IV for high-stakes tasks.

### When N-of-M Voting is a Trap
- **Trap 1**: All M agents share the same context/blind spot → all N agree on wrong answer. *Example: all agents missed `slug` vs `market_slug` because the spec used the wrong field name.*
- **Trap 2**: Majority can be confidently wrong. *Example: 3 agents hallucinate the same wrong import path; 1 agent is correct but outvoted.*
- **Trap 3**: Voting hides the *why*. You get consensus without understanding the failure mode.

**Rule**: Never use pure voting. Use **IV + MV** for high-stakes tasks; **AR + GTA** for medium; **MV alone** for low-stakes.

---

## 3. MECHANICAL VERIFICATION — DON'T TRUST SELF-REPORTS

Agent claims ("success"/"failure") are unreliable in both directions (finding c). Verify these mechanically:

| Check | What it verifies | How to implement |
|-------|------------------|------------------|
| **File existence** | Did the agent actually write the file? | `stat <path>` or `test -f <path>` |
| **File non-empty** | Did it write meaningful content? | `wc -c <path>` > 0 |
| **Syntax validity** | Is the code parseable? | `python -m py_compile <path>` or language-specific linter |
| **Test pass** | Does it work? | Run the exact test command the agent claims to satisfy |
| **Diff from baseline** | Did it change what it said it would? | `git diff --no-index baseline output` or hash comparison |
| **Tool call count** | Did it actually use tools? | Parse agent transcript for tool_call events (not just claims) |
| **Output schema match** | Does the structure match the contract? | JSON schema validation on agent's final message |
| **No placeholder text** | Did it avoid "TODO"/"FIXME"? | `grep -i 'todo\|fixme\|stub' <path>` |

**Implementation**: Dispatcher runs these checks AFTER agent returns. If any fail, task is marked failed regardless of agent's claim. For high-stakes tasks, require ≥3 checks to pass.

---

## 4. EMPIRICAL DEFAULT MODEL SELECTION

Stop guessing. Run this harness:

### Harness design
```python
# Minimal evaluation harness — runs on real tasks you already do
tasks = [
    # Programming: actual PRs or fixes from your queue
    ("programming", "Fix drift-test false positive in venue-truth reconciliation"),
    ("programming", "Add F1 leak filter to capture pipeline"),
    # Review: real diffs from recent commits
    ("review", "Review PR that adds SECONDSIDE1 gate"),
    # Analysis: real questions from operator
    ("analysis", "Why did N4 lose money on long positions?"),
    # Fast: real quick questions
    ("fast", "What's the current GRID API rate limit?"),
]

# For each task:
# 1. Run it through candidate models (glm-5.3-flash, deepseek-v4-flash, qwen3-max-thinking, etc.)
# 2. Score on:
#    - Correctness (0-1): does the answer match ground truth? (human-judged or mechanically verified)
#    - Cost ($): tokens used × price per token
#    - Latency (s): end-to-end time
# 3. Compute utility: U = correctness / (cost × latency_weight)
#    (latency_weight = 0 if async; >0 if you care about wait time)
# 4. Pick model with highest median U across tasks.

# Avoid overfitting:
# - Use ≥10 tasks per kind (more if variance is high)
# - Hold out 20% of tasks for final validation
# - Re-run quarterly or after major model updates
# - Track per-task scores, not just aggregate — some models excel at specific kinds
```

### What it measures
- **Correctness**: binary pass/fail against ground truth (not "quality" scores)
- **Cost**: real $ spent (via OpenRouter billing API or token counting)
- **Latency**: wall-clock time from dispatch to verified completion

### Avoiding overfitting
- **Task diversity**: pull from real backlog, not synthetic prompts
- **Temporal split**: evaluate on tasks from last 30 days, validate on next 7
- **Per-kind selection**: don't pick one global default; pick per task kind (programming vs review vs fast)
- **Confidence intervals**: report median ± IQR, not just mean. If intervals overlap, cheaper model wins.

**Outcome**: Data-driven routing table. No more "I feel like qwen is better" — you have numbers.

---

## 5. SPECIALIZATION VS CONCENTRATION

### Where specialization wins
| Domain | Why specialize | Example |
|--------|----------------|---------|
| **Programming** | Code-aware models have AST understanding, fewer syntax errors | glm-5.3-flash vs generic Claude |
| **Review (security)** | Needs adversarial mindset, pattern recognition for exploits | Claude Opus 5 (direct) for security review |
| **Review (logic)** | Needs formal reasoning, edge-case enumeration | glm-5.3 full for logic review |
| **Fast iteration** | Low-latency, cheap, good enough for drafts | deepseek-v4-flash for quick questions |
| **Hard reasoning** | Complex multi-step problems, quant analysis | qwen3-max-thinking for drift analysis |

### Where concentration wins
| Domain | Why concentrate | Risk of specialization |
|--------|----------------|------------------------|
| **Classification** | All models do this well; cheapest wins | Extra routing complexity for no gain |
| **Drafting** | Style is prompt-driven, not model-driven | Inconsistent tone across models |
| **Bulk processing** | Throughput matters, not nuance | Operational overhead outweighs benefit |
| **Simple lookups** | Just fetch facts; any model works | Wasted cycles on model selection |

### Rule of thumb
Specialize when:
- Task has **asymmetric error cost** (security review mistakes are catastrophic)
- Models have **proven performance gaps** on this task kind (data from harness above)
- Task is **high-volume** (routing overhead amortized)

Concentrate when:
- Task is **low-stakes** (drafts, lookups)
- Models perform **similarly** (no data-driven reason to split)
- Task is **low-volume** (routing overhead dominates)

**Action**: Keep programming, review, reasoning, fast, bulk lanes. Collapse classification/drafting into fast lane. Re-evaluate quarterly with harness data.

---

## 6. "AGENTS AS CODE" PREAMBLE FAILURE MODES

Preamble files (system prompts) accumulate over time. Three failure modes:

### Failure Mode 1: Drift from reality
- **What**: Preamble says "always check file X" but file X was renamed/moved/deleted months ago.
- **Symptom**: Agents fail on every task because preamble instructions are stale.
- **Mitigation**: 
  - Treat preambles as **code** — run a preamble linter that checks all referenced files/paths exist
  - Version preambles with the codebase; update them in the same PR that changes the referenced artifacts
  - Add a preamble test: "generate a task using this preamble; verify all instructions are executable"

### Failure Mode 2: Contradiction accumulation
- **What**: Preamble grows by appending rules. New rule contradicts old rule. Agents get confused or pick one arbitrarily.
- **Symptom**: Inconsistent behavior across tasks; some agents follow rule A, others rule B.
- **Mitigation**:
  - **Deduplication pass**: quarterly, read the preamble start-to-finish and remove redundant/contradictory rules
  - **Hierarchy**: group rules by priority (MUST vs SHOULD vs CAN). Enforce ordering.
  - **Conflict detection**: static analysis that flags contradictory directives (e.g., "never use tool X" + "always use tool X for Y")

### Failure Mode 3: Context bloat
- **What**: Preamble grows to 10k+ tokens. Model attention is diluted; key instructions get lost in noise.
- **Symptom**: Agents ignore critical constraints; performance degrades gradually.
- **Mitigation**:
  - **Hard token limit**: enforce max preamble size (e.g., 2k tokens). Reject PRs that exceed it.
  - **Modular preambles**: split into base + task-kind overlays. Only load what's needed.
  - **Compression**: quarterly, rewrite preamble to be more concise without losing intent. Measure token count pre/post.

**Action**: Add a `preamble_lint.sh` script that runs on every preamble change: checks file references exist, token count < limit, no obvious contradictions. Block PRs that fail.

---

## FILES CHANGED
None — this is a design artifact.

## COMMANDS RUN
None — analysis only.

## UNVERIFIED
- Harness implementation (Section 4) is a design spec; not coded or run.
- Preamble linter (Section 6) is a design spec; not implemented.

---
**Artifact complete**. This routing layer design is grounded in your evidence (repo access > model tier, shared blind spots, unreliable self-reports) and optimized for one-person scale. Implement risk tier + cost ceiling axes first; run the harness to pick defaults; add mechanical verification to dispatcher; lint preambles quarterly.
