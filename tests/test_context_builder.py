from pathlib import Path
from typing import Any

from nexus.config import Settings
from nexus.llm.context import ContextBuilder
from nexus.memory.store import MemoryStore
from nexus.tools.base import BaseTool, ToolRegistry, ToolResult, ToolSpec


class _DummyTool(BaseTool):
    name = "dummy"

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="dummy tool",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        )

    async def run(self, args: dict[str, Any]) -> ToolResult:  # noqa: ARG002
        return ToolResult(ok=True, content="dummy")


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        prompts_dir=tmp_path / "prompts",
        skills_dir=tmp_path / "skills",
        cli_enabled=False,
    )


def test_context_builder_loads_prompts_skills_and_memory(tmp_path: Path):
    settings = _settings(tmp_path)
    settings.prompts_dir.mkdir(parents=True, exist_ok=True)
    settings.skills_dir.mkdir(parents=True, exist_ok=True)

    (settings.prompts_dir / "system.md").write_text("SYSTEM CORE", encoding="utf-8")
    (settings.prompts_dir / "SOUL.md").write_text("SOUL CORE", encoding="utf-8")
    (settings.prompts_dir / "IDENTITY.md").write_text("IDENTITY CORE", encoding="utf-8")
    (settings.prompts_dir / "AGENTS.md").write_text("AGENTS CORE", encoding="utf-8")

    skill_dir = settings.skills_dir / "filesystem"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("Filesystem instructions", encoding="utf-8")

    memory = MemoryStore(settings.memories_dir)
    memory.append_long_term_note("Jamaica travel preference: beach")
    (settings.memories_dir / "2026-02-10.md").write_text("# Journal 2026-02-10\n- latest note", encoding="utf-8")
    memory.append_turn("chat-1", "assistant", "prior assistant")

    tools = ToolRegistry()
    tools.register(_DummyTool())

    builder = ContextBuilder(settings=settings, memory=memory, tools=tools)
    messages = builder.build_messages(chat_id="chat-1", user_text="Tell me about Jamaica")

    assert messages[0]["role"] == "system"
    system_text = messages[0]["content"]
    assert "SYSTEM CORE" in system_text
    assert "SOUL CORE" in system_text
    assert "IDENTITY CORE" in system_text
    assert "AGENTS CORE" in system_text
    assert "Filesystem instructions" in system_text
    assert "Jamaica travel preference" in system_text
    assert "2026-02-10" in system_text
    assert "dummy tool" in system_text

    # session history and current user message are appended after system
    assert any(msg["content"] == "prior assistant" for msg in messages[1:])
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "Tell me about Jamaica"


def test_context_builder_requires_system_prompt(tmp_path: Path):
    settings = _settings(tmp_path)
    memory = MemoryStore(settings.memories_dir)
    tools = ToolRegistry()
    tools.register(_DummyTool())
    builder = ContextBuilder(settings=settings, memory=memory, tools=tools)

    try:
        builder.build_messages(chat_id="chat-1", user_text="hello")
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "Required prompt file missing" in str(exc)

    assert raised is True
