# neo-localmcp Code Quality Audit — Issue #10

A dedicated code-**quality** pass (not correctness — that was audited separately
and passed). Scrutinizes KISS / SOLID / DRY, readability, comment quality against
CLAUDE.md's own standard ("default to no comments, only for non-obvious WHY"),
module-boundary cleanliness, and docs-vs-code drift. Findings are concrete, with
`file:line` references, severity, the problem, and a suggested direction. Depth
over breadth on core source, in the owner-chosen priority order.

**Method:** every finding rests on a direct deterministic read of the source
(`Read`), not on any model-generated summary. neo-localmcp's own CLI was used as
the primary navigator (see `docs/dogfooding-notes-issue-10.md`) and Ollama
fast/slow output was treated as a junior-dev signal to chase and then verify
(see per-module "Ollama leads" notes and `docs/mcp-perf-log-issue-10.md`).

**Severity key:** `high` = actively harms maintainability/correctness-adjacent
clarity or is a real doc-drift a reader would be misled by; `med` = worth fixing,
adds friction or risk; `low` = polish, note-and-move-on.

**Scope note:** analysis only. No source, test, or config file was modified in
producing this report. Line numbers reference the working tree at the branch base.

---

## Module verdicts at a glance

| # | Module | Verdict |
|---|--------|---------|
| 1 | `tools.py` | Comments exemplary; real problem is size — 1054 lines, `context_prepare` is a ~233-line 8-job function. 6 findings (1 high). |

---

## 1. `neo_localmcp/tools.py` (1054 lines)

**Verdict:** The MCP tool surface is functional and its comments are, on the
whole, unusually good — most explain a genuine non-obvious WHY (determinism,
budget-starvation, client-hang avoidance), which is exactly CLAUDE.md's standard.
The real quality problem is **size and single-function complexity**: `tools.py`
is a 1054-line grab-bag mixing thin CLI-facing wrappers, three near-parallel
response *renderers*, the entire ranking/centering engine, and Ollama
orchestration in one flat module. `context_prepare` alone is ~230 lines and is
the single densest, hardest-to-follow function in the codebase. Findings below
are about structure and a few genuinely-hard spots lacking a WHY, not about the
comments (which are mostly a model to keep).

### Findings

**1.1 — `context_prepare` is a ~233-line function doing 8 distinct jobs (SRP / KISS) — `tools.py:639-871` — severity: high**
The function inlines: (a) freshness/refresh decisions, (b) query normalization,
(c) three separate candidate-scoring passes (index/symbol hits, batched rg
search, explicit-path resolution), (d) doc-reference following, (e) the
retrieval-memory boost, (f) ranking + `read_first` selection, (g) excerpt-range
centering with three fallback tiers, and (h) the optional Ollama prompt+call and
result assembly. Each is independently testable and nameable
(`_score_index_and_symbol_hits`, `_score_batched_search`, `_select_read_first`,
`_build_excerpt_ranges`, `_run_ollama_ranking`). A future reader cannot hold the
whole function in their head, and the local variables (`candidates`,
`symbol_hits`, `sections_for_memory`, `section_by_path`, `explicit_paths`,
`followed_references`) are threaded through all eight jobs. *Direction:* extract
the scoring passes and the excerpt-range builder into named helpers that take and
return the `candidates` dict / ranges list; the top-level function then reads as
a pipeline. This is the highest-leverage readability change in the module.

**1.2 — Three overlapping response renderers duplicate structure (DRY) — `tools.py:86-139` (`_render_context_text`), `tools.py:143-198` (`_mcp_compact_context`), `tools.py:237-317` (`_mcp_tiny_context_text`) — severity: med**
All three walk the same `read_first` / `candidate_files` / `agent_guidance` /
`ollama_*` shape and re-implement the same field extraction (`item.get("path")`,
`item.get("category")`, `item.get("score")`, `_compact_line_hints(...)`,
`reasons[:3]`). `compact_item` (line 153) and the loops at 107-120 and 277-283
are the same projection three times over. The `git`/`interp` unpacking prologue
is copy-pasted at 100-104, 150-151, and 244-246. *Direction:* a single
`_project_read_first_item()` (there is already a near-identical `project_item` at
line 413 inside `_stable_context_projection`, making it **four** copies) plus a
shared header-fields helper would collapse most of this. `_format` (319) already
dispatches on format; the per-format bodies are where the duplication lives.

**1.3 — `_sanitize_ollama_advisory` mixes three concerns with unexplained magic constants — `tools.py:201-234` — severity: med**
The function does control-char stripping, section-keyword gating, and length
trimming in one pass. The trim logic at 229-232 has an unexplained `400`
threshold (`if last_newline > 400`) — a reader cannot tell why 400 and not, say,
`max_chars // 2`. The keyword list at 220 (`"recommended read order"`, etc.) is
coupled by exact string to the prompt authored 400 lines away at
`context_prepare:859` ("Return concise sections exactly named: Recommended read
order, ..."). If the prompt wording changes, this silently stops matching and
falls back to a raw truncation. *Direction:* name the `400` (`_MIN_KEEP_CHARS`)
with a one-line WHY, and hoist the section-name list to a module constant shared
with the prompt so the coupling is visible in one place.

**1.4 — `ollama_control` action-dispatch dict rebuilt on every call — `tools.py:1042-1054` — severity: low**
The `actions` dict of seven lambdas is reconstructed on each invocation. Harmless
performance-wise (this is a one-shot CLI/MCP call), but it's the kind of table
that reads more clearly as a module-level mapping. Minor. *Direction:* optional;
leave if preferred — flagged only for completeness.

**1.5 — `prepare_context` vs `context_prepare`: a naming near-collision that only comments would disambiguate, and there are none — `tools.py:639` and `tools.py:874` — severity: med**
`context_prepare` is the full implementation; `prepare_context` (874) is a thin
adapter that flips `max_files`/`limit` and defaults `output_format="mcp_text"`.
The two names are anagrams of each other and nothing at either definition says
"this is the MCP-facing adapter; that is the CLI implementation." A reader
grepping for one will land on the other. The module map in CLAUDE.md lists
`prepare_context/context_prepare` together but doesn't explain the split either.
*Direction:* a one-line docstring on each ("MCP entrypoint — see
`context_prepare`" / "core implementation; `prepare_context` is the MCP adapter")
is exactly the non-obvious-WHY comment CLAUDE.md's standard calls for.

**1.6 — `doctor` hard-codes a `commands` list that must be kept in sync with `cli.py` by hand — `tools.py:387` — severity: med**
The `"commands": [...]` array (24 literal strings) duplicates the subcommand set
that `cli.py`'s argparse already declares. Nothing enforces they stay in sync;
`remove-client` appears in the argparse help but not obviously mirrored here, and
the reverse drift (a command added to `cli.py` but not to this list) is silent.
This is a DRY/doc-drift hazard inside the code itself. *Direction:* derive the
list from the argparse subparser names, or drop it (the CLI `--help` is already
the authoritative inventory).

**1.7 — Good comments worth preserving (positive finding)**
Several comments here are exemplary against CLAUDE.md's standard and should be
kept as the template for the rest of the codebase: `tools.py:534-536` (why score
each reason once — determinism), `tools.py:544-546` (why track hint weights),
`tools.py:721-725` (why the memory boost is bounded below structural signal),
`tools.py:938-940` (why `eval_count >= num_predict` means truncation),
`tools.py:993` context (the `newline=""` fix, a real Windows CRLF bug per
PROJECT_NOTES). None of these restate the code; each explains a decision a reader
would otherwise have to reverse-engineer. No noise/redundant comments were found
in this module — a clean result on the comment-quality axis.

**Ollama leads for this module (fast-rank `qwen3:8b`, verified against source):**
The fast reranker independently flagged that `context_prepare` "might lack single
responsibility (combining context creation with ranking logic)" and that a block
around lines 847–875 "may violate KISS by handling multiple responsibilities."
Verified against source: 847–875 is the Ollama prompt-assembly + result-nesting
tail of `context_prepare`, i.e. one of the eight jobs identified in finding **1.1**
— so the model's high-level smell was **correct and corroborates 1.1** (recorded
as a datapoint, not the basis for the finding, which stands on my own read). It
also produced junior-dev-grade noise: it invented "audit logging" that does not
exist in `tools.py`, and cited `server.py:105` vaguely. Net: right on the smell,
wrong on specifics — chase-worthy, not authoritative. (Also notable: with
`--ollama-rank` on, the reranker named the canonical `neo_localmcp/tools.py` #1,
effectively *correcting* the deterministic worktree-copy pollution from Q5 — a
point in the tool's favor, logged in the perf log.)
