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
| 7 | `test-determinism` (5 runs) | 7.6 | ~2,000 | — | — | ok=true, 1 unique hash, 0 mismatches |
| 8 | `summarize --heading` (SLOW, cold) | 28.0 | ~800 | qwen3-coder:30b | total 15.1 / eval 1.35 / 81 tok | ~13.7s is 30B model load; summary accurate |
| 9 | `summarize --heading` (cache hit) | 0.57 | ~600 | qwen3-coder:30b (cached) | — | content-hash cache: 0.57s vs 28s |
| 10 | `summarize --heading` (SLOW, warm) | 4.0 | ~700 | qwen3-coder:30b | total 3.1 / eval 1.16 / 72 tok | warm model: no reload, fast eval |

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

### Entry 7 — `test-determinism` (5 runs)
- **Invocation:** `neo-localmcp test-determinism "debug repository indexing: index_repo, refresh" --repo-root . --runs 5`
- **Wall:** 7.63 s (~1.5 s/run). **Out:** ~2,000 tokens.
- **Result:** `ok: true`, 5 runs, **1 unique hash**, zero mismatches. Deterministic
  core confirmed stable across repeated runs (Ollama intentionally off for this test).

### Entries 8–10 — `summarize --heading` (SLOW path, `qwen3-coder:30b`) — never exercised in prior runs
- **Invocation (cold):** `neo-localmcp summarize docs/ARCHITECTURE.md --repo-root . --heading "Safety model"`
- **Cold (Entry 8):** 28.0 s wall. Self-reported: `total_duration` 15.08 s,
  `eval_duration` 1.35 s, `eval_count` 81 tokens, `timeout` 200 s, `truncated`
  false. **Key finding:** eval is only 1.35 s — the ~13.7 s gap between wall (28 s)
  and eval (1.35 s) is the one-time 30B-model load into VRAM (plus a few seconds of
  Ollama HTTP/CLI overhead). The 30B summary model's cost is **load-dominated**,
  not generation-dominated, for a bounded section summary.
- **Cache hit (Entry 9):** re-running the SAME heading returned in **0.57 s** with
  `cached: true`, `model: qwen3-coder:30b`. The content-hash section-summary cache
  works exactly as designed — a ~49x speedup over regeneration.
- **Warm, new section (Entry 10):** summarizing a *different* heading while the
  model stayed loaded took **4.0 s** wall (`total_duration` 3.13 s, `eval` 1.16 s,
  72 tokens). Confirms: once loaded (`keep_alive: 30m`), per-summary cost is a few
  seconds; the expensive event is the first cold load only.
- **Output quality:** the summary was factually accurate against the actual section
  text (verified by reading `docs/ARCHITECTURE.md`'s Safety model section directly)
  and correctly shaped as `summary:` + `keywords:` (8 keywords, within cap). No
  truncation, no runaway. On this run the SLOW path behaved well.
- **Determinism:** Ollama summary text is non-deterministic (generation); the cache
  makes a *repeat* deterministic only because it returns the stored copy.

---

## Summary of observed performance

| Path | Cold | Warm | Cached | Determinism |
|---|---|---|---|---|
| `context` deterministic | 2.5 s | ~1.5 s (per determinism run) | n/a | stable (1 hash / 5 runs) |
| `lookup` / `file` | 0.3–0.4 s | same | n/a | stable |
| `context --ollama-rank` (qwen3:8b) | 21.5 s | 14.5 s | n/a | advisory non-deterministic; core stable |
| `summarize --heading` (qwen3-coder:30b) | 28 s | 4 s | 0.57 s | text non-deterministic; cache makes repeat stable |

**Headline numbers for issue #9:**
- Deterministic retrieval is sub-3-seconds cold, ~1.5 s warm — cheap enough to run
  on every task.
- The FAST Ollama path (qwen3:8b) is **generation-bound** (~14 s eval for a
  1500-token advisory); load is negligible once warm.
- The SLOW Ollama path (qwen3-coder:30b) is **load-bound** (~14 s one-time VRAM load);
  per-summary eval is only ~1.3 s, and the content-hash cache turns a repeat into
  0.57 s.
- Ollama's deterministic-fallback guarantee held throughout: no Ollama call ever
  blocked or emptied a deterministic response.

## Benchmarking recommendations (the payoff for issue #9)

To make runs **repeatable and comparable**, a benchmark harness should standardize:

1. **A fixed corpus + fixed queries.** Pin a specific commit of a target repo and a
   frozen list of ~15–20 representative task strings across all intents
   (debug/feature/refactor/test/explain/context) and both hybrid (`task: Sym1, Sym2`)
   and pure-natural forms. Commit them to the repo (e.g. `benchmark/queries.jsonl`)
   so every run uses the identical inputs. Include at least one query that exercises
   each already-filed retrieval bug (#22 string-literal lookup, #23 identifier
   weighting, #24 overloaded term) so regressions/fixes are measurable.
2. **A clean-index precondition step.** Every benchmark run must start from a known
   index state — `reset-repo` then `index` on the pinned commit — because stale rows,
   branch changes, and (per this audit) sibling worktree copies materially change
   ranking. **Explicitly control `exclude_dirs`** for the benchmark repo so
   environment noise (venvs, `.claude/worktrees`) can't skew results run-to-run.
3. **Separate the deterministic and Ollama metrics.** Never mix them in one number:
   - *Deterministic:* wall latency, `estimated_tokens_returned`, `candidate_files`,
     `repository_searches`, and a **ranking-quality** metric — e.g. reciprocal rank of
     a hand-labeled gold file per query (MRR), or precision@5 of `read_first`. This is
     the metric that actually answers "is retrieval good," which raw token counts do not.
   - *Ollama:* record `load_duration`, `eval_duration`, `eval_count`, `elapsed`, and
     `timed_out`/`near_timeout` separately for fast vs summary models, always noting
     cold-vs-warm (they differ by 10–15 s). Warm the model once before timing the
     fast path, and time the summary path both cold and cache-hit.
4. **A token-reduction A/B, honestly bounded by task shape.** For the ">=30% fewer
   total tokens" acceptance target, compare *for a narrow edit task* (where MCP wins):
   (with-MCP context bundle tokens + the files actually opened afterward) vs
   (grep + whole-file reads to reach the same answer). Do NOT benchmark this on
   audit-shaped tasks — this audit found the total-token win structurally collapses
   when the task requires reading whole files regardless (see dogfooding notes). Pick
   task shapes deliberately and label them.
5. **Determinism as a gate, not a metric.** Run `test-determinism --runs 5` on every
   benchmarked query and fail the run if any query is non-deterministic — determinism
   is a correctness invariant, so it belongs as a pass/fail gate around the perf
   numbers, not as a score.
6. **Real token telemetry eventually.** All numbers here are char÷4 estimates (a
   known limitation). If the harness can capture client-reported usage, prefer it;
   until then, standardize the char÷4 convention and its exact definition (raw output
   chars) so estimates are at least comparable across runs.
7. **Report cold and warm as distinct rows.** The single most misleading thing in
   ad-hoc timing is conflating a cold model load with steady-state. Every Ollama row
   in a benchmark table should be explicitly cold or warm.
