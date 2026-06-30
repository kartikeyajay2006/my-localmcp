#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="${TMPDIR:-/tmp}/neo-localmcp-mcpb"
TARGET="${1:-$ROOT/packages/claude-desktop/neo-localmcp.mcpb}"
rm -rf "$STAGE"
cp -R "$ROOT/packages/claude-desktop/mcpb" "$STAGE"
cp -R "$ROOT/neo_localmcp" "$STAGE/neo_localmcp"
cp "$ROOT/pyproject.toml" "$STAGE/pyproject.toml"
cp "$ROOT/README.md" "$STAGE/README.md"
(cd "$STAGE" && npx --yes @anthropic-ai/mcpb pack . "$TARGET")
echo "Built $TARGET"
