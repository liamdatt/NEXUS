from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


APP_NAME = "nexus"
PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = Path(user_config_dir(APP_NAME))
DEFAULT_DATA_DIR = Path(user_data_dir(APP_NAME))
DEFAULT_GLOBAL_ENV = DEFAULT_CONFIG_DIR / ".env"
DEFAULT_PROMPTS_DIR = PACKAGE_ROOT / "prompts"
DEFAULT_SKILLS_DIR = PACKAGE_ROOT / "skills"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(DEFAULT_GLOBAL_ENV), ".env"),
        env_prefix="NEXUS_",
        extra="ignore",
    )

    env: str = "dev"
    config_dir: Path = DEFAULT_CONFIG_DIR
    data_dir: Path = DEFAULT_DATA_DIR
    bridge_dir: Path | None = None
    onboard_noninteractive: bool = False

    db_path: Path | None = None
    workspace: Path | None = None
    memories_dir: Path | None = None

    bridge_ws_url: str = "ws://127.0.0.1:8765"
    bridge_shared_secret: str = ""

    llm_primary_model: str = "google/gemini-3-flash-preview"
    llm_complex_model: str = "google/gemini-3-flash-preview"
    llm_fallback_model: str = "google/gemini-3-flash-preview"
    llm_max_tokens: int = 1200
    llm_timeout_seconds: int = 45

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    brave_api_key: str = ""
    timezone: str = "America/Los_Angeles"
    google_client_secret_path: Path | None = None
    google_token_path: Path | None = None
    google_calendar_id: str = "primary"
    email_summary_max_results: int = 10

    agent_max_steps: int = 20
    prompts_dir: Path | None = None
    skills_dir: Path | None = None
    memory_recent_days: int = 5
    agent_observation_max_chars: int = 4000

    session_window_turns: int = 20
    max_memory_sections: int = 3
    search_timeout_seconds: int = 15

    cli_enabled: bool = True
    cli_prompt: str = "nexus> "
    tui_history_limit: int = 50

    redaction_patterns: tuple[str, ...] = Field(
        default=(
            r"\b\+?\d{8,15}\b",
            r"\b(?:sk|rk|pk|xoxb)-[A-Za-z0-9_-]{12,}\b",
            r"\b(?:OPENROUTER|OPENAI|ANTHROPIC|BRAVE)_[A-Z0-9_]*=?[A-Za-z0-9_-]{8,}\b",
            r"\bya29\.[A-Za-z0-9._-]+\b",
            r"\b1//[A-Za-z0-9._-]+\b",
        )
    )

    @model_validator(mode="after")
    def _resolve_paths(self) -> Settings:
        self.config_dir = self.config_dir.expanduser().resolve()
        self.data_dir = self.data_dir.expanduser().resolve()
        if self.bridge_dir is not None:
            self.bridge_dir = self.bridge_dir.expanduser().resolve()
        if self.db_path is None:
            self.db_path = self.data_dir / "nexus.db"
        else:
            self.db_path = self.db_path.expanduser().resolve()
        if self.workspace is None:
            self.workspace = self.data_dir / "workspace"
        else:
            self.workspace = self.workspace.expanduser().resolve()
        if self.memories_dir is None:
            self.memories_dir = self.data_dir / "memories"
        else:
            self.memories_dir = self.memories_dir.expanduser().resolve()
        if self.google_client_secret_path is None:
            self.google_client_secret_path = self.config_dir / "google" / "client_secret.json"
        else:
            self.google_client_secret_path = self.google_client_secret_path.expanduser().resolve()
        if self.google_token_path is None:
            self.google_token_path = self.config_dir / "google" / "token.json"
        else:
            self.google_token_path = self.google_token_path.expanduser().resolve()
        if self.prompts_dir is None:
            self.prompts_dir = DEFAULT_PROMPTS_DIR
        else:
            self.prompts_dir = self.prompts_dir.expanduser().resolve()
        if self.skills_dir is None:
            self.skills_dir = DEFAULT_SKILLS_DIR
        else:
            self.skills_dir = self.skills_dir.expanduser().resolve()
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.workspace.mkdir(parents=True, exist_ok=True)
    settings.memories_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.google_client_secret_path.parent.mkdir(parents=True, exist_ok=True)
    settings.google_token_path.parent.mkdir(parents=True, exist_ok=True)
    settings.prompts_dir.mkdir(parents=True, exist_ok=True)
    settings.skills_dir.mkdir(parents=True, exist_ok=True)
    return settings
