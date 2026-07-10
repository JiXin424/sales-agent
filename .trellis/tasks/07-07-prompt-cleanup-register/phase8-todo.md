# Phase 8 Todo — 7 knowledge prompts 接 PromptRegistry（层次 2）

## 调用方改造
- [x] 1. `services/prompt_resolver_helper.py` 加 `resolve_knowledge_prompt` + 本地 `_KeepMissingDict`
- [x] 2. `ontology/extractor.py` extract_entities/extract_facts 加可选 db/tenant_id/agent_id，用 helper
- [x] 3. `ontology/runner.py` LLMExtractor 转发 db/tenant_id/agent_id；`ontology/ingestion_service.py` 更新 ExtractorProtocol + _ingest_one 透传 + IMAGE_INTERPRET 经 _read_content 透传 resolved prompt
- [x] 4. `services/md_optimizer.py` MDOptimizer.__init__ 加可选 db/tenant_id/agent_id；optimize 用 helper
- [x] 5. `services/knowledge_ingestor.py` _optimize_md 透传 db/tenant_id
- [x] 6. `graph/retrieval/ontology_graph.py` extract_terms_node：runtime.context.get("db") + state tenant_id/agent_id
- [x] 7. `ontology/retrieval_service.py` OntologyRetrievalService.__init__ 加可选 db/tenant_id/agent_id；_extract_search_terms 用 helper
- [x] 8. `ontology/answer_service.py` OntologyAnswerService.__init__ 加可选 db/tenant_id/agent_id；generate_answer 用 helper 取 template → 传给 render_response_prompt
- [x] 9. `api/routes/ontology.py` _build_explorer_services 传 db/tenant_id/agent_id；query/stream 端点预解析 ontology_response template 传给 _build_full_context

## 验证
- [x] `python3 -c "from sales_agent.services.prompt_resolver_helper import resolve_knowledge_prompt; print('ok')"` → ok
- [x] `python3 -m pytest tests/unit/ontology/ tests/unit/test_context_resolver.py tests/unit/test_evidence_router.py -q` → 66 passed, 1 pre-existing failure (test_extract_facts_chunks, dedup logic), 3 pre-existing DB connection errors
- [x] 循环 import 修复（lazy import）
