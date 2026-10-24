import io
import os
import zipfile
import unittest

from plugin.AIAssistant.agent.documents import extract_document
from plugin.AIAssistant.agent.tools import ToolError


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
STRICT_W_NS = "http://purl.oclc.org/ooxml/wordprocessingml/main"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


def _docx_xml(body):
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="%s"><Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>' % CT_NS).encode("utf-8")
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>'
        % (W_NS, body)).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", document)
    return output.getvalue()


def _docx_document_xml(document_xml):
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="%s"><Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>' % CT_NS).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", document_xml.encode("utf-8"))
    return output.getvalue()


class DocumentExtractionTests(unittest.TestCase):
    def test_text_decodes_utf8_utf16_and_cp1251(self):
        self.assertEqual(extract_document("note.txt", "Привет".encode("utf-8")), "Привет")
        self.assertEqual(extract_document("note.txt", b"\xff\xfe\x1f\x04@\x04\x38\x04\x32\x04\x35\x04\x42\x04"), "Привет")
        self.assertEqual(extract_document("note.txt", "Привет".encode("cp1251")), "Привет")

    def test_docx_extracts_paragraphs_and_tables_in_source_order(self):
        body = (
            '<w:p><w:r><w:t>Alpha</w:t><w:tab/><w:t>Beta</w:t><w:br/><w:t>Gamma</w:t></w:r></w:p>'
            '<w:tbl>'
            '<w:tr><w:tc><w:p><w:r><w:t>Cell 1</w:t></w:r></w:p></w:tc>'
            '<w:tc><w:p><w:r><w:t>Cell 2</w:t></w:r></w:p></w:tc></w:tr>'
            '<w:tr><w:tc><w:p><w:r><w:t>Row 2 A</w:t></w:r></w:p></w:tc>'
            '<w:tc><w:p><w:r><w:t>Row 2 B</w:t></w:r></w:p></w:tc></w:tr>'
            '</w:tbl>'
            '<w:p><w:r><w:t>Tail</w:t></w:r></w:p>')
        self.assertEqual(
            extract_document("spec.docx", _docx_xml(body)),
            "Alpha\tBeta\nGamma\nCell 1\tCell 2\nRow 2 A\tRow 2 B\nTail")

    def test_docx_accepts_strict_ooxml_namespace(self):
        document = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="%s"><w:body>'
            '<w:p><w:r><w:t>Strict paragraph</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Strict cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            '</w:body></w:document>' % STRICT_W_NS)
        self.assertEqual(
            extract_document("strict.docx", _docx_document_xml(document)),
            "Strict paragraph\nStrict cell")

    def test_docx_preserves_order_inside_structured_containers_and_nested_table(self):
        document = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="%s"><w:body>'
            '<w:p><w:r><w:t>before</w:t></w:r></w:p>'
            '<w:sdt><w:sdtPr><w:alias w:val="metadata-must-not-appear"/></w:sdtPr><w:sdtContent>'
            '<w:p><w:r><w:t>sdt paragraph</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>sdt table</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            '</w:sdtContent></w:sdt>'
            '<w:customXml><w:p><w:r><w:t>custom paragraph</w:t></w:r></w:p></w:customXml>'
            '<w:tbl><w:tr>'
            '<w:tc><w:p><w:r><w:t>cell one</w:t></w:r></w:p>'
            '<w:sdt><w:sdtPr><w:tag w:val="cell-metadata"/></w:sdtPr><w:sdtContent>'
            '<w:p><w:r><w:t>cell sdt</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>nested cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            '</w:sdtContent></w:sdt>'
            '<w:customXml><w:p><w:r><w:t>cell custom</w:t></w:r></w:p></w:customXml></w:tc>'
            '<w:tc><w:p><w:r><w:t>cell two</w:t></w:r></w:p></w:tc>'
            '</w:tr></w:tbl>'
            '<w:p><w:r><w:t>after</w:t></w:r></w:p>'
            '</w:body></w:document>' % W_NS)
        self.assertEqual(
            extract_document("containers.docx", _docx_document_xml(document)),
            "before\nsdt paragraph\nsdt table\ncustom paragraph\n"
            "cell one\ncell sdt\nnested cell\ncell custom\tcell two\nafter")

    def test_actual_gear_docx_smoke(self):
        path = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..", "Примеры деталей",
            "1. Шестерня - 3D", "Техническое задание для шестерни.docx"))
        if not os.path.isfile(path):
            self.skipTest("supplied gear DOCX is not present")
        with open(path, "rb") as stream:
            text = extract_document(os.path.basename(path), stream.read())
        self.assertTrue(text.strip())
        self.assertLessEqual(len(text), 120000)

    def test_rejects_doc_dtd_malformed_empty_binary_and_oversize_text(self):
        for filename, payload in [
                ("legacy.doc", b"binary"),
                ("empty.txt", b"   \n\t"),
                ("binary.txt", b"\x00\x01\x02"),
                ("broken.docx", b"not a zip")]:
            with self.assertRaises(ToolError):
                extract_document(filename, payload)
        dtd = ("<!DOCTYPE w:document [<!ENTITY x 'expanded'>]>"
               "<w:document xmlns:w='%s'><w:body><w:p><w:t>&x;</w:t></w:p>"
               "</w:body></w:document>" % W_NS).encode("utf-8")
        with self.assertRaises(ToolError):
            extract_document("dtd.docx", _docx_xml(dtd.decode("utf-8")))
        with self.assertRaises(ToolError):
            extract_document("large.txt", ("x" * 120001).encode("utf-8"))
        with self.assertRaises(ToolError):
            extract_document("raw-large.txt", b"x" * (24 * 1024 * 1024 + 1))

    def test_rejects_invalid_ooxml_zip_and_unsafe_entry(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("[Content_Types].xml", b"<Types/>")
            archive.writestr("../word/document.xml", b"bad")
        with self.assertRaises(ToolError):
            extract_document("bad.docx", output.getvalue())
