import io
from pathlib import Path

import pytest

from nexus.config import Settings
import nexus.tui.runtime as tui_runtime


class _FakeProcess:
    def __init__(self) -> None:
        self.stdout = io.StringIO("")
        self.stdin = io.StringIO()
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return 0 if self.returncode is None else self.returncode


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        bridge_ws_url="ws://127.0.0.1:8765",
        bridge_shared_secret="secret-123",
        cli_enabled=False,
    )


def test_start_stack_spawns_expected_processes_and_env(monkeypatch, tmp_path: Path):
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()

    controller = tui_runtime.RuntimeController(_settings(tmp_path), repo_root=tmp_path, bridge_dir=bridge_dir)
    monkeypatch.setattr(tui_runtime, "require_bridge_dir", lambda path: None)
    monkeypatch.setattr(tui_runtime, "require_npm", lambda: None)
    monkeypatch.setattr(controller, "_start_reader", lambda proc, source: None)

    calls: list[tuple[list[str], dict]] = []

    def fake_popen(cmd, **kwargs):  # noqa: ANN001
        calls.append((list(cmd), kwargs))
        return _FakeProcess()

    monkeypatch.setattr(tui_runtime.subprocess, "Popen", fake_popen)

    controller.start_stack()

    assert len(calls) == 2
    assert calls[0][0] == ["npm", "run", "dev"]
    assert calls[1][0] == [tui_runtime.sys.executable, "-m", "nexus.app"]

    bridge_env = calls[0][1]["env"]
    assert bridge_env["BRIDGE_HOST"] == "127.0.0.1"
    assert bridge_env["BRIDGE_PORT"] == "8765"
    assert bridge_env["BRIDGE_QR_MODE"] == "terminal"
    assert bridge_env["BRIDGE_EXIT_ON_CONNECT"] == "0"

    core_env = calls[1][1]["env"]
    assert core_env["NEXUS_CLI_ENABLED"] == "true"
    assert core_env["NEXUS_CLI_PROMPT"] == ""
    assert core_env["PYTHONUNBUFFERED"] == "1"


def test_stop_stack_terminates_running_processes(monkeypatch, tmp_path: Path):
    controller = tui_runtime.RuntimeController(_settings(tmp_path), repo_root=tmp_path, bridge_dir=tmp_path / "bridge")
    bridge = _FakeProcess()
    core = _FakeProcess()
    controller._bridge_proc = bridge
    controller._core_proc = core
    monkeypatch.setattr(controller, "_join_reader", lambda reader, timeout=2.0: None)

    controller.stop_stack()

    assert bridge.terminated
    assert core.terminated
    assert controller._bridge_proc is None
    assert controller._core_proc is None


def test_whatsapp_connect_sets_one_shot_env(monkeypatch, tmp_path: Path):
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()

    controller = tui_runtime.RuntimeController(_settings(tmp_path), repo_root=tmp_path, bridge_dir=bridge_dir)
    monkeypatch.setattr(tui_runtime, "require_bridge_dir", lambda path: None)
    monkeypatch.setattr(tui_runtime, "require_npm", lambda: None)
    monkeypatch.setattr(controller, "_start_reader", lambda proc, source: None)
    monkeypatch.setattr(controller, "_watch_connect_process", lambda proc, timeout: None)

    captured: dict = {}

    def fake_popen(cmd, **kwargs):  # noqa: ANN001
        captured["cmd"] = list(cmd)
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(tui_runtime.subprocess, "Popen", fake_popen)

    controller.start_whatsapp_connect(timeout=301, exit_delay_ms=61000, session_dir=str(tmp_path / "wa"))

    assert captured["cmd"] == ["npm", "run", "dev"]
    env = captured["kwargs"]["env"]
    assert env["BRIDGE_QR_MODE"] == "terminal"
    assert env["BRIDGE_EXIT_ON_CONNECT"] == "1"
    assert env["BRIDGE_EXIT_ON_CONNECT_DELAY_MS"] == "61000"
    assert env["BRIDGE_SESSION_DIR"] == str(tmp_path / "wa")


def test_start_stack_fails_if_npm_missing(monkeypatch, tmp_path: Path):
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    controller = tui_runtime.RuntimeController(_settings(tmp_path), repo_root=tmp_path, bridge_dir=bridge_dir)
    monkeypatch.setattr(tui_runtime, "require_bridge_dir", lambda path: None)
    monkeypatch.setattr(tui_runtime, "require_npm", lambda: (_ for _ in ()).throw(RuntimeError("npm missing")))

    with pytest.raises(RuntimeError, match="npm missing"):
        controller.start_stack()


def test_start_stack_fails_if_bridge_port_in_use(monkeypatch, tmp_path: Path):
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    controller = tui_runtime.RuntimeController(_settings(tmp_path), repo_root=tmp_path, bridge_dir=bridge_dir)
    monkeypatch.setattr(tui_runtime, "require_bridge_dir", lambda path: None)
    monkeypatch.setattr(tui_runtime, "require_npm", lambda: None)
    monkeypatch.setattr(tui_runtime, "is_bridge_running", lambda host, port: True)

    with pytest.raises(RuntimeError, match="bridge port already in use"):
        controller.start_stack()


def test_whatsapp_connect_fails_if_bridge_port_in_use(monkeypatch, tmp_path: Path):
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    controller = tui_runtime.RuntimeController(_settings(tmp_path), repo_root=tmp_path, bridge_dir=bridge_dir)
    monkeypatch.setattr(tui_runtime, "require_bridge_dir", lambda path: None)
    monkeypatch.setattr(tui_runtime, "require_npm", lambda: None)
    monkeypatch.setattr(tui_runtime, "is_bridge_running", lambda host, port: True)

    with pytest.raises(RuntimeError, match="bridge port already in use"):
        controller.start_whatsapp_connect()


def test_whatsapp_disconnect_checks_bridge_status(monkeypatch, tmp_path: Path):
    controller = tui_runtime.RuntimeController(_settings(tmp_path), repo_root=tmp_path, bridge_dir=tmp_path / "bridge")
    controller.whatsapp_status = lambda session_dir=None: {  # type: ignore[method-assign]
        "bridge_running": True,
        "bridge_host": "127.0.0.1",
        "bridge_port": 8765,
        "session_dir": str(tmp_path / "bridge" / "session"),
    }
    monkeypatch.setattr(tui_runtime, "is_bridge_running", lambda host, port: True)

    with pytest.raises(RuntimeError, match="bridge appears to be running"):
        controller.whatsapp_disconnect()
