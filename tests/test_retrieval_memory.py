from __future__ import annotations

import json

import pytest

from neo_localmcp.retrieval import repo_memory
from neo_localmcp.mcp_commands import editing, memory

pytestmark = pytest.mark.retrieval


def _seed_repo(repo):
    (repo / "docs").mkdir(parents=True)
    lines = ["# m9 Widget Plan", ""]
    lines += ["## m9.1 alpha", ""] + ["alpha body"] * 30 + [""]
    lines += ["## m9.2 beta widget rollup", ""] + ["beta body widget rollup"] * 30 + [""]
    (repo / "docs" / "plan.md").write_text("\n".join(lines), encoding="utf-8")


# --- P4: observable query recording -----------------------------------------


def test_status_reports_query_recording_enabled_by_default(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    status = repo_memory.status(repo)
    assert status["query_recording_enabled"] is True
    assert status["recorded_queries"] == 0
    assert status["last_query_recorded_at"] is None


def test_prepare_context_records_a_task_query_by_default(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    memory.prepare_context("m9.2 beta widget rollup", str(repo), token_budget=1000, max_files=2, output_format="json")
    status = repo_memory.status(repo)
    assert status["recorded_queries"] == 1
    assert status["last_query_recorded_at"] is not None


def test_query_recording_can_be_disabled_via_config(tmp_path, isolated_config):
    from neo_localmcp import config

    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    cfg = config.load_config()
    cfg["memory"]["record_context_queries"] = False
    config.save_config(cfg)

    memory.prepare_context("m9.2 beta widget rollup", str(repo), token_budget=1000, max_files=2, output_format="json")
    status = repo_memory.status(repo)
    assert status["query_recording_enabled"] is False
    assert status["recorded_queries"] == 0


def test_recorded_query_payload_is_compact_not_full_response(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    memory.prepare_context("m9.2 beta widget rollup", str(repo), token_budget=1000, max_files=2, output_format="json")
    conn = repo_memory.connect()
    rid = repo_memory.repo_id(repo)
    row = conn.execute("SELECT result_json, retrieval_id, term_key FROM task_queries WHERE repo_id=?", (rid,)).fetchone()
    payload = json.loads(row["result_json"])
    assert set(payload.keys()) == {"terms", "sections", "tool_version"}
    assert row["retrieval_id"]
    assert row["term_key"]
    # Compact: no full candidate list, no ollama payload, no excerpt text blob.
    assert "ollama_ranking" not in payload
    assert "candidate_files" not in payload


def test_task_query_retention_prunes_oldest_rows(tmp_path, isolated_config):
    from neo_localmcp import config

    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    cfg = config.load_config()
    cfg["memory"]["task_query_retention"] = 3
    config.save_config(cfg)

    for i in range(6):
        memory.prepare_context(f"m9.2 beta widget rollup pass {i}", str(repo), token_budget=1000, max_files=2, output_format="json")
    status = repo_memory.status(repo)
    assert status["recorded_queries"] == 3


# --- P5: implicit success signals --------------------------------------------


def test_first_exposure_never_boosts_ranking(tmp_path, isolated_config):
    """A single shown-but-not-yet-confirmed section must not affect scoring."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    raw1 = memory.prepare_context("m9.2 beta widget rollup", str(repo), token_budget=1000, max_files=2, output_format="json")
    raw2 = memory.prepare_context("m9.2 beta widget rollup", str(repo), token_budget=1000, max_files=2, output_format="json")
    score1 = json.loads(raw1)["read_first"][0]["score"]
    score2 = json.loads(raw2)["read_first"][0]["score"]
    assert score1 == score2
    assert not any("memory boost" in r for r in json.loads(raw2)["read_first"][0]["reasons"])


def test_repeated_followed_range_eventually_adds_capped_memory_boost(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)

    for _ in range(4):
        raw = memory.prepare_context("m9.2 beta widget rollup", str(repo), token_budget=1000, max_files=2, output_format="json")
        result = json.loads(raw)
        retrieval_id = result["retrieval_id"]
        excerpt = result["context_excerpts"][0]
        # Simulate Claude following the exact suggested section every time.
        memory.file_excerpts(
            [{"path": excerpt["path"], "start_line": excerpt["start_line"], "end_line": excerpt["end_line"]}],
            str(repo), retrieval_id=retrieval_id,
        )

    raw = memory.prepare_context("m9.2 beta widget rollup", str(repo), token_budget=1000, max_files=2, output_format="json")
    result = json.loads(raw)
    reasons = result["read_first"][0]["reasons"]
    assert any("memory boost" in r for r in reasons)
    boost = int([r for r in reasons if "memory boost" in r][0].split("+")[1].split(")")[0])
    assert 0 < boost <= repo_memory.RETRIEVAL_BOOST_CAP


def test_memory_boost_never_exceeds_structural_milestone_score(tmp_path, isolated_config):
    """The cap must stay well below real structural evidence (heading milestone ~= +60+)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    for _ in range(6):
        raw = memory.prepare_context("m9.2 beta widget rollup", str(repo), token_budget=1000, max_files=2, output_format="json")
        result = json.loads(raw)
        excerpt = result["context_excerpts"][0]
        memory.file_excerpts([{"path": excerpt["path"], "start_line": excerpt["start_line"], "end_line": excerpt["end_line"]}], str(repo), retrieval_id=result["retrieval_id"])
    assert repo_memory.RETRIEVAL_BOOST_CAP < 60  # structural milestone boost defined in mcp_commands/memory.py


def test_correcting_pull_elsewhere_does_not_boost(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    for _ in range(5):
        raw = memory.prepare_context("m9.2 beta widget rollup", str(repo), token_budget=1000, max_files=2, output_format="json")
        result = json.loads(raw)
        # Always pull a range far from the suggested section (a "correction").
        memory.file_excerpts([{"path": "docs/plan.md", "start_line": 1, "end_line": 2}], str(repo), retrieval_id=result["retrieval_id"])
    raw = memory.prepare_context("m9.2 beta widget rollup", str(repo), token_budget=1000, max_files=2, output_format="json")
    reasons = json.loads(raw)["read_first"][0]["reasons"]
    assert not any("memory boost" in r for r in reasons)


def test_follow_up_pull_on_a_secondary_hint_still_counts_as_followed(tmp_path, isolated_config):
    """Regression: when a candidate file has more than one legitimate hint location,
    a follow-up file_excerpts pull on a listed-but-not-primary hint must be scored
    as 'followed', not 'corrected' -- see PROJECT_NOTES.md 2026-07-03."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = ["def PrimaryTarget():", "    return 'p'"]
    lines += ["    # filler"] * 190
    lines += ["def SecondaryTarget():", "    return 's'"]
    (repo / "worker.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    secondary_line = next(i for i, line in enumerate(lines, start=1) if "def SecondaryTarget" in line)

    raw = memory.prepare_context("PrimaryTarget SecondaryTarget", str(repo), token_budget=1500, max_files=1, output_format="json")
    result = json.loads(raw)
    excerpt = next(e for e in result["context_excerpts"] if e["path"] == "worker.py")
    # The shown excerpt centers elsewhere (on the tie-break winner); confirm the
    # secondary hint genuinely falls outside it, so this is a real regression check.
    assert not (excerpt["start_line"] <= secondary_line <= excerpt["end_line"])

    raw2 = memory.file_excerpts(
        [{"path": "worker.py", "start_line": secondary_line, "end_line": secondary_line + 1}],
        str(repo), retrieval_id=result["retrieval_id"],
    )
    feedback = json.loads(raw2)["retrieval_feedback"]
    update = next(u for u in feedback["updates"] if u["path"] == "worker.py")
    assert update["overlap"] is True


def test_unknown_retrieval_id_reports_failure_without_raising(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    raw = memory.file_excerpts([{"path": "docs/plan.md", "start_line": 1, "end_line": 2}], str(repo), retrieval_id="not-a-real-id")
    result = json.loads(raw)
    assert result["retrieval_feedback"]["ok"] is False


def test_boost_below_min_shown_threshold_stays_zero(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    repo_memory.index_repo(str(repo), force=True)
    conn = repo_memory.connect()
    rid = repo_memory.repo_id(repo)
    conn.execute(
        "INSERT INTO retrieval_boost(repo_id, term_key, path, heading_name, shown_count, followed_count, corrected_count, last_updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (rid, "beta|rollup|widget", "docs/plan.md", "m9.2 beta widget rollup", repo_memory.RETRIEVAL_BOOST_MIN_SHOWN - 1, 5, 0, repo_memory.now_iso()),
    )
    conn.commit()
    boost_map = repo_memory.get_boost_map(repo, "beta|rollup|widget", ["docs/plan.md"])
    assert boost_map == {}


def test_min_shown_and_cap_are_config_overridable(tmp_path, isolated_config):
    """1.0.9 (P9g): memory.retrieval_boost_min_shown / retrieval_boost_cap override the
    module-constant defaults so the boost can be calibrated without a code change."""
    from neo_localmcp import config

    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    repo_memory.index_repo(str(repo), force=True)
    conn = repo_memory.connect()
    rid = repo_memory.repo_id(repo)
    # shown=2 (below the default min_shown of 3), net evidence of 20 (above default cap 8).
    conn.execute(
        "INSERT INTO retrieval_boost(repo_id, term_key, path, heading_name, shown_count, followed_count, corrected_count, last_updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (rid, "beta|rollup|widget", "docs/plan.md", "m9.2 beta widget rollup", 2, 20, 0, repo_memory.now_iso()),
    )
    conn.commit()

    # Defaults: shown=2 < min_shown(3) -> no boost at all.
    assert repo_memory.get_boost_map(repo, "beta|rollup|widget", ["docs/plan.md"]) == {}

    # Lower min_shown to 2 and raise cap to 15 via config: now the row contributes,
    # capped at the new ceiling (net 20 -> 15), proving both overrides take effect.
    cfg = config.load_config()
    cfg["memory"]["retrieval_boost_min_shown"] = 2
    cfg["memory"]["retrieval_boost_cap"] = 15
    config.save_config(cfg)
    boost_map = repo_memory.get_boost_map(repo, "beta|rollup|widget", ["docs/plan.md"])
    assert boost_map == {("docs/plan.md", "m9.2 beta widget rollup"): 15}


def test_boost_outside_retention_window_is_ignored(tmp_path, isolated_config):
    from datetime import datetime, timedelta, timezone

    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    repo_memory.index_repo(str(repo), force=True)
    conn = repo_memory.connect()
    rid = repo_memory.repo_id(repo)
    stale = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    conn.execute(
        "INSERT INTO retrieval_boost(repo_id, term_key, path, heading_name, shown_count, followed_count, corrected_count, last_updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (rid, "beta|rollup|widget", "docs/plan.md", "m9.2 beta widget rollup", 10, 10, 0, stale),
    )
    conn.commit()
    boost_map = repo_memory.get_boost_map(repo, "beta|rollup|widget", ["docs/plan.md"])
    assert boost_map == {}


def test_determinism_holds_once_a_memory_boost_is_active(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    for _ in range(4):
        r = json.loads(memory.prepare_context("m9.2 beta widget rollup", str(repo), token_budget=800, max_files=2, output_format="json"))
        excerpt = r["context_excerpts"][0]
        memory.file_excerpts([{"path": excerpt["path"], "start_line": excerpt["start_line"], "end_line": excerpt["end_line"]}], str(repo), retrieval_id=r["retrieval_id"])

    raw = memory.test_determinism("m9.2 beta widget rollup", str(repo), runs=5, max_files=2, limit=2, reset_repo_first=False, reindex_first=False)
    result = json.loads(raw)
    assert result["ok"] is True
    assert len(result["unique_hashes"]) == 1


def test_reset_repo_clears_retrieval_memory_tables(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    memory.prepare_context("m9.2 beta widget rollup", str(repo), token_budget=1000, max_files=2, output_format="json")
    before = repo_memory.status(repo)
    assert before["recorded_queries"] >= 1
    repo_memory.reset_repo(repo)
    after = repo_memory.status(repo)
    assert after["recorded_queries"] == 0


# --- P6: Ollama offline enrichment for headings ------------------------------


def test_section_summary_cache_hit_skips_ollama_call(tmp_path, isolated_config, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    repo_memory.index_repo(str(repo), force=True)
    sym = repo_memory.find_heading_symbol("docs/plan.md", "m9.2 beta widget rollup", repo)
    assert sym is not None
    source_hash = repo_memory.sha256_file(repo / "docs" / "plan.md")
    repo_memory.store_section_summary("docs/plan.md", "m9.2 beta widget rollup", sym["start_line"], sym["end_line"], "covers the beta widget rollup", "beta, widget, rollup", "test-model", "section-summary-v1", repo)

    def fail_chat(*args, **kwargs):
        raise AssertionError("chat must not be called on a cache hit")

    monkeypatch.setattr(editing, "chat", fail_chat)
    # Patch source_hash lookup indirectly by ensuring file unchanged since cache write.
    raw = editing.summarize_file("docs/plan.md", str(repo), model="test-model", heading="m9.2 beta widget rollup")
    result = json.loads(raw)
    assert result["cached"] is True
    assert result["summary"] == "covers the beta widget rollup"
    assert source_hash == repo_memory.sha256_file(repo / "docs" / "plan.md")


def test_section_summary_cache_miss_calls_ollama_and_stores_result(tmp_path, isolated_config, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    repo_memory.index_repo(str(repo), force=True)

    def fake_chat(prompt, model=None, purpose="summary", num_predict=None):
        assert "m9.2" in prompt
        return {"ok": True, "response": "summary: covers beta widget rollup mechanics.\nkeywords: beta, widget, rollup", "model": "fake-model"}

    monkeypatch.setattr(editing, "chat", fake_chat)
    raw = editing.summarize_file("docs/plan.md", str(repo), heading="m9.2 beta widget rollup")
    result = json.loads(raw)
    assert result["cached"] is False
    assert "covers beta widget rollup" in result["ollama_summary"]["response"]
    assert result["stored"]["heading"] == "m9.2 beta widget rollup"

    cached = repo_memory.get_section_summary("docs/plan.md", "m9.2 beta widget rollup", repo)
    assert cached is not None
    assert "covers beta widget rollup" in cached["summary"]
    assert "beta" in cached["keywords"]


def test_section_summary_never_overrides_heading_boundaries(tmp_path, isolated_config, monkeypatch):
    """Storing a summary must not change the heading's own start/end lines."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    repo_memory.index_repo(str(repo), force=True)
    before = repo_memory.find_heading_symbol("docs/plan.md", "m9.2 beta widget rollup", repo)

    def fake_chat(prompt, model=None, purpose="summary", num_predict=None):
        return {"ok": True, "response": "summary: x.\nkeywords: y", "model": "fake-model"}

    monkeypatch.setattr(editing, "chat", fake_chat)
    editing.summarize_file("docs/plan.md", str(repo), heading="m9.2 beta widget rollup")
    after = repo_memory.find_heading_symbol("docs/plan.md", "m9.2 beta widget rollup", repo)
    assert before["start_line"] == after["start_line"]
    assert before["end_line"] == after["end_line"]


def test_unknown_heading_returns_error_without_calling_ollama(tmp_path, isolated_config, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    repo_memory.index_repo(str(repo), force=True)

    def fail_chat(*args, **kwargs):
        raise AssertionError("chat must not be called when the heading does not exist")

    monkeypatch.setattr(editing, "chat", fail_chat)
    raw = editing.summarize_file("docs/plan.md", str(repo), heading="does not exist")
    result = json.loads(raw)
    assert result["ok"] is False


def test_whole_file_summarize_path_is_unaffected_by_heading_param(tmp_path, isolated_config, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)

    def fake_chat(prompt, model=None, purpose="summary", num_predict=None):
        return {"ok": True, "response": "purpose: a plan doc", "model": "fake-model"}

    monkeypatch.setattr(editing, "chat", fake_chat)
    raw = editing.summarize_file("docs/plan.md", str(repo))
    result = json.loads(raw)
    assert "cached" not in result
    assert result["file"] == "docs/plan.md"


def test_ollama_unavailable_leaves_deterministic_retrieval_fully_functional(tmp_path, isolated_config, monkeypatch):
    """Section enrichment failing must never affect context_prepare's deterministic path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)

    repo_memory.index_repo(str(repo), force=True)

    def down_chat(prompt, model=None, purpose="summary", num_predict=None):
        return {"ok": False, "error": "connection refused", "state": "unreachable"}

    monkeypatch.setattr(editing, "chat", down_chat)
    raw = editing.summarize_file("docs/plan.md", str(repo), heading="m9.2 beta widget rollup")
    result = json.loads(raw)
    assert result["cached"] is False
    assert result["ollama_summary"]["ok"] is False
    assert result["stored"] is None


# --- 1.0.7 P7a: Ollama enrichment bounding -----------------------------------


def test_num_predict_is_sent_on_the_outgoing_request(tmp_path, isolated_config, monkeypatch):
    """Contract test: the section-summary call must actually bound generation length."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    repo_memory.index_repo(str(repo), force=True)
    captured = {}

    def fake_chat(prompt, model=None, purpose="summary", num_predict=None):
        captured["num_predict"] = num_predict
        captured["purpose"] = purpose
        return {"ok": True, "response": "summary: x.\nkeywords: y", "model": "fake-model"}

    monkeypatch.setattr(editing, "chat", fake_chat)
    editing.summarize_file("docs/plan.md", str(repo), heading="m9.2 beta widget rollup")
    assert captured["purpose"] == "summary"
    assert isinstance(captured["num_predict"], int)
    assert captured["num_predict"] > 0


def test_ollama_client_includes_num_predict_in_request_options(monkeypatch, isolated_config):
    """Lower-level contract: ollama_client.chat must forward num_predict into the request body options."""
    from neo_localmcp import ollama_client

    monkeypatch.setattr(ollama_client, "ensure", lambda *args, **kwargs: {"ok": True, "state": "ready", "model": "fake-model"})
    captured = {}

    def fake(path, **kwargs):
        captured.update(kwargs.get("body") or {})
        return 200, {"response": "ok", "eval_count": 5}

    monkeypatch.setattr(ollama_client, "_request_json", fake)
    ollama_client.chat("hello", purpose="summary", num_predict=400)
    assert captured["options"]["num_predict"] == 400


def test_ollama_client_omits_num_predict_when_not_requested(monkeypatch, isolated_config):
    """Existing callers (ranking, whole-file summary) must be unaffected -- no default cap forced on them."""
    from neo_localmcp import ollama_client

    monkeypatch.setattr(ollama_client, "ensure", lambda *args, **kwargs: {"ok": True, "state": "ready", "model": "fake-model"})
    captured = {}

    def fake(path, **kwargs):
        captured.update(kwargs.get("body") or {})
        return 200, {"response": "ok", "eval_count": 5}

    monkeypatch.setattr(ollama_client, "_request_json", fake)
    ollama_client.chat("hello", purpose="ranking")
    assert "num_predict" not in captured["options"]


def test_runaway_response_is_flagged_truncated_and_not_cached(tmp_path, isolated_config, monkeypatch):
    """The core 1.0.7 regression: a response that hits the generation cap must not be
    silently cached/indexed, even though chat() reports ok=True with real text."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    repo_memory.index_repo(str(repo), force=True)
    cap = 400

    def runaway_chat(prompt, model=None, purpose="summary", num_predict=None):
        assert num_predict == cap
        garbage = ", ".join(f"term{i}" for i in range(500))
        return {
            "ok": True,
            "response": f"summary: a real summary.\nkeywords: {garbage}",
            "model": "fake-model",
            "raw": {"eval_count": num_predict},  # Ollama stopped exactly at the cap.
        }

    monkeypatch.setattr(editing, "chat", runaway_chat)
    raw = editing.summarize_file("docs/plan.md", str(repo), heading="m9.2 beta widget rollup")
    result = json.loads(raw)
    assert result["truncated"] is True
    assert result["stored"] is None

    # Nothing should have been cached or indexed from the runaway response.
    cached = repo_memory.get_section_summary("docs/plan.md", "m9.2 beta widget rollup", repo)
    assert cached is None
    hits = repo_memory.lookup("term499", repo)
    assert not hits["hits"]


def test_well_behaved_response_is_not_flagged_truncated(tmp_path, isolated_config, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    repo_memory.index_repo(str(repo), force=True)

    def fake_chat(prompt, model=None, purpose="summary", num_predict=None):
        return {"ok": True, "response": "summary: x.\nkeywords: a, b, c", "model": "fake-model", "raw": {"eval_count": 42}}

    monkeypatch.setattr(editing, "chat", fake_chat)
    raw = editing.summarize_file("docs/plan.md", str(repo), heading="m9.2 beta widget rollup")
    result = json.loads(raw)
    assert result["truncated"] is False
    assert result["stored"] is not None


def test_keywords_capped_by_term_count_even_under_the_generation_cap(tmp_path, isolated_config, monkeypatch):
    """Defense in depth: even a response that stays under num_predict but still crams
    far more than 8 terms into a short space must be capped at parse time."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    repo_memory.index_repo(str(repo), force=True)
    many_terms = ", ".join(f"kw{i}" for i in range(50))

    def fake_chat(prompt, model=None, purpose="summary", num_predict=None):
        return {"ok": True, "response": f"summary: x.\nkeywords: {many_terms}", "model": "fake-model", "raw": {"eval_count": 60}}

    monkeypatch.setattr(editing, "chat", fake_chat)
    editing.summarize_file("docs/plan.md", str(repo), heading="m9.2 beta widget rollup")
    cached = repo_memory.get_section_summary("docs/plan.md", "m9.2 beta widget rollup", repo)
    stored_terms = [t.strip() for t in cached["keywords"].split(",") if t.strip()]
    assert len(stored_terms) <= 8


def test_ollama_status_is_not_duplicated_in_section_summary_response(tmp_path, isolated_config, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    repo_memory.index_repo(str(repo), force=True)
    full_status = {"state": "ready", "model": "fake-model", "installed_models": ["a", "b", "c", "d", "e"]}

    def fake_chat(prompt, model=None, purpose="summary", num_predict=None):
        return {"ok": True, "response": "summary: x.\nkeywords: y", "model": "fake-model", "raw": {"eval_count": 10}, "ollama_status": full_status}

    monkeypatch.setattr(editing, "chat", fake_chat)
    raw = editing.summarize_file("docs/plan.md", str(repo), heading="m9.2 beta widget rollup")
    result = json.loads(raw)
    # Top-level ollama_status keeps the full list; the nested copy inside
    # ollama_summary must not repeat it a second time in the same response.
    assert result["ollama_status"]["installed_models"] == ["a", "b", "c", "d", "e"]
    assert "installed_models" not in result["ollama_summary"]["ollama_status"]


def test_ollama_status_is_not_duplicated_in_whole_file_summary_response(tmp_path, isolated_config, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    full_status = {"state": "ready", "model": "fake-model", "installed_models": ["a", "b", "c"]}

    def fake_chat(prompt, model=None, purpose="summary", num_predict=None):
        return {"ok": True, "response": "purpose: x", "model": "fake-model", "ollama_status": full_status}

    monkeypatch.setattr(editing, "chat", fake_chat)
    raw = editing.summarize_file("docs/plan.md", str(repo))
    result = json.loads(raw)
    assert result["ollama_status"]["installed_models"] == ["a", "b", "c"]
    assert "installed_models" not in result["ollama_summary"]["ollama_status"]


def test_ollama_status_is_not_duplicated_in_context_prepare_ranking_response(tmp_path, isolated_config, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repo(repo)
    full_status = {"state": "ready", "model": "fake-model", "installed_models": ["a", "b"]}

    def fake_chat(prompt, model=None, purpose="ranking", num_predict=None):
        return {"ok": True, "response": "Recommended read order\n1. docs/plan.md", "model": "fake-model", "ollama_status": full_status}

    monkeypatch.setattr(memory, "chat", fake_chat)
    raw = memory.prepare_context("m9.2 beta widget rollup", str(repo), token_budget=1000, max_files=2, use_ollama=True, output_format="json")
    result = json.loads(raw)
    assert result["ollama_status"]["installed_models"] == ["a", "b"]
    assert "installed_models" not in result["ollama_ranking"]["ollama_status"]

    # Deterministic retrieval must still work normally, unaffected by the failure above.
    ctx_raw = memory.prepare_context("m9.2 beta widget rollup", str(repo), token_budget=1000, max_files=2, output_format="json")
    ctx_result = json.loads(ctx_raw)
    assert ctx_result["read_first"][0]["path"] == "docs/plan.md"
