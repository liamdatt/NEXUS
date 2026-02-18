from __future__ import annotations

import json
from pathlib import Path

from nexus.config import Settings
from nexus.integrations import google_auth


def _settings(tmp_path: Path, *, cli_enabled: bool = True) -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        google_client_secret_path=tmp_path / "google" / "client_secret.json",
        google_token_path=tmp_path / "google" / "token.json",
        cli_enabled=cli_enabled,
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


def test_load_google_credentials_prefers_token_embedded_scopes(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    settings.google_token_path.parent.mkdir(parents=True, exist_ok=True)
    settings.google_token_path.write_text(
        json.dumps(
            {
                "token": "abc",
                "refresh_token": "ref",
                "scopes": ["scope.one", "scope.two"],
            }
        ),
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    class _Creds:
        expired = False
        refresh_token = "ref"
        valid = True
        scopes = ["scope.one", "scope.two"]

        @classmethod
        def from_authorized_user_file(cls, filename, scopes):  # noqa: ANN001
            captured["filename"] = filename
            captured["scopes"] = list(scopes)
            return cls()

        def refresh(self, request):  # noqa: ANN001
            pass

        def to_json(self):
            return json.dumps({"token": "abc"})

    monkeypatch.setattr(google_auth, "_require_google_auth_libs", lambda: (object, _Creds, object))

    creds = google_auth.load_google_credentials(settings)

    assert creds.valid is True
    assert captured["scopes"] == ["scope.one", "scope.two"]


def test_load_google_credentials_missing_token_message_for_hosted_runtime(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path, cli_enabled=False)

    monkeypatch.setattr(google_auth, "_require_google_auth_libs", lambda: (object, object, object))

    try:
        google_auth.load_google_credentials(settings)
        assert False, "expected RuntimeError when token is missing"
    except RuntimeError as exc:
        assert "hosted dashboard" in str(exc).lower()


def test_google_scopes_include_workspace_suite() -> None:
    expected = {
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/contacts.readonly",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/documents",
    }
    assert expected.issubset(set(google_auth.GOOGLE_SCOPES))
