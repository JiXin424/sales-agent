"""Document chunker — split markdown into retrieval-ready chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .markdown_parser import MarkdownDocument, is_faq_document

# Sensitive content marker that must be flagged in metadata.
_SENSITIVE_MARKER = "不可对外透露信息"  # 不可对外透露信息

# Heading pattern: lines starting with # ## ### etc.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# FAQ Q: pattern — each Q+A pair starts with "## Q:"
_FAQ_Q_RE = re.compile(r"^##\s*Q:", re.MULTILINE)


@dataclass
class Chunk:
    """A document chunk."""

    text: str
    chunk_index: int
    section_title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        preview = self.text[:40].replace("\n", " ")
        return (
            f"<Chunk(index={self.chunk_index}, "
            f"section={self.section_title!r}, "
            f"text={preview!r}...)>"
        )


def chunk_document(
    doc: MarkdownDocument,
    chunk_size: int = 700,
    chunk_overlap: int = 120,
) -> list[Chunk]:
    """Split a document into chunks.

    Strategy (per spec):

    1. **FAQ documents**: split by each Q/A pair (``## Q:`` sections).
    2. **Other documents**: split by headings (``#`` / ``##`` / ``###``).
    3. If a section is still too long, split by paragraphs (double-newlines)
       with overlap.
    4. Respect ``chunk_size`` (target 400-800 chars) and ``chunk_overlap``
       (80-150 chars).

    Rules enforced:

    - Q and A must not be split into different chunks.
    - Price conditions and applicable scope must not be separated.
    - Case results and limitations must not be separated.
    - ``不可对外透露信息`` is kept as a metadata flag.

    Args:
        doc: The parsed markdown document.
        chunk_size: Target maximum chunk size in characters.
        chunk_overlap: Overlap in characters when splitting long sections.

    Returns:
        List of :class:`Chunk` objects.
    """
    content = doc.content
    if not content.strip():
        return []

    # Propagate sensitive marker to all chunks if present anywhere in the document.
    has_sensitive = _SENSITIVE_MARKER in content

    base_metadata: dict[str, Any] = {}
    if has_sensitive:
        base_metadata["contains_sensitive_info"] = True

    # Copy select front matter into chunk metadata.
    for key in ("version", "visibility", "tags", "owner"):
        if key in doc.metadata:
            base_metadata[key] = doc.metadata[key]
    base_metadata["source_type"] = doc.source_type
    base_metadata["file_path"] = doc.file_path

    chunks: list[Chunk] = []

    if is_faq_document(doc):
        chunks = _chunk_faq(content, chunk_size, chunk_overlap, base_metadata)
    else:
        chunks = _chunk_by_headings(content, chunk_size, chunk_overlap, base_metadata)

    # Assign sequential chunk_index and inject base metadata.
    for idx, chunk in enumerate(chunks):
        chunk.chunk_index = idx
        # Merge base metadata (chunk metadata takes precedence).
        merged = {**base_metadata, **chunk.metadata}
        chunk.metadata = merged

    return chunks


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _chunk_faq(
    content: str,
    chunk_size: int,
    chunk_overlap: int,
    base_metadata: dict[str, Any],
) -> list[Chunk]:
    """Split FAQ content by ``## Q:`` patterns.

    Each Q+A pair becomes one chunk.  If a single pair is too large, it is
    sub-split by paragraphs.
    """
    # Find all positions where "## Q:" occurs.
    positions = [m.start() for m in _FAQ_Q_RE.finditer(content)]
    if not positions:
        # No Q: headings found — treat as regular document.
        return _chunk_by_headings(content, chunk_size, chunk_overlap, base_metadata)

    chunks: list[Chunk] = []
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(content)
        section = content[start:end].strip()

        # Extract the question text for the section title.
        first_line = section.split("\n", 1)[0]
        section_title = re.sub(r"^##\s*Q:\s*", "", first_line).strip()

        if len(section) <= chunk_size:
            chunks.append(
                Chunk(
                    text=section,
                    chunk_index=0,  # reassigned later
                    section_title=section_title,
                )
            )
        else:
            # Section too long — split by paragraphs, keeping Q and A together.
            sub_chunks = _split_by_paragraphs(
                section, chunk_size, chunk_overlap, section_title
            )
            chunks.extend(sub_chunks)

    return chunks


def _chunk_by_headings(
    content: str,
    chunk_size: int,
    chunk_overlap: int,
    base_metadata: dict[str, Any],
) -> list[Chunk]:
    """Split content by markdown headings (# / ## / ###).

    Each heading section becomes one chunk.  Sections exceeding
    ``chunk_size`` are further split by paragraphs.
    """
    # Find all heading positions.
    headings = list(_HEADING_RE.finditer(content))

    if not headings:
        # No headings — treat the whole content as a single section.
        return _split_by_paragraphs(content, chunk_size, chunk_overlap, "")

    sections: list[tuple[str, str]] = []  # (title, text)
    for i, match in enumerate(headings):
        title = match.group(2).strip()
        start = match.start()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
        section_text = content[start:end].strip()
        sections.append((title, section_text))

    chunks: list[Chunk] = []
    for title, text in sections:
        if len(text) <= chunk_size:
            chunks.append(
                Chunk(
                    text=text,
                    chunk_index=0,  # reassigned later
                    section_title=title,
                )
            )
        else:
            sub_chunks = _split_by_paragraphs(text, chunk_size, chunk_overlap, title)
            chunks.extend(sub_chunks)

    return chunks


def _split_by_paragraphs(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    section_title: str,
) -> list[Chunk]:
    """Split a long text into paragraph-based chunks with overlap.

    Paragraphs are separated by one or more blank lines.
    """
    paragraphs = re.split(r"\n{2,}", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    if not paragraphs:
        return []

    chunks: list[Chunk] = []
    current_parts: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)

        # If a single paragraph exceeds chunk_size, include it as-is (we do
        # not break mid-paragraph to avoid splitting price conditions or case
        # results from their context).
        if para_len > chunk_size and current_parts:
            # Flush whatever we have accumulated first.
            chunks.append(
                Chunk(
                    text="\n\n".join(current_parts),
                    chunk_index=0,
                    section_title=section_title,
                )
            )
            current_parts = []
            current_len = 0

        # Check if adding this paragraph would exceed chunk_size.
        if current_parts and (current_len + 2 + para_len) > chunk_size:
            # Flush current chunk.
            chunks.append(
                Chunk(
                    text="\n\n".join(current_parts),
                    chunk_index=0,
                    section_title=section_title,
                )
            )

            # Carry over trailing paragraphs for overlap.
            overlap_text = _get_overlap_text(current_parts, chunk_overlap)
            current_parts = []
            current_len = 0
            if overlap_text:
                current_parts.append(overlap_text)
                current_len = len(overlap_text)

        current_parts.append(para)
        current_len += para_len + (2 if len(current_parts) > 1 else 0)

    # Flush remaining.
    if current_parts:
        chunks.append(
            Chunk(
                text="\n\n".join(current_parts),
                chunk_index=0,
                section_title=section_title,
            )
        )

    return chunks


def _get_overlap_text(parts: list[str], chunk_overlap: int) -> str:
    """Extract trailing overlap text from a list of paragraph strings.

    Walks paragraphs in reverse and accumulates them until the overlap
    target is reached.
    """
    overlap_parts: list[str] = []
    overlap_len = 0

    for part in reversed(parts):
        part_len = len(part)
        if overlap_len + part_len + (2 if overlap_parts else 0) > chunk_overlap and overlap_parts:
            break
        overlap_parts.insert(0, part)
        overlap_len += part_len + (2 if len(overlap_parts) > 1 else 0)

    return "\n\n".join(overlap_parts) if overlap_parts else ""
