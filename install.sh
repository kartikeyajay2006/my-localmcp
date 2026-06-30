#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_HOME="${NEO_LOCALMCP_HOME:-$HOME/.neo-localmcp}"
VENV_DIR="$APP_HOME/venv"
BIN_DIR="$APP_HOME/bin"

if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "Python 3.10+ is required and was not found on PATH." >&2
  exit 1
fi

mkdir -p "$APP_HOME" "$BIN_DIR"
"$PYTHON" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install --upgrade --force-reinstall "$ROOT_DIR"

cat > "$BIN_DIR/neo-localmcp" <<EOF2
#!/usr/bin/env bash
exec "$VENV_DIR/bin/python" -m neo_localmcp.cli "\$@"
EOF2
chmod +x "$BIN_DIR/neo-localmcp"

PROFILE_FILE=""
SHELL_NAME="$(basename "${SHELL:-}")"
if [[ "$SHELL_NAME" == "zsh" ]]; then
  PROFILE_FILE="$HOME/.zshrc"
elif [[ "$SHELL_NAME" == "bash" ]]; then
  PROFILE_FILE="$HOME/.bashrc"
else
  PROFILE_FILE="$HOME/.profile"
fi

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  touch "$PROFILE_FILE"
  if ! grep -Fq "$BIN_DIR" "$PROFILE_FILE" 2>/dev/null; then
    {
      echo ""
      echo "# neo-localmcp"
      echo "export PATH=\"$BIN_DIR:\$PATH\""
    } >> "$PROFILE_FILE"
  fi
  export PATH="$BIN_DIR:$PATH"
fi

"$BIN_DIR/neo-localmcp" init
"$BIN_DIR/neo-localmcp" doctor || true

cat <<EOF2

Installed neo-localmcp.
Command: $BIN_DIR/neo-localmcp
Open a new terminal or run: export PATH="$BIN_DIR:\$PATH"

Next:
  1. Open a new terminal if PATH is not refreshed.
  2. Run setup once from anywhere:
       neo-localmcp setup --client all
  3. cd into the repo you want analyzed, then index/context there:
       cd /path/to/your/repo
       neo-localmcp index
       neo-localmcp context "debug settings persistence: BackdropMaterial, LoadSettingsAsync"
  4. Check paths anytime with: neo-localmcp where
EOF2
