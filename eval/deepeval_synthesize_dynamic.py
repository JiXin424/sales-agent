#!/usr/bin/env python3
"""动态出题包装脚本 —— 根据文档长度自动计算每篇文档的题目数。

题目数公式：limit = max(2, min(15, ceil(file_bytes / 5000)))
- 2KB 以下 → 2 题
- 25KB → 5 题
- 50KB → 10 题
- 75KB+ → 15 题（上限）

按 limit 值分组批量调用 Synthesizer，合并结果后输出 3 种格式。
"""
import argparse
import asyncio
import hashlib
import math
import os
import sys
from collections import defaultdict
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════

def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            result[key.strip()] = val
    return result


def compute_limit(file_bytes: int) -> int:
    """根据文件字节数计算每篇文档应出的最大题目数。"""
    return max(2, min(15, math.ceil(file_bytes / 5000)))


def _is_chinese(text: str) -> bool:
    if not text:
        return True
    alpha_chars = [c for c in text if c.isalpha()]
    if not alpha_chars:
        return True
    cn_chars = [c for c in alpha_chars if ord(c) > 127]
    return len(cn_chars) / len(alpha_chars) >= 0.5


def _write_markdown(goldens, path: Path) -> None:
    lines = [
        "# DeepEval Golden 测试数据（动态出题）",
        "",
        f"共 {len(goldens)} 条题目，按文档长度动态生成。",
        "",
        "---",
        "",
    ]
    for i, g in enumerate(goldens, 1):
        inp = g.input if hasattr(g, "input") else getattr(g, "input", "")
        exp = g.expected_output if hasattr(g, "expected_output") else getattr(g, "expected_output", "")
        ctx = g.context if hasattr(g, "context") else getattr(g, "context", [])
        src = g.source_file if hasattr(g, "source_file") else getattr(g, "source_file", "")

        lines.append(f"## 第 {i} 题")
        lines.append("")
        lines.append(f"**问题**：{inp}")
        lines.append("")
        if exp:
            lines.append(f"**参考答案**：{exp}")
            lines.append("")
        if src:
            lines.append(f"**来源文档**：`{src}`")
            lines.append("")
        if ctx:
            lines.append(f"**上下文**（{len(ctx)} 条）：")
            for j, c in enumerate(ctx[:5], 1):
                lines.append(f"> {c[:300]}")
            if len(ctx) > 5:
                lines.append(f"> ... 共 {len(ctx)} 条")
            lines.append("")
        lines.append("---")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _ensure_chinese(judge, goldens):
    """对非中文的 input / expected_output 调用 LLM 翻译为中文。"""
    async def _translate_one(g):
        inp = g.input if hasattr(g, "input") else getattr(g, "input", "")
        exp = g.expected_output if hasattr(g, "expected_output") else getattr(g, "expected_output", "")

        need_translate_input = not _is_chinese(inp)
        need_translate_output = not _is_chinese(exp)

        if not need_translate_input and not need_translate_output:
            return g

        tasks = []
        if need_translate_input:
            prompt = f"将以下内容翻译为中文，只返回翻译结果，不要加任何解释：\n\n{inp}"
            tasks.append(("input", judge.a_generate(prompt)))
        if need_translate_output:
            prompt = f"将以下内容翻译为中文，只返回翻译结果，不要加任何解释：\n\n{exp}"
            tasks.append(("output", judge.a_generate(prompt)))

        results = await asyncio.gather(*(t[1] for t in tasks))
        for (field, _), result in zip(tasks, results):
            if isinstance(result, tuple):
                result = result[0]
            if field == "input" and result:
                if hasattr(g, "input"):
                    g.input = result
                else:
                    setattr(g, "input", result)
            elif field == "output" and result:
                if hasattr(g, "expected_output"):
                    g.expected_output = result
                else:
                    setattr(g, "expected_output", result)
        return g

    async def _translate_all():
        return await asyncio.gather(*(_translate_one(g) for g in goldens))

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(_translate_all())
        return loop.run_until_complete(_translate_all())
    except RuntimeError:
        return asyncio.run(_translate_all())


# ═══════════════════════════════════════════════════════════════════════════
# Monkey-patches（与原脚本一致）
# ═══════════════════════════════════════════════════════════════════════════

def _apply_monkey_patches(embedding_model: str):
    # Patch 1：允许非 OpenAI embedding 模型名
    import deepeval.models.embedding_models.openai_embedding_model as _oem
    if embedding_model not in _oem.valid_openai_embedding_models:
        _oem.valid_openai_embedding_models.append(embedding_model)

    # Patch 2：DashScope embedding 批量上限 10
    _orig_embed_texts = _oem.OpenAIEmbeddingModel.embed_texts

    def _batched_embed_texts(self, texts):
        batch_size = 10
        if len(texts) <= batch_size:
            return _orig_embed_texts(self, texts)
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            all_embeddings.extend(_orig_embed_texts(self, batch))
        return all_embeddings

    _oem.OpenAIEmbeddingModel.embed_texts = _batched_embed_texts

    _orig_a_embed_texts = _oem.OpenAIEmbeddingModel.a_embed_texts

    async def _batched_a_embed_texts(self, texts):
        batch_size = 10
        if len(texts) <= batch_size:
            return await _orig_a_embed_texts(self, texts)
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            all_embeddings.extend(await _orig_a_embed_texts(self, batch))
        return all_embeddings

    _oem.OpenAIEmbeddingModel.a_embed_texts = _batched_a_embed_texts

    # Patch 3：ChromaDB 中文 collection 名 → MD5
    import deepeval.synthesizer.chunking.doc_chunker as _dc

    def _sanitize_source(source_file: str) -> str:
        full_path, _ = os.path.splitext(source_file)
        doc_name = os.path.basename(full_path)
        if not all(ord(c) < 128 for c in doc_name):
            doc_hash = hashlib.md5(doc_name.encode()).hexdigest()[:12]
            doc_name = f"doc_{doc_hash}"
        return str(Path(source_file).parent / (doc_name + Path(source_file).suffix))

    _orig_chunk_doc = _dc.DocumentChunker.chunk_doc

    def _patched_chunk_doc(self, chunk_size=1024, chunk_overlap=0,
                           client=None, collection_name=None):
        orig_source = self.source_file
        self.source_file = _sanitize_source(orig_source)
        try:
            return _orig_chunk_doc(self, chunk_size, chunk_overlap,
                                   client=client, collection_name=collection_name)
        finally:
            self.source_file = orig_source

    _dc.DocumentChunker.chunk_doc = _patched_chunk_doc

    _orig_a_chunk_doc = _dc.DocumentChunker.a_chunk_doc

    async def _patched_a_chunk_doc(self, chunk_size=1024, chunk_overlap=0,
                                   client=None, collection_name=None):
        orig_source = self.source_file
        self.source_file = _sanitize_source(orig_source)
        try:
            return await _orig_a_chunk_doc(self, chunk_size, chunk_overlap,
                                           client=client, collection_name=collection_name)
        finally:
            self.source_file = orig_source

    _dc.DocumentChunker.a_chunk_doc = _patched_a_chunk_doc

    # Patch 4：evaluate_chunk None cost bug
    import deepeval.synthesizer.chunking.context_generator as _cg
    _orig_evaluate_chunk = _cg.ContextGenerator.evaluate_chunk

    def _fixed_evaluate_chunk(self, chunk) -> float:
        prompt = _cg.FilterTemplate.evaluate_context(chunk)
        if self.using_native_model:
            res, cost = self.model.generate(prompt, schema=_cg.ContextScore)
            if cost is not None:
                self.total_cost += cost
            return (res.clarity + res.depth + res.structure + res.relevance) / 4
        else:
            try:
                res: _cg.ContextScore = self.model.generate(prompt, schema=_cg.ContextScore)
                return (res.clarity + res.depth + res.structure + res.relevance) / 4
            except TypeError:
                res = self.model.generate(prompt)
                data = _cg.trimAndLoadJson(res, self)
                return (data["clarity"] + data["depth"] + data["structure"] + data["relevance"]) / 4

    _cg.ContextGenerator.evaluate_chunk = _fixed_evaluate_chunk

    _orig_a_evaluate_chunk = _cg.ContextGenerator.a_evaluate_chunk

    async def _fixed_a_evaluate_chunk(self, chunk) -> float:
        prompt = _cg.FilterTemplate.evaluate_context(chunk)
        if self.using_native_model:
            res, cost = await self.model.a_generate(prompt, schema=_cg.ContextScore)
            if cost is not None:
                self.total_cost += cost
            return (res.clarity + res.depth + res.structure + res.relevance) / 4
        else:
            try:
                res: _cg.ContextScore = await self.model.a_generate(prompt, schema=_cg.ContextScore)
                return (res.clarity + res.depth + res.structure + res.relevance) / 4
            except TypeError:
                res = await self.model.a_generate(prompt)
                data = _cg.trimAndLoadJson(res, self)
                return (data["clarity"] + data["depth"] + data["structure"] + data["relevance"]) / 4

    _cg.ContextGenerator.a_evaluate_chunk = _fixed_a_evaluate_chunk

    # Patch 5：Synthesizer None cost bug
    import deepeval.synthesizer.synthesizer as _syn

    def _safe_cost_add(self, cost):
        if cost is not None and self.synthesis_cost is not None:
            self.synthesis_cost += cost

    _orig_generate_schema = _syn.Synthesizer._generate_schema

    def _fixed_generate_schema(self, prompt, schema, model):
        if _syn.is_native_model(model):
            res, cost = model.generate(prompt, schema)
            _safe_cost_add(self, cost)
            return res
        else:
            try:
                return model.generate(prompt, schema=schema)
            except TypeError:
                res = model.generate(prompt)
                data = _syn.trimAndLoadJson(res, self)
                if schema == _syn.SyntheticDataList:
                    return _syn.SyntheticDataList(
                        data=[_syn.SyntheticData(**item) for item in data["data"]])
                else:
                    return schema(**data)

    _syn.Synthesizer._generate_schema = _fixed_generate_schema

    _orig_a_generate_schema = _syn.Synthesizer._a_generate_schema

    async def _fixed_a_generate_schema(self, prompt, schema, model):
        if _syn.is_native_model(model):
            res, cost = await model.a_generate(prompt, schema)
            _safe_cost_add(self, cost)
            return res
        else:
            try:
                return await model.a_generate(prompt, schema=schema)
            except TypeError:
                res = await model.a_generate(prompt)
                data = _syn.trimAndLoadJson(res, self)
                if schema == _syn.SyntheticDataList:
                    return _syn.SyntheticDataList(
                        data=[_syn.SyntheticData(**item) for item in data["data"]])
                else:
                    return schema(**data)

    _syn.Synthesizer._a_generate_schema = _fixed_a_generate_schema

    _orig_generate = _syn.Synthesizer._generate

    def _fixed_generate(self, prompt):
        if self.using_native_model:
            res, cost = self.model.generate(prompt)
            _safe_cost_add(self, cost)
            return res
        else:
            try:
                return self.model.generate(prompt, schema=_syn.Response).response
            except TypeError:
                return self.model.generate(prompt)

    _syn.Synthesizer._generate = _fixed_generate

    _orig_a_generate = _syn.Synthesizer._a_generate

    async def _fixed_a_generate(self, prompt):
        if self.using_native_model:
            res, cost = await self.model.a_generate(prompt)
            _safe_cost_add(self, cost)
            return res
        else:
            try:
                return (await self.model.a_generate(prompt, schema=_syn.Response)).response
            except TypeError:
                return await self.model.a_generate(prompt)

    _syn.Synthesizer._a_generate = _fixed_a_generate

    # Patch 6：强制中文模板
    import deepeval.synthesizer.templates.template as _tpl

    @staticmethod
    def _cn_gen_inputs(
        context: str,
        max_goldens_per_context: str,
        scenario: str | None,
        task: str | None,
        input_format: str | None,
        available_source_files: list[str] | None = None,
        target_files_per_context: int | None = None,
    ) -> str:
        input_format_section = (
            f"`input` 必须严格遵循以下格式：{input_format}。"
            if input_format else "`input` 必须是一个字符串。"
        )
        scenario_section = (
            f"生成的 `input` 必须切合以下场景：```{scenario}```"
            if scenario else ""
        )
        task_section = (
            f"生成的 `input` 必须能够引出一个符合以下任务的回复：{task}"
            if task else ""
        )
        return f"""你是一位专业的内容策划。请根据以下上下文（一组文本片段），生成一个 JSON 对象列表，每个对象包含一个 `input` 键。

`input` 可以是问题或陈述句，必须能够被给定的上下文所回答或支持。

**
重要：
- 请确保只返回 JSON 格式，`data` 键对应一个 JSON 对象列表。
- 请尽量生成 {max_goldens_per_context} 条数据，除非生成的 `input` 开始重复。
- 生成的 `input` 必须全部使用中文，不允许任何英文。
- 你应该以每条上下文的字面意思为准，不要引入你自己的先验知识。
- 至少包含一条陈述句作为 input。

示例上下文：["爱因斯坦因其发现青霉素而获得诺贝尔奖。", "爱因斯坦于1968年获得诺贝尔奖。"]
示例 max_goldens_per_context：2
示例 JSON：
{{
    "data": [
        {{
            "input": "爱因斯坦因为什么获得诺贝尔奖？"
        }},
        {{
            "input": "爱因斯坦是个了不起的人"
        }}
    ]
}}

{input_format_section}
{scenario_section}
{task_section}

请尽量生成 {max_goldens_per_context} 条数据，除非生成的 `input` 开始重复。
**

最大生成数：
{max_goldens_per_context}

上下文：
{context}

JSON：
"""

    _tpl.SynthesizerTemplate.generate_synthetic_inputs = _cn_gen_inputs

    @staticmethod
    def _cn_gen_output(input: str, context: str, expected_output_format) -> str:
        fmt_section = (
            f"重要：请确保生成的回复严格遵循以下格式：{expected_output_format}，简洁直接，使用上下文中的支撑信息。"
            if expected_output_format
            else "重要：请确保生成的回复简洁直接，使用上下文中的支撑信息。"
        )
        return f"""根据以下输入（可能是问题），使用上下文中的信息生成回复。

**
{fmt_section}
**

上下文：
{context}

输入：
{input}

你必须使用中文生成回复，不允许使用任何英文。

生成的回复：
"""

    _tpl.SynthesizerTemplate.generate_synthetic_expected_output = _cn_gen_output

    @staticmethod
    def _cn_rewrite(evolved_input: str, scenario=None, task=None, input_format=None) -> str:
        scenario_section = f'场景："{scenario}"' if scenario else ""
        task_section = f'任务："{task}"' if task else ""
        input_format_section = f'输入格式："{input_format}"' if input_format else ""
        return f"""根据以下演化后的输入（可能是问题或陈述句），生成一个 JSON 对象，包含 `input` 键。

**
重要：请尽量少修改演化后的输入。但如果它不符合场景、任务或输入格式，则必须调整。输出必须是 JSON 格式，只包含 `input` 键。
生成的 `input` 必须使用中文。
**

示例演化输入："Is it okay to joke about someone losing their job?"
{f'示例场景："{scenario}"' if scenario else ""}
{f'示例任务："{task}"' if task else ""}
{f'示例输入格式："{input_format}"' if input_format else ""}
示例 JSON：{{
    "input": "拿别人失业开玩笑合适吗？如何在不伤害对方的情况下表达幽默？"
}}

演化输入：
{evolved_input}

{scenario_section}
{task_section}
{input_format_section}

JSON：
"""

    _tpl.SynthesizerTemplate.rewrite_evolved_input = _cn_rewrite


# ═══════════════════════════════════════════════════════════════════════════
# 主逻辑
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="按文档长度动态出题并合成 DeepEval Golden 数据"
    )
    parser.add_argument("--docs-dir", required=True, help="知识库文档目录")
    parser.add_argument("--output", default="eval/datasets/", help="输出目录")
    parser.add_argument("--max-goldens", type=int, default=0,
                        help="全局题目上限（0=不限）")
    args = parser.parse_args()

    # ── 加载 .env ──────────────────────────────────────────────────
    repo_root = Path(__file__).resolve().parent.parent
    env = _load_dotenv(repo_root / ".env")
    for k, v in env.items():
        if k not in os.environ:
            os.environ[k] = v

    embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")

    # ── 扫描文档，按文档长度计算动态 limit ─────────────────────────
    doc_dir = Path(args.docs_dir)
    if not doc_dir.is_dir():
        print(f"错误：文档目录不存在：{doc_dir}")
        sys.exit(1)

    doc_files = (
        list(doc_dir.rglob("*.md"))
        + list(doc_dir.rglob("*.txt"))
        + list(doc_dir.rglob("*.pdf"))
    )
    if not doc_files:
        print(f"错误：在 {doc_dir} 中未找到任何文档文件")
        sys.exit(1)

    # 按 (limit, 文件大小, 文件名) 整理
    file_info: list[tuple[int, int, Path]] = []
    for f in doc_files:
        size = f.stat().st_size
        limit = compute_limit(size)
        file_info.append((limit, size, f))

    # 按 limit 分组
    groups: dict[int, list[tuple[int, Path]]] = defaultdict(list)
    for limit, size, f in file_info:
        groups[limit].append((size, f))

    print(f"📂 共发现 {len(doc_files)} 个文档，按长度分入 {len(groups)} 个批次：")
    print(f"{'Limit':>6}  {'文档数':>6}  {'大小范围':>16}")
    print("-" * 32)
    total_expected = 0
    for limit in sorted(groups.keys()):
        docs = groups[limit]
        sizes = [s for s, _ in docs]
        min_kb, max_kb = min(sizes) / 1024, max(sizes) / 1024
        count = len(docs)
        total_expected += limit * count
        print(f"{limit:>6}  {count:>6}  {min_kb:>6.1f}K ~ {max_kb:>6.1f}K")
    print("-" * 32)
    print(f"📊 预期最多生成 {total_expected} 题（实际由 LLM 根据上下文质量决定）")
    print()

    # ── 应用 monkey-patches ─────────────────────────────────────────
    _apply_monkey_patches(embedding_model)

    # ── 初始化模型 ──────────────────────────────────────────────────
    from deepeval_metrics import get_judge_model
    judge = get_judge_model()
    print(f"🤖 裁判模型: {judge.get_model_name()}")

    from deepeval.models import OpenAIEmbeddingModel
    from deepeval.synthesizer import Synthesizer
    from deepeval.synthesizer.synthesizer import ContextConstructionConfig

    emb_key = os.getenv("EMBEDDING_API_KEY", "")
    emb_url = os.getenv("EMBEDDING_BASE_URL", "")

    embedder_kwargs = {"api_key": emb_key}
    if emb_url:
        embedder_kwargs["base_url"] = emb_url
    embedder = OpenAIEmbeddingModel(model=embedding_model, **embedder_kwargs)

    # ── 逐批次合成 ──────────────────────────────────────────────────
    all_goldens = []
    sorted_limits = sorted(groups.keys())

    for batch_idx, limit in enumerate(sorted_limits, 1):
        docs = groups[limit]
        doc_paths = [str(f) for _, f in docs]
        doc_names = [f.name for _, f in docs]

        print(f"\n{'='*60}")
        print(f"🔄 批次 {batch_idx}/{len(groups)}：limit={limit}，共 {len(docs)} 篇文档")
        print(f"   Docs: {', '.join(doc_names[:5])}{'...' if len(doc_names) > 5 else ''}")
        print(f"{'='*60}")

        ctx_config = ContextConstructionConfig(
            critic_model=judge,
            embedder=embedder,
            chunk_size=1024,
            max_contexts_per_document=limit,
        )

        synth = Synthesizer(model=judge)
        try:
            goldens = synth.generate_goldens_from_docs(
                document_paths=doc_paths,
                max_goldens_per_context=limit,
                context_construction_config=ctx_config,
            )
            print(f"   ✅ 本批生成 {len(goldens)} 题")
            all_goldens.extend(goldens)
        except Exception as e:
            print(f"   ❌ 批次失败: {e}")
            # 继续处理下一批
            continue

    # ── 后处理：翻译非中文内容 ─────────────────────────────────────
    print(f"\n🌐 检查并翻译非中文内容...")
    all_goldens = _ensure_chinese(judge, all_goldens)

    # ── 全局上限 ────────────────────────────────────────────────────
    if args.max_goldens and len(all_goldens) > args.max_goldens:
        print(f"✂️  截断：{len(all_goldens)} → {args.max_goldens}（--max-goldens）")
        all_goldens = all_goldens[:args.max_goldens]

    # ── 保存 ────────────────────────────────────────────────────────
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    from deepeval.dataset import EvaluationDataset
    ds = EvaluationDataset(goldens=all_goldens)

    json_path = ds.save_as("json", str(out_dir), "goldens")
    csv_path = ds.save_as("csv", str(out_dir), "goldens")
    md_path = out_dir / "goldens.md"
    _write_markdown(all_goldens, md_path)

    # ── 统计 ────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"🎉 全量出题完成！")
    print(f"   📝 总题数：{len(all_goldens)}")
    print(f"   📄 JSON：{json_path}")
    print(f"   📊 CSV：{csv_path}")
    print(f"   📋 Markdown：{md_path}")

    # 按来源文档统计
    src_counts: dict[str, int] = defaultdict(int)
    for g in all_goldens:
        src = g.source_file if hasattr(g, "source_file") else getattr(g, "source_file", "unknown")
        src_counts[src] += 1
    print(f"\n📊 按文档出题统计（前 20）：")
    for src, count in sorted(src_counts.items(), key=lambda x: -x[1])[:20]:
        fname = Path(src).name if src != "unknown" else src
        print(f"   {count:>3} 题  {fname}")
    if len(src_counts) > 20:
        print(f"   ... 共 {len(src_counts)} 篇文档有出题")


if __name__ == "__main__":
    main()
