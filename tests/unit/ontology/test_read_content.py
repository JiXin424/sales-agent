from unittest.mock import MagicMock, patch

from sales_agent.ontology.ingestion_service import _read_content


def test_read_content_md_txt_uses_read_text(tmp_path):
    p_md = tmp_path / "x.md"
    p_md.write_text("# hello", encoding="utf-8")
    assert _read_content(p_md) == "# hello"

    p_txt = tmp_path / "x.txt"
    p_txt.write_text("plain text", encoding="utf-8")
    assert _read_content(p_txt) == "plain text"


def test_read_content_docx(tmp_path):
    path = tmp_path / "x.docx"
    path.write_bytes(b"PK fake")
    with patch("docx.Document") as MockDoc:
        doc = MagicMock()
        p1 = MagicMock(); p1.text = "hello"
        p2 = MagicMock(); p2.text = ""        # 空段落，应被过滤
        p3 = MagicMock(); p3.text = "world"
        doc.paragraphs = [p1, p2, p3]
        MockDoc.return_value = doc
        assert _read_content(path) == "hello\nworld"
        MockDoc.assert_called_once_with(str(path))


def test_read_content_pdf(tmp_path):
    path = tmp_path / "x.pdf"
    path.write_bytes(b"%PDF fake")
    page1 = MagicMock(); page1.get_text.return_value = "alpha"
    page2 = MagicMock(); page2.get_text.return_value = "beta"
    ctx = MagicMock()
    ctx.__enter__.return_value = [page1, page2]
    with patch("fitz.open", return_value=ctx) as m:
        assert _read_content(path) == "alpha\nbeta"
        m.assert_called_once_with(str(path))


def test_read_content_pptx(tmp_path):
    path = tmp_path / "x.pptx"
    path.write_bytes(b"PK fake")
    with patch("pptx.Presentation") as MockPres:
        prs = MagicMock()
        slide = MagicMock()
        sh_text = MagicMock(); sh_text.has_text_frame = True
        sh_text.text_frame.text = "slide text"
        sh_notext = MagicMock(); sh_notext.has_text_frame = False
        slide.shapes = [sh_text, sh_notext]
        prs.slides = [slide]
        MockPres.return_value = prs
        assert _read_content(path) == "slide text"


def test_read_content_docx_failure_raises(tmp_path):
    path = tmp_path / "bad.docx"
    path.write_bytes(b"corrupt")
    with patch("docx.Document", side_effect=Exception("parse error")):
        try:
            _read_content(path)
            assert False, "should raise"
        except RuntimeError as e:
            assert "docx 解析失败" in str(e)
