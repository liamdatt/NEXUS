from __future__ import annotations

import asyncio
from pathlib import Path

from nexus.config import Settings
from nexus.tools.email import EmailTool


class _FakeGmailClient:
    def __init__(self) -> None:
        self.sent_payload = None
        self.draft_payload = None

    def list_messages(self, query: str, max_results: int):  # noqa: ANN001
        assert max_results > 0
        if query == "is:unread":
            return [
                {
                    "id": "m1",
                    "from": "Alice <alice@example.com>",
                    "subject": "Project Update",
                    "date": "Mon, 10 Feb 2026 09:00:00 -0700",
                    "snippet": "Latest project update and next action items.",
                }
            ]
        return [
            {
                "id": "m2",
                "from": "Bob <bob@example.com>",
                "subject": "Invoice",
                "date": "Mon, 10 Feb 2026 10:00:00 -0700",
                "snippet": "Invoice attached for February.",
            }
        ]

    def search_threads(self, query: str, max_results: int):  # noqa: ANN001
        assert query
        assert max_results > 0
        return [
            {
                "thread_id": "thr-1",
                "from": "Alice <alice@example.com>",
                "subject": "Threaded topic",
                "date": "Mon, 10 Feb 2026 09:00:00 -0700",
                "snippet": "Latest message in thread.",
                "message_count": 3,
            }
        ]

    def send_message(self, **kwargs):  # noqa: ANN003
        self.sent_payload = kwargs
        return {"id": "sent-1", "thread_id": "thr-1", "label_ids": ["SENT"]}

    def create_draft(self, **kwargs):  # noqa: ANN003
        self.draft_payload = kwargs
        return {"id": "drf-1", "message_id": "msg-1", "thread_id": "thr-1"}

    def send_draft(self, draft_id: str):
        assert draft_id == "drf-1"
        return {"id": "sent-2", "thread_id": "thr-1", "draft_id": "drf-1"}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        email_summary_max_results=10,
    )


def test_summarize_unread(tmp_path: Path):
    tool = EmailTool(_settings(tmp_path), client=_FakeGmailClient())
    result = asyncio.run(tool.run({"action": "summarize_unread"}))
    assert result.ok
    assert "Project Update" in result.content


def test_summarize_search(tmp_path: Path):
    tool = EmailTool(_settings(tmp_path), client=_FakeGmailClient())
    result = asyncio.run(tool.run({"action": "summarize_search", "query": "from:bob"}))
    assert result.ok
    assert "Invoice" in result.content


def test_send_email_requires_confirmation(tmp_path: Path):
    tool = EmailTool(_settings(tmp_path), client=_FakeGmailClient())
    result = asyncio.run(
        tool.run(
            {
                "action": "send_email",
                "to": ["a@example.com"],
                "subject": "Test",
                "body_text": "Hello",
            }
        )
    )
    assert not result.ok
    assert result.requires_confirmation
    assert "Reply YES to proceed" in result.content


def test_send_email_executes_when_confirmed(tmp_path: Path):
    client = _FakeGmailClient()
    tool = EmailTool(_settings(tmp_path), client=client)
    result = asyncio.run(
        tool.run(
            {
                "action": "send_email",
                "to": "a@example.com,b@example.com",
                "cc": ["c@example.com"],
                "subject": "Launch",
                "body_text": "Ready",
                "body_html": "<p>Ready</p>",
                "confirmed": True,
            }
        )
    )
    assert result.ok
    assert "Email sent successfully" in result.content
    assert client.sent_payload is not None
    assert client.sent_payload["to"] == ["a@example.com", "b@example.com"]


def test_create_draft_requires_confirmation(tmp_path: Path):
    tool = EmailTool(_settings(tmp_path), client=_FakeGmailClient())
    result = asyncio.run(
        tool.run(
            {
                "action": "create_draft",
                "to": ["a@example.com"],
                "subject": "Draft",
                "body_text": "Hello",
            }
        )
    )
    assert not result.ok
    assert result.requires_confirmation


def test_send_draft_executes_when_confirmed(tmp_path: Path):
    tool = EmailTool(_settings(tmp_path), client=_FakeGmailClient())
    result = asyncio.run(tool.run({"action": "send_draft", "draft_id": "drf-1", "confirmed": True}))
    assert result.ok
    assert "Draft sent." in result.content
