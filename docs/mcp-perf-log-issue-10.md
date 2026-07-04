# neo-localmcp MCP Performance / Benchmarking Log — Issue #10

Every neo-localmcp CLI command exercised during the issue-10 code-quality audit,
with wall-clock latency, estimated token I/O (chars ÷ 4, plus any self-reported
figure), Ollama model + load/eval/total timings where shown, and determinism
observations. This log is a first-class deliverable — its purpose is to directly
inform how to build **repeatable** benchmarking (issue #9). See the
"Benchmarking recommendations" section at the end for the concrete payoff.

Environment: Windows 11, host Python 3.14, `neo-localmcp` installed under
`~/.neo-localmcp`. Ollama fast model `qwen3:8b`, summary model `qwen3-coder:30b`
(per config). Repo under audit is this checkout, on branch
`meta/code-quality-audit-v2-wt` inside a git worktree.

All token figures are ESTIMATES (char-derived; no client usage telemetry — a
known PROJECT_STATUS limitation, stated for honesty).

---

## Per-command datapoints

| # | Command | Wall (s) | Est. tok out | Ollama model | load/eval/total (s) | Notes |
|---|---------|----------|--------------|--------------|----------------------|-------|
| 1 | `context` (deterministic) | 2.5 | ~900 | — | — | worktree-copy pollution; real file ranked #6; mojibake |
| 2 | `ollama status` | ~1 | ~400 | qwen3:8b | — | state=model_cold (installed, not loaded) |
| 3 | `context --ollama-rank` (cold) | 21.5 | ~13,300 | qwen3:8b | cold load incl. | first call, model cold |
| 4 | `context --ollama-rank` (warm) | ~14.5 | ~13,300 | qwen3:8b | total 14.5 / eval 14.1 / 1507 tok | reranker corrected worktree pollution |
| 5 | `lookup` (symbol) | 0.30 | ~500 | — | — | precise, no worktree pollution; end_line loose |
| 6 | `file` (around-line) | 0.41 | ~600 | — | — | correct excerpt window (52–71 for line 61) |

(Detailed entries below; table filled incrementally.)

---

### Entry 1 — `context` deterministic
- **Invocation:** `neo-localmcp context "audit tools.py context_prepare ranking and excerpt centering complexity: context_prepare, _add_candidate, _best_heading_section" --repo-root . --token-budget 1000 --format text`
- **Wall clock:** 2.508 s (`user`+`sys` ≈ 0.03 s — nearly all time is the child
  `neo-localmcp-server`/index work, not the shell).
- **Est. tokens out:** ~900 (≈3,600 chars of text output).
- **Ollama:** off (deterministic path).
- **Observations:** (a) `.claude/worktrees/agent-*` copies of the repo are indexed
  and dominate ranking — the real working-tree `tools.py` ranked #6 (score 152)
  behind 5 worktree duplicates (scores 505–568). (b) em-dash mojibake (issue #26)
  reproduced.

### Entry 2 — `ollama status`
- **Invocation:** `neo-localmcp ollama status`
- **Wall clock:** ~1 s. **Out:** ~400 tokens (JSON incl. full `installed_models`).
- **Result:** `state: model_cold`, `qwen3:8b` installed but not loaded, Ollama
  v0.30.6 reachable at `127.0.0.1:11434`.

### Entry 3 & 4 — `context --ollama-rank` (FAST path, `qwen3:8b`)
- **Invocation:** `neo-localmcp context "review context_prepare for KISS/SOLID complexity: context_prepare" --repo-root . --token-budget 800 --ollama-rank --format json`
- **Cold call:** 21.5 s wall (includes model load from cold).
- **Warm call (self-reported timing):** `total_duration` 14.508 s, `eval_duration`
  14.147 s, `eval_count` 1507 tokens, `elapsed` 14.514 s, `timeout` 60 s,
  `near_timeout` false, `timed_out` false. So load ≈ 0.36 s once warm; nearly all
  wall time is generation of a 1507-token, ~3,200-char advisory.
- **Est. tokens out:** the full JSON was ~53 KB ≈ 13,300 tokens (the advisory text
  itself ~3,200 chars ≈ 800 tokens; the rest is deterministic search diagnostics).
- **Determinism:** the Ollama advisory is NOT deterministic (free-text
  generation); the deterministic core underneath it is (verified separately via
  `test-determinism`).
- **Junior-dev signal quality:** correctly smelled the SRP/KISS problem in
  `context_prepare` (corroborated my finding 1.1), but hallucinated non-existent
  "audit logging" and gave a vague `server.py:105` cite. Right on the smell, wrong
  on specifics.
- **Notable win:** with `--ollama-rank`, the reranker named the canonical
  `neo_localmcp/tools.py` as #1, effectively correcting the deterministic
  worktree-copy pollution seen in Entry 1. The advisory prompt's "preserve
  deterministic top candidates unless clear reason" policy did useful work here.

### Entry 5 — `lookup` (symbol)
- **Invocation:** `neo-localmcp lookup "index_repo" --repo-root .`
- **Wall:** 0.30 s. **Out:** ~500 tokens. **Ollama:** n/a (pure FTS + symbol table).
- **Result:** 4 symbol hits + 2 file hits, each with file_path + start/end line.
  Fastest, highest-signal command; did NOT suffer worktree pollution (returned the
  canonical `neo_localmcp/repo_memory.py` symbol first).
- **Quality note:** reported `end_line` is a loose upper bound (regex extraction) —
  `index_repo` reported 258–338 but actually ends at 310. Known limitation, not new.

### Entry 6 — `file` (around-line excerpt)
- **Invocation:** `neo-localmcp file neo_localmcp/query.py --repo-root . --around-line 61 --context-lines 20`
- **Wall:** 0.41 s. **Out:** ~600 tokens. **Ollama:** n/a.
- **Result:** correct centered excerpt (lines 52–71 for a requested center of 61),
  10 symbols, `fresh: true`. Behaves as designed.
