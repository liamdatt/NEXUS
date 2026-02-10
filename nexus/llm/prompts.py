from __future__ import annotations

import json

from nexus.tools.base import ToolSpec


def build_system_prompt(tools: list[ToolSpec], memory_snippets: list[str]) -> str:
    """Compatibility helper for older call sites.

    New logic should use nexus.llm.context.ContextBuilder.
    """
    tools_json = json.dumps([tool.model_dump() for tool in tools], indent=2)
    memory_text = "\n\n".join(memory_snippets) if memory_snippets else "(no relevant long-term memory)"

    return (
        "You are Nexus, an action-oriented assistant.\n\n"
        "Return strict JSON object only with:\n"
        '- "thought": string (required)\n'
        '- "call": {"name":"<tool>","arguments":{...}} (optional)\n'
        '- "response": string (optional)\n'
        "Exactly one of call/response must be present.\n\n"
        f"Available tools:\n{tools_json}\n\n"
        f"Relevant long-term memory:\n{memory_text}"
    )


def build_turn_messages(system_prompt: str, chat_history: list[dict[str, str]], user_text: str) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(chat_history[-12:])
    messages.append({"role": "user", "content": user_text})
    return messages
