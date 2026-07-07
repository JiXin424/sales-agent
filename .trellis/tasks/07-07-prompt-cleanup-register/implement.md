# Implement — 执行计划

## 阶段 1：删死代码
- [ ] 1.1 读 context_loader.py 全文，确认 maybe_update_summary 边界（:177-280）+ 独占 import（grep 文件内 re/json/ConversationSummary/ConversationMessage/select/get_settings/ChatModel/AsyncSession 出现次数）
- [ ] 1.2 删 maybe_update_summary（:177-280）+ 清理独占 import
- [ ] 1.3 读 img_parser.py 确认 image_to_text 边界（:73-165）+ base64/Any 独占性
- [ ] 1.4 删 image_to_text（:73-165）+ 清理 base64（若独占）；保留 IMAGE_INTERPRET_PROMPT / is_image_file / get_image_mime_type
- [ ] 1.5 `grep -rn "maybe_update_summary" src/` → 空；`grep -rn "image_to_text" src/` → 仅 `_image_to_text_via_vision`（ingestion_service 内）

## 阶段 2：web 默认启用
- [ ] 2.1 `core/config.py:142` WebSearchConfig.enabled: bool = False → True
- [ ] 2.2 确认 default.yaml 无 web_search 段（已确认）
- [ ] 2.3 确认 web_fallback.py:40 无 key 兜底（已确认）

## 阶段 3：补注册 + 运行时走 PromptRegistry（层次 2）
- [ ] 3.1 `services/prompt_defaults.py` `_system_router_risk_entries()` import CONTEXT_RESOLVER_PROMPT + CLARIFICATION_RESOLVER_PROMPT；加 `BuiltinPrompt("router","context_resolver",CONTEXT_RESOLVER_PROMPT,(),"上下文消解（话语-话题关系）")` + `BuiltinPrompt("router","clarification_resolver",CLARIFICATION_RESOLVER_PROMPT,(),"澄清回复决策")`。验证 `len(BUILTIN_PROMPTS)==26`、`get_builtin("router","context_resolver")` 可取。
- [ ] 3.2 新增 helper `async def resolve_router_prompt(db, key, tenant_id, agent_id=None, *, default)` 到 `prompt_resolver_helper.py`：db 为空或 `PromptRegistry(db).resolve_prompt("router",key,tenant_id,agent_id)` 抛错时返回 default 常量。
- [ ] 3.3 改 `resolve_context`（context_resolver.py:98）签名加 `db=None, tenant_id=None, agent_id=None`；在 async 层 `await resolve_router_prompt(...)` 拿 system prompt，再传给 `_build_messages`（改 `_build_messages` 接 `system_prompt` 参数，去掉对常量的直接引用）。
- [ ] 3.4 改 `resolve_clarification`（topic_manager.py:409）签名加 `db=None, tenant_id=None, agent_id=None`；`:462` 的 CLARIFICATION_RESOLVER_PROMPT 改用 `await resolve_router_prompt(...)`。
- [ ] 3.5 改 `route_intent_evidence`（evidence_router.py:95）签名加 `db=None, tenant_id=None, agent_id=None`；`:126` 的 EVIDENCE_ROUTER_PROMPT 改用 `await resolve_router_prompt(...)`。
- [ ] 3.6 改 `context_resolution_node`（context_resolution.py）：调 `resolve_context`(:280) 与 `resolve_clarification`(:95-99) 时，从 state 取 tenant_id/agent_id（已有 :206-207）、ctx 取 db 传入。
- [ ] 3.7 改 `evidence_routing_node`（evidence_routing.py:71）：补从 state 取 tenant_id/agent_id、ctx 取 db，传给 `route_intent_evidence`。
- [ ] 3.8 验证：db=None 时三 service 回退常量（30 个单测不传 db 不变、全过）；topic_routing OFF 时两节点不进（生产零变化）。

## 阶段 4：ChatPipeline deprecation + eval 迁移 todo
- [ ] 4.1 `services/chat_pipeline.py` 类 docstring 强化废弃说明（指向 invoke_online_turn + eval 迁移）
- [ ] 4.2 新建 `tasks/todo_eval_migrate_to_graph.md`（独立后续工作记录）

## 阶段 5：验证（CLAUDE.md #4）
- [ ] 5.1 `pytest tests/unit/ tests/integration/test_chat_graph.py -x`（既有 DB 环境失败允许保留，需对比 HEAD 确认非本次引入）
- [ ] 5.2 `python -c "from sales_agent.services.prompt_defaults import BUILTIN_PROMPTS; print(len(BUILTIN_PROMPTS))"` → 26
- [ ] 5.3 grep 确认无残留死代码引用
- [ ] 5.4 资深工程师自检：web 无 key 不崩；resolver 注册不影响运行时（硬编码不变）；删死代码不破坏 import

## 阶段 6：收尾（CLAUDE.md 强制）
- [ ] 6.1 `README.md`「产品文档对照」prompt 数 24→26 + 更新日志索引
- [ ] 6.2 `changelog/2026-07-07.md` 记录（对象/类型/影响范围/改动明细/原因）
- [ ] 6.3 `tasks/lessons.md` 追加：①「引用≠可达」审计方法；② evidence_router 注册但运行时硬编码（半残）；③ ChatPipeline eval 依赖不可删
- [ ] 6.4 `git diff` 自检 → commit（按 CLAUDE.md 提交规范）

## 阶段 7：8 个内部 prompt 补注册（knowledge 类）+ 前端显示
依据：`research/knowledge-prompt-resolve-callers.md`。去重后 **7 个 key**（`_ENTITY_EXTRACTION_PROMPT` 两份 diff 完全相同，共用 `ontology_term_extractor`）。
- [ ] 7.1 `services/prompt_defaults.py` 新增 `_knowledge_entries()`，注册 7 个 `BuiltinPrompt(category="knowledge")`：entity_extraction / fact_extraction / image_interpret / md_optimize_system / md_optimize_user / ontology_term_extractor（_ENTITY_EXTRACTION，ontology_graph.py 那份，retrieval_service.py 共用）/ ontology_response。追加到 `BUILTIN_PROMPTS`。验证 `len==33`、`get_builtin("knowledge",每个key)` 可取。`required_placeholders` 按 research 占位符矩阵设（5 个含 `{placeholder}`）。
- [ ] 7.2 前端 `console/src/utils/constants.ts`：`PROMPT_CATEGORY_LABELS` 加 `web:'联网搜索'`+`knowledge:'知识库'`（**修复漏 web 的已知 bug**）；`PROMPT_KEYS_BY_CATEGORY` 加 `web:[{key:'web_analysis',...}]` + `knowledge:[7 个 key 中文 label]`。
- [ ] 7.3 `console/src/pages/Prompts/PromptListPage.tsx:39` `CATEGORY_ORDER` 加 `'web','knowledge'`；`console/src/api/types.ts:339` `PromptCategory` 加 `'web'|'knowledge'`；`AgentPromptsPage.tsx` 检查同步。

## 阶段 8：8 个内部 prompt 运行时接 PromptRegistry（层次 2）
**硬约束**：所有新 `db/tenant_id/agent_id` 参数**一律默认 None**；db 空/tenant_id 空/resolve 抛错 → 回退硬编码常量（参照 `resolve_router_prompt`）。**tests 零改动**（test_runner/test_retrieval_service/test_ingestion_service/explorer/e2e 直接构造 service 不传 db，已确认）。
**占位符陷阱**：5 个模板含 `{placeholder}` + 字面 JSON `{{ }}` → resolve 后必须 `.format_map(SafeDict)` 兜底（防 lessons #30）。
- [ ] 8.1 `prompt_resolver_helper.py` 加 `resolve_knowledge_prompt(db, key, tenant_id, agent_id=None, *, default, **fmt_kwargs)`：db 空/resolve 抛错回退 default；用 `_KeepMissingDict`（lessons #30 SafeDict）format_map 注入 fmt_kwargs。
- [ ] 8.2 `ontology/extractor.py` extract_entities/extract_facts 加可选 db/tenant_id/agent_id；用 resolve_knowledge_prompt(entity_extraction/fact_extraction)。
- [ ] 8.3 `ontology/ingestion_service.py`：OntologyIngestionService 已有 db_session（构造第 1 参）→ 传给 extract_*；IMAGE_INTERPRET 4 处（:148/171/201/227，httpx 直发）用 resolve_knowledge_prompt(image_interpret)。
- [ ] 8.4 `services/md_optimizer.py` MDOptimizer.__init__ 加可选 db/tenant_id/agent_id；optimize 用 resolve_knowledge_prompt(md_optimize_system/user)。
- [ ] 8.5 `services/knowledge_ingestor.py` 构造 MDOptimizer 传 db（KnowledgeIngestor.db 已有）；cli.py:56 确保 db 传入；uploads.py/documents.py 死路径不影响。
- [ ] 8.6 `graph/retrieval/ontology_graph.py` extract_terms_node：`runtime.context.get("db")` + state tenant_id/agent_id，用 resolve_knowledge_prompt(ontology_term_extractor)。
- [ ] 8.7 `ontology/retrieval_service.py` OntologyRetrievalService.__init__ 加可选 db/tenant_id/agent_id；_extract_search_terms 用 resolve_knowledge_prompt(ontology_term_extractor)。
- [ ] 8.8 `ontology/answer_service.py` OntologyAnswerService.__init__ 加可选 db/tenant_id/agent_id；generate_answer/render_response_prompt 用 resolve_knowledge_prompt(ontology_response)。
- [ ] 8.9 `api/routes/ontology.py` _build_explorer_services 构造 retrieval/answer service 时传 db（:379 已有 db）；_do_ingest 已有 bg_db。
- [ ] 8.10 验证：tests/unit/ontology/ + tests/integration/test_ontology_* 全过（不传 db 走常量）；db=None 行为不变；/builtin /effective 列出 knowledge 7 项。

## 阶段 9：收尾更新（数字 26→33）
- [ ] 9.1 README.md「产品文档对照」prompt 数 26→33 + 新增 knowledge 类
- [ ] 9.2 changelog/2026-07-07.md 追加 8 内部 prompt 补注册 + knowledge 分类 + 层次2
- [ ] 9.3 lessons.md 追加：8 内部 prompt 注册+层次2；占位符 format_map SafeDict 兜底

## 关键验证命令
```bash
grep -rn "maybe_update_summary" src/                         # 期望空
grep -rn "image_to_text" src/                                # 期望仅 _image_to_text_via_vision
python -c "from sales_agent.services.prompt_defaults import BUILTIN_PROMPTS; print(len(BUILTIN_PROMPTS))"  # 33（26 router + 7 knowledge）
pytest tests/unit/ tests/integration/test_chat_graph.py -x
```

## Review

### 完成情况
- ✅ 阶段 1-2：删死代码（`maybe_update_summary` / `image_to_text`）+ `web_search.enabled` 默认 True。注：两处删除与并发会话重合（并发也删了），最终状态一致。
- ✅ 阶段 3：router 层次2（context_resolver / clarification_resolver 补注册 + 三个 router service 接 `resolve_router_prompt`，db 可选默认 None，单测零改动）。
- ✅ 阶段 4：ChatPipeline deprecation docstring 强化（指向 `invoke_online_turn` + eval 迁移）+ `tasks/todo_eval_migrate_to_graph.md`。
- ✅ 阶段 7：knowledge 7 prompt 补注册（26→33）+ 前端 web/knowledge 分类（修 web 漏显 bug）。
- ✅ 阶段 8：knowledge 层次2（`resolve_knowledge_prompt` helper + SafeDict + 8 调用方接，循环导入用 lazy import 解决）。
- ✅ 阶段 9：README（33 + knowledge）+ changelog/2026-07-07.md + lessons #38。

### 验证
- `BUILTIN_PROMPTS`==33；router 测试 29 passed；ontology 66 passed；死代码 grep 空；web enabled True；前端 PromptCategory 含 web/knowledge。
- 1 pre-existing failure（`test_extract_facts_chunks`，ontology 去重既有 bug，stash 对比确认非本次）+ DB connection errors（环境无 postgres）。

### 事故与找回
- router 层次2 改动中途被并发会话 `git reset --hard origin/main` 清掉（reflog 证据，HEAD@{1}）。靠 `git stash list` + `git fsck --lost-found` 从 **stash@{0}** 找回 6 文件（`git checkout stash@{0} --`），img_parser / context_loader 与并发删除重合。见 lessons #38。
- `/root/CLAUDE.md` 已加规则「实现类任务先 EnterWorktree 隔离」+「共享目录禁用破坏性 git」防再发。

### 未 commit
- 改动全在工作目录（未提交），且与并发会话改动混在一起。提交时需精确 `git add` 本任务文件（见 changelog 改动明细），避开并发文件（console/* 、graph/__init__.py、被删 4 文件等）。stash@{0} 保留待验收后再清理。
