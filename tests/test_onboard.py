from __future__ import annotations

from pathlib import Path

from nexus.config import Settings
from nexus.onboard import run_onboard


def _settings(tmp_path: Path, *, openrouter_key: str = "") -> Settings:
    return Settings(
        _env_file=None,
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        bridge_dir=tmp_path / "bridge",
        openrouter_api_key=openrouter_key,
        brave_api_key="",
    )


def _prepare_bridge_dir(path: Path) -> Path:
    (path / "src").mkdir(parents=True, exist_ok=True)
    (path / "package.json").write_text('{"name":"bridge"}\n', encoding="utf-8")
    (path / "src" / "server.ts").write_text("console.log('ok')\n", encoding="utf-8")
    return path


def test_onboard_interactive_writes_global_env_and_runs_npm(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    bridge_dir = _prepare_bridge_dir(settings.bridge_dir or (tmp_path / "bridge"))
    monkeypatch.setattr("nexus.onboard.require_npm", lambda: None)
    monkeypatch.setattr("nexus.onboard.ensure_bridge_runtime_dir", lambda settings, auto_prepare=True: bridge_dir)
    monkeypatch.setattr("nexus.onboard.parse_bridge_target", lambda ws_url: ("127.0.0.1", 8765))
    monkeypatch.setattr("nexus.onboard.is_bridge_running", lambda host, port: False)

    calls: list[tuple[list[str], str]] = []

    def _fake_run(cmd, cwd, check):  # noqa: ANN001
        calls.append((list(cmd), str(cwd)))
        return None

    monkeypatch.setattr("nexus.onboard.subprocess.run", _fake_run)

    answers = iter(["openrouter-key", "brave-key"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    rc = run_onboard(settings, non_interactive=False, assume_yes=True)
    assert rc == 0
    assert calls == [(["npm", "install"], str(bridge_dir))]

    env_path = settings.config_dir / ".env"
    assert env_path.exists()
    text = env_path.read_text(encoding="utf-8")
    assert "NEXUS_OPENROUTER_API_KEY=openrouter-key" in text
    assert "NEXUS_BRAVE_API_KEY=brave-key" in text
    assert f"NEXUS_BRIDGE_DIR={bridge_dir}" in text


def test_onboard_non_interactive_fails_without_required_key(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path, openrouter_key="")
    bridge_dir = _prepare_bridge_dir(settings.bridge_dir or (tmp_path / "bridge"))
    monkeypatch.setattr("nexus.onboard.require_npm", lambda: None)
    monkeypatch.setattr("nexus.onboard.ensure_bridge_runtime_dir", lambda settings, auto_prepare=True: bridge_dir)

    rc = run_onboard(settings, non_interactive=True, assume_yes=True)
    assert rc == 1
