from __future__ import annotations

from pathlib import Path

from nexus.config import Settings
from nexus.onboard import collect_doctor_status, run_doctor


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        bridge_dir=tmp_path / "bridge",
        openrouter_api_key="test-key",
    )


def _bridge_dir(path: Path) -> Path:
    (path / "src").mkdir(parents=True, exist_ok=True)
    (path / "package.json").write_text('{"name":"bridge"}\n', encoding="utf-8")
    (path / "src" / "server.ts").write_text("console.log('ok')\n", encoding="utf-8")
    (path / "node_modules" / ".bin").mkdir(parents=True, exist_ok=True)
    (path / "node_modules" / ".bin" / "tsx").write_text("#!/usr/bin/env node\n", encoding="utf-8")
    return path


def test_doctor_reports_stable_labels(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    bridge = _bridge_dir(settings.bridge_dir or (tmp_path / "bridge"))
    monkeypatch.setattr("nexus.onboard.ensure_bridge_runtime_dir", lambda settings, auto_prepare=False: bridge)
    monkeypatch.setattr("nexus.onboard.shutil.which", lambda exe: "/usr/bin/npm")
    monkeypatch.setattr("nexus.onboard.google_auth_status", lambda settings: {"connected": False, "client_secret_exists": False, "token_exists": False})
    monkeypatch.setattr("nexus.onboard.parse_bridge_target", lambda ws_url: ("127.0.0.1", 8765))
    monkeypatch.setattr("nexus.onboard.is_bridge_running", lambda host, port: False)

    report = collect_doctor_status(settings)
    expected_keys = {
        "python_executable",
        "python_version",
        "config_dir",
        "config_env_file",
        "config_env_exists",
        "data_dir",
        "db_path",
        "workspace",
        "bridge_dir",
        "bridge_dir_exists",
        "bridge_runtime_ready",
        "npm_on_path",
        "openrouter_api_key_set",
        "brave_api_key_set",
        "google_client_secret_path",
        "google_client_secret_exists",
        "google_token_path",
        "google_token_exists",
        "google_connected",
        "bridge_host",
        "bridge_port",
        "bridge_running",
        "bridge_port_available",
        "bridge_url_error",
        "google_error",
        "doctor_ok",
    }
    assert expected_keys.issubset(report.keys())


def test_doctor_returns_nonzero_when_required_checks_fail(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    settings.openrouter_api_key = ""
    bridge = _bridge_dir(settings.bridge_dir or (tmp_path / "bridge"))
    monkeypatch.setattr("nexus.onboard.ensure_bridge_runtime_dir", lambda settings, auto_prepare=False: bridge)
    monkeypatch.setattr("nexus.onboard.shutil.which", lambda exe: None)
    monkeypatch.setattr("nexus.onboard.google_auth_status", lambda settings: {"connected": False})
    monkeypatch.setattr("nexus.onboard.parse_bridge_target", lambda ws_url: ("127.0.0.1", 8765))
    monkeypatch.setattr("nexus.onboard.is_bridge_running", lambda host, port: False)

    assert run_doctor(settings) == 1
