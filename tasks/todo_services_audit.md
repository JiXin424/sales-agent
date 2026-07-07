# Services Audit TODO (read-only)

## Goal
Audit all 45 .py files under src/sales_agent/services/ for MOCK/placeholder and dead code.

## Phases
- [ ] Phase 1: Inventory + identify LIVE entry points (main.py, api/routes, cli, dingtalk)
- [ ] Phase 2: Dispatch 5 parallel subagents to read + grep each file group
  - Group A: chat_pipeline.py + chat-related (chat_pipeline, online_conversation, conversation_logger, response_formatter, output_normalizer, md_optimizer, structured_router_output, request_validator, latency_stats, latency_tracker)
  - Group B: Agent services (agent_clone_service, agent_executor, agent_migration, agent_readiness_service, agent_service, runtime_version_bootstrap)
  - Group C: Pilot/eval (pilot_metrics_service, pilot_report_service, pilot_status_service, eval_runner_service, feedback_classification_service, feedback_service, review_queue_service, alert_service)
  - Group D: Knowledge/topic/routing (knowledge_gap_service, knowledge_ingestor, topic_manager, task_router, path_router, evidence_router, context_loader, context_resolver, retriever)
  - Group E: Misc utilities (prompt_defaults, prompt_registry, prompt_resolver_helper, question_suite_service, release_service, release_types, risk_checker, run_tracer, tenant_resolver, web_search, change_comparison_service)
- [ ] Phase 3: Cross-verify DEAD candidates with grep across src/tests/root scripts
- [ ] Phase 4: Consolidate findings into final report

## Rules
- READ-ONLY: never modify files
- Exclude from grep: __pycache__, .claude/worktrees/, docs/, changelog/
- graph/ already cleaned; don't audit but services->graph imports are LIVE
- ChatPipeline is kept (root eval/deepeval_*.py depends on it)
