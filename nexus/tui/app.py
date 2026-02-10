from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Final

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static, TabbedContent, TabPane

from nexus.config import Settings, get_settings
from nexus.db.models import Database
from nexus.tui.envfile import EnvFile
from nexus.tui.runtime import RuntimeController, RuntimeEvent


CHAT_ID: Final[str] = "cli-user"
MASK_REVEAL: Final[int] = 4
CONFIG_FIELDS: Final[list[tuple[str, bool]]] = [
    ("NEXUS_OPENROUTER_API_KEY", True),
    ("NEXUS_BRAVE_API_KEY", True),
    ("NEXUS_BRIDGE_SHARED_SECRET", True),
    ("NEXUS_GOOGLE_CLIENT_SECRET_PATH", False),
    ("NEXUS_GOOGLE_TOKEN_PATH", False),
]


class NexusTUI(App[None]):
    TITLE = "Nexus Operator Console"
    SUB_TITLE = "Runtime + Chat + Integrations"
    CSS = """
    #status-line {
        height: 1;
        padding: 0 1;
        background: $panel;
    }
    #layout {
        height: 1fr;
    }
    #sidebar {
        width: 30;
        min-width: 30;
        border: solid $panel;
        padding: 1;
    }
    #sidebar Button {
        width: 100%;
        margin-bottom: 1;
    }
    #main-tabs {
        width: 1fr;
        border: solid $panel;
    }
    #chat-input {
        margin-top: 1;
    }
    .cfg-label {
        margin-top: 1;
    }
    """
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("f5", "refresh", "Refresh"),
        Binding("ctrl+r", "restart_stack", "Restart Stack"),
    ]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.runtime = RuntimeController(settings=settings, event_callback=self._on_runtime_event)
        self.db = Database(settings.db_path)
        self.env_path = self.settings.config_dir / ".env"

        self._seen_ids: set[str] = set()
        self._bridge_running: bool = False
        self._google_connected: bool = False
        self._restart_required: bool = False
        self._status_refresh_inflight: bool = False
        self._config_values: dict[str, str] = {}

    @staticmethod
    def _cfg_input_id(key: str) -> str:
        return f"cfg-{key.lower().replace('_', '-')}"

    @staticmethod
    def _mask_value(value: str) -> str:
        if not value:
            return ""
        if len(value) <= MASK_REVEAL:
            return "*" * len(value)
        return f"{'*' * (len(value) - MASK_REVEAL)}{value[-MASK_REVEAL:]}"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="status-line")
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield Label("Views")
                yield Button("Chat", id="action-view-chat")
                yield Button("Logs", id="action-view-logs")
                yield Button("Config", id="action-view-config")
                yield Label("Runtime")
                yield Button("Start Stack", id="action-start", variant="success")
                yield Button("Stop Stack", id="action-stop", variant="warning")
                yield Button("Restart Stack", id="action-restart")
                yield Label("WhatsApp")
                yield Button("Connect", id="action-wa-connect")
                yield Button("Status", id="action-wa-status")
                yield Button("Disconnect", id="action-wa-disconnect")
                yield Label("Google")
                yield Button("Connect", id="action-google-connect")
                yield Button("Status", id="action-google-status")
                yield Button("Disconnect", id="action-google-disconnect")
                yield Button("Refresh", id="action-refresh")
            with TabbedContent(id="main-tabs"):
                with TabPane("Chat", id="pane-chat"):
                    yield RichLog(id="chat-log", auto_scroll=True, wrap=True, highlight=False, markup=False)
                    yield Input(id="chat-input", placeholder="Type message and press Enter")
                with TabPane("Logs", id="pane-logs"):
                    yield RichLog(id="runtime-log", auto_scroll=True, wrap=True, highlight=False, markup=False)
                with TabPane("Config", id="pane-config"):
                    yield Static("Edit .env values. Save writes to disk.", id="config-help")
                    for key, _is_secret in CONFIG_FIELDS:
                        yield Label(key, classes="cfg-label")
                        yield Input(id=self._cfg_input_id(key))
                    yield Button("Save Config", id="action-save-config", variant="primary")
        yield Footer()

    async def on_mount(self) -> None:
        self._load_config_inputs()
        self._load_chat_history()
        self._show_tab("pane-logs")
        self._update_status_line()
        self.set_interval(1.0, self._poll_runtime_state)
        self.set_interval(1.0, self._poll_chat_messages)
        self.set_interval(8.0, self._trigger_status_refresh)
        self._trigger_status_refresh()

    async def on_shutdown(self) -> None:
        await asyncio.to_thread(self.runtime.stop_all)

    def _append_runtime(self, message: str) -> None:
        self.query_one("#runtime-log", RichLog).write(message)

    def _append_chat(self, role: str, text: str) -> None:
        safe_text = text.strip()
        if not safe_text:
            return
        who = "you" if role == "user" else "nexus"
        self.query_one("#chat-log", RichLog).write(f"{who}: {safe_text}")

    def _load_chat_history(self) -> None:
        messages = self.db.get_recent_messages(CHAT_ID, limit=self.settings.tui_history_limit)
        for row in messages:
            message_id = str(row.get("id") or "")
            if not message_id:
                continue
            self._seen_ids.add(message_id)
            self._append_chat(str(row.get("role") or ""), str(row.get("text") or ""))

    def _poll_chat_messages(self) -> None:
        messages = self.db.get_recent_messages(CHAT_ID, limit=self.settings.tui_history_limit)
        for row in messages:
            message_id = str(row.get("id") or "")
            if not message_id or message_id in self._seen_ids:
                continue
            self._seen_ids.add(message_id)
            self._append_chat(str(row.get("role") or ""), str(row.get("text") or ""))

    def _poll_runtime_state(self) -> None:
        self.runtime.poll_stack()
        self._update_status_line()

    def _load_config_inputs(self) -> None:
        env = EnvFile.load(self.env_path)
        for key, is_secret in CONFIG_FIELDS:
            actual = env.get(key, "")
            self._config_values[key] = actual
            display = self._mask_value(actual) if is_secret else actual
            self.query_one(f"#{self._cfg_input_id(key)}", Input).value = display

    def _save_config(self) -> None:
        env = EnvFile.load(self.env_path)
        updated: dict[str, str] = {}
        for key, is_secret in CONFIG_FIELDS:
            inp = self.query_one(f"#{self._cfg_input_id(key)}", Input)
            raw_value = inp.value
            old_value = self._config_values.get(key, "")
            if is_secret and raw_value == self._mask_value(old_value):
                new_value = old_value
            else:
                new_value = raw_value
            env.upsert(key, new_value)
            updated[key] = new_value

        env.write(self.env_path)
        self._config_values = updated
        for key, is_secret in CONFIG_FIELDS:
            value = self._config_values.get(key, "")
            self.query_one(f"#{self._cfg_input_id(key)}", Input).value = self._mask_value(value) if is_secret else value

        self._restart_required = self.runtime.is_stack_running()
        if self._restart_required:
            self._append_runtime("INFO: config saved; restart stack to apply updated env values.")
        else:
            self._append_runtime("INFO: config saved.")
        self._update_status_line()

    def _on_runtime_event(self, event: RuntimeEvent) -> None:
        app_thread_id = getattr(self, "_thread_id", None)
        if app_thread_id == threading.get_ident():
            self._handle_runtime_event(event)
            return
        try:
            self.call_from_thread(self._handle_runtime_event, event)
        except RuntimeError as exc:
            text = str(exc)
            if "different thread" in text:
                self._handle_runtime_event(event)
            elif "App is not running" in text:
                return
            else:
                raise

    def _handle_runtime_event(self, event: RuntimeEvent) -> None:
        if event.kind == "log":
            self._append_runtime(f"[{event.source}] {event.message}")
        else:
            self._append_runtime(f"{event.kind.upper()} [{event.source}] {event.message}")
        self._update_status_line()

    def _update_status_line(self) -> None:
        stack_state = "running" if self.runtime.is_stack_running() else "stopped"
        pairing_state = "running" if self.runtime.is_connect_running() else "idle"
        restart_flag = "yes" if self._restart_required else "no"
        google_state = "connected" if self._google_connected else "disconnected"
        bridge_state = "up" if self._bridge_running else "down"
        text = (
            f"stack={stack_state} | bridge={bridge_state} | pairing={pairing_state} | "
            f"google={google_state} | restart_required={restart_flag}"
        )
        self.query_one("#status-line", Static).update(text)

    def _show_tab(self, pane_id: str) -> None:
        self.query_one("#main-tabs", TabbedContent).active = pane_id

    def _trigger_status_refresh(self) -> None:
        if self._status_refresh_inflight:
            return
        self._status_refresh_inflight = True
        asyncio.create_task(self._refresh_status())

    async def _refresh_status(self) -> None:
        try:
            wa = await asyncio.to_thread(self.runtime.whatsapp_status)
            self._bridge_running = bool(wa.get("bridge_running"))
            try:
                google = await asyncio.to_thread(self.runtime.google_status)
                self._google_connected = bool(google.get("connected"))
            except Exception:
                self._google_connected = False
        finally:
            self._status_refresh_inflight = False
            self._update_status_line()

    @staticmethod
    def _format_mapping(data: dict[str, object]) -> str:
        return "\n".join(f"{key}: {value}" for key, value in data.items())

    async def _restart_stack(self) -> None:
        await asyncio.to_thread(self.runtime.stop_all)
        await asyncio.to_thread(self.runtime.start_stack)
        self._restart_required = False
        self._trigger_status_refresh()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        try:
            if button_id == "action-start":
                await asyncio.to_thread(self.runtime.start_stack)
                self._restart_required = False
            elif button_id == "action-stop":
                await asyncio.to_thread(self.runtime.stop_all)
            elif button_id == "action-restart":
                await self._restart_stack()
            elif button_id == "action-wa-connect":
                if self.runtime.is_stack_running():
                    raise RuntimeError("stop the running stack before WhatsApp connect")
                self._show_tab("pane-logs")
                self._append_runtime("INFO: starting WhatsApp connect flow; QR will appear in logs.")
                await asyncio.to_thread(
                    self.runtime.start_whatsapp_connect,
                    timeout=300,
                    exit_delay_ms=60000,
                )
            elif button_id == "action-wa-status":
                status = await asyncio.to_thread(self.runtime.whatsapp_status)
                self._bridge_running = bool(status.get("bridge_running"))
                self._append_runtime(self._format_mapping(status))
            elif button_id == "action-wa-disconnect":
                if self.runtime.is_stack_running():
                    raise RuntimeError("stop the running stack before WhatsApp disconnect")
                message = await asyncio.to_thread(self.runtime.whatsapp_disconnect)
                self._append_runtime(f"INFO: {message}")
            elif button_id == "action-google-connect":
                message = await asyncio.to_thread(self.runtime.google_connect)
                self._append_runtime(f"INFO: {message}")
            elif button_id == "action-google-status":
                status = await asyncio.to_thread(self.runtime.google_status)
                self._google_connected = bool(status.get("connected"))
                self._append_runtime(self._format_mapping(status))
            elif button_id == "action-google-disconnect":
                message = await asyncio.to_thread(self.runtime.google_disconnect)
                self._append_runtime(f"INFO: {message}")
            elif button_id == "action-save-config":
                self._save_config()
            elif button_id == "action-refresh":
                self._trigger_status_refresh()
            elif button_id == "action-view-chat":
                self._show_tab("pane-chat")
            elif button_id == "action-view-logs":
                self._show_tab("pane-logs")
            elif button_id == "action-view-config":
                self._show_tab("pane-config")
            else:
                return
        except Exception as exc:  # noqa: BLE001
            self._append_runtime(f"ERROR: {exc}")
        finally:
            self._trigger_status_refresh()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "chat-input":
            return
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        try:
            await asyncio.to_thread(self.runtime.send_chat, text)
        except Exception as exc:  # noqa: BLE001
            self._append_runtime(f"ERROR: {exc}")

    def action_refresh(self) -> None:
        self._trigger_status_refresh()

    async def action_restart_stack(self) -> None:
        try:
            await self._restart_stack()
        except Exception as exc:  # noqa: BLE001
            self._append_runtime(f"ERROR: {exc}")


def run_tui(settings: Settings | None = None) -> int:
    resolved = settings or get_settings()
    app = NexusTUI(resolved)
    app.run()
    return 0
