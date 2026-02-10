from __future__ import annotations

from urllib.parse import quote, urlparse

import requests
from markdownify import markdownify as html_to_md

from nexus.config import Settings
from nexus.tools.base import BaseTool, ToolResult, ToolSpec


class WebTool(BaseTool):
    name = "web"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="Web search and URL fetch",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["search_web", "fetch_url"]},
                    "query": {"type": "string"},
                    "url": {"type": "string"},
                    "provider": {"type": "string", "enum": ["auto", "brave", "duckduckgo"]},
                },
                "required": ["action"],
            },
        )

    def _allow_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0"}
        return (parsed.hostname or "") not in blocked_hosts

    @staticmethod
    def _format_results(provider: str, items: list[dict[str, str]]) -> str:
        if not items:
            return f"provider: {provider}\nNo results"
        lines = [f"provider: {provider}"]
        for item in items[:5]:
            lines.append(f"- {item.get('title', '')}\n  {item.get('url', '')}\n  {item.get('description', '')}")
        return "\n".join(lines)

    def _search_brave(self, query: str) -> str:
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.settings.brave_api_key,
        }
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 5},
            headers=headers,
            timeout=self.settings.search_timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        items = [
            {
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "description": str(item.get("description", "")),
            }
            for item in data.get("web", {}).get("results", [])[:5]
        ]
        return self._format_results("brave", items)

    def _search_duckduckgo(self, query: str) -> str:
        url = f"https://duckduckgo.com/html/?q={quote(query)}"
        resp = requests.get(url, timeout=self.settings.search_timeout_seconds)
        resp.raise_for_status()
        html = resp.text
        items: list[dict[str, str]] = []
        marker = 'class="result__a"'
        for chunk in html.split(marker)[1:6]:
            href_part = chunk.split("href=", 1)[-1]
            href = href_part.split('"', 2)[1] if '"' in href_part else ""
            title = chunk.split(">", 1)[-1].split("</a>", 1)[0].strip()
            items.append({"title": title, "url": href, "description": ""})
        return self._format_results("duckduckgo", items)

    @staticmethod
    def _format_search_error(exc: Exception) -> str:
        if isinstance(exc, requests.HTTPError):
            code = exc.response.status_code if exc.response is not None else "unknown"
            if code == 401 or code == 403:
                return "Search provider authentication failed. Check API key/permissions."
            if code == 429:
                return "Search provider rate limit reached. Try again later."
            return f"Search provider HTTP error: status={code}"
        if isinstance(exc, requests.Timeout):
            return "Search timed out. Try a shorter query or retry."
        if isinstance(exc, requests.RequestException):
            return f"Search network error: {exc}"
        return f"Search failed: {exc}"

    async def run(self, args: dict) -> ToolResult:
        action = args.get("action")
        if action == "search_web":
            query = args.get("query", "").strip()
            if not query:
                return ToolResult(ok=False, content="query is required")
            provider = str(args.get("provider") or "auto").strip().lower()
            if provider not in {"auto", "brave", "duckduckgo"}:
                return ToolResult(ok=False, content="provider must be one of auto|brave|duckduckgo")
            try:
                if provider == "brave":
                    if not self.settings.brave_api_key:
                        return ToolResult(ok=False, content="Brave API key is not configured")
                    return ToolResult(ok=True, content=self._search_brave(query))
                if provider == "duckduckgo":
                    return ToolResult(ok=True, content=self._search_duckduckgo(query))

                # provider == auto
                if self.settings.brave_api_key:
                    try:
                        return ToolResult(ok=True, content=self._search_brave(query))
                    except Exception as brave_exc:  # noqa: BLE001
                        fallback = self._search_duckduckgo(query)
                        note = self._format_search_error(brave_exc)
                        return ToolResult(ok=True, content=f"{fallback}\n\nnote: brave unavailable, fallback used ({note})")
                return ToolResult(ok=True, content=self._search_duckduckgo(query))
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=self._format_search_error(exc))

        if action == "fetch_url":
            url = args.get("url", "").strip()
            if not url:
                return ToolResult(ok=False, content="url is required")
            if not self._allow_url(url):
                return ToolResult(ok=False, content="URL blocked by policy")
            try:
                resp = requests.get(url, timeout=self.settings.search_timeout_seconds)
                resp.raise_for_status()
                md = html_to_md(resp.text)
                return ToolResult(ok=True, content=md[:12000])
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, content=f"fetch failed: {exc}")

        return ToolResult(ok=False, content=f"Unsupported action: {action}")
