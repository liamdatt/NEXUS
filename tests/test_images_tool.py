from __future__ import annotations

import asyncio
from pathlib import Path

from nexus.config import Settings
from nexus.tools.images import ImagesTool


class _FakeImageClient:
    def __init__(self) -> None:
        self.last_generate: dict[str, object] | None = None
        self.last_edit: dict[str, object] | None = None

    def generate(
        self,
        *,
        prompt: str,
        model: str,
        size: str | None = None,
        resolution: str | None = None,
        output_path: str | None = None,
    ):
        self.last_generate = {
            "prompt": prompt,
            "model": model,
            "size": size,
            "resolution": resolution,
            "output_path": output_path,
        }
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

    def edit(
        self,
        *,
        prompt: str,
        input_paths: list[str],
        model: str,
        size: str | None = None,
        resolution: str | None = None,
        output_path: str | None = None,
    ):
        self.last_edit = {
            "prompt": prompt,
            "model": model,
            "size": size,
            "resolution": resolution,
            "output_path": output_path,
            "input_paths": input_paths,
        }
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


def test_images_generate_when_confirmed_with_controls(tmp_path: Path):
    client = _FakeImageClient()
    tool = ImagesTool(_settings(tmp_path), client=client)
    result = asyncio.run(
        tool.run(
            {
                "action": "generate",
                "prompt": "sunset",
                "size": "1024x1024",
                "resolution": "2K",
                "output_path": "out/generated.png",
                "confirmed": True,
            }
        )
    )
    assert result.ok
    assert result.artifacts[0]["type"] == "image"
    assert client.last_generate is not None
    assert client.last_generate["model"] == "google/gemini-2.5-flash-image"
    assert client.last_generate["size"] == "1024x1024"
    assert client.last_generate["resolution"] == "2K"
    assert client.last_generate["output_path"] == "out/generated.png"


def test_images_edit_requires_input_paths(tmp_path: Path):
    tool = ImagesTool(_settings(tmp_path), client=_FakeImageClient())
    result = asyncio.run(tool.run({"action": "edit", "prompt": "make it blue", "confirmed": True}))
    assert not result.ok


def test_images_edit_when_confirmed(tmp_path: Path):
    source = tmp_path / "workspace" / "input.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"abc")

    client = _FakeImageClient()
    tool = ImagesTool(_settings(tmp_path), client=client)
    result = asyncio.run(
        tool.run(
            {
                "action": "edit",
                "prompt": "make it blue",
                "input_paths": ["input.png"],
                "size": "1344x768",
                "resolution": "1K",
                "output_path": "out/edited.png",
                "confirmed": True,
            }
        )
    )
    assert result.ok
    assert client.last_edit is not None
    assert client.last_edit["size"] == "1344x768"
    assert client.last_edit["resolution"] == "1K"
    assert client.last_edit["output_path"] == "out/edited.png"
