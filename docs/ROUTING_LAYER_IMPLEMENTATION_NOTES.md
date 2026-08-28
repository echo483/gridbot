# ROUTING LAYER IMPLEMENTATION NOTES

## Files created
- `/home/ops/gridbot/docs/ROUTING_LAYER_ANALYSIS.md` — full analysis artifact (Sections 1-6)
- `/home/ops/gridbot/scripts/eval_harness.py` — empirical model selection harness (stub)
- `/home/ops/gridbot/scripts/preamble_lint.py` — preamble linter (working)
- `/home/ops/gridbot/tasks.jsonl` — task corpus for harness (10 real tasks from backlog)

## Commands run + real output

### Preamble linter (final)
```bash
$ uv run python scripts/preamble_lint.py --all
Linting: agents/draft.md
  ✓ Passed

Linting: agents/review.md
  ✓ Passed

Linting: agents/verify.md
  ✓ Passed

Linting: agents/review2.md
  ✓ Passed

Linting: agents/programming2.md
  ✓ Passed

Linting: agents/analysis.md
  ✓ Passed

Linting: agents/programming.md
  ✓ Passed

✅ All preambles passed
Exit code: 0
```
**Status**: Linter works. Checks:
- Token count < 2000 (all passed)
- File references exist (skips model names like `z-ai/glm-5.3` and absolute paths)
- No obvious contradictions (none found)

### Evaluation harness (programming kind, stub)
```bash
$ uv run python scripts/eval_harness.py --task-kind programming
============================================================
Evaluating kind: programming
Tasks: 1 train, 2 holdout
============================================================

Task: Fix drift-test false positive in venue-truth reconciliation...
  glm-5.3-flash                  ✓ score=1000.000 cost=$0.0010 latency=0.00s
  deepseek-v4-flash              ✓ score=1000.000 cost=$0.0010 latency=0.00s
  qwen3-max-thinking             ✓ score=1000.000 cost=$0.0010 latency=0.00s

============================================================
TRAIN RESULTS (median utility ± IQR)
============================================================
glm-5.3-flash                  median=1000.000 ±0.000 (n=1)
deepseek-v4-flash              median=1000.000 ±0.000 (n=1)
qwen3-max-thinking             median=1000.000 ±0.000 (n=1)

Recommended default for 'programming': qwen3-max-thinking

============================================================
HOLDOUT VALIDATION
============================================================
Task: Add F1 leak filter to capture pipeline (drop rows where roun...
  glm-5.3-flash                  ✓ score=1000.000
  deepseek-v4-flash              ✓ score=1000.000
  qwen3-max-thinking             ✓ score=1000.000

Task: Add mechanical verification to dispatcher (file existence, s...
  glm-5.3-flash                  ✓ score=1000.000
  deepseek-v4-flash              ✓ score=1000.000
  qwen3-max-thinking             ✓ score=1000.000

Exit code: 0
```
**Status**: Harness skeleton works. **Not yet wired** to real dispatch/verification. Stub returns dummy scores (always correct, $0.001 cost, 0s latency).

## Next steps (unverified / pending)
1. **Wire eval_harness.py to real dispatch**:
   - Implement `run_task()` to call OpenRouter/direct API
   - Capture real cost (OpenRouter billing API or token counters)
   - Capture real latency (wall-clock time)
   - Apply mechanical verification (Section 3 checks) for correctness
2. **Populate ground truth in tasks.jsonl**:
   - Replace placeholder `ground_truth` values with actual verified answers/hashes
   - Add ≥10 tasks per kind for statistical significance
3. **Add risk-tier and cost-ceiling axes to dispatcher**:
   - Task metadata: `risk_tier` (high/medium/low), `cost_ceiling` ($)
   - Logic: reject tasks exceeding ceiling; route high-risk to redundant lanes (IV pattern)
4. **Integrate mechanical verification into dispatcher**:
   - After agent returns, run file existence, syntax validity, test pass, schema match, no-placeholder checks
   - Mark task failed if any check fails, regardless of agent's claim
5. **Run harness quarterly** to re-evaluate defaults. Track per-task-kind rankings over time.

## Summary
- **Analysis artifact complete**: all 6 questions answered with concrete, actionable patterns.
- **Two tools delivered**: eval harness (stub) + preamble linter (working).
- **Evidence-based**: recommendations grounded in your findings (repo access > model tier, shared blind spots, unreliable self-reports).
- **One-person scale optimized**: only axes/patterns that pay for themselves are recommended.
