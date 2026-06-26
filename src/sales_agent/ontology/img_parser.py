"""图片/扫描件 AI 视觉解读。

调用视觉 LLM 将图片转为结构化文本描述，支持：
- JPEG / PNG / WebP / BMP / GIF（静态帧）
- 扫描件 PDF（需要先做页面级截图，外部预处理，此处只处理单张图片）
- HEIC / HEIF（需 pillow-heif 可选依赖）

设计要点：
1. 复用项目已有的 OpenAI 兼容 chat 客户端
2. 图片以 base64 data URL 形式传入
3. Prompt 要求提取文字、数据、图表、布局描述
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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

# 最大图片大小（base64 前），超过则做压缩提醒
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


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


async def image_to_text(
    path: Path,
    chat_model: Any,
    *,
    vision_model: str = "",
    prompt: str = "",
    max_tokens: int = 2048,
) -> str:
    """将图片转为结构化文本描述。

    Args:
        path: 图片文件路径。
        chat_model: LLM client，需支持 OpenAI 兼容的 vision API。
            ```chat_model.chat.completions.create(model=..., messages=[...])```
        vision_model: Vision model 名称（默认使用 chat_model 的默认模型）。
        prompt: 自定义提示词（为空则使用内置销售知识库专用 prompt）。
        max_tokens: 最大输出 token 数。

    Returns:
        图片的结构化文本描述。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 文件格式不支持或文件过大。
        RuntimeError: LLM 调用失败。
    """
    if not path.exists():
        raise FileNotFoundError(f"图片文件不存在: {path}")

    # 检查格式
    ext = path.suffix.lower()
    mime_type = SUPPORTED_IMAGE_EXTENSIONS.get(ext)
    if not mime_type:
        # 尝试 HEIF
        mime_type = _HEIF_EXTENSIONS.get(ext)
        if mime_type:
            try:
                from PIL import Image  # noqa: F401
            except ImportError:
                raise ValueError(
                    f"HEIF 图片需要 pillow-heif 依赖: pip install pillow-heif"
                )
        else:
            raise ValueError(f"不支持的图片格式: {ext}")

    # 检查大小
    file_size = path.stat().st_size
    if file_size > _MAX_IMAGE_BYTES:
        logger.warning(
            "Image file %s exceeds %d MB, may be rejected by vision API",
            path.name, _MAX_IMAGE_BYTES // (1024 * 1024),
        )

    # 读取并编码
    with open(path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("ascii")

    data_url = f"data:{mime_type};base64,{image_data}"
    user_prompt = prompt or IMAGE_INTERPRET_PROMPT

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]

    model = vision_model or getattr(chat_model, "model_name", "qwen-vl-plus")

    try:
        response = await chat_model.chat(
            messages=messages,
            model=model,
            temperature=0.1,
            max_tokens=max_tokens,
        )
    except Exception as e:
        logger.error("Vision API call failed for %s: %s", path.name, e)
        raise RuntimeError(f"图片解读失败 ({path.name}): {e}") from e

    # 提取文本
    content = getattr(response, "content", None)
    if not content:
        if hasattr(response, "choices") and response.choices:
            content = response.choices[0].message.content

    if not content or not content.strip():
        raise RuntimeError(f"Vision model 对 {path.name} 返回空结果")

    return content.strip()
