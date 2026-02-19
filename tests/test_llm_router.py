from pathlib import Path

from nexus.config import Settings
from nexus.llm.router import LLMRouter


def test_model_chain_prefixed_for_openrouter(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        openrouter_api_key="sk-test",
        llm_primary_model="google/gemini-3-flash-preview",
        llm_complex_model="openrouter/openai/gpt-4.1",
        llm_fallback_model="anthropic/claude-3.5-sonnet",
    )
    router = LLMRouter(settings)

    chain = router._model_chain(complex_task=False)
    assert chain[0] == "openrouter/google/gemini-3-flash-preview"
    assert chain[1] == "openrouter/anthropic/claude-3.5-sonnet"
    assert chain[2] == "openrouter/openai/gpt-4.1"


def test_model_chain_uses_nexus_model_override(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        openrouter_api_key="sk-test",
        model_override="anthropic/claude-sonnet-4.6",
        llm_primary_model="google/gemini-3-flash-preview",
        llm_complex_model="moonshotai/kimi-k2.5",
        llm_fallback_model="google/gemini-3-flash-preview",
    )
    router = LLMRouter(settings)

    chain = router._model_chain(complex_task=False)
    assert chain[0] == "openrouter/anthropic/claude-sonnet-4.6"
    assert chain[1] == "openrouter/anthropic/claude-sonnet-4.6"
    assert chain[2] == "openrouter/anthropic/claude-sonnet-4.6"
