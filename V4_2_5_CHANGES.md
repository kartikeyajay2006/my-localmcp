# neo-localmcp V4.2.5

Small Ollama hardening release after V4.2.4 proved the Claude Code deterministic MCP path.

## Changes

- Bumped default Ollama timeout from 180s to 200s.
- Existing configs using the old 180s default are treated as 200s at runtime.
- MCP `context_prepare(..., use_ollama=true)` now labels Ollama status clearly as used/timed_out/failed.
- Deterministic `READ FIRST` remains generated separately and authoritative.
- Ollama output is appended only as a bounded non-authoritative advisory.
- If Ollama times out or fails, deterministic context still returns and the advisory reports the failure instead of leaking partial model text.
- Reduced the Ollama prompt payload for MCP ranking to the requested top candidates.
- Worker safety cap reduced for Ollama MCP output to avoid client-side rendering stalls.

## Rule

Ollama must never corrupt or overwrite deterministic `READ FIRST`.
