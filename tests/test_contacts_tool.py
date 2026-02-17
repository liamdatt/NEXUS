from __future__ import annotations

import asyncio
from pathlib import Path

from nexus.config import Settings
from nexus.tools.contacts import ContactsTool


class _FakeContactsClient:
    def list_contacts(self, max_results: int):
        assert max_results == 20
        return [
            {
                "display_name": "Alice",
                "emails": ["alice@example.com"],
                "phones": ["+15551234567"],
            }
        ]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
    )


def test_contacts_list(tmp_path: Path):
    tool = ContactsTool(_settings(tmp_path), client=_FakeContactsClient())
    result = asyncio.run(tool.run({"action": "list", "max_results": 20}))
    assert result.ok
    assert "Alice" in result.content
