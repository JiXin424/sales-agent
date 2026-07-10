"""图片格式识别与视觉解读 prompt 常量。

历史 image_to_text（调用视觉 LLM 把图片转文本）已由 ingestion_service 的
``_image_to_text_via_vision`` 取代；本模块仅保留格式识别工具与 prompt 常量，
供 ingestion_service 复用。
"""

from __future__ import annotations

from pathlib import Path

# 支持的直接图片格式及其 MIME 类型
SUPPORTED_IMAGE_EXTENSIONS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}

# 需要 pillow-heif 的格式（可选）
_HEIF_EXTENSIONS = {".heic": "image/heic", ".heif": "image/heif"}

# ── Prompt ──────────────────────────────────────────────────────────────

IMAGE_INTERPRET_PROMPT = """请详细描述这张图片的内容，提取以下信息：

1. **文字内容**：图片中出现的所有文字，逐条列出（标题、正文、标注等）
2. **数据信息**：如果包含表格、图表、数字数据，请提取为结构化描述
3. **图表/流程**：如果包含流程图、架构图、思维导图，请描述其结构和逻辑关系
4. **关键信息摘要**：用 2-3 句话总结图片要传达的核心信息

请用中文回答，以 Markdown 格式输出。对于销售/产品相关的内容，请特别标注关键卖点或数据。
"""


# ── Public API ──────────────────────────────────────────────────────────


def is_image_file(path: Path) -> bool:
    """判断文件是否为支持的图片格式。"""
    return path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS


def get_image_mime_type(path: Path) -> str:
    """获取图片的 MIME 类型。"""
    ext = path.suffix.lower()
    if ext in SUPPORTED_IMAGE_EXTENSIONS:
        return SUPPORTED_IMAGE_EXTENSIONS[ext]
    if ext in _HEIF_EXTENSIONS:
        return _HEIF_EXTENSIONS[ext]
    raise ValueError(f"不支持的图片格式: {ext}")
