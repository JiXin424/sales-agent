"""MD 优化器：LLM 增强 Markdown 文档以提升向量检索召回率。

在 chunk → embed 之前，用 LLM 为 MD 文件自动注入：
1. 完善 YAML frontmatter（title, source_type, tags, version）
2. 添加 ``search_keywords`` 字段（5-15 个用户可能搜索的关键词/口语表达）
3. FAQ 文档：确保每个 Q&A 以 ``## Q:`` 为标记，方便 chunker 精准切分
4. 正文首段注入一句话摘要（检索锚点）

用法：
    optimizer = MDOptimizer(chat_model)
    enhanced = await optimizer.optimize(raw_md_content)
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Prompt ──────────────────────────────────────────────────────────────

MD_OPTIMIZE_SYSTEM_PROMPT = """你是一个销售知识库优化专家。你的任务是将原始 Markdown 文档优化为更适合向量检索的格式。

## 优化目标
1. 补充/完善 YAML frontmatter（如果原文没有 frontmatter，则新增；如果有则不完整则补充）
2. 添加 `search_keywords` 字段，列出用户可能用来搜索本文档的 5-15 个关键词/短语（含同义词、口语化表达）
3. 如果是 FAQ 格式，确保每个 Q&A 以 `## Q:` 开头
4. 在正文开头添加一行 `> **摘要：**` 的一句话摘要（20-50 字），作为检索锚点

## 输出格式
请**仅输出优化后的完整 Markdown 文本**，不要加任何解释、说明或代码块标记。
输出必须以 YAML frontmatter（`---` 包裹）开头。

## 示例
输入：
```
# 产品定价说明
我们的标准产品包定价为每年 5 万，包含 AI 教练 + 知识库 + 数据分析面板。
```

输出：
```
---
title: 标准产品包定价说明
source_type: product_doc
tags: [定价, 产品包, 费用说明]
search_keywords: [价格, 多少钱, 报价, 5万, 标准包, 产品费用, 收费, 定价方案, 费用标准, 买要花多少]
---
> **摘要：** 标准产品包年费 5 万，含 AI 教练、知识库和数据分析面板三大模块。

# 产品定价说明

我们的标准产品包定价为每年 5 万，包含 AI 教练 + 知识库 + 数据分析面板。
```
"""

MD_OPTIMIZE_USER_TEMPLATE = """请优化以下销售知识库 Markdown 文档。

## 注意事项
- 如果原文是产品介绍，search_keywords 应侧重"用户会怎么搜这个产品"（价格、功能、对比、用途）
- 如果原文是竞品分析，search_keywords 应侧重"竞品名字 + 对比 + 优劣势"
- 如果原文是 FAQ，search_keywords 应侧重"问题关键词 + 场景化表达"
- 如果原文是销售策略/行为案例，search_keywords 应侧重"场景 + 痛点 + 方法论"
- source_type 从以下中选择：product_doc, competitor_analysis, faq, sales_strategy, top_sales_behavior, customer_doc, general

## 原始文档
{content}"""


# ── Optimizer ───────────────────────────────────────────────────────────


class MDOptimizer:
    """LLM-based MD document optimizer."""

    def __init__(self, chat_model: Any) -> None:
        """Args:
            chat_model: LLM client that supports
                ``chat_model.chat(messages, temperature=..., max_tokens=...)``
                returning an OpenAI-compatible response.
        """
        self.chat_model = chat_model

    async def optimize(self, raw_content: str, source_type_hint: str = "") -> str:
        """优化单篇 Markdown 文档。

        Args:
            raw_content: 原始 Markdown 文本。
            source_type_hint: source_type 提示，会注入到 user prompt 中。

        Returns:
            优化后的 Markdown 文本（含 YAML frontmatter）。

        Raises:
            ValueError: LLM 返回空结果。
            RuntimeError: LLM 调用失败。
        """
        user_prompt = MD_OPTIMIZE_USER_TEMPLATE.format(content=raw_content)
        if source_type_hint:
            hint = (
                f"\n\n**提示：** 这篇文档的 source_type 应该是 `{source_type_hint}`。"
                f"请在 frontmatter 中使用此值。"
            )
            user_prompt += hint

        messages = [
            {"role": "system", "content": MD_OPTIMIZE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            response = await self.chat_model.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=4096,
            )
        except Exception as e:
            logger.error("MD optimization LLM call failed: %s", e)
            raise RuntimeError(f"MD 优化失败：{e}") from e

        content = getattr(response, "content", None)
        if not content:
            # 尝试 OpenAI 格式
            if hasattr(response, "choices") and response.choices:
                content = response.choices[0].message.content

        if not content or not content.strip():
            raise ValueError("LLM 返回为空，MD 优化失败")

        # 清洗：去除可能残留的代码块标记
        optimized = content.strip()
        if optimized.startswith("```markdown"):
            optimized = optimized[len("```markdown"):].strip()
        elif optimized.startswith("```md"):
            optimized = optimized[len("```md"):].strip()
        elif optimized.startswith("```"):
            optimized = optimized[len("```"):].strip()
        if optimized.endswith("```"):
            optimized = optimized[:-3].strip()

        return optimized

    async def optimize_batch(
        self,
        documents: list[tuple[str, str]],  # (raw_content, source_type_hint)
        concurrency: int = 3,
    ) -> list[tuple[str, str | None]]:
        """批量优化多篇文档。

        Args:
            documents: [(raw_content, source_type_hint), ...]
            concurrency: 最大并发数（暂未实现真正的 asyncio.gather，顺序执行）

        Returns:
            [(optimized_content, error_message), ...]
            成功时 error_message 为 None。
        """
        results: list[tuple[str, str | None]] = []
        for raw_content, hint in documents:
            try:
                optimized = await self.optimize(raw_content, hint)
                results.append((optimized, None))
            except Exception as e:
                logger.warning("Batch optimization failed for one doc: %s", e)
                # 失败时保留原文
                results.append((raw_content, str(e)))
        return results
