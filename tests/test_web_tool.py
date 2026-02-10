from __future__ import annotations

import asyncio
from pathlib import Path

import requests

from nexus.config import Settings
from nexus.tools.web import WebTool


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self._json_data


def _settings(tmp_path: Path, brave_key: str = "") -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        brave_api_key=brave_key,
        search_timeout_seconds=2,
    )


def test_search_auto_uses_brave_when_key_present(monkeypatch, tmp_path: Path):
    tool = WebTool(_settings(tmp_path, brave_key="token-123"))

    def fake_get(url, params=None, headers=None, timeout=None):  # noqa: ANN001
        assert "brave.com" in url
        return _FakeResponse(
            json_data={
                "web": {
                    "results": [
                        {"title": "Jamaica", "url": "https://example.com/jamaica", "description": "Island nation"}
                    ]
                }
            }
        )

    monkeypatch.setattr("nexus.tools.web.requests.get", fake_get)
    result = asyncio.run(tool.run({"action": "search_web", "query": "jamaica"}))
    assert result.ok
    assert "provider: brave" in result.content


def test_search_auto_falls_back_when_brave_fails(monkeypatch, tmp_path: Path):
    tool = WebTool(_settings(tmp_path, brave_key="token-123"))

    calls = {"count": 0}

    def fake_get(url, params=None, headers=None, timeout=None):  # noqa: ANN001
        calls["count"] += 1
        if "brave.com" in url:
            return _FakeResponse(status_code=429)
        html = '<a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fx">Result</a>'
        return _FakeResponse(text=html)

    monkeypatch.setattr("nexus.tools.web.requests.get", fake_get)
    result = asyncio.run(tool.run({"action": "search_web", "query": "jamaica"}))
    assert result.ok
    assert "provider: duckduckgo" in result.content
    assert "fallback used" in result.content
    assert calls["count"] >= 2


def test_search_forced_brave_without_key_errors(tmp_path: Path):
    tool = WebTool(_settings(tmp_path, brave_key=""))
    result = asyncio.run(tool.run({"action": "search_web", "query": "jamaica", "provider": "brave"}))
    assert not result.ok
    assert "Brave API key is not configured" in result.content
