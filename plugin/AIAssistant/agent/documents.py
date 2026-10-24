"""Bounded, dependency-free extraction for user supplied TXT and DOCX files."""
import io
import os
import posixpath
import unicodedata
import xml.etree.ElementTree as ElementTree
import zipfile

from .tools import ToolError


MAX_DOCUMENT_CHARS = 120000
MAX_DOCX_RAW = 24 * 1024 * 1024
MAX_DOCX_ENTRIES = 256
MAX_DOCX_ENTRY = 8 * 1024 * 1024
MAX_DOCX_TOTAL = 32 * 1024 * 1024
MAX_DOCX_RATIO = 200

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
STRICT_W_NS = "http://purl.oclc.org/ooxml/wordprocessingml/main"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_W = "{%s}" % W_NS
_CT = "{%s}" % CONTENT_TYPES_NS
_WORD_NAMESPACES = (W_NS, STRICT_W_NS)


def _error(message):
    raise ToolError(message)


def _check_filename(filename):
    if (not isinstance(filename, str) or not filename or len(filename) > 180 or
            re_has_filename_separator(filename) or filename in (".", "..")):
        _error("Требуется имя файла без пути.")
    extension = os.path.splitext(filename)[1].lower()
    if extension not in (".docx", ".txt"):
        if extension == ".doc":
            _error("Формат .doc не поддерживается. Сохраните документ как DOCX или TXT.")
        _error("Поддерживаются документы DOCX и TXT.")
    return extension


def re_has_filename_separator(filename):
    return any(char in filename for char in ("\\", "/", ":")) or any(ord(char) < 32 for char in filename)


def _check_document_chars(text):
    if not isinstance(text, str) or not text.strip():
        _error("Документ пустой.")
    if len(text) > MAX_DOCUMENT_CHARS:
        _error("Документ превышает лимит 120000 символов.")
    for char in text:
        if char in ("\t", "\n", "\r"):
            continue
        category = unicodedata.category(char)
        if category.startswith("C"):
            _error("Текстовый документ содержит двоичные или управляющие символы.")
    return text


def _decode_text(data):
    if not data:
        _error("Документ пустой.")
    if data.startswith(b"PK"):
        _error("TXT не должен быть ZIP или DOCX.")
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        try:
            text = data.decode("utf-16")
        except (UnicodeDecodeError, UnicodeError):
            _error("Не удалось декодировать TXT в UTF-16.")
        return _check_document_chars(text)
    try:
        text = data.decode("utf-8-sig")
    except (UnicodeDecodeError, UnicodeError):
        try:
            text = data.decode("cp1251")
        except (UnicodeDecodeError, UnicodeError):
            _error("Не удалось декодировать TXT.")
        cyrillic = sum(1 for char in text if "\u0400" <= char <= "\u04ff")
        if cyrillic < 2:
            _error("TXT не похож на текст в UTF-8 или CP1251.")
    if b"\x00" in data:
        _error("Текстовый документ содержит двоичные или управляющие символы.")
    return _check_document_chars(text)


def _forbidden_xml_declaration(data):
    lowered = data.lower()
    for marker in (b"<!doctype", b"<!entity"):
        if marker in lowered:
            return True
    for encoding in ("utf-16-le", "utf-16-be"):
        try:
            text = data.decode(encoding).lower()
        except (UnicodeDecodeError, UnicodeError):
            continue
        if "<!doctype" in text or "<!entity" in text:
            return True
    return False


def _parse_xml(data):
    if _forbidden_xml_declaration(data):
        _error("DOCX XML с DTD или сущностями запрещён.")
    try:
        return ElementTree.fromstring(data)
    except (ElementTree.ParseError, ValueError, UnicodeError):
        _error("Некорректный XML внутри DOCX.")


def _safe_zip_name(name):
    normalized_name = name.replace("\\", "/")
    if (not normalized_name or normalized_name.startswith("/") or
            normalized_name.startswith("//") or ":" in normalized_name or
            any(ord(char) < 32 for char in normalized_name)):
        _error("DOCX содержит небезопасный путь.")
    parts = normalized_name.split("/")
    if any(part == ".." for part in parts):
        _error("DOCX содержит небезопасный путь.")
    normalized = posixpath.normpath(normalized_name)
    if normalized in ("", ".") or normalized.startswith("../") or normalized == "..":
        _error("DOCX содержит небезопасный путь.")
    if any(part == "" for part in parts[:-1]) or any(part == "." for part in parts):
        _error("DOCX содержит ненормализованный путь.")
    return normalized.lower()


def _open_docx(data):
    if len(data) > MAX_DOCX_RAW:
        _error("DOCX превышает лимит 24 MiB.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, OSError, ValueError):
        _error("DOCX должен быть корректным ZIP-пакетом.")
    try:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_DOCX_ENTRIES:
            _error("DOCX содержит недопустимое число ZIP-записей.")
        seen = set()
        total = 0
        by_name = {}
        for item in entries:
            name = _safe_zip_name(item.filename)
            if name in seen:
                _error("DOCX содержит повторяющиеся ZIP-записи.")
            seen.add(name)
            if item.flag_bits & 0x1:
                _error("Зашифрованные DOCX не поддерживаются.")
            if item.file_size > MAX_DOCX_ENTRY:
                _error("ZIP-запись DOCX превышает лимит 8 MiB.")
            total += item.file_size
            if total > MAX_DOCX_TOTAL:
                _error("Распакованный DOCX превышает лимит 32 MiB.")
            if item.file_size > 1024 * 1024 and item.file_size > max(item.compress_size, 1) * MAX_DOCX_RATIO:
                _error("Сжатие ZIP-записи DOCX превышает безопасный коэффициент.")
            by_name[name] = item
        if "[content_types].xml" not in by_name or "word/document.xml" not in by_name:
            _error("DOCX не содержит обязательные части OOXML.")
        return archive, by_name
    except ToolError:
        archive.close()
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, RuntimeError, ValueError, KeyError):
        archive.close()
        _error("DOCX должен быть корректным ZIP-пакетом.")


def _read_entry(archive, item):
    try:
        data = archive.read(item)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, RuntimeError, ValueError):
        _error("Не удалось прочитать ZIP-запись DOCX.")
    if len(data) > MAX_DOCX_ENTRY:
        _error("ZIP-запись DOCX превышает лимит 8 MiB.")
    return data


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _is_word_tag(tag, local):
    return any(tag == "{%s}%s" % (namespace, local) for namespace in _WORD_NAMESPACES)


def _node_text(node):
    chunks = []
    for child in node.iter():
        local = _local_name(child.tag)
        if local in ("t", "instrText"):
            chunks.append(child.text or "")
        elif local == "tab":
            chunks.append("\t")
        elif local in ("br", "cr"):
            chunks.append("\n")
    return "".join(chunks)


def _paragraph_text(paragraph):
    return _node_text(paragraph)


def _iter_blocks(container):
    """Yield direct paragraphs/tables and those in supported content containers."""
    for child in container:
        if _is_word_tag(child.tag, "p") or _is_word_tag(child.tag, "tbl"):
            yield child
        elif (_is_word_tag(child.tag, "sdt") or _is_word_tag(child.tag, "sdtContent") or
              _is_word_tag(child.tag, "customXml")):
            for block in _iter_blocks(child):
                yield block


def _iter_rows(table):
    for child in table:
        if _is_word_tag(child.tag, "tr"):
            yield child
        elif (_is_word_tag(child.tag, "sdt") or _is_word_tag(child.tag, "sdtContent") or
              _is_word_tag(child.tag, "customXml")):
            for row in _iter_rows(child):
                yield row


def _iter_cells(row):
    for child in row:
        if _is_word_tag(child.tag, "tc"):
            yield child
        elif (_is_word_tag(child.tag, "sdt") or _is_word_tag(child.tag, "sdtContent") or
              _is_word_tag(child.tag, "customXml")):
            for cell in _iter_cells(child):
                yield cell


def _cell_text(cell):
    blocks = []
    for block in _iter_blocks(cell):
        if _is_word_tag(block.tag, "p"):
            blocks.append(_paragraph_text(block))
        elif _is_word_tag(block.tag, "tbl"):
            blocks.append(_table_text(block))
    return "\n".join(blocks)


def _table_text(table):
    rows = []
    for row in _iter_rows(table):
        rows.append("\t".join(_cell_text(cell) for cell in _iter_cells(row)))
    return "\n".join(rows)


def _extract_docx(data):
    archive, by_name = _open_docx(data)
    try:
        content_types = _parse_xml(_read_entry(archive, by_name["[content_types].xml"]))
        if content_types.tag != _CT + "Types":
            _error("DOCX содержит некорректную часть Content Types.")
        content_type_parts = []
        for element in content_types.iter():
            if element.tag == _CT + "Override":
                content_type_parts.append((element.attrib.get("PartName"), element.attrib.get("ContentType")))
        if not any(part == "/word/document.xml" and content_type and content_type.endswith(".main+xml")
                   for part, content_type in content_type_parts):
            _error("DOCX не содержит основной OOXML-документ.")
        document = _parse_xml(_read_entry(archive, by_name["word/document.xml"]))
        if not _is_word_tag(document.tag, "document"):
            _error("DOCX содержит некорректный основной документ.")
        body = next((child for child in document if _is_word_tag(child.tag, "body")), None)
        if body is None:
            _error("DOCX не содержит тело документа.")
        blocks = []
        for child in _iter_blocks(body):
            if _is_word_tag(child.tag, "p"):
                blocks.append(_paragraph_text(child))
            elif _is_word_tag(child.tag, "tbl"):
                blocks.append(_table_text(child))
        return _check_document_chars("\n".join(blocks))
    finally:
        archive.close()


def extract_document(filename, data):
    """Extract a bounded document as text or raise a deterministic ToolError."""
    try:
        extension = _check_filename(filename)
        if not isinstance(data, bytes):
            _error("Содержимое документа должно быть байтами.")
        if len(data) > MAX_DOCX_RAW:
            _error("Документ превышает лимит 24 MiB.")
        if extension == ".txt":
            return _decode_text(data)
        return _extract_docx(data)
    except ToolError:
        raise
    except Exception:
        _error("Не удалось обработать документ.")
