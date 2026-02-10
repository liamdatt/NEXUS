from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from uuid import uuid4

from nexus.core.protocol import InboundMessage


class CLIChannel:
    def __init__(self, prompt: str = "nexus> ") -> None:
        self.prompt = prompt

    def _read_line(self) -> str:
        if self.prompt:
            try:
                return input(self.prompt)
            except EOFError:
                return "quit"
        line = sys.stdin.readline()
        if line == "":
            return "quit"
        return line.rstrip("\n")

    async def run(self, handler):
        while True:
            text = await asyncio.to_thread(self._read_line)
            if text.strip().lower() in {"exit", "quit"}:
                break
            msg = InboundMessage(
                id=str(uuid4()),
                channel="cli",
                chat_id="cli-user",
                sender_id="cli-user",
                is_self_chat=True,
                is_from_me=False,
                text=text,
                timestamp=datetime.now(timezone.utc),
            )
            await handler(msg, trace_id=str(uuid4()))

    async def send(self, text: str) -> None:
        # In TUI mode prompt is empty and chat rendering is handled by the TUI DB poller.
        if not self.prompt:
            return
        print(f"nexus: {text}")
