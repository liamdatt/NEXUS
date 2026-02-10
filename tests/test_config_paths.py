from pathlib import Path

from nexus.config import Settings


def test_portable_defaults_derive_from_config_and_data_dirs(tmp_path: Path):
    config_dir = tmp_path / "cfg"
    data_dir = tmp_path / "data"
    settings = Settings(
        _env_file=None,
        config_dir=config_dir,
        data_dir=data_dir,
        db_path=None,
        workspace=None,
        memories_dir=None,
        google_client_secret_path=None,
        google_token_path=None,
        prompts_dir=None,
        skills_dir=None,
    )

    assert settings.db_path == data_dir / "nexus.db"
    assert settings.workspace == data_dir / "workspace"
    assert settings.memories_dir == data_dir / "memories"
    assert settings.google_client_secret_path == config_dir / "google" / "client_secret.json"
    assert settings.google_token_path == config_dir / "google" / "token.json"
    assert settings.prompts_dir.name == "prompts"
    assert settings.skills_dir.name == "skills"


def test_env_precedence_os_then_cwd_then_home(monkeypatch, tmp_path: Path):
    home_env = tmp_path / "home.env"
    cwd_env = tmp_path / "cwd.env"
    home_env.write_text("NEXUS_OPENROUTER_API_KEY=home-key\n", encoding="utf-8")
    cwd_env.write_text("NEXUS_OPENROUTER_API_KEY=cwd-key\n", encoding="utf-8")

    monkeypatch.delenv("NEXUS_OPENROUTER_API_KEY", raising=False)
    from_files = Settings(_env_file=(home_env, cwd_env))
    assert from_files.openrouter_api_key == "cwd-key"

    monkeypatch.setenv("NEXUS_OPENROUTER_API_KEY", "os-key")
    from_env = Settings(_env_file=(home_env, cwd_env))
    assert from_env.openrouter_api_key == "os-key"
