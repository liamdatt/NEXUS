from __future__ import annotations

import json
from pathlib import Path

from nexus.config import Settings
from nexus.integrations import google_auth


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        google_client_secret_path=tmp_path / "google" / "client_secret.json",
        google_token_path=tmp_path / "google" / "token.json",
    )


def test_google_connect_writes_token_file(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    settings.google_client_secret_path.parent.mkdir(parents=True, exist_ok=True)
    settings.google_client_secret_path.write_text('{"installed":{"client_id":"x"}}', encoding="utf-8")

    class _Creds:
        def to_json(self):
            return json.dumps({"token": "abc"})

    class _Flow:
        @classmethod
        def from_client_secrets_file(cls, filename, scopes):  # noqa: ANN001
            return cls()

        def run_local_server(self, port=0, open_browser=True):  # noqa: ANN001
            return _Creds()

    monkeypatch.setattr(google_auth, "_require_google_auth_libs", lambda: (object, object, _Flow))

    msg = google_auth.connect_google(settings)

    assert "Google auth connected" in msg
    assert settings.google_token_path.exists()
    assert json.loads(settings.google_token_path.read_text(encoding="utf-8"))["token"] == "abc"


def test_google_status_reports_disconnected_without_token(tmp_path: Path):
    settings = _settings(tmp_path)
    settings.google_client_secret_path.parent.mkdir(parents=True, exist_ok=True)
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")

    status = google_auth.google_auth_status(settings)

    assert status["client_secret_exists"] is True
    assert status["token_exists"] is False
    assert status["connected"] is False


def test_google_disconnect_removes_token(tmp_path: Path):
    settings = _settings(tmp_path)
    settings.google_token_path.parent.mkdir(parents=True, exist_ok=True)
    settings.google_token_path.write_text("{}", encoding="utf-8")

    msg = google_auth.disconnect_google(settings)

    assert "deleted" in msg.lower()
    assert not settings.google_token_path.exists()
