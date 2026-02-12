from __future__ import annotations

import os
import shutil
import socket
from importlib import resources
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from nexus.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPO_BRIDGE_DIR = REPO_ROOT / "bridge"


class _TraversableLike(Protocol):
    def is_dir(self) -> bool:
        ...

    def iterdir(self):
        ...

    def read_bytes(self) -> bytes:
        ...

    @property
    def name(self) -> str:
        ...


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        values[key] = value
    return values


def resolve_session_dir(bridge_dir: Path, cli_value: str | None) -> Path:
    if cli_value:
        raw_path = cli_value
    else:
        bridge_env = read_dotenv(bridge_dir / ".env")
        raw_path = bridge_env.get("BRIDGE_SESSION_DIR", "./session")
    session_dir = Path(raw_path).expanduser()
    if not session_dir.is_absolute():
        session_dir = bridge_dir / session_dir
    return session_dir.resolve()


def _bridge_runtime_template_dir():
    return resources.files("nexus").joinpath("bridge_runtime")


def _copy_tree(source: _TraversableLike, destination: Path) -> None:
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            _copy_tree(child, destination / child.name)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())


def resolve_bridge_dir(settings: Settings) -> Path:
    if settings.bridge_dir is not None:
        return settings.bridge_dir
    if DEFAULT_REPO_BRIDGE_DIR.exists():
        return DEFAULT_REPO_BRIDGE_DIR.resolve()
    return (settings.data_dir / "bridge").resolve()


def bridge_runtime_has_tsx(bridge_dir: Path) -> bool:
    local_bin = bridge_dir / "node_modules" / ".bin" / ("tsx.cmd" if os.name == "nt" else "tsx")
    if local_bin.exists():
        return True
    return shutil.which("tsx") is not None


def bridge_runtime_ready(bridge_dir: Path) -> bool:
    package_json = bridge_dir / "package.json"
    server_ts = bridge_dir / "src" / "server.ts"
    return package_json.exists() and server_ts.exists()


def bridge_runtime_dependencies_ready(bridge_dir: Path) -> bool:
    return bridge_runtime_ready(bridge_dir) and bridge_runtime_has_tsx(bridge_dir)


def prepare_bridge_runtime(
    settings: Settings,
    target_dir: Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    bridge_dir = (target_dir or resolve_bridge_dir(settings)).expanduser().resolve()
    if bridge_runtime_ready(bridge_dir) and not overwrite:
        return bridge_dir

    template_dir = _bridge_runtime_template_dir()
    if not template_dir.is_dir():
        raise RuntimeError("Packaged bridge runtime assets are missing.")

    if not bridge_dir.exists():
        bridge_dir.mkdir(parents=True, exist_ok=True)
    _copy_tree(template_dir, bridge_dir)
    return bridge_dir


def ensure_bridge_runtime_dir(settings: Settings, *, auto_prepare: bool = True) -> Path:
    bridge_dir = resolve_bridge_dir(settings)
    if bridge_runtime_ready(bridge_dir):
        return bridge_dir
    if auto_prepare:
        return prepare_bridge_runtime(settings, target_dir=bridge_dir)
    return bridge_dir


def parse_bridge_target(ws_url: str) -> tuple[str, int]:
    parsed = urlparse(ws_url)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError(f"Invalid NEXUS_BRIDGE_WS_URL: {ws_url}")
    if parsed.port is not None:
        return parsed.hostname, parsed.port
    if parsed.scheme == "ws":
        return parsed.hostname, 80
    if parsed.scheme == "wss":
        return parsed.hostname, 443
    raise ValueError(f"Unsupported bridge URL scheme: {parsed.scheme}")


def bridge_probe_host(host: str) -> str:
    if host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return host


def is_bridge_running(host: str, port: int, timeout_seconds: float = 0.6) -> bool:
    probe_host = bridge_probe_host(host)
    try:
        with socket.create_connection((probe_host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def require_bridge_dir(bridge_dir: Path) -> None:
    if not bridge_dir.exists():
        raise RuntimeError(f"Bridge directory not found: {bridge_dir}")
    if not bridge_runtime_ready(bridge_dir):
        raise RuntimeError(f"Bridge runtime files are missing in: {bridge_dir}")


def require_npm() -> None:
    if shutil.which("npm") is None:
        raise RuntimeError("`npm` is required but was not found on PATH.")


def build_bridge_env(
    settings: Settings,
    *,
    qr_mode: str = "terminal",
    exit_on_connect: bool = False,
    exit_on_connect_delay_ms: int | None = None,
) -> dict[str, str]:
    host, port = parse_bridge_target(settings.bridge_ws_url)
    bind_host = settings.bridge_bind_host or host
    env = dict(os.environ)
    env["BRIDGE_HOST"] = bind_host
    env["BRIDGE_PORT"] = str(port)
    env["BRIDGE_SHARED_SECRET"] = settings.bridge_shared_secret
    env["BRIDGE_QR_MODE"] = qr_mode
    env["BRIDGE_EXIT_ON_CONNECT"] = "1" if exit_on_connect else "0"
    if exit_on_connect_delay_ms is not None:
        env["BRIDGE_EXIT_ON_CONNECT_DELAY_MS"] = str(max(0, int(exit_on_connect_delay_ms)))
    return env
