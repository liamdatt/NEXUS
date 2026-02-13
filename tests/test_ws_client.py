import asyncio
import json
import logging
from pathlib import Path

from nexus.channels.ws_client import BridgeClient
from nexus.config import Settings


class _DummyWS:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self):
        return None


async def _on_inbound(msg, trace_id):  # noqa: ANN001
    return None


def test_bridge_client_uses_additional_headers(monkeypatch, tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        bridge_ws_url="ws://127.0.0.1:8765",
        bridge_shared_secret="change-me",
    )

    client = BridgeClient(settings=settings, on_inbound=_on_inbound)
    seen: dict = {}

    def fake_connect(url, **kwargs):  # noqa: ANN001
        seen["url"] = url
        seen["kwargs"] = kwargs
        client._running = False
        return _DummyWS()

    monkeypatch.setattr("nexus.channels.ws_client.websockets.connect", fake_connect)

    asyncio.run(client.run_forever())

    assert seen["url"] == "ws://127.0.0.1:8765"
    assert seen["kwargs"]["additional_headers"] == {
        "x-nexus-client": "core",
        "x-nexus-secret": "change-me",
    }


def test_bridge_client_handles_list_payload_without_crash(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
    )

    seen = {"inbound": 0, "deliveries": 0}

    async def on_inbound(msg, trace_id):  # noqa: ANN001
        seen["inbound"] += 1

    def on_delivery(provider_message_id, chat_id):  # noqa: ANN001
        seen["deliveries"] += 1

    client = BridgeClient(settings=settings, on_inbound=on_inbound, on_delivery=on_delivery)

    inbound_env = json.dumps(
        {
            "event": "bridge.inbound_message",
            "message_id": "m1",
            "timestamp": "2026-02-09T00:00:00Z",
            "channel": "whatsapp",
            "trace_id": "t1",
            "payload": [
                {
                    "id": "in-1",
                    "chat_id": "123@lid",
                    "sender_id": "123@lid",
                    "is_self_chat": True,
                    "is_from_me": True,
                    "text": "hello",
                    "timestamp": "2026-02-09T00:00:00Z",
                }
            ],
        }
    )
    delivery_env = json.dumps(
        {
            "event": "bridge.delivery_receipt",
            "message_id": "m2",
            "timestamp": "2026-02-09T00:00:00Z",
            "channel": "whatsapp",
            "trace_id": "t2",
            "payload": [{"provider_message_id": "p1", "chat_id": "123@lid"}],
        }
    )

    asyncio.run(client._handle_message(inbound_env))
    asyncio.run(client._handle_message(delivery_env))

    assert seen == {"inbound": 1, "deliveries": 1}


def test_bridge_client_handles_bridge_diagnostic_events_without_crash(tmp_path: Path, caplog):
    settings = Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
    )

    client = BridgeClient(settings=settings, on_inbound=_on_inbound)

    events = [
        json.dumps(
            {
                "event": "bridge.connection_update",
                "message_id": "m1",
                "timestamp": "2026-02-13T00:00:00Z",
                "channel": "whatsapp",
                "trace_id": "t1",
                "payload": {
                    "connection": "close",
                    "has_qr": False,
                    "status_code": 428,
                    "logged_out": False,
                    "reconnect_scheduled": True,
                    "timestamp": "2026-02-13T00:00:00Z",
                },
            }
        ),
        json.dumps(
            {
                "event": "bridge.qr",
                "message_id": "m2",
                "timestamp": "2026-02-13T00:00:00Z",
                "channel": "whatsapp",
                "trace_id": "t2",
                "payload": {"qr": "abc"},
            }
        ),
        json.dumps(
            {
                "event": "bridge.connected",
                "message_id": "m3",
                "timestamp": "2026-02-13T00:00:00Z",
                "channel": "whatsapp",
                "trace_id": "t3",
                "payload": {"status": "connected"},
            }
        ),
        json.dumps(
            {
                "event": "bridge.disconnected",
                "message_id": "m4",
                "timestamp": "2026-02-13T00:00:00Z",
                "channel": "whatsapp",
                "trace_id": "t4",
                "payload": {"reason": "connection_closed"},
            }
        ),
        json.dumps(
            {
                "event": "bridge.error",
                "message_id": "m5",
                "timestamp": "2026-02-13T00:00:00Z",
                "channel": "whatsapp",
                "trace_id": "t5",
                "payload": {"error": "pairing_timeout_waiting_for_qr"},
            }
        ),
    ]

    with caplog.at_level(logging.INFO):
        for env in events:
            asyncio.run(client._handle_message(env))

    assert "BridgeClient connection update:" in caplog.text
    assert "BridgeClient received bridge.qr" in caplog.text
    assert "BridgeClient received bridge.connected" in caplog.text
    assert "BridgeClient received bridge.disconnected" in caplog.text
    assert "pairing_timeout_waiting_for_qr" in caplog.text
