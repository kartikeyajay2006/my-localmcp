# Dogfooding Notes — Issue #10 Code Quality Audit (v2 run)

Written as-I-go while using neo-localmcp's own CLI (`context`, `lookup`, `file`,
`summarize`) as the primary navigation tool for a module-by-module code-quality
audit of the repo. Goal: log retrieval quality/friction and a with-MCP vs
without-MCP token comparison. All token numbers are **estimates** (char-derived,
no client telemetry — a known PROJECT_STATUS limitation; stated here to be honest).

Method for token estimates:
- **with-MCP** = size of the CLI JSON/text output the tool actually returned
  (chars ÷ 4), plus the `estimated_tokens` figure the `context` tool self-reports
  when present.
- **without-MCP counterfactual** = raw chars ÷ 4 of the whole file(s) I would
  otherwise have had to open (grep + Read) to answer the same question, files
  named so it's auditable.

Shape caveat recorded up front: this is an **audit** task, which by nature reads a
LOT of code (I must eventually read most modules in full to judge comment quality
and KISS/SOLID). That is a fundamentally different task-shape from a narrow edit,
where MCP shines most (it narrows discovery). Discovery-narrowing still helps here
for *locating* the right code, but the "then read the whole file anyway" step is
unavoidable for an audit, so the total-token savings will be structurally lower
than the project's >=30% target for this task shape. That shape difference is
itself a finding.

Prior-run notes (Q1–Q4) are preserved separately; this v2 run continues the log
from Q5 with a durable, committed report as the deliverable.

Ollama-treatment rule for this run (owner refinement): treat Ollama's fast-rank
and slow-summary output like **a newly-hired junior dev's code-review comment** —
a real signal worth chasing, but never deferred to. Every lead it surfaces is
verified against the actual source before it becomes a finding, and whether it was
right or wrong is itself recorded as a datapoint about the tool's usefulness.

---

## Running log

### Q5 — `context "audit tools.py context_prepare ranking and excerpt centering complexity: context_prepare, _add_candidate, _best_heading_section"` (budget 1000, text)
- **Latency:** 2.5 s wall (deterministic, no Ollama).
- **MAJOR friction — worktree pollution:** 5 of the top 6 `read_first` results were
  **duplicate `tools.py` copies from OTHER agents' git worktrees**
  (`.claude/worktrees/agent-a192e4.../neo_localmcp/tools.py`, etc.), each scoring
  505–568, while the *real* working-tree `neo_localmcp/tools.py` ranked **#6 at
  score 152** — dead last and ~4x lower. This is the same class of ranking
  pollution documented in PROJECT_NOTES 2026-07-03 (2) (the `.venv-phase14` case),
  in a new guise: `config.py`'s default `exclude_dirs` does not exclude `.claude/`
  or `.claude/worktrees/`, so every sibling agent worktree gets fully indexed and
  its identical `tools.py` copies out-rank the file I actually care about. For an
  AI-led repo that *uses git worktrees for parallel agents* (as this very audit
  does), this is a real, recurring quality-of-retrieval problem. Logged as a
  candidate finding (see report, retrieval area). Confirmed I am myself running in
  such a worktree (`.claude/worktrees/agent-aa077c0c12254326b`).
- **Mojibake reproduced:** the `—` separator rendered as `�` (`score 152 � around
  line 529`). This is issue #26, already filed — not re-filing, just confirming it
  reproduces on this console in this run.
- **with-MCP cost:** ranked text output ≈ 3,600 chars ≈ **900 tokens**.
- **without-MCP counterfactual:** to locate the ranking/centering logic I'd grep
  `def context_prepare|_add_candidate|_best_heading_section` then open tools.py
  (1054 lines ≈ 40 KB ≈ **10,000 tokens**) to find them. Discovery savings ≈ 91%
  for *locating* — but for the audit I read tools.py in full regardless, so no
  net total-token win (the task-shape caveat, live again).
- **Workaround for the rest of this run:** I read source directly with the Read
  tool (source is truth per the safety model) and use `context`/`lookup` primarily
  to exercise+benchmark the MCP and observe retrieval quality, rather than
  depending on its ranking while the worktree pollution is present.
