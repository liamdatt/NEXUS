from __future__ import annotations

import json
from pathlib import Path

from nexus.config import Settings
from nexus.memory.store import MemoryStore
from nexus.skills.loader import load_skill_documents
from nexus.tools.base import ToolRegistry


class ContextBuilder:
    PROMPT_FILES = ("system.md", "SOUL.md", "IDENTITY.md", "AGENTS.md")

    def __init__(self, settings: Settings, memory: MemoryStore, tools: ToolRegistry) -> None:
        self.settings = settings
        self.memory = memory
        self.tools = tools

    def _read_prompt_file(self, file_name: str, required: bool = False) -> str:
        path = self.settings.prompts_dir / file_name
        if not path.exists():
            if required:
                raise RuntimeError(f"Required prompt file missing: {path}")
            return ""
        return path.read_text(encoding="utf-8").strip()

    @staticmethod
    def _clip(text: str, max_chars: int) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}...(truncated)"

    def _build_prompt_sections(self, query: str) -> str:
        sections: list[str] = []

        system_text = self._read_prompt_file("system.md", required=True)
        sections.append(system_text)

        for name in ("SOUL.md", "IDENTITY.md", "AGENTS.md"):
            text = self._read_prompt_file(name, required=False)
            if text:
                sections.append(text)

        tools_json = json.dumps([spec.model_dump() for spec in self.tools.specs()], ensure_ascii=False, indent=2)
        sections.append(f"## Tools\nAvailable tool specs (JSON schema):\n{tools_json}")

        skills = load_skill_documents(self.settings.skills_dir)
        if skills:
            skill_lines = ["## Skills"]
            for skill in skills:
                skill_lines.append(f"### {skill.name}\n{skill.content}")
            sections.append("\n\n".join(skill_lines))

        long_term = self.memory.relevant_memory(query=query, limit=self.settings.max_memory_sections)
        if long_term:
            lt_text = "\n\n".join(f"### Memory Snippet {idx + 1}\n{snippet}" for idx, snippet in enumerate(long_term))
            sections.append(f"## Long-Term Memory\n{lt_text}")

        recent_notes = self.memory.recent_daily_notes(days=self.settings.memory_recent_days)
        if recent_notes:
            per_note_limit = max(1000, self.settings.agent_observation_max_chars // 2)
            note_parts = []
            for day, text in recent_notes:
                note_parts.append(f"### {day}\n{self._clip(text, per_note_limit)}")
            notes_text = "\n\n".join(note_parts)
            sections.append(f"## Recent Daily Notes\n{notes_text}")

        return "\n\n".join(section for section in sections if section.strip())

    def build_messages(
        self,
        *,
        chat_id: str,
        user_text: str,
        step_messages: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        system_prompt = self._build_prompt_sections(query=user_text)
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend(self.memory.session_history(chat_id)[-12:])
        messages.append({"role": "user", "content": user_text})
        if step_messages:
            messages.extend(step_messages)
        return messages


def ensure_prompt_scaffold(prompts_dir: Path) -> None:
    prompts_dir.mkdir(parents=True, exist_ok=True)
    defaults = {
        "system.md": "# Nexus System Prompt\n",
        "SOUL.md": "# Soul\n",
        "IDENTITY.md": "# Identity\n",
        "AGENTS.md": "# Agent Notes\n",
    }
    for name, fallback in defaults.items():
        path = prompts_dir / name
        if not path.exists():
            path.write_text(fallback, encoding="utf-8")
