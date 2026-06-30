from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import tools
from .client_setup import client_status, setup_clients
from .config import CONFIG_PATH, ensure_config
from .identity import IDENTITY


def print_json_text(text: str) -> None:
    print(text)


def cmd_init(args: argparse.Namespace) -> int:
    print_json_text(tools.init())
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print_json_text(tools.status(args.repo_root))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    print_json_text(tools.doctor(args.repo_root))
    return 0


def cmd_where(args: argparse.Namespace) -> int:
    print_json_text(tools.where(args.repo_root))
    return 0


def cmd_model_status(args: argparse.Namespace) -> int:
    print_json_text(tools.model_status())
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import main as server_main
    server_main()
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    ensure_config()
    results = setup_clients(args.client, apply=not args.dry_run)
    print(json.dumps({"ok": True, "product": IDENTITY.product_name, "config_path": str(CONFIG_PATH), "applied": not args.dry_run, "results": results}, indent=2))
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    print(str(CONFIG_PATH))
    return 0


def cmd_clients(args: argparse.Namespace) -> int:
    print(json.dumps(client_status(), indent=2, ensure_ascii=False))
    return 0


def cmd_set_ollama(args: argparse.Namespace) -> int:
    print_json_text(tools.set_ollama(args.base_url, args.summary_model, args.fast_model, args.num_ctx))
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    print_json_text(tools.repo_index(args.repo_root, max_files=args.max_files, force=args.force))
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    print_json_text(tools.repo_refresh(args.repo_root, max_files=args.max_files, force=args.force))
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    print_json_text(tools.repo_reindex(args.repo_root, max_files=args.max_files))
    return 0


def cmd_reset_repo(args: argparse.Namespace) -> int:
    if not args.yes:
        print_json_text(json.dumps({"ok": False, "error": "Refusing to reset without --yes", "hint": "Run: neo-localmcp reset-repo --yes"}, indent=2))
        return 2
    print_json_text(tools.reset_repo(args.repo_root))
    return 0


def cmd_reset_all(args: argparse.Namespace) -> int:
    if not args.yes:
        print_json_text(json.dumps({"ok": False, "error": "Refusing to reset all repo context without --yes", "hint": "Run: neo-localmcp reset-all --yes"}, indent=2))
        return 2
    print_json_text(tools.reset_all())
    return 0


def cmd_test_determinism(args: argparse.Namespace) -> int:
    print_json_text(tools.test_determinism(args.task, args.repo_root, runs=args.runs, max_files=args.max_files, limit=args.limit, reset_repo_first=args.reset_repo, reindex_first=args.reindex_first))
    return 0


def cmd_lookup(args: argparse.Namespace) -> int:
    print_json_text(tools.repo_lookup(args.query, args.repo_root, args.limit))
    return 0


def cmd_file(args: argparse.Namespace) -> int:
    print_json_text(tools.file_context(args.path, args.repo_root, args.around_line, args.context_lines))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    use_ollama = bool(args.ollama_rank) and not bool(args.no_ollama)
    print_json_text(tools.prepare_context(args.task, args.repo_root, token_budget=args.token_budget, max_files=args.max_files, use_ollama=use_ollama, model=args.model, output_format=args.format))
    return 0


def cmd_ollama(args: argparse.Namespace) -> int:
    print_json_text(tools.ollama_control(args.ollama_action, getattr(args, "model", None), getattr(args, "purpose", "ranking")))
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    print_json_text(tools.summarize_file(args.path, args.repo_root, args.model))
    return 0


def cmd_apply_patch(args: argparse.Namespace) -> int:
    patch_text = sys.stdin.read() if args.patch_file == "-" else open(args.patch_file, "r", encoding="utf-8").read()
    print_json_text(tools.apply_unified_patch(patch_text, args.repo_root, check_only=args.check_only))
    return 0


def cmd_record_change(args: argparse.Namespace) -> int:
    print_json_text(tools.record_change(args.summary, args.paths, args.repo_root))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=IDENTITY.cli_name, description="neo-localmcp: deterministic repository context for Claude/Codex. Context lookup is fast/deterministic by default; Ollama ranking is opt-in.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Create the fresh neo-localmcp config."); p.set_defaults(func=cmd_init)
    p = sub.add_parser("status", help="Fast status: config, repo context DB, Ollama reachability."); p.add_argument("--repo-root", default="auto"); p.set_defaults(func=cmd_status)
    p = sub.add_parser("doctor", help="Full health check and command inventory."); p.add_argument("--repo-root", default="auto"); p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("where", help="Show install/config paths and the repo currently being analyzed."); p.add_argument("--repo-root", default="auto"); p.set_defaults(func=cmd_where)
    p = sub.add_parser("serve", help="Run the MCP server over stdio."); p.set_defaults(func=cmd_serve)
    p = sub.add_parser("config", help="Print config path."); p.set_defaults(func=cmd_config)
    p = sub.add_parser("clients", help="Show detected Claude/Codex client config paths and MCP blocks."); p.set_defaults(func=cmd_clients)

    p = sub.add_parser("model", help="Model/Ollama helpers.")
    model_sub = p.add_subparsers(dest="model_command", required=True)
    mp = model_sub.add_parser("status", help="Show configured Ollama models and reachable models."); mp.set_defaults(func=cmd_model_status)

    p = sub.add_parser("ollama", help="Inspect and manage Ollama readiness.")
    ollama_sub = p.add_subparsers(dest="ollama_action", required=True)
    for action in ["status", "ensure", "start", "warm", "unload", "stop", "test"]:
        op = ollama_sub.add_parser(action, help=f"Ollama {action} operation.")
        if action in {"status", "ensure", "warm", "unload", "test"}:
            op.add_argument("--model")
            op.add_argument("--purpose", choices=["ranking", "query", "summary"], default="ranking")
        op.set_defaults(func=cmd_ollama)

    p = sub.add_parser("setup", help="Install MCP config/slash commands for supported clients.")
    p.add_argument("--client", action="append", choices=["all", "claude-code", "claude-desktop", "codex", "codex-cli", "codex-desktop"], help="Client to set up. Repeatable. Defaults to Claude Code, Claude Desktop, Codex CLI, and Codex Desktop.")
    p.add_argument("--dry-run", action="store_true", help="Show what would be written without changing files.")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("set-ollama", help="Set Ollama URL/model defaults.")
    p.add_argument("--base-url"); p.add_argument("--summary-model"); p.add_argument("--fast-model"); p.add_argument("--num-ctx", type=int)
    p.set_defaults(func=cmd_set_ollama)

    p = sub.add_parser("index", help="Hash-aware repository index of files and symbols.")
    p.add_argument("--repo-root", default="auto"); p.add_argument("--max-files", type=int, default=None); p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("refresh", help="Update stale/missing repository context.")
    p.add_argument("--repo-root", default="auto"); p.add_argument("--max-files", type=int, default=None); p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("reindex", help="Force rebuild repository context with the current V1 indexer.")
    p.add_argument("--repo-root", default="auto"); p.add_argument("--max-files", type=int, default=None)
    p.set_defaults(func=cmd_reindex)

    p = sub.add_parser("reset-repo", help="Delete only the current repo's indexed context from the shared DB. Keeps config and other repos.")
    p.add_argument("--repo-root", default="auto"); p.add_argument("--yes", action="store_true", help="Required safety confirmation.")
    p.set_defaults(func=cmd_reset_repo)

    p = sub.add_parser("reset-all", help="Delete the full repo context DB. Keeps config and installed client setup.")
    p.add_argument("--yes", action="store_true", help="Required safety confirmation.")
    p.set_defaults(func=cmd_reset_all)

    p = sub.add_parser("test-determinism", help="Run the same deterministic context query multiple times and verify stable output.")
    p.add_argument("task")
    p.add_argument("--repo-root", default="auto")
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--max-files", type=int, default=6)
    p.add_argument("--limit", type=int, default=6)
    p.add_argument("--reset-repo", action="store_true", help="Reset current repo context, then reindex before testing.")
    p.add_argument("--reindex-first", action="store_true", help="Force reindex before testing without resetting repo records first.")
    p.set_defaults(func=cmd_test_determinism)

    p = sub.add_parser("lookup", help="Search repository context memory for files/symbols.")
    p.add_argument("query"); p.add_argument("--repo-root", default="auto"); p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_lookup)

    p = sub.add_parser("file", help="Return one file's cached context, symbols, freshness, and optional excerpt.")
    p.add_argument("path"); p.add_argument("--repo-root", default="auto"); p.add_argument("--around-line", type=int); p.add_argument("--context-lines", type=int, default=40)
    p.set_defaults(func=cmd_file)

    p = sub.add_parser("context", help="Prepare source-first files/lines for a natural or hybrid task before Claude/Codex reads broadly.")
    p.add_argument("task", help="Natural or hybrid query, e.g. 'debug settings persistence: BackdropMaterial, LoadSettingsAsync'")
    p.add_argument("--repo-root", default="auto")
    p.add_argument("--max-files", type=int, default=6, help="Maximum source files returned in the bounded context bundle.")
    p.add_argument("--token-budget", type=int, default=3000, help="Approximate source-excerpt token budget.")
    p.add_argument("--ollama-rank", action="store_true", help="Opt in to Ollama ranking. Context is deterministic/no-Ollama by default in V1.")
    p.add_argument("--no-ollama", action="store_true", help="Compatibility flag: force deterministic ranking. This is already the default in V1.")
    p.add_argument("--model", help="Override Ollama model when --ollama-rank is used.")
    p.add_argument("--format", choices=["text", "json", "mcp_text", "mcp_json"], default="text", help="CLI output format. MCP tools use bounded mcp_text by default in V1.")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("summarize", help="Summarize one file with Ollama and store it as working context.")
    p.add_argument("path"); p.add_argument("--repo-root", default="auto"); p.add_argument("--model")
    p.set_defaults(func=cmd_summarize)

    p = sub.add_parser("apply-patch", help="Apply an exact approved unified diff; never generates code.")
    p.add_argument("patch_file", help="Patch file path, or '-' for stdin."); p.add_argument("--repo-root", default="auto"); p.add_argument("--check-only", action="store_true")
    p.set_defaults(func=cmd_apply_patch)

    p = sub.add_parser("record-change", help="Record a completed change and re-index listed paths.")
    p.add_argument("summary"); p.add_argument("paths", nargs="*"); p.add_argument("--repo-root", default="auto")
    p.set_defaults(func=cmd_record_change)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
