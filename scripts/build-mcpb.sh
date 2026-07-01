#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/.*__version__\s*=\s*"\([^"]*\)".*/\1/p' "$ROOT/neo_localmcp/__init__.py")"
if [ -z "$VERSION" ]; then
    echo "Could not determine neo-localmcp version from $ROOT/neo_localmcp/__init__.py" >&2
    exit 1
fi
STAGE="${TMPDIR:-/tmp}/neo-localmcp-mcpb"
TARGET="${1:-$ROOT/packages/claude-desktop/neo-localmcp-v$VERSION.mcpb}"
rm -rf "$STAGE"
cp -R "$ROOT/packages/claude-desktop/mcpb" "$STAGE"
cp -R "$ROOT/neo_localmcp" "$STAGE/neo_localmcp"
cp "$ROOT/pyproject.toml" "$STAGE/pyproject.toml"
cp "$ROOT/README.md" "$STAGE/README.md"
(cd "$STAGE" && npx --yes @anthropic-ai/mcpb pack . "$TARGET")
echo "Built $TARGET"
