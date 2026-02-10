import io
import subprocess
from argparse import Namespace
from pathlib import Path

from nexus import cli_app
from nexus.config import Settings


class _FakeProcess:
    def __init__(self, wait_result: int = 0):
        self.stdout = io.StringIO("")
        self._wait_result = wait_result
        self.returncode: int | None = wait_result

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return self._wait_result


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        bridge_ws_url="ws://127.0.0.1:8765",
        bridge_shared_secret="change-me",
        cli_enabled=False,
    )


def test_parser_accepts_all_new_commands():
    parser = cli_app.build_parser()

    onboard_args = parser.parse_args(["onboard", "--non-interactive", "--yes"])
    assert onboard_args.command == "onboard"
    assert onboard_args.non_interactive is True
    assert onboard_args.yes is True

    doctor_args = parser.parse_args(["doctor"])
    assert doctor_args.command == "doctor"

    start_args = parser.parse_args(["start"])
    assert start_args.command == "start"

    tui_args = parser.parse_args(["tui"])
    assert tui_args.command == "tui"

    connect_args = parser.parse_args(["whatsapp", "connect"])
    assert connect_args.command == "whatsapp"
    assert connect_args.whatsapp_command == "connect"
    assert connect_args.timeout == 180
    assert connect_args.exit_delay_ms == 30000

    disconnect_args = parser.parse_args(["whatsapp", "disconnect", "--yes"])
    assert disconnect_args.command == "whatsapp"
    assert disconnect_args.whatsapp_command == "disconnect"
    assert disconnect_args.yes is True

    status_args = parser.parse_args(["whatsapp", "status", "--session-dir", "/tmp/wa"])
    assert status_args.command == "whatsapp"
    assert status_args.whatsapp_command == "status"
    assert status_args.session_dir == "/tmp/wa"

    auth_args = parser.parse_args(["auth", "google", "status"])
    assert auth_args.command == "auth"
    assert auth_args.auth_provider == "google"
    assert auth_args.google_auth_command == "status"


def test_start_launches_bridge_and_core_with_expected_env(monkeypatch, tmp_path: Path):
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    monkeypatch.setattr(cli_app, "BRIDGE_DIR", bridge_dir)
    monkeypatch.setattr(cli_app, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli_app, "_require_npm", lambda: None)
    monkeypatch.setattr(cli_app, "_is_bridge_running", lambda host, port: False)
    monkeypatch.setattr(cli_app, "_run_stack", lambda bridge_proc, core_proc: 0)

    calls: list[tuple[list[str], dict]] = []

    def fake_popen(cmd, **kwargs):  # noqa: ANN001
        calls.append((list(cmd), kwargs))
        return _FakeProcess(wait_result=0)

    monkeypatch.setattr(cli_app.subprocess, "Popen", fake_popen)

    rc = cli_app._cmd_start(Namespace(), _settings(tmp_path))

    assert rc == 0
    assert len(calls) == 2
    assert calls[0][0] == ["npm", "run", "dev"]
    assert calls[1][0] == [cli_app.sys.executable, "-m", "nexus.app"]

    bridge_env = calls[0][1]["env"]
    assert bridge_env["BRIDGE_HOST"] == "127.0.0.1"
    assert bridge_env["BRIDGE_PORT"] == "8765"
    assert bridge_env["BRIDGE_SHARED_SECRET"] == "change-me"
    assert bridge_env["BRIDGE_QR_MODE"] == "terminal"
    assert bridge_env["BRIDGE_EXIT_ON_CONNECT"] == "0"

    core_env = calls[1][1]["env"]
    assert core_env["NEXUS_CLI_ENABLED"] == "false"


def test_start_refuses_if_bridge_port_already_in_use(monkeypatch, tmp_path: Path):
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    monkeypatch.setattr(cli_app, "BRIDGE_DIR", bridge_dir)
    monkeypatch.setattr(cli_app, "_require_npm", lambda: None)
    monkeypatch.setattr(cli_app, "_is_bridge_running", lambda host, port: True)

    popen_calls: list[tuple] = []
    monkeypatch.setattr(cli_app.subprocess, "Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)))

    rc = cli_app._cmd_start(Namespace(), _settings(tmp_path))

    assert rc == 1
    assert popen_calls == []


def test_connect_uses_one_shot_env_and_handles_timeout(monkeypatch, tmp_path: Path):
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    monkeypatch.setattr(cli_app, "BRIDGE_DIR", bridge_dir)
    monkeypatch.setattr(cli_app, "_require_npm", lambda: None)
    monkeypatch.setattr(cli_app, "_is_bridge_running", lambda host, port: False)

    captured: dict = {}
    terminate_calls: list[str] = []

    class _TimeoutProcess:
        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired(cmd="npm run dev", timeout=timeout)

    def fake_popen(cmd, **kwargs):  # noqa: ANN001
        captured["cmd"] = list(cmd)
        captured["kwargs"] = kwargs
        return _TimeoutProcess()

    monkeypatch.setattr(cli_app.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli_app, "_terminate_process", lambda proc, name, grace_seconds=8.0: terminate_calls.append(name))

    args = Namespace(timeout=5, exit_delay_ms=25000, session_dir=str(tmp_path / "custom-session"))
    rc = cli_app._cmd_whatsapp_connect(args, _settings(tmp_path))

    assert rc == 1
    assert captured["cmd"] == ["npm", "run", "dev"]
    env = captured["kwargs"]["env"]
    assert env["BRIDGE_QR_MODE"] == "terminal"
    assert env["BRIDGE_EXIT_ON_CONNECT"] == "1"
    assert env["BRIDGE_EXIT_ON_CONNECT_DELAY_MS"] == "25000"
    assert env["BRIDGE_SESSION_DIR"] == str(tmp_path / "custom-session")
    assert terminate_calls == ["bridge"]


def test_connect_refuses_if_bridge_port_already_in_use(monkeypatch, tmp_path: Path):
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    monkeypatch.setattr(cli_app, "BRIDGE_DIR", bridge_dir)
    monkeypatch.setattr(cli_app, "_require_npm", lambda: None)
    monkeypatch.setattr(cli_app, "_is_bridge_running", lambda host, port: True)

    called: list[tuple] = []
    monkeypatch.setattr(cli_app.subprocess, "Popen", lambda *args, **kwargs: called.append((args, kwargs)))

    args = Namespace(timeout=5, exit_delay_ms=25000, session_dir=None)
    rc = cli_app._cmd_whatsapp_connect(args, _settings(tmp_path))

    assert rc == 1
    assert called == []


def test_disconnect_deletes_session_when_bridge_is_offline(monkeypatch, tmp_path: Path):
    bridge_dir = tmp_path / "bridge"
    session_dir = bridge_dir / "auth" / "session"
    session_dir.mkdir(parents=True)
    (session_dir / "creds.json").write_text("{}", encoding="utf-8")
    (bridge_dir / ".env").write_text("BRIDGE_SESSION_DIR=./auth/session\n", encoding="utf-8")

    monkeypatch.setattr(cli_app, "BRIDGE_DIR", bridge_dir)
    monkeypatch.setattr(cli_app, "_is_bridge_running", lambda host, port: False)

    args = Namespace(session_dir=None, yes=True)
    rc = cli_app._cmd_whatsapp_disconnect(args, _settings(tmp_path))

    assert rc == 0
    assert not session_dir.exists()


def test_disconnect_refuses_if_bridge_is_running(monkeypatch, tmp_path: Path):
    bridge_dir = tmp_path / "bridge"
    session_dir = bridge_dir / "session"
    session_dir.mkdir(parents=True)

    monkeypatch.setattr(cli_app, "BRIDGE_DIR", bridge_dir)
    monkeypatch.setattr(cli_app, "_is_bridge_running", lambda host, port: True)

    args = Namespace(session_dir=None, yes=True)
    rc = cli_app._cmd_whatsapp_disconnect(args, _settings(tmp_path))

    assert rc == 1
    assert session_dir.exists()


def test_resolve_session_dir_uses_relative_path_from_bridge_env(tmp_path: Path):
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    (bridge_dir / ".env").write_text("BRIDGE_SESSION_DIR=./state/wa\n", encoding="utf-8")

    resolved = cli_app._resolve_session_dir(bridge_dir, cli_value=None)

    assert resolved == (bridge_dir / "state" / "wa").resolve()
