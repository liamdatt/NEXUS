from __future__ import annotations

import asyncio
from pathlib import Path

from nexus.config import Settings
from nexus.tools.images import ImagesTool


class _FakeImageClient:
    def generate(self, *, prompt: str, model: str):
        assert prompt
        assert model
        return {
            "text": "generated",
            "artifacts": [
                {
                    "path": "/tmp/generated.png",
                    "file_name": "generated.png",
                    "mime_type": "image/png",
                }
            ],
        }

    def edit(self, *, prompt: str, input_paths: list[str], model: str):
        assert prompt
        assert model
        assert input_paths
        return {
            "text": "edited",
            "artifacts": [
                {
                    "path": "/tmp/edited.png",
                    "file_name": "edited.png",
                    "mime_type": "image/png",
                }
            ],
        }


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "nexus.db",
        workspace=tmp_path / "workspace",
        memories_dir=tmp_path / "memories",
        openrouter_api_key="sk-test",
    )


def test_images_generate_requires_confirmation(tmp_path: Path):
    tool = ImagesTool(_settings(tmp_path), client=_FakeImageClient())
    result = asyncio.run(tool.run({"action": "generate", "prompt": "sunset"}))
    assert not result.ok
    assert result.requires_confirmation


def test_images_generate_when_confirmed(tmp_path: Path):
    tool = ImagesTool(_settings(tmp_path), client=_FakeImageClient())
    result = asyncio.run(
        tool.run({"action": "generate", "prompt": "sunset", "confirmed": True})
    )
    assert result.ok
    assert result.artifacts[0]["type"] == "image"


def test_images_edit_requires_input_paths(tmp_path: Path):
    tool = ImagesTool(_settings(tmp_path), client=_FakeImageClient())
    result = asyncio.run(tool.run({"action": "edit", "prompt": "make it blue", "confirmed": True}))
    assert not result.ok


def test_images_edit_when_confirmed(tmp_path: Path):
    source = tmp_path / "workspace" / "input.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"abc")

    tool = ImagesTool(_settings(tmp_path), client=_FakeImageClient())
    result = asyncio.run(
        tool.run(
            {
                "action": "edit",
                "prompt": "make it blue",
                "input_paths": ["input.png"],
                "confirmed": True,
            }
        )
    )
    assert result.ok
