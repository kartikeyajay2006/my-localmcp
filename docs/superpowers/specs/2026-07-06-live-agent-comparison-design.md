# Live agent-vs-agent token comparison (`--live-agent-comparison`, #65)

## Origin

Split out from #9 (automated token-reduction benchmarking) during that
feature's own design discussion, because this piece is fundamentally
different in kind from the rest of the benchmark tool: it spends real API
budget, is non-deterministic by nature, and needs a live agent driver rather
than neo-localmcp's own self-reported estimates. The rest of #9 (phases
1a-1f, shipped in 1.1.1) measures `estimated_tokens_returned` — a char÷4
proxy — against a deterministic baseline. This feature instead runs a real
agent through a task twice, once restricted to neo-localmcp's own tools and
once restricted to raw filesystem tools, and sums the *actual* API-reported
token usage for each run. That is what validates the README's stated
acceptance targets (>=50% fewer discovery/read tokens, >=30% fewer total
task tokens) against real agent behavior instead of a proxy.

The issue itself left five design questions explicitly unresolved (stopping
condition, cost ceiling, run-to-run variance, fair no-MCP framing,
credentials). All five, plus several follow-on implementation-shape
questions, were resolved through brainstorming on 2026-07-06; this document
records the resolved design, not the discussion.

Relationship to #9: shares #9's task/query corpus
(`neo_localmcp/benchmark_queries/default.jsonl`) and output-directory
convention (`neo-localmcp_benchmarks/<timestamp>/...`), but is designed and
implemented independently, per the issue's own reasoning above.

## Decisions locked in during design

- **Driver: Claude Agent SDK**, not a hand-rolled Anthropic API tool loop. It
  already ships built-in `Read`/`Grep`/`Glob` tools for the "without MCP"
  baseline, and can attach this repo's own MCP server (via the existing
  `neo-localmcp-server` stdio entry point, `pyproject.toml`'s
  `[project.scripts]`) restricted to specific tools for the "with MCP"
  condition. Least new code to build and maintain; least surface for a
  hand-rolled harness bug to skew the comparison itself.
- **Optional dependency, not a base one.** `neo-localmcp[benchmark-live-agent]`
  pins `claude-agent-sdk`; the base install stays untouched, matching the
  existing `[wizard]` extra convention (`psutil` is pinned there, not in base
  deps) — most users of neo-localmcp will never invoke this feature or spend
  API budget on it.
- **Stopping condition: explicit `report_answer` tool + turn-cap fallback.**
  Both conditions get one extra tool, `report_answer(file, line_range,
  confidence)`, that the agent must call to finish. A turn cap (default 15)
  is a hard backstop; a run that hits it is marked incomplete rather than
  left to wander indefinitely (a real risk specifically in the no-MCP
  condition).
- **Cost ceiling: explicit task selection required, small hard cap, and an
  explicit confirmation gate.** No default corpus-wide run. The caller
  selects specific task(s) by id; `--max-tasks` (default 5) rejects an
  oversized request. Without `--yes`, the command prints exactly how many
  live model calls the invocation will make (tasks x 2 conditions x runs)
  and requires interactive confirmation; if stdin isn't a tty and `--yes` is
  absent, it aborts rather than silently spending money. This is the
  strictest reading of the issue's "must never run implicitly" requirement.
- **Run count: 1 run per condition per task by default**, optional `--runs N`
  (small cap, e.g. max 3) for callers who explicitly want averaged, steadier
  numbers at proportionally higher cost. The default invocation is the
  cheapest possible one (a smoke test), not an averaged one.
- **Baseline framing: identical task prompt, condition-appropriate system
  framing.** The user-facing task prompt is byte-identical in both
  conditions (that's the thing being measured). The no-MCP condition's
  system prompt explicitly tells the agent it has `Read`/`Grep`/`Glob`
  available and should explore broadly with them, so it isn't artificially
  timid or unsure what tools exist — avoiding an artificially hobbled
  baseline while keeping the actual ask unchanged.
- **Task selection: pick by id from the existing #9 corpus.** A short `id`
  field is added to each row of `benchmark_queries/default.jsonl` (and any
  `--queries` override file). `--task-name <id>` (repeatable) selects
  specific entries; each already has a known `gold_file` to score
  correctness against, and nothing new needs to be maintained.
- **Correctness is scored, not just tokens.** `report_answer`'s reported
  `file` is compared against the task's `gold_file` (exact match). Without
  this, a run where an agent gives up early and guesses would look
  artificially cheap and fast, distorting the comparison the whole feature
  exists to make honest.
- **Credentials: reuse ambient `ANTHROPIC_API_KEY` / Claude Code auth.** The
  Claude Agent SDK already resolves credentials the way the Claude Code CLI
  does. No new credential-storage code in neo-localmcp; if credentials are
  missing, the command fails fast with a clear message before spending
  anything.
- **CLI: a new dedicated subcommand**, `neo-localmcp benchmark-live-agent`,
  not a flag on the existing `benchmark <group>` command. Structurally
  impossible to trigger via `benchmark full` or any existing group, and its
  flag shape (`--task-name`, `--runs`, `--max-tasks`, `--yes`) doesn't need
  to fit the existing group-based command's shape.
- **README must state the Claude/Anthropic-only requirement explicitly.**
  The rest of neo-localmcp (including the rest of the benchmark tool) needs
  no external LLM API access at all — only optionally local Ollama. This one
  command is the sole exception, since it is Claude Agent SDK-driven and
  spends real Anthropic API budget. The command reference table entry and
  any surrounding prose must say so plainly, not bury it in the extras
  install line, so a user doesn't discover the credential/cost requirement
  only by hitting the fail-fast error. (README's "two things about
  token-reduction measurement" bullets, updated 2026-07-06, already
  establish this framing for the not-yet-built state; the implementation
  plan must update them again once this actually ships, and add the command
  to the CLI administration table alongside `benchmark`.)

## Architecture

A new module, `neo_localmcp/live_agent_benchmark.py`, separate from
`benchmark.py` per the reasoning above, but importing `benchmark.py`'s
report-writing/output-path helpers (`_write_report`'s path convention) rather
than duplicating them. The optional `claude-agent-sdk` import is isolated to
this one module and guarded — importing `neo_localmcp.cli` or
`neo_localmcp.benchmark` must never require the extra to be installed;
only invoking `benchmark-live-agent` itself does.

`cli.py` gains a new subcommand wired to a `cmd_benchmark_live_agent`
function, structurally separate from `cmd_benchmark`.

## Per-task run flow

For each selected task (by id, from the corpus):

1. Load the task's `task` prompt string and `gold_file` from the corpus.
2. Run condition **with-mcp**: a Claude Agent SDK session configured with
   this repo's `neo-localmcp-server` stdio entry point as its only MCP
   server, tools allow-listed to `prepare_context`/`file_excerpts`/
   `repo_lookup` plus `report_answer`. System prompt frames these as the
   agent's available context tools.
3. Run condition **without-mcp**: the same SDK session shape but no MCP
   server configured; built-in `Read`/`Grep`/`Glob` plus `report_answer`.
   System prompt explicitly invites broad exploration with those tools.
4. Both conditions get the identical user-facing task prompt. A turn cap
   (default 15) stops a run that never calls `report_answer`; that run is
   marked `stopped_by: "turn_cap"` and `correct: None` (no answer given).
   A run that calls `report_answer` is marked `stopped_by: "report_answer"`.
5. Real `input_tokens`/`output_tokens` are read from the SDK's per-turn
   response objects and summed per run — never a char÷4 estimate. This is
   flagged distinctly in the report (e.g. `token_source: "real"`) from the
   rest of the benchmark's estimated figures, per the transparency
   requirement established in `docs/1.1.1_PLAN.md`.
6. Correctness: `report_answer`'s `file` is compared against the task's
   `gold_file` (exact path match after normalization).
7. Repeat steps 2-6 `--runs` times (default 1); when `--runs > 1`, token
   counts and correctness are averaged/aggregated per condition per task.
8. A `reduction_ratio` is computed the same way #9's `mem` group computes it
   (with-mcp real total tokens vs. without-mcp real total tokens), so both
   figures land in a comparable place in the report.

## CLI

```
neo-localmcp benchmark-live-agent \
    --task-name <id> [--task-name <id> ...] \
    [--queries <path>] \
    [--runs N] \
    [--max-tasks N] \
    [--yes] \
    [--out <dir>]
```

- `--task-name` is required, repeatable; entries beyond `--max-tasks`
  (default 5) are rejected with a clear error before anything runs.
- `--queries` overrides the default corpus file, same convention as the
  existing `benchmark` command's `--queries` flag.
- `--runs` defaults to 1, capped at a small maximum (3).
- Without `--yes`: prints the exact live-call count
  (`len(task_names) * 2 * runs`) and prompts for interactive confirmation
  if stdin is a tty; aborts immediately (no calls made) if stdin is not a
  tty and `--yes` is absent.
- Missing `ANTHROPIC_API_KEY` or the `claude-agent-sdk` package: fails fast
  with a clear, actionable message (`pip install
  "neo-localmcp[benchmark-live-agent]"` / `export ANTHROPIC_API_KEY=...`)
  before any API call is attempted.
- Never part of `benchmark full` or any existing group; only reachable via
  this dedicated subcommand.

## Error handling

A turn-cap timeout or a mid-run SDK/network error marks that one run
`ok: False` with an `error` message, but does not abort the whole
invocation — remaining tasks/conditions/runs still proceed, matching
`benchmark.py`'s existing per-check error isolation (a failed check is
recorded, not fatal). The final report and its written summary always
reflect exactly what happened, including partial failures — never silently
drops a failed run from the output.

## Testing

The real Claude Agent SDK is never invoked in automated tests. The
run-a-single-condition step is dependency-injected (a `run_condition`
callable parameter, defaulting to the real SDK-backed implementation),
mirroring how `ollama_status_fn` is already stubbed in
`tests/installer/test_verification.py`'s pattern. Tests cover:

- Report shape, `reduction_ratio` scoring, and correctness comparison
  against a fake `run_condition` that returns canned token counts and
  answers — no real API calls, no optional dependency required in CI.
- Cost-ceiling and confirmation-gate behavior (`--max-tasks` rejection,
  `--yes` bypass, non-tty abort) via the fake runner.
- A clean, actionable error when `claude-agent-sdk` is not installed
  (import-guarded, asserted via a monkeypatched failed import) — this must
  pass without the optional extra present, since CI won't install it.

CI does not need the `[benchmark-live-agent]` extra installed at all; the
"is the dependency actually importable and does the real SDK integration
work" question is validated manually (documented as a remaining manual
verification step, same as the wizard's outstanding real macOS run),
because a real end-to-end run spends real API budget and is explicitly
never something CI should trigger automatically.

## Deferred / explicitly out of scope

- **Multiple agent SDKs / providers.** Anthropic's Claude Agent SDK only;
  no OpenAI/other-provider comparison mode.
- **Cross-model comparison** (e.g. comparing different Claude model tiers'
  token usage) — this feature compares tool-access conditions for one
  model, not models against each other.
- **CI integration.** Like the rest of #9, this is an on-demand developer
  tool, not a CI gate — doubly so here given real cost.
- **A UI/dashboard for live-agent-comparison results.** Output is the same
  JSON/Markdown/CSV report format as the rest of #9.
