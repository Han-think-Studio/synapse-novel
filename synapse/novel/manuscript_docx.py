"""Read the visible body text and paragraph styles from a bounded DOCX archive."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

MAX_XML_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 4096

_WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DOCUMENT = "word/document.xml"
_STYLES = "word/styles.xml"
_BODY = f"{{{_WORD}}}body"
_PARAGRAPH = f"{{{_WORD}}}p"
_PARAGRAPH_PROPERTIES = f"{{{_WORD}}}pPr"
_PARAGRAPH_STYLE = f"{{{_WORD}}}pStyle"
_STYLE = f"{{{_WORD}}}style"
_STYLE_NAME = f"{{{_WORD}}}name"
_TEXT = f"{{{_WORD}}}t"
_TAB = f"{{{_WORD}}}tab"
_BREAK = f"{{{_WORD}}}br"
_CARRIAGE_RETURN = f"{{{_WORD}}}cr"
_FIELD_CHAR = f"{{{_WORD}}}fldChar"
_FIELD_SIMPLE = f"{{{_WORD}}}fldSimple"
_INSTRUCTION_TEXT = f"{{{_WORD}}}instrText"
_DELETED = {f"{{{_WORD}}}del", f"{{{_WORD}}}moveFrom"}
_IGNORED_TEXTBOX = f"{{{_WORD}}}txbxContent"


class DocxReadError(ValueError):
    """A bounded DOCX read failed with a filename-specific message."""


@dataclass(frozen=True)
class Paragraph:
    text: str
    heading1: bool = False


def _xml_bytes(archive: ZipFile, info, *, filename: str) -> bytes:
    if info.file_size > MAX_XML_BYTES:
        raise DocxReadError(
            f"DOCX XML이 크기 제한({MAX_XML_BYTES} bytes)을 넘었습니다: {filename}"
        )
    try:
        raw = archive.read(info)
    except (BadZipFile, RuntimeError, OSError, EOFError, NotImplementedError) as exc:
        raise DocxReadError(f"DOCX XML을 읽을 수 없습니다: {filename}") from exc
    if len(raw) != info.file_size or len(raw) > MAX_XML_BYTES:
        raise DocxReadError(
            f"DOCX XML이 크기 제한({MAX_XML_BYTES} bytes)을 넘었거나 손상되었습니다: {filename}"
        )
    return raw


def _heading_style_ids(raw: bytes | None, *, filename: str) -> set[str]:
    if raw is None:
        return set()
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise DocxReadError(f"DOCX 스타일 XML 형식이 맞지 않습니다: {filename}") from exc
    result: set[str] = set()
    for style in root.iter(_STYLE):
        style_id = style.get(f"{{{_WORD}}}styleId")
        name = style.find(_STYLE_NAME)
        value = name.get(f"{{{_WORD}}}val") if name is not None else None
        if isinstance(style_id, str) and isinstance(value, str) and value.strip().casefold() == "heading 1":
            result.add(style_id)
    return result


def _paragraph_text(paragraph, field_depth: list[int]) -> str:
    pieces: list[str] = []

    def visit(node) -> None:
        if node.tag in _DELETED or node.tag == _IGNORED_TEXTBOX:
            return
        if node.tag in {_FIELD_SIMPLE, _INSTRUCTION_TEXT}:
            return
        if node.tag == _FIELD_CHAR:
            field_type = node.get(f"{{{_WORD}}}fldCharType")
            if field_type == "begin":
                field_depth[0] += 1
            elif field_type == "end" and field_depth[0]:
                field_depth[0] -= 1
            return
        if node.tag == _TEXT:
            if not field_depth[0]:
                pieces.append(node.text or "")
            return
        if node.tag == _TAB:
            if not field_depth[0]:
                pieces.append("\t")
            return
        if node.tag in {_BREAK, _CARRIAGE_RETURN}:
            if not field_depth[0]:
                pieces.append(" ")
            return
        for child in node:
            visit(child)

    visit(paragraph)
    return "".join(pieces)


def _body_paragraphs(body) -> list:
    paragraphs: list = []

    def visit(node) -> None:
        for child in node:
            if child.tag == _IGNORED_TEXTBOX:
                continue
            if child.tag == _PARAGRAPH:
                paragraphs.append(child)
                continue
            visit(child)

    visit(body)
    return paragraphs


def read_docx(path: str | Path) -> list[Paragraph]:
    """Return body paragraphs in document order, including paragraphs in tables.

    The archive entry count and uncompressed sizes of the only two XML parts read
    are checked before their contents are decompressed.
    """

    source = Path(path)
    filename = source.name
    try:
        with ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise DocxReadError(
                    f"DOCX 항목 수 제한({MAX_ARCHIVE_ENTRIES}개)을 넘었습니다: {filename}"
                )
            if any(info.flag_bits & 0x1 for info in entries):
                raise DocxReadError(f"암호화된 DOCX는 가져올 수 없습니다: {filename}")

            by_name: dict[str, list] = {}
            for info in entries:
                by_name.setdefault(info.filename, []).append(info)
            document_entries = by_name.get(_DOCUMENT, [])
            if not document_entries:
                raise DocxReadError(f"DOCX 본문 문서를 찾을 수 없습니다: {filename}")
            if len(document_entries) != 1 or len(by_name.get(_STYLES, [])) > 1:
                raise DocxReadError(f"DOCX XML 항목이 중복되어 있습니다: {filename}")

            document_info = document_entries[0]
            styles_entries = by_name.get(_STYLES, [])
            styles_info = styles_entries[0] if styles_entries else None
            document_raw = _xml_bytes(archive, document_info, filename=filename)
            styles_raw = _xml_bytes(archive, styles_info, filename=filename) if styles_info else None
    except DocxReadError:
        raise
    except (BadZipFile, OSError, EOFError, ValueError) as exc:
        raise DocxReadError(f"DOCX 압축 파일을 읽을 수 없습니다: {filename}") from exc

    try:
        document = ElementTree.fromstring(document_raw)
    except ElementTree.ParseError as exc:
        raise DocxReadError(f"DOCX 본문 XML 형식이 맞지 않습니다: {filename}") from exc
    body = document.find(_BODY)
    if body is None:
        raise DocxReadError(f"DOCX 본문을 찾을 수 없습니다: {filename}")

    heading_ids = _heading_style_ids(styles_raw, filename=filename)
    result: list[Paragraph] = []
    field_depth = [0]
    for paragraph in _body_paragraphs(body):
        style_id: str | None = None
        properties = paragraph.find(_PARAGRAPH_PROPERTIES)
        if properties is not None:
            style = properties.find(_PARAGRAPH_STYLE)
            if style is not None:
                style_id = style.get(f"{{{_WORD}}}val")
        result.append(Paragraph(_paragraph_text(paragraph, field_depth), style_id in heading_ids))
    return result


__all__ = ["MAX_ARCHIVE_ENTRIES", "MAX_XML_BYTES", "DocxReadError", "Paragraph", "read_docx"]
