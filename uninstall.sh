#!/usr/bin/env bash
set -euo pipefail
APP_HOME="${NEO_LOCALMCP_HOME:-$HOME/.neo-localmcp}"
case "$(basename "$APP_HOME")" in .neo-localmcp) ;; *) echo "Refusing unexpected application directory: $APP_HOME" >&2; exit 2 ;; esac
rm -rf -- "$APP_HOME/venv" "$APP_HOME/venvs" "$APP_HOME/bin"
rm -f -- "$APP_HOME/neo-localmcp.mcpb"
rm -f -- "$APP_HOME/current-venv.txt"
if [[ "${1:-}" == "--remove-data" ]]; then
  rm -f -- "$APP_HOME/config.yaml" "$APP_HOME/repo-context.sqlite" "$APP_HOME/repo-context.sqlite-wal" "$APP_HOME/repo-context.sqlite-shm" "$APP_HOME/ollama-supervisor.json"
fi
echo "Removed neo-localmcp runtime. Data is preserved unless --remove-data was supplied."
