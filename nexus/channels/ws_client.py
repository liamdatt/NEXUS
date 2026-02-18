from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

from nexus.config import Settings
from nexus.core.protocol import Envelope, InboundMessage, OutboundMessage


InboundHandler = Callable[[InboundMessage, str], Awaitable[None]]
DeliveryHandler = Callable[[str, str], None]
logger = logging.getLogger(__name__)


class BridgeClient:
    def __init__(
        self,
        settings: Settings,
        on_inbound: InboundHandler,
        on_delivery: DeliveryHandler | None = None,
    ) -> None:
        self.settings = settings
        self.on_inbound = on_inbound
        self.on_delivery = on_delivery
        self._ws = None
        self._running = False

    async def run_forever(self) -> None:
        self._running = True
        headers = {"x-nexus-client": "core"}
        if self.settings.bridge_shared_secret:
            headers["x-nexus-secret"] = self.settings.bridge_shared_secret

        logger.info("BridgeClient starting; target=%s", self.settings.bridge_ws_url)
        while self._running:
            try:
                async with websockets.connect(
                    self.settings.bridge_ws_url,
                    additional_headers=headers,
                ) as ws:
                    self._ws = ws
                    logger.info("BridgeClient connected to bridge")
                    async for raw in ws:
                        await self._handle_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._ws = None
                logger.warning("BridgeClient connection error: %s", exc)
                await asyncio.sleep(2)

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _handle_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("BridgeClient received invalid JSON: %s", exc)
            return

        if not isinstance(data, dict):
            logger.warning("BridgeClient received non-object envelope: %s", type(data).__name__)
            return

        event = data.get("event")
        if not isinstance(event, str):
            logger.warning("BridgeClient received envelope without valid event")
            return
        trace_id = str(data.get("trace_id", ""))
        payload_obj: Any = data.get("payload")

        if event == "bridge.inbound_message":
            payloads = payload_obj if isinstance(payload_obj, list) else [payload_obj]
            for payload in payloads:
                if not isinstance(payload, dict):
                    logger.warning("BridgeClient ignored inbound payload type=%s", type(payload).__name__)
                    continue
                logger.debug(
                    "Inbound WA message id=%s chat_id=%s self=%s from_me=%s",
                    payload.get("id"),
                    payload.get("chat_id"),
                    payload.get("is_self_chat"),
                    payload.get("is_from_me"),
                )
                try:
                    msg = InboundMessage(channel="whatsapp", **payload)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("BridgeClient inbound payload validation failed: %s", exc)
                    continue
                await self.on_inbound(msg, trace_id)
        elif event == "bridge.delivery_receipt":
            payloads = payload_obj if isinstance(payload_obj, list) else [payload_obj]
            for payload in payloads:
                if not isinstance(payload, dict):
                    logger.warning("BridgeClient ignored delivery payload type=%s", type(payload).__name__)
                    continue
                provider_message_id = str(payload.get("provider_message_id", ""))
                provider_message_ids = payload.get("provider_message_ids")
                chat_id = str(payload.get("chat_id", ""))
                if not self.on_delivery or not chat_id:
                    continue
                seen: set[str] = set()
                if provider_message_id:
                    seen.add(provider_message_id)
                    self.on_delivery(provider_message_id, chat_id)
                if isinstance(provider_message_ids, list):
                    for item in provider_message_ids:
                        candidate = str(item or "")
                        if not candidate or candidate in seen:
                            continue
                        seen.add(candidate)
                        self.on_delivery(candidate, chat_id)
        elif event == "bridge.qr":
            logger.info("BridgeClient received bridge.qr")
        elif event == "bridge.connected":
            logger.info("BridgeClient received bridge.connected")
        elif event == "bridge.disconnected":
            reason = payload_obj.get("reason") if isinstance(payload_obj, dict) else None
            logger.info("BridgeClient received bridge.disconnected reason=%s", reason)
        elif event == "bridge.error":
            error = payload_obj.get("error") if isinstance(payload_obj, dict) else payload_obj
            logger.warning("BridgeClient reported bridge.error: %s", error)
        elif event == "bridge.connection_update":
            payloads = payload_obj if isinstance(payload_obj, list) else [payload_obj]
            for payload in payloads:
                if not isinstance(payload, dict):
                    logger.warning(
                        "BridgeClient ignored connection_update payload type=%s",
                        type(payload).__name__,
                    )
                    continue
                logger.info(
                    "BridgeClient connection update: connection=%s has_qr=%s status_code=%s logged_out=%s reconnect_scheduled=%s",
                    payload.get("connection"),
                    payload.get("has_qr"),
                    payload.get("status_code"),
                    payload.get("logged_out"),
                    payload.get("reconnect_scheduled"),
                )

    async def send_outbound(self, message: OutboundMessage) -> None:
        if not self._ws:
            logger.warning("Outbound dropped because bridge socket is not connected")
            return
        env = Envelope(event="core.outbound_message", payload=message.model_dump())
        await self._ws.send(env.model_dump_json())

    async def send_ack(self, inbound_id: str) -> None:
        if not self._ws:
            return
        env = Envelope(event="core.ack", payload={"inbound_id": inbound_id})
        await self._ws.send(env.model_dump_json())
