from __future__ import annotations

from nexus.core.text_format import format_whatsapp_text


def test_whatsapp_format_converts_headings_bullets_links_and_emphasis() -> None:
    raw = (
        "## International News\r\n"
        "\r\n"
        "* Item one\r\n"
        "•\u2060  \u200bItem two\r\n"
        "[Source](https://example.com/story)\r\n"
        "---\r\n"
        "__Bold__ and **Strong**"
    )
    formatted = format_whatsapp_text(raw)
    assert formatted == (
        "*International News*\n\n"
        "- Item one\n"
        "- Item two\n"
        "Source (https://example.com/story)\n\n"
        "*Bold* and *Strong*"
    )


def test_whatsapp_format_preserves_fenced_code_blocks() -> None:
    raw = (
        "## Title\n"
        "```python\n"
        "## keep heading\n"
        "* keep bullet\n"
        "[keep](https://example.com)\n"
        "```\n"
        "**done**"
    )
    formatted = format_whatsapp_text(raw)
    assert formatted == (
        "*Title*\n"
        "```python\n"
        "## keep heading\n"
        "* keep bullet\n"
        "[keep](https://example.com)\n"
        "```\n"
        "*done*"
    )


def test_whatsapp_format_collapses_excessive_blank_lines_and_trims() -> None:
    raw = "Line 1   \n\n\n\nLine 2\n   \nLine 3  \n\n\n"
    formatted = format_whatsapp_text(raw)
    assert formatted == "Line 1\n\nLine 2\n\nLine 3"
