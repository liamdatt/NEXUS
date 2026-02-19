from __future__ import annotations

import re

_ZERO_WIDTH_CHARS = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_HRULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_MARKDOWN_LIST_RE = re.compile(r"^\s*[-+*]\s+(.*)$")
_UNICODE_LIST_RE = re.compile(r"^\s*[•●◦○▪▫‣⁃∙]+\s*(.*)$")
_STRONG_STARS_RE = re.compile(r"(?<!\*)\*\*([^*\n]+)\*\*(?!\*)")
_STRONG_UNDERSCORE_RE = re.compile(r"(?<!_)__([^_\n]+)__(?!_)")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")


def _remove_zero_width(text: str) -> str:
    return "".join(ch for ch in text if ch not in _ZERO_WIDTH_CHARS)


def _normalize_list_line(line: str) -> str:
    markdown_match = _MARKDOWN_LIST_RE.match(line)
    if markdown_match:
        item = markdown_match.group(1).strip()
        return f"- {item}" if item else "-"

    unicode_match = _UNICODE_LIST_RE.match(line)
    if unicode_match:
        item = unicode_match.group(1).strip()
        return f"- {item}" if item else "-"

    return line


def _normalize_inline(line: str) -> str:
    line = _LINK_RE.sub(r"\1 (\2)", line)
    line = _STRONG_STARS_RE.sub(r"*\1*", line)
    line = _STRONG_UNDERSCORE_RE.sub(r"*\1*", line)
    return line


def _collapse_blank_lines(lines: list[str]) -> list[str]:
    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        if not line.strip():
            if previous_blank:
                continue
            collapsed.append("")
            previous_blank = True
            continue
        collapsed.append(line.rstrip())
        previous_blank = False

    while collapsed and not collapsed[0].strip():
        collapsed.pop(0)
    while collapsed and not collapsed[-1].strip():
        collapsed.pop()
    return collapsed


def format_whatsapp_text(text: str) -> str:
    if not text:
        return ""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    in_code_block = False
    out_lines: list[str] = []

    for raw_line in normalized.split("\n"):
        line = raw_line.rstrip()
        fence = line.strip().startswith("```")
        if fence:
            in_code_block = not in_code_block
            out_lines.append(line)
            continue

        if in_code_block:
            out_lines.append(line)
            continue

        line = _remove_zero_width(line)
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            heading = heading_match.group(1).strip()
            out_lines.append(f"*{heading}*" if heading else "")
            continue

        if _HRULE_RE.match(line):
            out_lines.append("")
            continue

        line = _normalize_list_line(line)
        line = _normalize_inline(line).strip()
        out_lines.append(line)

    return "\n".join(_collapse_blank_lines(out_lines))
