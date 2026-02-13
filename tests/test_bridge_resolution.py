from pathlib import Path

from nexus.config import Settings
import nexus.runtime_helpers as runtime_helpers


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")


def _write_bridge_template_files(root: Path) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / ".env.example").write_text("BRIDGE_QR_MODE=url\n", encoding="utf-8")
    (root / "package.json").write_text('{"name":"bridge"}\n', encoding="utf-8")
    (root / "tsconfig.json").write_text('{"compilerOptions":{"strict":true}}\n', encoding="utf-8")
    (root / "src" / "protocol.ts").write_text("export const protocol = 'v1';\n", encoding="utf-8")
    (root / "src" / "server.ts").write_text("export const server = 'new';\n", encoding="utf-8")
    (root / "src" / "whatsapp.ts").write_text("export const wa = 'new';\n", encoding="utf-8")


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
    _write_bridge_template_files(template)

    monkeypatch.setattr(runtime_helpers, "_bridge_runtime_template_dir", lambda: template)

    bridge_dir = runtime_helpers.prepare_bridge_runtime(settings, target_dir=target)
    assert bridge_dir == target.resolve()
    assert (bridge_dir / "package.json").exists()
    assert (bridge_dir / "src" / "server.ts").exists()


def test_prepare_bridge_runtime_overwrites_drifted_template_files(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    target = settings.data_dir / "bridge"
    template = tmp_path / "template"
    _write_bridge_template_files(template)
    monkeypatch.setattr(runtime_helpers, "_bridge_runtime_template_dir", lambda: template)

    runtime_helpers.prepare_bridge_runtime(settings, target_dir=target)
    stale_path = target / "src" / "server.ts"
    stale_path.write_text("export const server = 'old';\n", encoding="utf-8")

    runtime_helpers.prepare_bridge_runtime(settings, target_dir=target)
    assert stale_path.read_text(encoding="utf-8") == "export const server = 'new';\n"


def test_prepare_bridge_runtime_skips_copy_when_up_to_date(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    target = settings.data_dir / "bridge"
    template = tmp_path / "template"
    _write_bridge_template_files(template)
    _write_bridge_template_files(target)
    monkeypatch.setattr(runtime_helpers, "_bridge_runtime_template_dir", lambda: template)

    def _fail_copy(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("copy should not be called for up-to-date bridge runtime")

    monkeypatch.setattr(runtime_helpers, "_copy_tree", _fail_copy)

    bridge_dir = runtime_helpers.prepare_bridge_runtime(settings, target_dir=target)
    assert bridge_dir == target.resolve()


def test_prepare_bridge_runtime_recopies_when_tracked_file_missing(monkeypatch, tmp_path: Path):
    settings = _settings(tmp_path)
    target = settings.data_dir / "bridge"
    template = tmp_path / "template"
    _write_bridge_template_files(template)
    monkeypatch.setattr(runtime_helpers, "_bridge_runtime_template_dir", lambda: template)

    runtime_helpers.prepare_bridge_runtime(settings, target_dir=target)
    missing_path = target / "src" / "whatsapp.ts"
    missing_path.unlink()

    runtime_helpers.prepare_bridge_runtime(settings, target_dir=target)
    assert missing_path.read_text(encoding="utf-8") == "export const wa = 'new';\n"
