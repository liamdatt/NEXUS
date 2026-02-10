from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from nexus.config import Settings
from nexus.integrations.google_auth import connect_google, disconnect_google, google_auth_status
from nexus.runtime_helpers import (
    build_bridge_env,
    is_bridge_running,
    parse_bridge_target,
    prepare_bridge_runtime,
    resolve_bridge_dir,
    require_bridge_dir,
    require_npm,
    resolve_session_dir,
)


@dataclass(slots=True)
class RuntimeEvent:
    kind: str
    source: str
    message: str


class RuntimeController:
    def __init__(
        self,
        settings: Settings,
        *,
        repo_root: Path | None = None,
        bridge_dir: Path | None = None,
        event_callback: Callable[[RuntimeEvent], None] | None = None,
    ) -> None:
        self.settings = settings
        self.repo_root = repo_root or Path.cwd()
        self.bridge_dir = bridge_dir or resolve_bridge_dir(settings)
        self._event_callback = event_callback

        self._bridge_proc: subprocess.Popen[str] | None = None
        self._core_proc: subprocess.Popen[str] | None = None
        self._connect_proc: subprocess.Popen[str] | None = None

        self._bridge_reader: threading.Thread | None = None
        self._core_reader: threading.Thread | None = None
        self._connect_reader: threading.Thread | None = None
        self._connect_watcher: threading.Thread | None = None

    def set_event_callback(self, callback: Callable[[RuntimeEvent], None] | None) -> None:
        self._event_callback = callback

    def _emit(self, kind: str, source: str, message: str) -> None:
        if self._event_callback:
            self._event_callback(RuntimeEvent(kind=kind, source=source, message=message))

    def _stream_output(self, proc: subprocess.Popen[str], source: str) -> None:
        stream = proc.stdout
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                if not line:
                    break
                self._emit("log", source, line.rstrip("\n"))
        finally:
            stream.close()

    def _start_reader(self, proc: subprocess.Popen[str], source: str) -> threading.Thread:
        thread = threading.Thread(target=self._stream_output, args=(proc, source), daemon=True)
        thread.start()
        return thread

    def _terminate_process(self, proc: subprocess.Popen[str], name: str, grace_seconds: float = 8.0) -> None:
        if proc.poll() is not None:
            return
        self._emit("info", "runtime", f"stopping {name}...")
        proc.terminate()
        try:
            proc.wait(timeout=grace_seconds)
            return
        except subprocess.TimeoutExpired:
            self._emit("info", "runtime", f"force-killing {name}...")
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def _join_reader(self, reader: threading.Thread | None, timeout: float = 2.0) -> None:
        if reader is None:
            return
        reader.join(timeout=timeout)

    def is_stack_running(self) -> bool:
        return bool(
            self._bridge_proc
            and self._bridge_proc.poll() is None
            and self._core_proc
            and self._core_proc.poll() is None
        )

    def poll_stack(self) -> dict[str, int | bool | None]:
        bridge_rc = self._bridge_proc.poll() if self._bridge_proc else None
        core_rc = self._core_proc.poll() if self._core_proc else None

        if self._bridge_proc and bridge_rc is not None:
            self._emit("error", "bridge", f"bridge exited with code {bridge_rc}")
            self.stop_stack()
        elif self._core_proc and core_rc is not None:
            self._emit("error", "core", f"core exited with code {core_rc}")
            self.stop_stack()

        return {
            "running": self.is_stack_running(),
            "bridge_rc": bridge_rc,
            "core_rc": core_rc,
        }

    def start_stack(self) -> None:
        self.poll_stack()
        if self.is_stack_running():
            raise RuntimeError("stack is already running")

        self.bridge_dir = prepare_bridge_runtime(self.settings, target_dir=self.bridge_dir)
        require_bridge_dir(self.bridge_dir)
        require_npm()
        host, port = parse_bridge_target(self.settings.bridge_ws_url)
        if is_bridge_running(host, port):
            raise RuntimeError(f"bridge port already in use at {host}:{port}")

        bridge_env = build_bridge_env(self.settings, qr_mode="terminal", exit_on_connect=False)
        core_env = dict(os.environ)
        core_env["NEXUS_CLI_ENABLED"] = "true"
        core_env["NEXUS_CLI_PROMPT"] = ""
        core_env.setdefault("PYTHONUNBUFFERED", "1")

        self._emit("info", "runtime", "starting bridge and core...")
        try:
            bridge_proc = subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=str(self.bridge_dir),
                env=bridge_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise RuntimeError(f"failed to start bridge: {exc}") from exc

        try:
            core_proc = subprocess.Popen(
                [sys.executable, "-m", "nexus.app"],
                cwd=str(self.repo_root),
                env=core_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self._terminate_process(bridge_proc, "bridge")
            raise RuntimeError(f"failed to start core: {exc}") from exc

        self._bridge_proc = bridge_proc
        self._core_proc = core_proc
        self._bridge_reader = self._start_reader(bridge_proc, "bridge")
        self._core_reader = self._start_reader(core_proc, "core")
        self._emit("status", "runtime", "stack started")

    def stop_stack(self) -> None:
        core_proc = self._core_proc
        bridge_proc = self._bridge_proc

        if core_proc:
            self._terminate_process(core_proc, "core")
        if bridge_proc:
            self._terminate_process(bridge_proc, "bridge")

        self._join_reader(self._core_reader)
        self._join_reader(self._bridge_reader)

        self._core_proc = None
        self._bridge_proc = None
        self._core_reader = None
        self._bridge_reader = None
        self._emit("status", "runtime", "stack stopped")

    def send_chat(self, text: str) -> None:
        self.poll_stack()
        if not self._core_proc or self._core_proc.poll() is not None:
            raise RuntimeError("core is not running")
        if self._core_proc.stdin is None:
            raise RuntimeError("core stdin is unavailable")
        try:
            self._core_proc.stdin.write(f"{text.rstrip()}\n")
            self._core_proc.stdin.flush()
        except OSError as exc:
            raise RuntimeError(f"failed to send chat input: {exc}") from exc

    def _watch_connect_process(self, proc: subprocess.Popen[str], timeout: int) -> None:
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._emit("error", "wa-connect", f"pairing timed out after {timeout}s")
            self._terminate_process(proc, "whatsapp-connect bridge")
            rc = 1
        except Exception as exc:  # noqa: BLE001
            self._emit("error", "wa-connect", f"pairing failed: {exc}")
            self._terminate_process(proc, "whatsapp-connect bridge")
            rc = 1

        if rc == 0:
            self._emit("status", "wa-connect", "WhatsApp pairing flow completed.")
        else:
            self._emit("error", "wa-connect", f"pairing process exited with code {rc}")

        self._join_reader(self._connect_reader)
        self._connect_reader = None
        self._connect_proc = None
        self._connect_watcher = None

    def start_whatsapp_connect(
        self,
        *,
        timeout: int = 300,
        exit_delay_ms: int = 60000,
        session_dir: str | None = None,
    ) -> None:
        if timeout <= 0:
            raise RuntimeError("timeout must be greater than 0")
        self.poll_stack()
        if self.is_stack_running():
            raise RuntimeError("stop the running stack before pairing WhatsApp")
        if self._connect_proc and self._connect_proc.poll() is None:
            raise RuntimeError("a WhatsApp connect flow is already running")

        self.bridge_dir = prepare_bridge_runtime(self.settings, target_dir=self.bridge_dir)
        require_bridge_dir(self.bridge_dir)
        require_npm()
        host, port = parse_bridge_target(self.settings.bridge_ws_url)
        if is_bridge_running(host, port):
            raise RuntimeError(f"bridge port already in use at {host}:{port}")

        bridge_env = build_bridge_env(
            self.settings,
            qr_mode="terminal",
            exit_on_connect=True,
            exit_on_connect_delay_ms=exit_delay_ms,
        )
        if session_dir:
            bridge_env["BRIDGE_SESSION_DIR"] = str(Path(session_dir).expanduser())

        try:
            proc = subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=str(self.bridge_dir),
                env=bridge_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise RuntimeError(f"failed to start bridge pairing process: {exc}") from exc

        self._connect_proc = proc
        self._connect_reader = self._start_reader(proc, "wa-connect")
        self._connect_watcher = threading.Thread(
            target=self._watch_connect_process,
            args=(proc, timeout),
            daemon=True,
        )
        self._connect_watcher.start()
        self._emit("status", "wa-connect", "started WhatsApp pairing mode")

    def is_connect_running(self) -> bool:
        return bool(self._connect_proc and self._connect_proc.poll() is None)

    def whatsapp_status(self, session_dir: str | None = None) -> dict[str, object]:
        require_bridge_dir(self.bridge_dir)
        host, port = parse_bridge_target(self.settings.bridge_ws_url)
        session_path = resolve_session_dir(self.bridge_dir, session_dir)
        session_exists = session_path.exists()
        session_has_files = session_exists and session_path.is_dir() and any(session_path.iterdir())
        bridge_running = is_bridge_running(host, port)
        return {
            "bridge_host": host,
            "bridge_port": port,
            "bridge_running": bridge_running,
            "session_dir": str(session_path),
            "session_exists": session_exists,
            "session_has_files": bool(session_has_files),
        }

    def whatsapp_disconnect(self, session_dir: str | None = None) -> str:
        self.poll_stack()
        if self.is_stack_running():
            raise RuntimeError("bridge appears to be running; stop the stack before disconnecting")
        if self.is_connect_running():
            raise RuntimeError("WhatsApp connect flow is still running; stop it before disconnecting")

        status = self.whatsapp_status(session_dir=session_dir)
        if status["bridge_running"]:
            host = str(status["bridge_host"])
            port = int(status["bridge_port"])
            # Give shutdown a short grace period to release the port before declaring failure.
            for _ in range(5):
                time.sleep(0.3)
                if not is_bridge_running(host, port):
                    status["bridge_running"] = False
                    break
            if status["bridge_running"]:
                raise RuntimeError(
                    f"bridge appears to be running at {host}:{port}; stop all bridge processes before disconnecting"
                )

        session_path = Path(str(status["session_dir"]))
        if not session_path.exists():
            return f"session already clean: {session_path}"
        if session_path.is_file():
            session_path.unlink()
        else:
            shutil.rmtree(session_path)
        return f"removed WhatsApp session: {session_path}"

    def google_connect(self) -> str:
        return connect_google(self.settings)

    def google_status(self) -> dict[str, object]:
        return google_auth_status(self.settings)

    def google_disconnect(self) -> str:
        return disconnect_google(self.settings)

    def stop_all(self) -> None:
        self.stop_stack()
        proc = self._connect_proc
        if proc and proc.poll() is None:
            self._terminate_process(proc, "whatsapp-connect bridge")
        self._join_reader(self._connect_reader)
        self._connect_proc = None
        self._connect_reader = None
        self._connect_watcher = None
