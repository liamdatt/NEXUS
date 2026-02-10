from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from nexus.config import Settings, get_settings
from nexus.integrations.google_auth import connect_google, disconnect_google, google_auth_status
from nexus.onboard import run_doctor, run_onboard
from nexus.runtime_helpers import (
    bridge_probe_host,
    build_bridge_env,
    is_bridge_running,
    parse_bridge_target,
    prepare_bridge_runtime,
    read_dotenv,
    resolve_bridge_dir,
    require_bridge_dir,
    require_npm,
    resolve_session_dir,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_DIR = REPO_ROOT / "bridge"

# Backward-compatible aliases for existing tests and monkeypatch call sites.
_read_dotenv = read_dotenv
_resolve_session_dir = resolve_session_dir
_parse_bridge_target = parse_bridge_target
_bridge_probe_host = bridge_probe_host
_is_bridge_running = is_bridge_running
_require_bridge_dir = require_bridge_dir
_require_npm = require_npm
_build_bridge_env = build_bridge_env
_resolve_bridge_dir = resolve_bridge_dir
_prepare_bridge_runtime = prepare_bridge_runtime


def _stream_output(proc: subprocess.Popen[str], prefix: str) -> None:
    stream = proc.stdout
    if stream is None:
        return
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            sys.stdout.write(f"{prefix} {line}")
            sys.stdout.flush()
    finally:
        stream.close()


def _terminate_process(proc: subprocess.Popen[str], name: str, grace_seconds: float = 8.0) -> None:
    if proc.poll() is not None:
        return
    print(f"[nexus] stopping {name}...")
    proc.terminate()
    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        print(f"[nexus] force-killing {name}...")
    proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _effective_bridge_dir(settings: Settings, *, ensure_runtime: bool = True) -> Path:
    # Keep test monkeypatch compatibility for BRIDGE_DIR while allowing portable installs.
    candidate = BRIDGE_DIR.expanduser().resolve()
    if settings.bridge_dir is not None:
        candidate = settings.bridge_dir
    elif not candidate.exists():
        candidate = _resolve_bridge_dir(settings)

    if ensure_runtime:
        candidate = _prepare_bridge_runtime(settings, target_dir=candidate)
        _require_bridge_dir(candidate)
    else:
        candidate = candidate.expanduser().resolve()
    return candidate


def _run_stack(bridge_proc: subprocess.Popen[str], core_proc: subprocess.Popen[str]) -> int:
    stop_requested = threading.Event()
    shutdown_reason = {"kind": "running"}  # running | signal | process_exit

    def handle_signal(signum, frame) -> None:  # noqa: ANN001, ARG001
        if not stop_requested.is_set():
            shutdown_reason["kind"] = "signal"
            stop_requested.set()

    prev_sigint = signal.getsignal(signal.SIGINT)
    prev_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        while True:
            if stop_requested.is_set():
                break

            bridge_rc = bridge_proc.poll()
            core_rc = core_proc.poll()
            if bridge_rc is not None or core_rc is not None:
                shutdown_reason["kind"] = "process_exit"
                stop_requested.set()
                if bridge_rc is not None:
                    print(f"[nexus] bridge exited with code {bridge_rc}")
                if core_rc is not None:
                    print(f"[nexus] core exited with code {core_rc}")
                break
            time.sleep(0.2)
    finally:
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)

    _terminate_process(core_proc, "core")
    _terminate_process(bridge_proc, "bridge")
    return 0 if shutdown_reason["kind"] == "signal" else 1


def _cmd_start(args: argparse.Namespace, settings: Settings) -> int:  # noqa: ARG001
    try:
        bridge_dir = _effective_bridge_dir(settings, ensure_runtime=True)
        _require_npm()
        host, port = _parse_bridge_target(settings.bridge_ws_url)
    except RuntimeError as exc:
        print(f"[nexus] {exc}")
        return 1
    except ValueError as exc:
        print(f"[nexus] {exc}")
        return 1

    if _is_bridge_running(host, port):
        print(f"[nexus] bridge port already in use at {host}:{port}.")
        print("[nexus] stop existing bridge/runtime before running `nexus start`.")
        return 1

    try:
        bridge_env = _build_bridge_env(settings, qr_mode="terminal", exit_on_connect=False)
    except ValueError as exc:
        print(f"[nexus] {exc}")
        return 1

    core_env = dict(os.environ)
    core_env["NEXUS_CLI_ENABLED"] = "false"
    core_env.setdefault("PYTHONUNBUFFERED", "1")

    print("[nexus] starting bridge and core...")
    try:
        bridge_proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=str(bridge_dir),
            env=bridge_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        print(f"[nexus] failed to start bridge: {exc}")
        return 1

    try:
        core_proc = subprocess.Popen(
            [sys.executable, "-m", "nexus.app"],
            cwd=str(Path.cwd()),
            env=core_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        print(f"[nexus] failed to start core: {exc}")
        _terminate_process(bridge_proc, "bridge")
        return 1

    bridge_reader = threading.Thread(target=_stream_output, args=(bridge_proc, "[bridge]"), daemon=True)
    core_reader = threading.Thread(target=_stream_output, args=(core_proc, "[core]"), daemon=True)
    bridge_reader.start()
    core_reader.start()

    exit_code = _run_stack(bridge_proc, core_proc)
    bridge_reader.join(timeout=2)
    core_reader.join(timeout=2)
    return exit_code


def _cmd_whatsapp_connect(args: argparse.Namespace, settings: Settings) -> int:
    if args.timeout <= 0:
        print("[nexus] --timeout must be greater than 0")
        return 2

    try:
        bridge_dir = _effective_bridge_dir(settings, ensure_runtime=True)
        _require_npm()
        host, port = _parse_bridge_target(settings.bridge_ws_url)
    except RuntimeError as exc:
        print(f"[nexus] {exc}")
        return 1
    except ValueError as exc:
        print(f"[nexus] {exc}")
        return 1

    if _is_bridge_running(host, port):
        print(f"[nexus] bridge port already in use at {host}:{port}.")
        print("[nexus] stop existing bridge/runtime before pairing.")
        return 1

    try:
        bridge_env = _build_bridge_env(
            settings,
            qr_mode="terminal",
            exit_on_connect=True,
            exit_on_connect_delay_ms=args.exit_delay_ms,
        )
    except ValueError as exc:
        print(f"[nexus] {exc}")
        return 1

    if args.session_dir:
        bridge_env["BRIDGE_SESSION_DIR"] = str(Path(args.session_dir).expanduser())

    print("[nexus] starting WhatsApp pairing mode...")
    try:
        proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=str(bridge_dir),
            env=bridge_env,
        )
    except OSError as exc:
        print(f"[nexus] failed to start bridge: {exc}")
        return 1

    try:
        rc = proc.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"[nexus] pairing timed out after {args.timeout}s")
        _terminate_process(proc, "bridge")
        return 1
    except KeyboardInterrupt:
        print("\n[nexus] interrupted, stopping bridge...")
        _terminate_process(proc, "bridge")
        return 130

    if rc == 0:
        print("[nexus] WhatsApp pairing flow completed.")
        return 0
    print(f"[nexus] bridge exited with code {rc}")
    return 1


def _cmd_whatsapp_disconnect(args: argparse.Namespace, settings: Settings) -> int:
    try:
        bridge_dir = _effective_bridge_dir(settings, ensure_runtime=False)
        host, port = _parse_bridge_target(settings.bridge_ws_url)
    except (RuntimeError, ValueError) as exc:
        print(f"[nexus] {exc}")
        return 1

    if _is_bridge_running(host, port):
        print("[nexus] bridge appears to be running; stop `nexus start` before disconnecting.")
        return 1

    session_dir = _resolve_session_dir(bridge_dir, args.session_dir)
    if not session_dir.exists():
        print(f"[nexus] session already clean: {session_dir}")
        return 0

    if not args.yes:
        answer = input(f"Delete WhatsApp session at {session_dir}? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("[nexus] cancelled.")
            return 0

    if session_dir.is_file():
        session_dir.unlink()
    else:
        shutil.rmtree(session_dir)
    print(f"[nexus] removed WhatsApp session: {session_dir}")
    return 0


def _cmd_whatsapp_status(args: argparse.Namespace, settings: Settings) -> int:
    try:
        bridge_dir = _effective_bridge_dir(settings, ensure_runtime=False)
        host, port = _parse_bridge_target(settings.bridge_ws_url)
    except (RuntimeError, ValueError) as exc:
        print(f"[nexus] {exc}")
        return 1

    session_dir = _resolve_session_dir(bridge_dir, args.session_dir)
    session_exists = session_dir.exists()
    session_has_files = session_exists and session_dir.is_dir() and any(session_dir.iterdir())
    bridge_running = _is_bridge_running(host, port)

    print(f"bridge_host: {host}")
    print(f"bridge_port: {port}")
    print(f"bridge_running: {'yes' if bridge_running else 'no'}")
    print(f"session_dir: {session_dir}")
    print(f"session_exists: {'yes' if session_exists else 'no'}")
    print(f"session_has_files: {'yes' if session_has_files else 'no'}")
    return 0


def _cmd_auth_google_connect(args: argparse.Namespace, settings: Settings) -> int:  # noqa: ARG001
    try:
        message = connect_google(settings)
    except Exception as exc:  # noqa: BLE001
        print(f"[nexus] Google auth connect failed: {exc}")
        return 1
    print(message)
    return 0


def _cmd_auth_google_status(args: argparse.Namespace, settings: Settings) -> int:  # noqa: ARG001
    status = google_auth_status(settings)
    print(f"client_secret_path: {status['client_secret_path']}")
    print(f"client_secret_exists: {'yes' if status['client_secret_exists'] else 'no'}")
    print(f"token_path: {status['token_path']}")
    print(f"token_exists: {'yes' if status['token_exists'] else 'no'}")
    print(f"connected: {'yes' if status['connected'] else 'no'}")
    if status.get("token_expiry"):
        print(f"token_expiry: {status['token_expiry']}")
    scopes = status.get("scopes") or []
    if scopes:
        print(f"scopes: {', '.join(scopes)}")
    if status.get("error"):
        print(f"error: {status['error']}")
    return 0 if status["connected"] else 1


def _cmd_auth_google_disconnect(args: argparse.Namespace, settings: Settings) -> int:  # noqa: ARG001
    try:
        message = disconnect_google(settings)
    except Exception as exc:  # noqa: BLE001
        print(f"[nexus] Google auth disconnect failed: {exc}")
        return 1
    print(message)
    return 0


def _cmd_onboard(args: argparse.Namespace, settings: Settings) -> int:
    return run_onboard(
        settings,
        non_interactive=bool(args.non_interactive),
        assume_yes=bool(args.yes),
    )


def _cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:  # noqa: ARG001
    return run_doctor(settings)


def _cmd_tui(args: argparse.Namespace, settings: Settings) -> int:  # noqa: ARG001
    try:
        from nexus.tui.app import run_tui
    except Exception as exc:  # noqa: BLE001
        print(f"[nexus] TUI dependencies are unavailable: {exc}")
        print("[nexus] Install dependencies with `pip install -e .` (or `uv tool install nexus-ai`).")
        return 1

    try:
        return int(run_tui(settings=settings))
    except Exception as exc:  # noqa: BLE001
        print(f"[nexus] TUI failed: {exc}")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nexus", description="Nexus runtime CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    onboard_parser = subparsers.add_parser("onboard", help="Bootstrap Nexus config and runtime dependencies")
    onboard_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Do not prompt for input; fail if required values are missing",
    )
    onboard_parser.add_argument("--yes", action="store_true", help="Reserved for prompt auto-accept in onboarding")
    onboard_parser.set_defaults(handler=_cmd_onboard)

    doctor_parser = subparsers.add_parser("doctor", help="Run install and configuration diagnostics")
    doctor_parser.set_defaults(handler=_cmd_doctor)

    start_parser = subparsers.add_parser("start", help="Start bridge + Nexus core in foreground")
    start_parser.set_defaults(handler=_cmd_start)

    tui_parser = subparsers.add_parser("tui", help="Launch Nexus operator TUI")
    tui_parser.set_defaults(handler=_cmd_tui)

    wa_parser = subparsers.add_parser("whatsapp", help="Manage WhatsApp auth/session")
    wa_subparsers = wa_parser.add_subparsers(dest="whatsapp_command", required=True)

    connect_parser = wa_subparsers.add_parser("connect", help="Start one-shot WhatsApp pairing flow")
    connect_parser.add_argument("--timeout", type=int, default=180, help="Pairing timeout in seconds (default: 180)")
    connect_parser.add_argument(
        "--exit-delay-ms",
        type=int,
        default=30000,
        help="Delay after connection opens before bridge exits (default: 30000)",
    )
    connect_parser.add_argument("--session-dir", type=str, default=None, help="Override BRIDGE_SESSION_DIR")
    connect_parser.set_defaults(handler=_cmd_whatsapp_connect)

    disconnect_parser = wa_subparsers.add_parser(
        "disconnect",
        help="Delete local WhatsApp session (requires bridge to be stopped)",
    )
    disconnect_parser.add_argument("--session-dir", type=str, default=None, help="Override BRIDGE_SESSION_DIR")
    disconnect_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    disconnect_parser.set_defaults(handler=_cmd_whatsapp_disconnect)

    status_parser = wa_subparsers.add_parser("status", help="Show bridge/session status")
    status_parser.add_argument("--session-dir", type=str, default=None, help="Override BRIDGE_SESSION_DIR")
    status_parser.set_defaults(handler=_cmd_whatsapp_status)

    auth_parser = subparsers.add_parser("auth", help="Manage external service authentication")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_provider", required=True)

    google_parser = auth_subparsers.add_parser("google", help="Manage Google OAuth credentials")
    google_subparsers = google_parser.add_subparsers(dest="google_auth_command", required=True)

    google_connect = google_subparsers.add_parser("connect", help="Run Google OAuth flow and save token")
    google_connect.set_defaults(handler=_cmd_auth_google_connect)

    google_status = google_subparsers.add_parser("status", help="Show Google auth status")
    google_status.set_defaults(handler=_cmd_auth_google_status)

    google_disconnect = google_subparsers.add_parser("disconnect", help="Delete local Google token")
    google_disconnect.set_defaults(handler=_cmd_auth_google_disconnect)

    return parser


def run_cli(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    resolved_settings = settings or get_settings()
    handler = args.handler
    return int(handler(args, resolved_settings))


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
