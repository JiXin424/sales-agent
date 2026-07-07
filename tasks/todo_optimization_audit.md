# Optimization 子系统 Mock/死代码审计 Todo

## 范围
- src/sales_agent/optimization/ 下所有 .py（23 个）
- src/sales_agent/cli_optimization.py

## 步骤
- [ ] 1. 测绘 LIVE 入口：api/routes/optimization.py、mcp_server/、根 eval/、cli_optimization.py、main.py、dingtalk stream/worker、graph/online_graph.py
- [ ] 2. 读每个 optimization/*.py，找 mock/stub/placeholder/dead 迹象
- [ ] 3. 对每个候选符号 grep 全仓库（src+tests+根脚本），确认零引用
- [ ] 4. 分类输出：MOCK/DEAD/需复核/LIVE + 连带影响

## 排除
.claude/worktrees/、__pycache__/、docs/、changelog/
