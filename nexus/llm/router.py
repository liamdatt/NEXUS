from __future__ import annotations

import asyncio
from typing import Any

from litellm import completion

from nexus.config import Settings


class LLMRouter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _normalize_model(self, model: str) -> str:
        if self.settings.openrouter_api_key and not model.startswith("openrouter/"):
            return f"openrouter/{model}"
        return model

    def _model_chain(self, complex_task: bool) -> list[str]:
        if complex_task:
            chain = [self.settings.llm_complex_model, self.settings.llm_primary_model, self.settings.llm_fallback_model]
        else:
            chain = [self.settings.llm_primary_model, self.settings.llm_fallback_model, self.settings.llm_complex_model]
        return [self._normalize_model(model) for model in chain]

    async def complete_json(self, messages: list[dict[str, str]], complex_task: bool = False) -> dict[str, Any]:
        last_error = None
        for model in self._model_chain(complex_task):
            try:
                kwargs = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": self.settings.llm_max_tokens,
                    "timeout": self.settings.llm_timeout_seconds,
                    "response_format": {"type": "json_object"},
                }
                if self.settings.openrouter_api_key:
                    kwargs["api_base"] = self.settings.openrouter_base_url
                    kwargs["api_key"] = self.settings.openrouter_api_key
                response = await asyncio.to_thread(completion, **kwargs)
                text = response.choices[0].message.content
                return {
                    "ok": True,
                    "model": model,
                    "content": text,
                    "usage": getattr(response, "usage", None),
                }
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)

        return {"ok": False, "error": last_error or "unknown model failure"}
