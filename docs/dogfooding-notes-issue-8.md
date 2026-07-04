# Dogfooding notes — issue #8 (summary-cache behavior on Ollama model swap)

Running log of my actual experience using neo-localmcp's own CLI (`context`/`lookup`/`file`)
to research and close issue #8. Written as I go, not reconstructed.

Task shape: this is a *research/decision* task (understand how model-tagged summaries
behave on a model swap, then decide + document), not a code-edit task. Retrieval needs
to point me at `summary_model` / `section_summaries` handling across `tools.py`,
`ollama_client.py`, `repo_memory.py`.

---

## Run log

### 1. `neo-localmcp context "summary_model tagging on section_summaries and files: ..." --token-budget 1200`

Ranked #1 `neo_localmcp/ollama_client.py` (score 205), then the two `*_PLAN.md`
docs, then `installer/ollama.py`, `docs/ARCHITECTURE.md` (Safety model heading —
useful, that's exactly the section I needed for grounding the decision), then
`PROJECT_NOTES.md`.

- **Mixed usefulness.** The ranking surfaced `ollama_client.py` and the ARCHITECTURE
  Safety-model section (both genuinely relevant), but it did NOT surface the two files
  where the actual summary-cache logic lives: `repo_memory.py` (the `section_summaries`
  table + `store_section_summary`/`get_section_summary`) and `tools.py`
  (`_summarize_section`'s cache-hit check). The query's strong terms got parsed as
  `summaries, stored, whether, model, swap, invalidates, them` — note "whether",
  "them", "swap", "invalidates" are natural-language filler for *this* codebase and
  pulled ranking toward `model`-heavy files (ollama_client, installer/ollama) rather
  than the storage/cache-decision code. `section_summaries` and `summary_model` were
  demoted to *weak* terms, which is backwards for my intent — those were the two most
  load-bearing identifiers in the question.
- **No estimated-tokens footer printed.** CLAUDE.md's smoke-test example implies
  `context ... --token-budget N` self-reports estimated tokens, and PROJECT_NOTES
  (2026-07-03 (2)) describes an `estimated_tokens` figure, but this CLI run printed no
  budget/estimated-tokens footer at all — just the ranked "Read first" list + agent
  guidance. Either the footer is MCP-response-only (not CLI-rendered) or it's gated on
  something. Friction for the token-comparison task: I can't capture the tool's own
  self-estimate, so my with-MCP numbers below are eyeballed from output size.

### 2. `neo-localmcp lookup "section_summaries"` and `lookup "summary_model"`

- `lookup "section_summaries"` → **zero hits, zero symbols.** `section_summaries` is a
  lowercase SQL table name inside a `CREATE TABLE` string literal, not an extracted
  symbol, so the symbol index has nothing to match. For a research task centered on a
  DB table, `lookup` is a dead end — the table name that dominates the question is
  invisible to it.
- `lookup "summary_model"` → **2 hits, 1 symbol, and this one was genuinely good.** It
  pointed straight at `tools.py:set_ollama` (start_line 1020) — the exact config-change
  entry point relevant to option 2 (does changing the model invalidate anything?).
  Fast, precise, exactly what I needed for that half of the question.

### 3. Fell back to `Grep` for the storage/cache-decision code

`lookup` couldn't reach the table, and `context` didn't rank the storage files, so I
grepped `repo_memory.py` for `section_summaries|summary_model|.summary` and `tools.py`
for `get_section_summary|source_hash|cached`. That immediately landed the two decisive
lines:

- `repo_memory.py:226-230` — re-indexing a content-changed file resets `summary_model`
  (and all summary fields) to NULL. Content hash is the only invalidation trigger.
- `tools.py:916` — `if cached and cached.get("source_hash") == current_hash and (not
  model or cached.get("model") == model)`. The model is only compared when the caller
  passes `model` explicitly; `summarize_file`'s MCP `model` param defaults to `None`,
  so in normal use the model check short-circuits and the old-model summary is served.

**Assessment of retrieval for this task:** `lookup` on a real identifier (`summary_model`)
was the single best hit of the session. But the two pieces of code that actually decide
the behavior (the cache-hit conditional and the re-index NULL-reset) were reached by
grep, not by neo-localmcp — `context` mis-weighted the load-bearing identifiers as weak
terms, and `lookup` can't see SQL table names at all. For a research/decision task the
retrieval was a helpful *orientation* layer (pointed me at ARCHITECTURE's Safety model
and at `set_ollama`) but not sufficient on its own to locate the decisive logic.

---

## Token-cost comparison: with-MCP vs. approximate without-MCP

Caveat up front: these are **estimates, not precise counts.** neo-localmcp's own token
figures are char-derived until real client telemetry exists (a known limitation in
PROJECT_STATUS.md), and the `context` CLI run here didn't even print its self-estimate
(see run #1). So the with-MCP side is eyeballed from output size and the without-MCP side
is `chars ÷ 4 ≈ tokens` over the whole files I'd otherwise have had to read end to end.
This is also a *research/decision* task, not a code-edit task — see the note at the end
about why that skews the comparison.

File sizes (auditable basis for the counterfactual), measured in the worktree:

| File | bytes (`wc -c`) | ≈ tokens (bytes÷4) |
|---|---|---|
| `neo_localmcp/repo_memory.py` | 36,418 | ~9,100 |
| `neo_localmcp/tools.py` | 54,357 | ~13,600 |
| `neo_localmcp/ollama_client.py` | 17,674 | ~4,400 |
| `docs/ARCHITECTURE.md` | 3,259 | ~800 |

Reading all four end-to-end (the naive without-MCP way to answer "how do
model-tagged summaries behave on a swap") ≈ **~27,900 tokens**. In practice I only
needed focused regions of the first three (~200 lines total) plus the ARCHITECTURE
Safety-model section, so the realistic without-MCP read was ~3,000-3,500 tokens once I
knew where to look — the whole-file figure is the upper bound if I'd read blind.

Per-retrieval tally:

- **context run #1** — with-MCP actual: the ranked "Read first" + agent-guidance output
  was ~3,500 chars ≈ **~875 tokens** returned. Without-MCP counterfactual for the same
  "where does summary/model handling live" question: I'd have grepped and then read
  large chunks of `ollama_client.py` + `tools.py` + `repo_memory.py` to find it — even
  reading just the relevant halves is easily 400-500 lines ≈ **~6,000-8,000 tokens**.
  Net: MCP cheaper here by roughly 7-9x *for the orientation step* — but see the honesty
  note: the orientation didn't actually pinpoint the decisive code, so part of that
  "saving" is illusory (I still had to grep+read afterward).
- **lookup summary_model** — with-MCP actual: JSON hit list ~700 chars ≈ **~175 tokens**,
  and it gave me the exact symbol + line (`set_ollama` @ 1020). Without-MCP: grepping
  `summary_model` across the tree then reading around each hit — the grep alone returns
  ~10 lines, but confirming the config-write path means reading ~35 lines of
  `set_ollama` ≈ **~250 tokens**. Roughly a wash-to-slightly-cheaper, and much faster to
  read (structured hit vs. scanning grep output).
- **lookup section_summaries** — with-MCP actual: ~200 chars, **zero useful content**.
  Without-MCP: a single `grep section_summaries repo_memory.py` (what I actually did
  next) returned the 8 decisive lines directly ≈ **~150 tokens** and *worked*. Here the
  MCP call was pure overhead — a wasted ~50 tokens that returned nothing.
- **grep fallbacks (repo_memory + tools)** — these are the without-MCP path I actually
  used to find the answer. Two targeted greps (~30 lines total ≈ ~400 tokens) plus
  reading ~200 focused lines across the two files (~2,500 tokens) ≈ **~2,900 tokens** to
  reach the two decisive lines. neo-localmcp did not save these — it didn't rank the
  storage files, so this cost was incurred regardless of the MCP.

**Bottom line (honest):** On the pure orientation question ("where does this subsystem
live"), the MCP `context`/`lookup` calls were cheaper than blind whole-file reads by
several-fold, consistent with the ≥50% discovery-token target *in principle*. But for
this specific research/decision task the MCP did **not** locate the two lines the
decision actually turned on — I reached those by grep + focused reads (~2,900 tokens)
that the MCP couldn't shortcut. So the *realized* saving on this task was modest and
partly offset by two low/zero-value calls (`lookup section_summaries` returned nothing;
`context` mis-weighted the key identifiers). Net I'd call it roughly break-even-to-mildly-
positive, well short of the headline ≥50% on *this* task — not because the tool is broken,
but because a "which of two documented behaviors is intended, and is the stale artifact
ever surfaced as authoritative" question is answered by reading specific conditional
logic and the architecture's framing, not by ranked file discovery, which is what
neo-localmcp optimizes for. The ≥50%/≥30% targets are stated against *representative
coding tasks* (find-and-edit); a decision/documentation task is a genuinely different
shape and the comparison came out lopsided toward "MCP helps orient, grep finds the
decisive line." That shape difference is itself the finding.

---

## Decision recorded

**Option 1: status quo, document only.** Summaries are advisory enrichment
(ARCHITECTURE Safety model: "Cached context narrows reads; it does not replace source
truth" — the current source file and git diff are authoritative). A summary produced by
an older model is still an accurate description of an *unchanged* file; nothing surfaces
it as authoritative, and re-indexing on any content change already clears it. No concrete
correctness problem with stale-but-valid summaries was found, so adding invalidation
machinery to `set-ollama` would be exactly the speculative fallback machinery CLAUDE.md's
minimalism convention warns against. Documenting the intended behavior in CLAUDE.md's
Known gaps + PROJECT_STATUS + PROJECT_NOTES so a future contributor doesn't "fix" it
without knowing it's deliberate.


---

## Issues filed from this session

Checked `gh issue list --state all --limit 50` first — no existing issue covered either
gap (#9 is a benchmark harness, not retrieval quality). Filed two:

- **#22** `fix(retrieval): lookup returns nothing for SQL table names / string-literal
  identifiers` — `lookup "section_summaries"` returned zero results because the table
  name lives in a `CREATE TABLE` string literal, not an extracted symbol; grep found the
  8 decisive lines instantly. Suggests an FTS/substring fallback when the symbol index is
  empty.
- **#23** `fix(retrieval): context mis-weights code identifiers (summary_model,
  section_summaries) as weak terms` — the parser marked the two load-bearing identifiers
  as *weak* and prose connectives (`whether`, `them`, `swap`) as *strong*, so ranking
  missed `repo_memory.py`/`tools.py` where the answer lived. Suggests treating
  identifier-shaped tokens as strong by construction.

Both are `type:fix` + `area:mcp-toolkit` + `area:retrieval`. Only filed things I actually
hit this session, not speculation.
