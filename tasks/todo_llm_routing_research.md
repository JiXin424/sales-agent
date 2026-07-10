# Todo: 摸清 route_task / check_risk 节点 LLM 接入现状与方案

- [x] 1. 读 chat_graph.py 的 `route_task` 节点实现（当前 rule 调用、有无 LLM 入口）
- [x] 2. 读 chat_graph.py 的 `check_risk` 节点实现（当前 full_check 调用、有无 LLM 入口）
- [x] 3. 读 task_router.py service `route_task()` 实现 + git diff HEAD（LLM 分支、开关、返回结构）
- [x] 4. 读 risk_checker.py `check_llm_risk` 实现 + `full_check` 返回结构对比
- [x] 5. 读 prompt 注册/加载机制（TASK_ROUTER_PROMPT / RISK_CHECK_PROMPT / generation.py:81 的 22 prompts）
- [x] 6. 评估接入方案：最小改动、返回结构兼容性、feature flag、推迟理由
- [x] 7. 结构化总结（最小改动路径 + 风险点）

## Review
- 关键修正：节点函数不在 chat_graph.py，而在 nodes/routing.py 与 nodes/risk_check.py
- 关键发现：Runtime.context 已落地（7 个节点在用），routing.py docstring 关于"Phase 3"的说法已过时
- service 层 route_task() 与 check_llm_risk() 都 async、都需要 chat_model+db，结构兼容 rule 路径
- 无现成 on/off flag，需新增 enable_llm_router / enable_llm_risk_check
- task_router.py 有未提交改动：修复 DB prompt 的 {花括号} 崩溃 + 嵌套 JSON 解析（之前 LLM 路由实际是坏的）
