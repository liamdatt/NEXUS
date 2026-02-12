from pathlib import Path

from nexus.config import Settings
import nexus.runtime_helpers as runtime_helpers


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")


def test_resolve_bridge_dir_precedence(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)

    explicit = tmp_path / "custom-bridge"
    explicit.mkdir(parents=True)
    settings.bridge_dir = explicit.resolve()
    assert runtime_helpers.resolve_bridge_dir(settings) == explicit.resolve()

    settings.bridge_dir = None
    repo_bridge = tmp_path / "repo" / "bridge"
    repo_bridge.mkdir(parents=True)
    monkeypatch.setattr(runtime_helpers, "DEFAULT_REPO_BRIDGE_DIR", repo_bridge)
    assert runtime_helpers.resolve_bridge_dir(settings) == repo_bridge.resolve()

    missing_repo = tmp_path / "missing-repo" / "bridge"
    monkeypatch.setattr(runtime_helpers, "DEFAULT_REPO_BRIDGE_DIR", missing_repo)
    assert runtime_helpers.resolve_bridge_dir(settings) == (settings.data_dir / "bridge").resolve()


def test_prepare_bridge_runtime_copies_packaged_assets(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    target = settings.data_dir / "bridge"
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "package.json").write_text('{"name":"bridge"}\n', encoding="utf-8")
    (template / "src" / "server.ts").write_text("console.log('ok')\n", encoding="utf-8")
    (template / "node_modules" / ".bin").mkdir(parents=True)
    (template / "node_modules" / ".bin" / "tsx").write_text("#!/usr/bin/env node\n", encoding="utf-8")

    monkeypatch.setattr(runtime_helpers, "_bridge_runtime_template_dir", lambda: template)

    bridge_dir = runtime_helpers.prepare_bridge_runtime(settings, target_dir=target)
    assert bridge_dir == target.resolve()
    assert (bridge_dir / "package.json").exists()
    assert (bridge_dir / "src" / "server.ts").exists()
