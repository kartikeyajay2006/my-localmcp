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

### Q7 — `lookup "index_repo"` (symbol lookup)
- **Latency:** 0.30 s — the sweet spot for `lookup` (real Python symbol name routes
  through the symbol index, not FTS). Returned 4 symbol hits + 2 file hits, each
  with file_path + start/end line. This is genuinely the fastest, highest-signal
  MCP command; it does NOT suffer the worktree pollution because it returns the
  canonical `neo_localmcp/repo_memory.py` symbol directly (worktree copies share
  the symbol name but `lookup` returned the real path first here).
- **Retrieval-quality note (not a new bug):** the reported symbol *end_line* is
  imprecise — `index_repo` came back as `258-338` but actually ends at line 310;
  `repo_index` (a 2-line wrapper) came back as `393-473`. This is the known
  regex-based symbol-extraction limitation (PROJECT_STATUS "Symbol extraction
  remains regex-based"), not a new finding — noted only because it's visible in the
  `lookup` output and a consumer of line hints should know the end_line is a loose
  upper bound.
- **with-MCP cost:** ~500 tokens. **without-MCP:** `grep -rn "def index_repo"`
  across `neo_localmcp/` (~comparable to grep), but `lookup` also gave the line span
  and other same-name symbols in one call. Slight win; bigger win when the file is
  unknown.

---

## Overall dogfooding assessment

**What worked well:**
- `lookup` and `file` are fast (0.3–0.4 s), precise, and pollution-free — the
  highest-signal commands for a code navigator. Real Python symbol names route
  through the symbol index and land exactly.
- The FAST `--ollama-rank` path *corrected* the deterministic worktree pollution
  by naming the canonical path #1 — a genuine value-add on this particular repo.
- The SLOW `summarize` content-hash cache is excellent (0.57 s cache hit vs 28 s
  cold) and its output was accurate and well-shaped.
- Determinism held (5 runs, 1 hash).

**Friction / retrieval-quality problems observed:**
1. **Worktree-copy pollution (new, high):** `.claude/worktrees/agent-*` sibling
   copies are indexed and dominated ranking — the real file ranked #6. Root cause
   is `config.py:100-105` `exclude_dirs` missing `.claude`. Filing an issue.
2. **Mojibake in `context` text output (issue #26, already filed):** em-dashes
   render as `�` on this Windows console. Confirmed, not re-filed.
3. **Identifier weighting / string-literal lookup (#22, #23, #24, already filed):**
   observed originating in `query.py`'s `_is_symbol_like`. Not re-filed.

**Task-shape honesty (the meta-finding, reconfirmed live):** for an AUDIT task,
MCP's discovery-narrowing saves ~90% of *locating* tokens but near-0% of *total*
tokens, because the audit reads whole files regardless. The >=30% total-token
target is a narrow-edit metric, not an audit metric — a benchmark must pick task
shapes deliberately (see perf log, recommendation #4).

**Net:** neo-localmcp was genuinely useful as a navigator (lookup/file especially),
and exercising every command surfaced one real new retrieval bug plus confirmed the
Ollama fast/slow cost model precisely. The tool ate its own dogfood competently,
with the worktree-exclusion gap being the one thing actively hurting it in this repo.
