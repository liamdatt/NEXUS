from pathlib import Path

from nexus.tui.envfile import EnvFile


def test_envfile_parse_and_upsert_preserves_comments_and_order(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comment line\n"
        "NEXUS_OPENROUTER_API_KEY=abc123\n"
        "export FOO='bar baz'\n"
        "RAW=ok\n",
        encoding="utf-8",
    )

    env = EnvFile.load(env_path)
    assert env.get("NEXUS_OPENROUTER_API_KEY") == "abc123"
    assert env.get("FOO") == "bar baz"
    assert env.get("RAW") == "ok"

    env.upsert("NEXUS_BRAVE_API_KEY", "brv-123")
    env.write()

    lines = env_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# comment line"
    assert lines[1] == "NEXUS_OPENROUTER_API_KEY=abc123"
    assert lines[2] == "export FOO='bar baz'"
    assert lines[3] == "RAW=ok"
    assert lines[4] == "NEXUS_BRAVE_API_KEY=brv-123"


def test_envfile_masked_display_hides_all_but_suffix(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("TOKEN=abcdefghijkl\nSHORT=abc\n", encoding="utf-8")

    env = EnvFile.load(env_path)
    assert env.masked("TOKEN") == "********ijkl"
    assert env.masked("SHORT") == "***"
    assert env.masked("MISSING") == ""
