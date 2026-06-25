from pathlib import Path
from unittest.mock import MagicMock, patch

from sales_agent.ontology.ingestion_service import _read_content


def test_read_content_md_txt_uses_read_text(tmp_path):
    path = tmp_path / "test.md"
    path.write_text("# hello", encoding="utf-8")
    assert _read_content(path) == "# hello"

    path2 = tmp_path / "test.txt"
    path2.write_text("plain text", encoding="utf-8")
    assert _read_content(path2) == "plain text"


def test_read_content_docx_calls_docling(tmp_path):
    path = tmp_path / "test.docx"
    path.write_bytes(b"PK\x03\x04 fake docx")

    # _read_content 在函数内 from docling.document_converter import DocumentConverter，
    # 所以 patch 源模块的属性才能拦截。
    with patch("docling.document_converter.DocumentConverter") as MockConv:
        mock_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.document.export_to_markdown.return_value = "# converted docx"
        mock_instance.convert.return_value = mock_result
        MockConv.return_value = mock_instance

        result = _read_content(path)
        assert result == "# converted docx"
        mock_instance.convert.assert_called_once_with(str(path))


def test_read_content_pdf_calls_docling(tmp_path):
    path = tmp_path / "test.pdf"
    path.write_bytes(b"%PDF-1.4 fake pdf")

    with patch("docling.document_converter.DocumentConverter") as MockConv:
        mock_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.document.export_to_markdown.return_value = "# converted pdf"
        mock_instance.convert.return_value = mock_result
        MockConv.return_value = mock_instance

        result = _read_content(path)
        assert result == "# converted pdf"


def test_read_content_docx_failure_raises(tmp_path):
    path = tmp_path / "bad.docx"
    path.write_bytes(b"corrupt")

    with patch("docling.document_converter.DocumentConverter") as MockConv:
        mock_instance = MagicMock()
        mock_instance.convert.side_effect = RuntimeError("parse error")
        MockConv.return_value = mock_instance

        try:
            _read_content(path)
            assert False, "should have raised RuntimeError"
        except RuntimeError as e:
            assert "docling 转换失败" in str(e)
