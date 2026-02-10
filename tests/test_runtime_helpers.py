from pathlib import Path

import pytest

from nexus.config import Settings
import nexus.runtime_helpers as runtime_helpers


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        bridge_ws_url="ws://127.0.0.1:8765",
        bridge_shared_secret="secret-123",
        cli_enabled=False,
    )


def test_parse_bridge_target_handles_explicit_and_default_ports():
    assert runtime_helpers.parse_bridge_target("ws://127.0.0.1:8765") == ("127.0.0.1", 8765)
    assert runtime_helpers.parse_bridge_target("ws://localhost") == ("localhost", 80)
    assert runtime_helpers.parse_bridge_target("wss://example.com") == ("example.com", 443)

    with pytest.raises(ValueError):
        runtime_helpers.parse_bridge_target("not-a-url")


def test_build_bridge_env_sets_expected_values(tmp_path: Path):
    env = runtime_helpers.build_bridge_env(_settings(tmp_path), qr_mode="terminal", exit_on_connect=True, exit_on_connect_delay_ms=4567)
    assert env["BRIDGE_HOST"] == "127.0.0.1"
    assert env["BRIDGE_PORT"] == "8765"
    assert env["BRIDGE_SHARED_SECRET"] == "secret-123"
    assert env["BRIDGE_QR_MODE"] == "terminal"
    assert env["BRIDGE_EXIT_ON_CONNECT"] == "1"
    assert env["BRIDGE_EXIT_ON_CONNECT_DELAY_MS"] == "4567"


def test_resolve_session_dir_prefers_cli_value_then_bridge_env(tmp_path: Path):
    bridge_dir = tmp_path / "bridge"
    bridge_dir.mkdir()
    (bridge_dir / ".env").write_text("BRIDGE_SESSION_DIR=./state/wa\n", encoding="utf-8")

    from_cli = runtime_helpers.resolve_session_dir(bridge_dir, str(tmp_path / "custom"))
    assert from_cli == (tmp_path / "custom").resolve()

    from_env = runtime_helpers.resolve_session_dir(bridge_dir, None)
    assert from_env == (bridge_dir / "state" / "wa").resolve()


def test_is_bridge_running_uses_socket_probe(monkeypatch):
    class _DummyConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    monkeypatch.setattr(runtime_helpers.socket, "create_connection", lambda addr, timeout=0.0: _DummyConn())
    assert runtime_helpers.is_bridge_running("127.0.0.1", 8765)

    def _raise(*args, **kwargs):  # noqa: ANN002, ANN003
        raise OSError("down")

    monkeypatch.setattr(runtime_helpers.socket, "create_connection", _raise)
    assert not runtime_helpers.is_bridge_running("127.0.0.1", 8765)
