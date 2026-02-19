from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.errors import FPDFUnicodeEncodingException
from pypdf import PdfReader, PdfWriter

from nexus.config import Settings
from nexus.tools.base import BaseTool, ToolResult, ToolSpec

PDF_MIME = "application/pdf"
UNICODE_FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
)
LATIN1_REPLACEMENTS = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": ",",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2026": "...",
        "\u00a0": " ",
    }
)


def _to_path_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out
    return []


class PdfTool(BaseTool):
    name = "pdf"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description="Create, inspect, extract, merge, and AI-edit PDFs in workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "inspect", "extract_text", "merge", "edit_page_nl"],
                    },
                    "path": {"type": "string"},
                    "output_path": {"type": "string"},
                    "input_paths": {"type": "array", "items": {"type": "string"}},
                    "page": {"type": "integer"},
                    "page_index_mode": {"type": "string", "enum": ["auto", "zero_based", "one_based"]},
                    "instruction": {"type": "string"},
                    "text": {"type": "string"},
                    "title": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["action"],
            },
        )

    def _resolve_workspace_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.settings.workspace / candidate
        resolved = candidate.resolve()
        workspace = self.settings.workspace.resolve()
        if workspace != resolved and workspace not in resolved.parents:
            raise PermissionError("path escapes workspace")
        return resolved

    @staticmethod
    def _artifact(path: Path) -> dict[str, Any]:
        return {
            "type": "document",
            "path": str(path),
            "file_name": path.name,
            "mime_type": PDF_MIME,
        }

    @staticmethod
    def _write_preview(action: str, details: list[str]) -> str:
        return "\n".join(
            [
                "PDF write operation requires confirmation.",
                f"action={action}",
                *details,
                "Reply YES to proceed or NO to cancel.",
            ]
        )

    @staticmethod
    def _verify_pdf(path: Path) -> bool:
        try:
            reader = PdfReader(str(path))
            _ = len(reader.pages)
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _candidate_pages(page: int, mode: str) -> list[int]:
        if mode == "zero_based":
            return [page]
        if mode == "one_based":
            return [page - 1]

        candidates: list[int] = []
        for candidate in (page, page - 1):
            if candidate >= 0 and candidate not in candidates:
                candidates.append(candidate)
        return candidates

    @staticmethod
    def _latin1_safe_text(text: str) -> str:
        normalized = text.translate(LATIN1_REPLACEMENTS)
        return normalized.encode("latin-1", errors="replace").decode("latin-1")

    @staticmethod
    def _build_pdf(*, title: str, text: str, unicode_enabled: bool) -> FPDF:
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        body = text
        heading = title

        if unicode_enabled:
            regular_font = Path(UNICODE_FONT_CANDIDATES[0][0])
            bold_font = Path(UNICODE_FONT_CANDIDATES[0][1])
            for regular_candidate, bold_candidate in UNICODE_FONT_CANDIDATES:
                regular_path = Path(regular_candidate)
                bold_path = Path(bold_candidate)
                if regular_path.exists():
                    regular_font = regular_path
                    bold_font = bold_path
                    break
            pdf.add_font("NexusSans", "", str(regular_font))
            if bold_font.exists():
                pdf.add_font("NexusSans", "B", str(bold_font))
            pdf.set_font("NexusSans", size=12)
        else:
            heading = PdfTool._latin1_safe_text(title)
            body = PdfTool._latin1_safe_text(text)
            pdf.set_font("Helvetica", size=12)

        if heading:
            if unicode_enabled:
                pdf.set_font("NexusSans", style="B", size=16)
            else:
                pdf.set_font("Helvetica", style="B", size=16)
            pdf.multi_cell(0, 10, heading)
            pdf.ln(2)
            if unicode_enabled:
                pdf.set_font("NexusSans", size=12)
            else:
                pdf.set_font("Helvetica", size=12)
        pdf.multi_cell(0, 8, body)
        return pdf

    @staticmethod
    def _unicode_font_available() -> bool:
        return any(Path(regular).exists() for regular, _bold in UNICODE_FONT_CANDIDATES)

    @staticmethod
    def _nano_pdf_command_prefixes() -> list[list[str]]:
        prefixes: list[list[str]] = []
        cli = shutil.which("nano-pdf")
        if cli:
            prefixes.append([cli])
        prefixes.append([sys.executable, "-m", "nano_pdf"])
        prefixes.append([sys.executable, "-m", "nano_pdf.cli"])

        deduped: list[list[str]] = []
        seen: set[tuple[str, ...]] = set()
        for prefix in prefixes:
            key = tuple(prefix)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(prefix)
        return deduped

    async def run(self, args: dict[str, Any]) -> ToolResult:
        action = str(args.get("action") or "")

        if action == "create":
            raw_path = str(args.get("path") or "").strip()
            if not raw_path:
                return ToolResult(ok=False, content="path is required for create")
            try:
                file_path = self._resolve_workspace_path(raw_path)
            except PermissionError as exc:
                return ToolResult(ok=False, content=f"path rejected: {exc}")
            if file_path.suffix.lower() != ".pdf":
                file_path = file_path.with_suffix(".pdf")
            text = str(args.get("text") or "").strip() or "(empty document)"
            title = str(args.get("title") or "").strip()
            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=self._write_preview(action, [f"path={file_path}", f"title={title or '-'}"]),
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )
            file_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                pdf = self._build_pdf(title=title, text=text, unicode_enabled=self._unicode_font_available())
                pdf.output(str(file_path))
            except FPDFUnicodeEncodingException:
                # Last-resort fallback: map unsupported glyphs to latin-1 safe text.
                pdf = self._build_pdf(title=title, text=text, unicode_enabled=False)
                pdf.output(str(file_path))
            return ToolResult(ok=True, content=f"PDF created.\npath={file_path}", artifacts=[self._artifact(file_path)])

        if action == "inspect":
            raw_path = str(args.get("path") or "").strip()
            if not raw_path:
                return ToolResult(ok=False, content="path is required for inspect")
            try:
                file_path = self._resolve_workspace_path(raw_path)
            except PermissionError as exc:
                return ToolResult(ok=False, content=f"path rejected: {exc}")
            if not file_path.exists() or not file_path.is_file():
                return ToolResult(ok=False, content=f"pdf not found: {file_path}")

            reader = PdfReader(str(file_path))
            metadata = reader.metadata or {}
            lines = [f"PDF inspect.\npath={file_path}\npages={len(reader.pages)}"]
            if metadata:
                keys = ["/Title", "/Author", "/Creator", "/Producer", "/CreationDate", "/ModDate"]
                meta_bits = []
                for key in keys:
                    value = metadata.get(key)
                    if value:
                        meta_bits.append(f"{key[1:].lower()}={value}")
                if meta_bits:
                    lines.append("metadata=" + ", ".join(meta_bits))

            previews: list[str] = []
            for idx, page in enumerate(reader.pages[:3]):
                text = (page.extract_text() or "").strip()
                if text:
                    previews.append(f"page_{idx}_preview={text[:240]}")
            if previews:
                lines.extend(previews)
            return ToolResult(ok=True, content="\n".join(lines))

        if action == "extract_text":
            raw_path = str(args.get("path") or "").strip()
            if not raw_path:
                return ToolResult(ok=False, content="path is required for extract_text")
            try:
                file_path = self._resolve_workspace_path(raw_path)
            except PermissionError as exc:
                return ToolResult(ok=False, content=f"path rejected: {exc}")
            if not file_path.exists() or not file_path.is_file():
                return ToolResult(ok=False, content=f"pdf not found: {file_path}")
            reader = PdfReader(str(file_path))
            page = args.get("page")
            if isinstance(page, int):
                if page < 0 or page >= len(reader.pages):
                    return ToolResult(ok=False, content=f"page out of range: {page}")
                text = reader.pages[page].extract_text() or ""
                return ToolResult(ok=True, content=f"PDF text extracted.\npath={file_path}\npage={page}\n\n{text[:12000]}")
            chunks: list[str] = []
            for idx, pdf_page in enumerate(reader.pages):
                text = pdf_page.extract_text() or ""
                chunks.append(f"--- page {idx} ---\n{text}")
            merged = "\n\n".join(chunks)
            if len(merged) > 12000:
                merged = merged[:12000] + "...(truncated)"
            return ToolResult(ok=True, content=f"PDF text extracted.\npath={file_path}\n\n{merged}")

        if action == "merge":
            raw_inputs = _to_path_list(args.get("input_paths"))
            raw_output = str(args.get("output_path") or args.get("path") or "").strip()
            if len(raw_inputs) < 2:
                return ToolResult(ok=False, content="input_paths requires at least two PDFs")
            if not raw_output:
                return ToolResult(ok=False, content="output_path is required for merge")
            try:
                input_paths = [self._resolve_workspace_path(item) for item in raw_inputs]
                output_path = self._resolve_workspace_path(raw_output)
            except PermissionError as exc:
                return ToolResult(ok=False, content=f"path rejected: {exc}")
            for item in input_paths:
                if not item.exists() or not item.is_file():
                    return ToolResult(ok=False, content=f"pdf not found: {item}")
            if output_path.suffix.lower() != ".pdf":
                output_path = output_path.with_suffix(".pdf")
            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=self._write_preview(action, [f"output_path={output_path}", f"input_count={len(input_paths)}"]),
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            writer = PdfWriter()
            for item in input_paths:
                reader = PdfReader(str(item))
                for page in reader.pages:
                    writer.add_page(page)
            with output_path.open("wb") as fp:
                writer.write(fp)
            return ToolResult(
                ok=True,
                content=f"PDFs merged.\noutput_path={output_path}\ninputs={len(input_paths)}",
                artifacts=[self._artifact(output_path)],
            )

        if action == "edit_page_nl":
            raw_path = str(args.get("path") or "").strip()
            instruction = str(args.get("instruction") or "").strip()
            if not raw_path:
                return ToolResult(ok=False, content="path is required for edit_page_nl")
            if not instruction:
                return ToolResult(ok=False, content="instruction is required for edit_page_nl")
            page = args.get("page")
            if not isinstance(page, int):
                return ToolResult(ok=False, content="page is required for edit_page_nl")
            page_index_mode = str(args.get("page_index_mode") or "auto").strip().lower() or "auto"
            if page_index_mode not in {"auto", "zero_based", "one_based"}:
                return ToolResult(ok=False, content=f"unsupported page_index_mode: {page_index_mode}")

            raw_output = str(args.get("output_path") or raw_path).strip()
            try:
                input_path = self._resolve_workspace_path(raw_path)
                output_path = self._resolve_workspace_path(raw_output)
            except PermissionError as exc:
                return ToolResult(ok=False, content=f"path rejected: {exc}")
            if not input_path.exists() or not input_path.is_file():
                return ToolResult(ok=False, content=f"pdf not found: {input_path}")
            if output_path.suffix.lower() != ".pdf":
                output_path = output_path.with_suffix(".pdf")
            if not args.get("confirmed"):
                return ToolResult(
                    ok=False,
                    content=self._write_preview(
                        action,
                        [
                            f"input_path={input_path}",
                            f"output_path={output_path}",
                            f"page={page}",
                            f"page_index_mode={page_index_mode}",
                        ],
                    ),
                    requires_confirmation=True,
                    risk_level="high",
                    proposed_action={"action": action, **args},
                )

            output_path.parent.mkdir(parents=True, exist_ok=True)
            if output_path.resolve() != input_path.resolve():
                shutil.copy2(input_path, output_path)

            attempts = self._candidate_pages(page, page_index_mode)
            errors: list[str] = []
            missing_dependency = True
            module_missing_error = False
            non_dependency_error = False
            command_prefixes = self._nano_pdf_command_prefixes()
            for candidate in attempts:
                if candidate < 0:
                    continue
                for command_prefix in command_prefixes:
                    cmd = [*command_prefix, "edit", str(output_path), str(candidate), instruction]
                    try:
                        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
                    except FileNotFoundError as exc:
                        errors.append(f"page={candidate}: dependency not found ({' '.join(command_prefix)})")
                        continue
                    missing_dependency = False
                    if proc.returncode != 0:
                        stderr = (proc.stderr or proc.stdout or "").strip()
                        lowered = stderr.lower()
                        if "no module named" in lowered and "nano_pdf" in lowered:
                            module_missing_error = True
                        else:
                            non_dependency_error = True
                        errors.append(f"page={candidate}: {stderr or 'unknown error'}")
                        continue
                    if not self._verify_pdf(output_path):
                        non_dependency_error = True
                        errors.append(f"page={candidate}: output validation failed")
                        continue
                    return ToolResult(
                        ok=True,
                        content=(
                            "PDF page edit complete.\n"
                            f"output_path={output_path}\n"
                            f"requested_page={page}\n"
                            f"applied_page_index={candidate}\n"
                            f"page_index_mode={page_index_mode}"
                        ),
                        artifacts=[self._artifact(output_path)],
                    )

            if missing_dependency or (module_missing_error and not non_dependency_error):
                return ToolResult(
                    ok=False,
                    content=(
                        "PDF edit dependency is unavailable in runtime. "
                        "Rebuild/deploy runtime image with nano-pdf."
                    ),
                )
            details = "; ".join(errors) if errors else "unknown error"
            return ToolResult(ok=False, content=f"nano-pdf edit failed: {details}")

        return ToolResult(ok=False, content=f"Unsupported action: {action}")
