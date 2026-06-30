from __future__ import annotations

import re
from pathlib import Path
from typing import Any

FILLER_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "could", "do", "does", "doing",
    "explain", "find", "files", "file", "flow", "for", "from", "give", "help", "how", "identify",
    "in", "into", "is", "it", "likely", "list", "locate", "me", "need", "of", "on", "or", "please",
    "read", "show", "tell", "that", "the", "these", "this", "to", "use", "what", "when", "where", "which",
    "why", "with", "we", "were", "we're", "you", "your", "repo", "repository", "project", "codebase",
    # Intent words should set intent, not become grep terms.
    "debug", "bug", "fix", "crash", "crashes", "error", "exception", "fail", "failing", "broken", "regression",
    "diagnose", "issue", "problem", "trace", "add", "build", "create", "implement", "feature", "support", "new",
    "extend", "refactor", "cleanup", "simplify", "rename", "split", "move", "rework", "test", "tests",
    "coverage", "assert", "spec", "unit", "integration", "overview", "understand", "summarize", "describe", "architecture", "map",
    # Planning nouns describe the requested answer shape, not repository entities.
    "goal", "goals", "decision", "decisions", "implementation", "phase", "phases", "constraint", "constraints", "breakdown", "entry-point",
}

INTENT_KEYWORDS: list[tuple[str, set[str]]] = [
    ("debug", {"debug", "bug", "fix", "crash", "crashes", "error", "exception", "fail", "failing", "broken", "regression", "diagnose", "issue", "problem", "trace"}),
    ("feature", {"add", "build", "create", "implement", "feature", "support", "new", "extend"}),
    ("refactor", {"refactor", "cleanup", "simplify", "rename", "split", "move", "rework"}),
    ("test", {"test", "tests", "coverage", "assert", "spec", "unit", "integration"}),
    ("explain", {"explain", "overview", "understand", "summarize", "describe", "architecture", "flow", "map"}),
]

SOURCE_EXTS = {".cs", ".xaml", ".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte", ".go", ".rs", ".java", ".kt", ".kts", ".swift", ".rb", ".php", ".sql", ".html", ".css", ".scss"}
DOC_EXTS = {".md", ".rst", ".txt", ".adoc"}
CONFIG_EXTS = {".json", ".xml", ".yml", ".yaml", ".toml", ".ini", ".csproj", ".sln", ".props", ".targets"}


def _split_focus(text: str) -> tuple[str, str]:
    if ":" not in text:
        return text.strip(), ""
    left, right = text.split(":", 1)
    return left.strip(), right.strip()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_.\\/-]{1,}", text)


def _is_symbol_like(token: str) -> bool:
    return bool(
        re.search(r"[A-Z][a-z0-9]+[A-Z_]", token)
        or re.search(r"[A-Za-z_][A-Za-z0-9_]*Async\b", token)
        or re.fullmatch(r"[A-Za-z]\d+(?:\.\d+)*", token)
        or "." in token
        or "/" in token
        or "\\" in token
    )


def _clean_term(token: str) -> str:
    return token.strip().strip("'\"`.,;()[]{}<>")


def infer_intent(task: str) -> str:
    lower_words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_'-]*", task)}
    scores: dict[str, int] = {}
    for intent, words in INTENT_KEYWORDS:
        scores[intent] = len(lower_words & words)
    best, score = max(scores.items(), key=lambda item: item[1])
    if score <= 0:
        return "context"
    # Debug/feature/refactor should beat explain if both are present, because source-first is safer for dev tasks.
    if scores.get("debug", 0):
        return "debug"
    if scores.get("feature", 0):
        return "feature"
    if scores.get("refactor", 0):
        return "refactor"
    if scores.get("test", 0):
        return "test"
    return best


def normalize_query(task: str) -> dict[str, Any]:
    natural, focus = _split_focus(task)
    raw_natural = [_clean_term(t) for t in _tokens(natural)]
    raw_focus = [_clean_term(t) for t in re.split(r"[,\s]+", focus) if _clean_term(t)]

    ignored: list[str] = []
    weak_terms: list[str] = []
    strong_terms: list[str] = []

    for term in raw_focus:
        # A colon is a useful focus hint, but prose after it is still prose. Do not
        # turn filler words such as "and" into high-weight repository searches.
        if term and (term.lower() not in FILLER_WORDS or _is_symbol_like(term)) and term not in strong_terms:
            strong_terms.append(term)

    for term in raw_natural:
        if not term:
            continue
        lw = term.lower()
        if lw in FILLER_WORDS or (len(term) < 3 and not _is_symbol_like(term)):
            if term not in ignored:
                ignored.append(term)
            continue
        if _is_symbol_like(term):
            if term not in strong_terms:
                strong_terms.append(term)
        elif term not in weak_terms:
            weak_terms.append(term)

    # Preserve useful original casing and keep the search bounded.
    search_terms = []
    for term in strong_terms + weak_terms:
        if term not in search_terms:
            search_terms.append(term)

    intent = infer_intent(task)
    if intent in {"debug", "feature", "refactor"}:
        ranking_policy = "source_first"
    elif intent == "test":
        ranking_policy = "tests_first"
    elif intent == "explain":
        ranking_policy = "orientation_then_source"
    else:
        ranking_policy = "balanced_source_leaning"

    return {
        "raw": task,
        "intent": intent,
        "topic": natural,
        "strong_terms": strong_terms[:16],
        "weak_terms": weak_terms[:16],
        "search_terms": search_terms[:20],
        "ignored_terms": ignored[:30],
        "hybrid_hint_used": bool(focus),
        "ranking_policy": ranking_policy,
        "preferred_query_style": "natural task plus known symbols/files, e.g. 'debug settings persistence: BackdropMaterial, LoadSettingsAsync, MainViewModel'",
    }


def classify_path(path: str) -> str:
    p = path.replace("\\", "/")
    lower = p.lower()
    name = Path(p).name.lower()
    suffix = Path(p).suffix.lower()
    if any(part in lower for part in ["/bin/", "/obj/", "/dist/", "/build/", "/node_modules/", "/generated/", "/.git/"]):
        return "generated"
    if name in {"agents.md", "claude.md"} or "/.github/instructions/" in lower or "/commands/" in lower:
        return "instructions"
    parts = re.split(r"[/_.\\-]+", lower)
    if ("test" in parts or "tests" in parts or "spec" in parts or "specs" in parts or name.endswith("tests.cs") or name.endswith("test.cs") or name.endswith(".test.ts") or name.endswith(".spec.ts") or name.endswith(".test.tsx") or name.endswith(".spec.tsx")):
        return "test"
    if name in {"project_status.md", "project_notes.md", "status.md", "notes.md", "readme.md"}:
        return "status" if name.startswith("project_") else "docs"
    if lower.startswith("docs/") or "/docs/" in lower or suffix in DOC_EXTS:
        return "docs"
    if suffix in SOURCE_EXTS:
        return "source"
    if suffix in CONFIG_EXTS:
        return "config"
    return "other"


def category_boost(category: str, intent: str) -> int:
    if intent in {"debug", "feature", "refactor"}:
        return {"source": 20, "test": 14, "config": 9, "status": 7, "docs": 2, "instructions": -8, "generated": -30, "other": 0}.get(category, 0)
    if intent == "test":
        return {"test": 22, "source": 14, "config": 5, "status": 4, "docs": 1, "instructions": -8, "generated": -30, "other": 0}.get(category, 0)
    if intent == "explain":
        return {"status": 16, "docs": 12, "source": 8, "test": 2, "config": 2, "instructions": -4, "generated": -30, "other": 0}.get(category, 0)
    return {"source": 12, "test": 8, "status": 8, "docs": 5, "config": 4, "instructions": -5, "generated": -30, "other": 0}.get(category, 0)


def extract_file_references(text: str) -> list[str]:
    refs: list[str] = []
    # Normal explicit paths.
    pattern = r"[A-Za-z0-9_./\\-]+\.(?:cs|xaml|py|ts|tsx|js|jsx|md|json|xml|csproj|sln|props|targets|yml|yaml|toml|sql|swift|go|rs|java|kt|kts)"
    for match in re.findall(pattern, text):
        cleaned = match.strip("'\"`.,;()[]{}<>").replace("\\", "/")
        if cleaned and cleaned not in refs:
            refs.append(cleaned)
    # Common shorthand: MainWindow.xaml(.cs)
    for match in re.findall(r"([A-Za-z0-9_./\\-]+\.xaml)\(\.cs\)", text):
        base = match.replace("\\", "/")
        for item in (base, base + ".cs"):
            if item not in refs:
                refs.append(item)
    return sorted(refs)
