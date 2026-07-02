"""Atomic artifact writer with content hashing.

Writes report artifacts to disk under a configurable root directory. Every
write is atomic (tmp → fsync → rename) and the SHA-256 hash of the final
file is returned so callers can verify integrity.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .renderers import RENDERERS


@dataclass
class ArtifactFile:
    """Reference to a written artifact file."""

    format: str
    path: str
    hash_sha256: str


class ArtifactWriter:
    """Write report artifacts atomically."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def write_all(
        self,
        report_doc: dict[str, Any],
        *,
        tenant_id: str,
        agent_id: str,
        iteration_id: str,
        report_id: str,
    ) -> dict[str, ArtifactFile]:
        """Write JSON, Markdown, HTML, and CSV artifacts for *report_doc*."""
        base = (
            self.root / tenant_id / agent_id / iteration_id / report_id
        )
        base.mkdir(parents=True, exist_ok=True)

        results: dict[str, ArtifactFile] = {}
        for fmt, renderer in RENDERERS.items():
            content = renderer(report_doc)
            results[fmt] = self._write_atomic(base / f"report.{fmt}", content, fmt)

        return results

    def _write_atomic(self, path: Path, content: str, fmt: str) -> ArtifactFile:
        """Write *content* atomically to *path* and return hash + format."""
        tmp = path.with_suffix(f".{fmt}.tmp")
        data = content.encode("utf-8")

        # Write to temp file
        tmp.write_bytes(data)
        os.fsync(tmp.open("rb").fileno())

        # Hash
        file_hash = hashlib.sha256(data).hexdigest()

        # Atomic rename
        os.replace(tmp, path)

        return ArtifactFile(format=fmt, path=str(path), hash_sha256=file_hash)
