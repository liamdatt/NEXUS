from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nexus.config import Settings


GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
)


def _require_google_auth_libs():
    try:
        from google.auth.transport.requests import Request  # noqa: PLC0415
        from google.oauth2.credentials import Credentials  # noqa: PLC0415
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Google auth dependencies are not installed. Install with `pip install -e .` "
            "(or `uv tool install nexus-ai`)."
        ) from exc
    return Request, Credentials, InstalledAppFlow


def _token_path(settings: Settings) -> Path:
    return settings.google_token_path.expanduser().resolve()


def _client_secret_path(settings: Settings) -> Path:
    return settings.google_client_secret_path.expanduser().resolve()


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _token_scopes(token_path: Path) -> list[str]:
    try:
        raw = json.loads(token_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return list(GOOGLE_SCOPES)
    if not isinstance(raw, dict):
        return list(GOOGLE_SCOPES)
    scopes = raw.get("scopes")
    if isinstance(scopes, list):
        scoped = [str(item) for item in scopes if isinstance(item, str) and item.strip()]
        if scoped:
            return scoped
    scope = raw.get("scope")
    if isinstance(scope, str):
        scoped = [chunk for chunk in scope.split() if chunk]
        if scoped:
            return scoped
    return list(GOOGLE_SCOPES)


def connect_google(settings: Settings) -> str:
    Request, Credentials, InstalledAppFlow = _require_google_auth_libs()
    del Request, Credentials  # keep interface explicit; only flow used during connect

    client_secret = _client_secret_path(settings)
    if not client_secret.exists():
        raise RuntimeError(f"Google client secret file not found: {client_secret}")

    token_path = _token_path(settings)
    _ensure_parent(token_path)

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), scopes=list(GOOGLE_SCOPES))
    try:
        creds = flow.run_local_server(port=0, open_browser=True)
    except Exception:
        # Fallback for headless/remote terminals.
        creds = flow.run_console()

    token_path.write_text(creds.to_json(), encoding="utf-8")
    return f"Google auth connected. Token saved to {token_path}"


def load_google_credentials(settings: Settings):
    Request, Credentials, _InstalledAppFlow = _require_google_auth_libs()

    token_path = _token_path(settings)
    if not token_path.exists():
        guidance = (
            "Connect Google from the hosted dashboard first."
            if not settings.cli_enabled
            else "Run `nexus auth google connect` first."
        )
        raise RuntimeError(
            f"Google token not found at {token_path}. {guidance}"
        )

    creds = Credentials.from_authorized_user_file(str(token_path), scopes=_token_scopes(token_path))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
    if not creds.valid:
        guidance = (
            "Reconnect Google from the hosted dashboard."
            if not settings.cli_enabled
            else "Run `nexus auth google connect` again."
        )
        raise RuntimeError(
            "Google credentials are invalid or expired without refresh token. "
            f"{guidance}"
        )
    return creds


def google_auth_status(settings: Settings) -> dict[str, Any]:
    status: dict[str, Any] = {
        "client_secret_path": str(_client_secret_path(settings)),
        "token_path": str(_token_path(settings)),
        "client_secret_exists": _client_secret_path(settings).exists(),
        "token_exists": _token_path(settings).exists(),
        "connected": False,
    }

    if not status["token_exists"]:
        return status

    try:
        creds = load_google_credentials(settings)
    except Exception as exc:  # noqa: BLE001
        status["error"] = str(exc)
        return status

    status["connected"] = bool(creds.valid)
    if getattr(creds, "expiry", None):
        expiry = creds.expiry
        if isinstance(expiry, datetime):
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            status["token_expiry"] = expiry.isoformat()
    status["scopes"] = list(getattr(creds, "scopes", []) or [])
    return status


def disconnect_google(settings: Settings) -> str:
    token_path = _token_path(settings)
    if not token_path.exists():
        return f"No Google token found at {token_path}"
    token_path.unlink()
    return f"Google token deleted: {token_path}"
