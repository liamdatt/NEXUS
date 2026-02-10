import asyncio

from nexus.tools.files import FileSystemTool


def test_filesystem_tool_sandbox_and_confirmation(tmp_path):
    tool = FileSystemTool(tmp_path)

    write_first = asyncio.run(
        tool.run({"action": "write_file", "path": "notes/a.txt", "content": "hello"})
    )
    assert write_first.ok

    overwrite = asyncio.run(
        tool.run({"action": "write_file", "path": "notes/a.txt", "content": "updated"})
    )
    assert not overwrite.ok
    assert overwrite.requires_confirmation

    overwrite_confirmed = asyncio.run(
        tool.run(
            {
                "action": "write_file",
                "path": "notes/a.txt",
                "content": "updated",
                "confirmed": True,
            }
        )
    )
    assert overwrite_confirmed.ok

    outside = asyncio.run(tool.run({"action": "read_file", "path": "../../etc/passwd"}))
    assert not outside.ok
    assert "sandbox" in outside.content.lower() or "permission" in outside.content.lower()


def test_list_dir_empty_is_explicit(tmp_path):
    tool = FileSystemTool(tmp_path)
    result = asyncio.run(tool.run({"action": "list_dir", "path": "."}))
    assert result.ok
    assert "empty" in result.content.lower()
