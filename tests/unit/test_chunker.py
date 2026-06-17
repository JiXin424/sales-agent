"""Chunker 单元测试。"""

from sales_agent.rag.markdown_parser import parse_markdown, MarkdownDocument, is_faq_document
from sales_agent.rag.chunker import chunk_document


class TestMarkdownParser:
    def test_parse_with_front_matter(self, tmp_path):
        md = tmp_path / "test.md"
        md.write_text("""---
title: 产品介绍
source_type: product_doc
version: "2026-06-01"
tags:
  - 产品
---

# 产品介绍

这是产品内容。
""")
        doc = parse_markdown(str(md))
        assert doc.title == "产品介绍"
        assert doc.source_type == "product_doc"
        assert "产品" in doc.metadata.get("tags", [])
        assert "产品介绍" in doc.content

    def test_parse_missing_title_fallback(self, tmp_path):
        """When front matter is present but missing title, fall back to auto-detect."""
        md = tmp_path / "test.md"
        md.write_text("""---
source_type: product_doc
---

# 我的产品

内容
""")
        doc = parse_markdown(str(md))
        # Falls back to auto-detect: title from heading, source_type from dir
        assert doc.title == "我的产品"
        assert doc.source_type == "general"  # "tmp_path" dir not in map

    def test_is_faq_document(self, tmp_path):
        md = tmp_path / "faq.md"
        md.write_text("""---
title: FAQ
source_type: faq
---

内容
""")
        doc = parse_markdown(str(md))
        assert is_faq_document(doc) is True

    def test_is_not_faq_document(self, tmp_path):
        md = tmp_path / "product.md"
        md.write_text("""---
title: 产品
source_type: product_doc
---

内容
""")
        doc = parse_markdown(str(md))
        assert is_faq_document(doc) is False


class TestChunker:
    def test_chunk_by_headings(self, tmp_path):
        md = tmp_path / "test.md"
        md.write_text("""---
title: 测试文档
source_type: product_doc
---

# 标题一

这是第一段内容，包含了一些关于产品的介绍信息。

## 子标题一

这是子标题下的内容，描述了具体的功能特点。

## 子标题二

这是另一个子标题下的内容。

### 三级标题

三级标题下的内容也会被切分。
""")
        doc = parse_markdown(str(md))
        chunks = chunk_document(doc, chunk_size=700, chunk_overlap=120)
        assert len(chunks) >= 2
        assert all(chunk.text for chunk in chunks)
        assert all(isinstance(chunk.chunk_index, int) for chunk in chunks)

    def test_chunk_faq_document(self, tmp_path):
        md = tmp_path / "faq.md"
        md.write_text("""---
title: FAQ
source_type: faq
---

# 常见问题

## Q: 问题一？

A: 这是问题一的答案，包含了详细的解释说明。

## Q: 问题二？

A: 这是问题二的答案。
""")
        doc = parse_markdown(str(md))
        chunks = chunk_document(doc, chunk_size=700, chunk_overlap=120)
        # 至少应该有 2 个 chunk（每个 Q/A 一个）
        assert len(chunks) >= 2

    def test_chunk_preserves_metadata(self, tmp_path):
        md = tmp_path / "test.md"
        md.write_text("""---
title: 测试
source_type: product_doc
tags:
  - 测试
---

# 内容

这是内容。
""")
        doc = parse_markdown(str(md))
        chunks = chunk_document(doc)
        assert len(chunks) >= 1
        assert chunks[0].metadata.get("source_type") == "product_doc"

    def test_sensitive_info_flag(self, tmp_path):
        md = tmp_path / "test.md"
        md.write_text("""---
title: 案例
source_type: case_study
---

# 案例

## 可对外表达口径

可以说的内容

## 不可对外透露信息

不能说的秘密内容
""")
        doc = parse_markdown(str(md))
        chunks = chunk_document(doc)
        # 至少有一个 chunk 标记了 contains_sensitive_info
        sensitive = [c for c in chunks if c.metadata.get("contains_sensitive_info")]
        assert len(sensitive) >= 1
