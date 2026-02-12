from __future__ import annotations

import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from nexus.config import Settings
from nexus.integrations.google_auth import google_auth_status
from nexus.runtime_helpers import (
    bridge_runtime_dependencies_ready,
    ensure_bridge_runtime_dir,
    is_bridge_running,
    parse_bridge_target,
    require_npm,
)
from nexus.tui.envfile import EnvFile


def _bool_label(value: bool) -> str:
    return "yes" if value else "no"


def _prompt(prompt: str) -> str:
    return input(prompt).strip()


def _resolve_global_env_path(settings: Settings) -> Path:
    return settings.config_dir / ".env"


def _default_secret(settings: Settings) -> str:
    if settings.bridge_shared_secret:
        return settings.bridge_shared_secret
    return secrets.token_urlsafe(24)


def run_onboard(settings: Settings, *, non_interactive: bool = False, assume_yes: bool = False) -> int:
    del assume_yes  # retained for CLI compatibility and future prompt skipping.
    non_interactive = bool(non_interactive or settings.onboard_noninteractive)

    print("[nexus] onboarding setup")
    print(f"config_dir: {settings.config_dir}")
    print(f"data_dir: {settings.data_dir}")

    if sys.version_info < (3, 11):
        print("[nexus] Python 3.11+ is required.")
        return 1

    try:
        require_npm()
    except RuntimeError as exc:
        print(f"[nexus] {exc}")
        return 1

    settings.config_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.workspace.mkdir(parents=True, exist_ok=True)
    settings.memories_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.google_client_secret_path.parent.mkdir(parents=True, exist_ok=True)
    settings.google_token_path.parent.mkdir(parents=True, exist_ok=True)

    bridge_dir = ensure_bridge_runtime_dir(settings, auto_prepare=True)
    print(f"bridge_dir: {bridge_dir}")

    env_path = _resolve_global_env_path(settings)
    env = EnvFile.load(env_path)
    env.upsert("NEXUS_CONFIG_DIR", str(settings.config_dir))
    env.upsert("NEXUS_DATA_DIR", str(settings.data_dir))
    env.upsert("NEXUS_BRIDGE_DIR", str(bridge_dir))
    env.upsert("NEXUS_BRIDGE_SHARED_SECRET", env.get("NEXUS_BRIDGE_SHARED_SECRET") or _default_secret(settings))
    env.upsert("NEXUS_BRIDGE_WS_URL", env.get("NEXUS_BRIDGE_WS_URL") or settings.bridge_ws_url)
    env.upsert(
        "NEXUS_GOOGLE_CLIENT_SECRET_PATH",
        env.get("NEXUS_GOOGLE_CLIENT_SECRET_PATH") or str(settings.google_client_secret_path),
    )
    env.upsert(
        "NEXUS_GOOGLE_TOKEN_PATH",
        env.get("NEXUS_GOOGLE_TOKEN_PATH") or str(settings.google_token_path),
    )

    openrouter_key = env.get("NEXUS_OPENROUTER_API_KEY") or settings.openrouter_api_key
    if not openrouter_key and non_interactive:
        print("[nexus] Missing OpenRouter API key. Set NEXUS_OPENROUTER_API_KEY and rerun onboarding.")
        return 1
    if not openrouter_key:
        openrouter_key = _prompt("OpenRouter API key (required): ")
        if not openrouter_key:
            print("[nexus] OpenRouter API key is required.")
            return 1
    env.upsert("NEXUS_OPENROUTER_API_KEY", openrouter_key)

    brave_key = env.get("NEXUS_BRAVE_API_KEY") or settings.brave_api_key
    if not brave_key and not non_interactive:
        brave_key = _prompt("Brave API key (optional, press Enter to skip): ")
    if brave_key:
        env.upsert("NEXUS_BRAVE_API_KEY", brave_key)

    env.write(env_path)
    print(f"[nexus] wrote config: {env_path}")

    try:
        subprocess.run(["npm", "install", "--include=dev"], cwd=str(bridge_dir), check=True)
    except subprocess.CalledProcessError as exc:
        print(f"[nexus] bridge dependency install failed: {exc}")
        return 1

    if not bridge_runtime_dependencies_ready(bridge_dir):
        print("[nexus] bridge runtime is missing required dependencies after install (tsx not found).")
        return 1

    try:
        host, port = parse_bridge_target(settings.bridge_ws_url)
    except ValueError as exc:
        print(f"[nexus] {exc}")
        return 1
    bridge_running = is_bridge_running(host, port)
    print(f"bridge_host: {host}")
    print(f"bridge_port: {port}")
    print(f"bridge_running: {_bool_label(bridge_running)}")
    print("next_steps:")
    print("  1. nexus auth google connect")
    print("  2. nexus whatsapp connect")
    print("  3. nexus start")
    print("[nexus] onboarding complete.")
    return 0


def collect_doctor_status(settings: Settings) -> dict[str, Any]:
    env_path = _resolve_global_env_path(settings)
    bridge_dir = ensure_bridge_runtime_dir(settings, auto_prepare=False)
    npm_on_path = shutil.which("npm") is not None

    google_error = ""
    google = {
        "connected": False,
        "client_secret_exists": False,
        "token_exists": False,
        "client_secret_path": str(settings.google_client_secret_path),
        "token_path": str(settings.google_token_path),
    }
    try:
        google = google_auth_status(settings)
        google_error = str(google.get("error") or "")
    except Exception as exc:  # noqa: BLE001
        google_error = str(exc)

    host = ""
    port = 0
    parse_error = ""
    bridge_running = False
    try:
        host, port = parse_bridge_target(settings.bridge_ws_url)
        bridge_running = is_bridge_running(host, port)
    except Exception as exc:  # noqa: BLE001
        parse_error = str(exc)

    report: dict[str, Any] = {
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "config_dir": str(settings.config_dir),
        "config_env_file": str(env_path),
        "config_env_exists": env_path.exists(),
        "data_dir": str(settings.data_dir),
        "db_path": str(settings.db_path),
        "workspace": str(settings.workspace),
        "bridge_dir": str(bridge_dir),
        "bridge_dir_exists": bridge_dir.exists(),
        "bridge_runtime_ready": bridge_runtime_dependencies_ready(bridge_dir),
        "npm_on_path": npm_on_path,
        "openrouter_api_key_set": bool(settings.openrouter_api_key),
        "brave_api_key_set": bool(settings.brave_api_key),
        "google_client_secret_path": str(google.get("client_secret_path", settings.google_client_secret_path)),
        "google_client_secret_exists": bool(google.get("client_secret_exists")),
        "google_token_path": str(google.get("token_path", settings.google_token_path)),
        "google_token_exists": bool(google.get("token_exists")),
        "google_connected": bool(google.get("connected")),
        "bridge_host": host,
        "bridge_port": port,
        "bridge_running": bridge_running,
        "bridge_port_available": (not bridge_running) if not parse_error else False,
        "bridge_url_error": parse_error,
        "google_error": google_error,
    }
    doctor_ok = bool(
        report["npm_on_path"]
        and report["openrouter_api_key_set"]
        and report["bridge_runtime_ready"]
        and not parse_error
    )
    report["doctor_ok"] = doctor_ok
    return report


def run_doctor(settings: Settings) -> int:
    report = collect_doctor_status(settings)
    for key, value in report.items():
        if isinstance(value, bool):
            print(f"{key}: {_bool_label(value)}")
        else:
            print(f"{key}: {value}")

    if not report["doctor_ok"]:
        print("hints:")
        if not report["openrouter_api_key_set"]:
            print("  - set NEXUS_OPENROUTER_API_KEY (run `nexus onboard`)")
        if not report["npm_on_path"]:
            print("  - install Node.js and ensure `npm` is on PATH")
        if not report["bridge_runtime_ready"]:
            print("  - run `nexus onboard` to prepare bridge runtime assets")
        print("  - if `nexus` is not on PATH, run `python -m nexus.cli_app doctor`")
        return 1

    print("[nexus] doctor checks passed.")
    print("hint_run_module: python -m nexus.cli_app doctor")
    return 0
