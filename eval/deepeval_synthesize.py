#!/usr/bin/env python3
"""从产品文档自动生成 DeepEval Golden 测试数据。

环境变量：从项目根 .env 加载。
  - OPENAI_API_KEY / OPENAI_BASE_URL / DEEPEVAL_MODEL → 裁判 LLM
  - EMBEDDING_API_KEY / EMBEDDING_BASE_URL / EMBEDDING_MODEL → 嵌入模型
"""
import argparse
import hashlib
import os
import sys
from pathlib import Path


def _load_dotenv(path: Path) -> dict[str, str]:
    """手动解析 .env 文件。"""
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


def main():
    parser = argparse.ArgumentParser(
        description="从产品文档自动生成 DeepEval Golden 测试数据"
    )
    parser.add_argument(
        "--docs-dir", required=True,
        help="产品文档目录（递归扫描 .md/.txt/.pdf）",
    )
    parser.add_argument(
        "--output", default="eval/datasets/",
        help="输出目录（默认 eval/datasets/）",
    )
    parser.add_argument(
        "--max-goldens", type=int, default=20,
        help="最大生成的 Golden 总数（默认 20，0 = 不限制）",
    )
    parser.add_argument(
        "--limit-per-doc", type=int, default=3,
        help="每篇文档最多生成多少个 Golden（默认 3）",
    )
    args = parser.parse_args()

    # ── 加载根 .env ────────────────────────────────────────────────
    repo_root = Path(__file__).resolve().parent.parent
    env = _load_dotenv(repo_root / ".env")
    for k, v in env.items():
        if k not in os.environ:
            os.environ[k] = v

    # =================================================================
    # Monkey-patch 1：允许非 OpenAI 的 embedding 模型名
    # =================================================================
    embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")
    import deepeval.models.embedding_models.openai_embedding_model as _oem
    if embedding_model not in _oem.valid_openai_embedding_models:
        _oem.valid_openai_embedding_models.append(embedding_model)

    # =================================================================
    # Monkey-patch 2：DashScope embedding 批量上限 10（sync + async）
    # =================================================================
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

    # =================================================================
    # Monkey-patch 3：ChromaDB 不支持中文 collection 名（sync + async）
    # =================================================================
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
            return _orig_chunk_doc(
                self, chunk_size, chunk_overlap,
                client=client, collection_name=collection_name,
            )
        finally:
            self.source_file = orig_source

    _dc.DocumentChunker.chunk_doc = _patched_chunk_doc

    _orig_a_chunk_doc = _dc.DocumentChunker.a_chunk_doc

    async def _patched_a_chunk_doc(self, chunk_size=1024, chunk_overlap=0,
                                   client=None, collection_name=None):
        orig_source = self.source_file
        self.source_file = _sanitize_source(orig_source)
        try:
            return await _orig_a_chunk_doc(
                self, chunk_size, chunk_overlap,
                client=client, collection_name=collection_name,
            )
        finally:
            self.source_file = orig_source

    _dc.DocumentChunker.a_chunk_doc = _patched_a_chunk_doc

    # =================================================================
    # Monkey-patch 4：修复 evaluate_chunk 的 None cost bug（sync + async）
    # =================================================================
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
                res: _cg.ContextScore = self.model.generate(
                    prompt, schema=_cg.ContextScore
                )
                return (res.clarity + res.depth + res.structure + res.relevance) / 4
            except TypeError:
                res = self.model.generate(prompt)
                data = _cg.trimAndLoadJson(res, self)
                return (data["clarity"] + data["depth"] +
                        data["structure"] + data["relevance"]) / 4

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
                res: _cg.ContextScore = await self.model.a_generate(
                    prompt, schema=_cg.ContextScore
                )
                return (res.clarity + res.depth + res.structure + res.relevance) / 4
            except TypeError:
                res = await self.model.a_generate(prompt)
                data = _cg.trimAndLoadJson(res, self)
                return (data["clarity"] + data["depth"] +
                        data["structure"] + data["relevance"]) / 4

    _cg.ContextGenerator.a_evaluate_chunk = _fixed_a_evaluate_chunk

    # =================================================================
    # Monkey-patch 5：修复 Synthesizer 内部 4 个方法的 None cost bug
    # =================================================================
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

    # =================================================================
    # Monkey-patch 6：强制中文——替换模板为中文版
    # =================================================================
    import deepeval.synthesizer.templates.template as _tpl

    _orig_gen_inputs = _tpl.SynthesizerTemplate.generate_synthetic_inputs

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

    _orig_gen_output = _tpl.SynthesizerTemplate.generate_synthetic_expected_output

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

    _orig_rewrite = _tpl.SynthesizerTemplate.rewrite_evolved_input

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

    # =================================================================
    # 扫描文档目录
    # =================================================================
    doc_dir = Path(args.docs_dir)
    if not doc_dir.is_dir():
        print(f"错误：文档目录不存在或不可读：{doc_dir}")
        sys.exit(1)

    doc_files = (
        list(doc_dir.rglob("*.md"))
        + list(doc_dir.rglob("*.txt"))
        + list(doc_dir.rglob("*.pdf"))
    )
    if not doc_files:
        print(f"错误：在 {doc_dir} 中未找到任何 .md/.txt/.pdf 文件")
        sys.exit(1)

    print(f"发现 {len(doc_files)} 个文档文件，开始生成 Golden 数据...")

    # =================================================================
    # 初始化裁判模型
    # =================================================================
    from deepeval_metrics import get_judge_model
    judge = get_judge_model()
    print(f"裁判模型: {judge.get_model_name()}")

    # =================================================================
    # 初始化嵌入模型（使用独立 EMBEDDING_* 变量）
    # =================================================================
    from deepeval.models import OpenAIEmbeddingModel
    from deepeval.synthesizer import Synthesizer
    from deepeval.synthesizer.synthesizer import ContextConstructionConfig

    emb_key = os.getenv("EMBEDDING_API_KEY", "")
    emb_url = os.getenv("EMBEDDING_BASE_URL", "")

    embedder_kwargs = {"api_key": emb_key}
    if emb_url:
        embedder_kwargs["base_url"] = emb_url
    embedder = OpenAIEmbeddingModel(model=embedding_model, **embedder_kwargs)

    ctx_config = ContextConstructionConfig(
        critic_model=judge,
        embedder=embedder,
        chunk_size=1024,
        max_contexts_per_document=args.limit_per_doc,
    )

    # =================================================================
    # 合成 Golden
    # =================================================================
    synth = Synthesizer(model=judge)
    goldens = synth.generate_goldens_from_docs(
        document_paths=[str(f) for f in doc_files],
        max_goldens_per_context=args.limit_per_doc,
        context_construction_config=ctx_config,
    )

    # ── 后处理：强制所有输入/输出翻译为中文 ──────────────────────
    goldens = _ensure_chinese(judge, goldens)

    # ── 限制总数 ──────────────────────────────────────────────────
    if args.max_goldens and len(goldens) > args.max_goldens:
        goldens = goldens[:args.max_goldens]

    # ── 保存 ──────────────────────────────────────────────────────
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    from deepeval.dataset import EvaluationDataset
    ds = EvaluationDataset(goldens=goldens)

    json_path = ds.save_as("json", str(out_dir), "goldens")
    csv_path = ds.save_as("csv", str(out_dir), "goldens")

    # ── Markdown 输出 ─────────────────────────────────────────────
    md_path = out_dir / "goldens.md"
    _write_markdown(goldens, md_path)

    print(f"\n生成完成！")
    print(f"  Golden 数量：{len(goldens)}")
    print(f"  JSON 文件：{json_path}")
    print(f"  CSV 文件：{csv_path}")
    print(f"  MD 文件：{md_path}")


def _is_chinese(text: str) -> bool:
    """检查文本是否以中文为主。"""
    if not text:
        return True
    alpha_chars = [c for c in text if c.isalpha()]
    if not alpha_chars:
        return True
    cn_chars = [c for c in alpha_chars if ord(c) > 127]
    return len(cn_chars) / len(alpha_chars) >= 0.5


def _ensure_chinese(judge, goldens):
    """对非中文的 input / expected_output 调用 LLM 翻译为中文。"""
    import asyncio

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


def _write_markdown(goldens, path: Path) -> None:
    """将 Golden 列表写入可读的 Markdown 文件。"""
    lines = [
        "# DeepEval Golden 测试数据",
        "",
        f"共 {len(goldens)} 条题目，由 DeepEval Synthesizer 自动生成。",
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


if __name__ == "__main__":
    main()
