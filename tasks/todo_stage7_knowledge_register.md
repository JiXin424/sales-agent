# Stage 7: 注册 7 个 knowledge prompt + 前端显示

## 后端
- [ ] 7.1 prompt_defaults.py 新增 _knowledge_entries()（7 项），追加到 BUILTIN_PROMPTS
- [ ] 7.1b 补 context_resolver/clarification_resolver 到 _system_router_risk_entries（达到 33 总数）
- [ ] 7.1c 更新 category docstring 加 knowledge

## 前端
- [ ] 7.2 constants.ts: PROMPT_CATEGORY_LABELS 加 web/knowledge；PROMPT_KEYS_BY_CATEGORY 加 web/knowledge
- [ ] 7.3 PromptListPage.tsx: CATEGORY_ORDER 加 web/knowledge
- [ ] 7.3b types.ts: PromptCategory 加 web/knowledge
- [ ] 7.3c AgentPromptsPage.tsx: CATEGORIES 加 web/knowledge

## 验证
- [ ] python3 -c BUILTIN_PROMPTS count = 33
- [ ] pytest test_context_resolver test_evidence_router
- [ ] tsc --noEmit 无错
