from __future__ import annotations

from neo_localmcp import config
from neo_localmcp.installer import configure_models


def test_configure_models_sets_only_given_fields(isolated_config):
    config.save_config({**config.load_config(), "ollama": {
        "base_url": "http://127.0.0.1:11434", "fast_model": "old-fast", "summary_model": "old-summary",
    }})

    result = configure_models(fast_model="new-fast")

    assert result["fast_model"] == "new-fast"
    assert result["summary_model"] == "old-summary"
    assert result["base_url"] == "http://127.0.0.1:11434"


def test_configure_models_persists_to_disk(isolated_config):
    configure_models(base_url="http://example:1234/", summary_model="big-model")

    reloaded = config.load_config()["ollama"]
    assert reloaded["base_url"] == "http://example:1234"  # trailing slash stripped
    assert reloaded["summary_model"] == "big-model"


def test_configure_models_sets_num_ctx(isolated_config):
    result = configure_models(num_ctx=8192)

    assert result["num_ctx"] == 8192


def test_configure_models_with_nothing_given_is_a_noop(isolated_config):
    before = configure_models(fast_model="fast-a", summary_model="summary-a")

    after = configure_models()

    assert after == before


def test_configure_models_embed_model_is_tri_state(isolated_config):
    # None (omitted) -> keep current; a name -> enable; "" -> explicitly disable (store None).
    assert configure_models()["embed_model"] is None  # default unset

    assert configure_models(embed_model="nomic-embed-text")["embed_model"] == "nomic-embed-text"

    # omitting it keeps the enabled value
    assert configure_models(fast_model="x")["embed_model"] == "nomic-embed-text"

    # empty string disables it back to None
    assert configure_models(embed_model="")["embed_model"] is None
    assert config.load_config()["ollama"]["embed_model"] is None
