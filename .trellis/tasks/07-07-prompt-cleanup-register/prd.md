# 清理废弃 prompt 死代码 + 补注册 resolver + web 兜底默认启用

## Goal

删除零调用死代码（maybe_update_summary / image_to_text）、补注册 context/clarification resolver 并让三个 router prompt 运行时走 PromptRegistry（层次 2，后台编辑生效）、web_search 默认启用；ChatPipeline 保留（eval 依赖），eval 迁移独立立项。

## Background

对全项目 prompt 做了**运行时可达性审计**（从生产入口钉钉 Stream / HTTP `/agent/chat` 反追，非 import 引用）。结论：

- 注册表 `BUILTIN_PROMPTS` 共 24 项：18 项默认可达（🟢），6 项默认 OFF 灰度特性（🟡：task_router / evidence_router / risk_check / coach_daily_eval×2 / web_analysis），**无纯死 prompt**。
- 真正的死代码：`maybe_update_summary`（零调用）、`image_to_text`（零外部调用）。
- 在用但未注册：`context_resolver` / `clarification_resolver`（与同子系统的 evidence_router 不一致）。
- evidence_router 虽已注册，但运行时硬编码用常量、不读 DB 版本（「注册了但半残」）。
- web 兜底默认 OFF，但无 key 时 web_fallback 安全返回 None，可默认开启。
- ChatPipeline 对生产零调用，但被 `eval/deepeval_*.py` 依赖（lessons #31），**不能删**，保留 + 强化 deprecation，eval 迁移独立立项。

## Requirements

1. **删死代码** `maybe_update_summary`（context_loader.py:177-280，含内联 summary_prompt）。
2. **删死代码** `image_to_text` 函数（img_parser.py:73-165），保留 IMAGE_INTERPRET_PROMPT 常量与 is_image_file / get_image_mime_type。
3. **web 兜底默认启用**：`WebSearchConfig.enabled` 默认 False → True（config.py:142）。
4. **补注册 + 运行时走 PromptRegistry（层次 2）**：context_resolver + clarification_resolver 注册到 BUILTIN_PROMPTS（24→26），且三个 router prompt（context_resolver / clarification_resolver / evidence_router）运行时改走 `PromptRegistry.resolve_prompt`，让后台编辑生效。db 可选默认 None（None 回退常量），单测零改动。详见 design 决策 1。
5. **ChatPipeline 保留 + 强化 deprecation** 标记；新建独立 todo 记录 eval→graph 迁移工作。

## Out of Scope

- 删除 ChatPipeline（eval 依赖，独立立项）。
- 迁移 eval 到 `invoke_online_turn`（独立立项）。
- 下线 6 个默认 OFF 灰度 prompt（用户决定全保留）。
- router prompt 与 parser schema 的启动期契约校验（lessons #30/#31 同族，本次走「resolve 失败回退常量」容错，不做启动期校验）。

## Acceptance Criteria

- [ ] `maybe_update_summary` 删除，全仓 grep 零残留，独占 import 清理。
- [ ] `image_to_text` 删除，IMAGE_INTERPRET_PROMPT 等保留，ingestion_service 入库视觉解析不受影响。
- [ ] `WebSearchConfig.enabled` 默认 True；无 BOCHA_API_KEY 时 web_fallback 仍安全 return None（不崩）。
- [ ] `BUILTIN_PROMPTS` 含 context_resolver / clarification_resolver 两项（24→26）；`/builtin`、`/effective` API 自动列出（无需前端改）。
- [ ] 三个 router prompt 运行时走 `resolve_prompt`（db 有值时走三级回退，db=None 时回退硬编码常量）；30 个单测调用点不传 db 仍通过。
- [ ] ChatPipeline 文件头/类 docstring 明确标注「生产零调用，仅 eval 依赖，待 eval 迁移后删除」。
- [ ] `tasks/todo_eval_migrate_to_graph.md` 建立独立后续工作。
- [ ] pytest 全绿（重点 context_resolver / topic_manager / evidence_router / context_loader / img_parser / prompt 注册）；既有失败（test_context_routing 等 DB 环境问题，非本次）允许保留。
- [ ] README「产品文档对照」prompt 数 24→26；changelog/2026-07-07.md 记录；lessons.md 追加审计教训。

## Notes

- 验证永远优先走生产入口（CLAUDE.md #4）：本项目生产入口是钉钉 Stream，非 HTTP `/agent/chat`。
- ChatPipeline 是 lessons #31 警告的「eval 老路径」，本次不碰其调用方（research 确认它不调三个 router service）。
