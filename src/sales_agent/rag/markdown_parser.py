"""Markdown parser with YAML front matter support.

Supports two modes:
1. **Explicit front matter**: file starts with ``---`` delimiters containing
   YAML with at least ``title`` and ``source_type``.
2. **Auto-detected**: if no front matter is found, the title is extracted from
   the first ``#`` heading and ``source_type`` is inferred from the parent
   directory name (via :data:`DIRECTORY_SOURCE_TYPE_MAP`).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


class MarkdownDocument:
    """Parsed markdown document."""

    def __init__(
        self,
        title: str,
        source_type: str,
        content: str,
        metadata: dict[str, Any],
        file_path: str,
    ) -> None:
        self.title = title
        self.source_type = source_type
        self.content = content
        self.metadata = metadata
        self.file_path = file_path

    def __repr__(self) -> str:
        return (
            f"<MarkdownDocument(title={self.title!r}, "
            f"source_type={self.source_type!r}, "
            f"file_path={self.file_path!r})>"
        )


# Regex to capture YAML front matter between --- delimiters at the start of a file.
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n",
    re.DOTALL,
)

# First-level heading pattern for title extraction.
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)

# Mapping from directory names (lowercase) to source_type values.
# Extend this map as new knowledge base categories are added.
DIRECTORY_SOURCE_TYPE_MAP: dict[str, str] = {
    "companyproduct": "product_doc",
    "competitor": "competitor_analysis",
    "customer": "customer_doc",
    "salesstrategy": "sales_strategy",
    "systemfaq": "faq",
    "topsalesbehavior": "top_sales_behavior",
}


def _infer_source_type(path: Path) -> str:
    """Infer ``source_type`` from the file's parent directory name."""
    dir_name = path.parent.name.lower().replace(" ", "").replace("-", "").replace("_", "")
    return DIRECTORY_SOURCE_TYPE_MAP.get(dir_name, "general")


def _extract_title_from_content(text: str) -> str:
    """Extract the first ``# heading`` from markdown text."""
    match = _H1_RE.search(text)
    if match:
        return match.group(1).strip()
    # Fallback: first non-empty line, truncated.
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("---") and not line.startswith(">"):
            return line[:100]
    return "Untitled"


def parse_markdown(file_path: str | Path) -> MarkdownDocument:
    """Parse a Markdown file with optional YAML front matter.

    Front matter format::

        ---
        title: 产品价值说明
        source_type: product_doc
        version: 2026-06-10
        visibility: internal_sales
        tags:
          - 产品
          - 价值
        owner: 销售运营
        ---

    If no front matter is found, the title is extracted from the first ``#``
    heading and ``source_type`` is inferred from the parent directory name.

    Returns:
        A :class:`MarkdownDocument` with parsed front matter and body content.

    Raises:
        FileNotFoundError: If *file_path* does not exist.
    """
    path = Path(file_path)
    raw_text = path.read_text(encoding="utf-8")

    match = _FRONTMATTER_RE.match(raw_text)

    if match is not None:
        # --- Explicit front matter path ---
        front_matter_raw = match.group(1)
        body = raw_text[match.end():]

        try:
            parsed = yaml.safe_load(front_matter_raw)
            if isinstance(parsed, dict) and parsed.get("title") and parsed.get("source_type"):
                metadata = {
                    k: v for k, v in parsed.items() if k not in ("title", "source_type")
                }
                return MarkdownDocument(
                    title=str(parsed["title"]),
                    source_type=str(parsed["source_type"]),
                    content=body.strip(),
                    metadata=metadata,
                    file_path=str(path),
                )
            # YAML parsed but missing required fields — fall through to auto-detect.
        except yaml.YAMLError:
            pass  # Not valid YAML — fall through to auto-detect.

    # --- No front matter: auto-detect title and source_type ---
    title = _extract_title_from_content(raw_text)
    source_type = _infer_source_type(path)

    return MarkdownDocument(
        title=title,
        source_type=source_type,
        content=raw_text.strip(),
        metadata={},
        file_path=str(path),
    )


def is_faq_document(doc: MarkdownDocument) -> bool:
    """Check if document is FAQ type.

    A document is considered FAQ if its ``source_type`` is ``"faq"`` or its
    body content contains ``## Q:`` heading patterns.
    """
    return doc.source_type == "faq" or bool(
        re.search(r"^##\s*Q:", doc.content, re.MULTILINE)
    )
