from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


BridgeEvent = Literal[
    "bridge.ready",
    "bridge.qr",
    "bridge.inbound_message",
    "bridge.delivery_receipt",
    "bridge.error",
    "core.outbound_message",
    "core.ack",
]


class Envelope(BaseModel):
    event: BridgeEvent
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    channel: Literal["whatsapp"] = "whatsapp"
    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    payload: dict[str, Any]


class MediaItem(BaseModel):
    type: Literal["image", "document"]
    mime_type: str | None = None
    file_name: str | None = None
    caption: str | None = None


class InboundMessage(BaseModel):
    id: str
    channel: Literal["whatsapp", "cli"]
    chat_id: str
    sender_id: str
    is_self_chat: bool
    is_from_me: bool
    text: str | None = None
    media: list[MediaItem] | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Attachment(BaseModel):
    type: Literal["document", "image"]
    path: str
    file_name: str | None = None
    mime_type: str | None = None
    caption: str | None = None


class OutboundMessage(BaseModel):
    id: str
    channel: Literal["whatsapp", "cli"]
    chat_id: str
    text: str | None = None
    attachments: list[Attachment] | None = None
    reply_to: str | None = None


class DeliveryReceipt(BaseModel):
    outbound_id: str
    provider_message_id: str
    chat_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PendingAction(BaseModel):
    action_id: str
    tool_name: str
    risk_level: Literal["low", "medium", "high"]
    expires_at: datetime
    proposed_args: dict[str, Any]
    status: Literal["pending", "approved", "denied", "expired"] = "pending"
    chat_id: str
