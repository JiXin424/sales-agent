# Implement — Graph Debug checkpoint time-travel（只读）

## Phase A — 后端接线

- [x] A1. `/run`:`await get_checkpointer()` → compile(checkpointer);`thread_id = f"debug:{uuid4()}"`;`conversation_id` 同值;`started` 回传 thread_id。
- [x] A2. `GET .../threads/{tid}/checkpoints`(列表):前缀校验 403 + `aget_state_history` + 映射,返回 `{"checkpoints":[...]}`。
- [x] A3. `GET .../checkpoints/{cid}/state`:前缀校验 + 匹配 + `{values, next}`。
- [x] A4. `_compile_with_checkpointer(graph_id)` helper 复用。

验证:`pytest tests/unit/graph/` 43 通过(无回归);进程内 probe(monkey-patch `get_checkpointer` 共享 `InMemorySaver`)确认字段映射 + 403 + 形状。此仓库未配置 ruff/mypy。

## Phase B — 前端

- [x] B1. `graphDebug.ts`:`getCheckpoints` / `getCheckpointState`(SSE run 不动)。
- [x] B2. `GraphDebugPage.tsx`:解析 `started.thread_id`;`done` 后拉 checkpoints。
- [x] B3. 时间轴(Ant Design `Steps`)+ JSON viewer(自写 `JsonNode`,**零新依赖**)。
- [x] B4. localStorage 最近 20 条 run + `Select` 切换 + Tabs。

验证:`cd console && npm run build` 通过(tsc + vite)。

## Review Gate(实现中自查 + 修复)

- [x] 资深工程师标准。`/run` 响应只增不减;前缀校验无绕过;无 DB migration;无新依赖。
- **实现中发现并修复两处偏差**(详见 design.md 风险2 + lessons #20):
  1. **跨层形状漂移**:后端列表最初返回裸数组,前端期望 `{checkpoints:[...]}`(`resp.checkpoints ?? []`)→ 时间轴会恒空。已改后端包 `{checkpoints:...}`(+ `CheckpointListResponse` model + `response_model`)。
  2. **node 来源错误**:langgraph>=1.2 的 `metadata.writes` **不存在**(进程内实测:metadata 仅 `source`/`step`/`parents`),原 writes 推断恒 null → 改用 `snapshot.tasks[*].name`(回退 `next[0]`)。

## Phase C — 端到端验证

- [x] C4. 非 debug 前缀 → 403(进程内验证 HTTPException 403)。
- [ ] C1. 起 server(单 worker)+ console,chat 图 run → 时间轴 N 步 → 点步 state 正确。**待目标环境**(当前 dev 机:代码在容器外、DB 在容器内、8000 是旧镜像,HTTP 端到端跑不了)。
- [ ] C2. 刷新页面 → localStorage 重载(跨重启)。**待浏览器环境**。
- [ ] C3. 多 worker(`--workers 2`)PG 共享。**待有 `DATABASE_URL` 的环境**(本机 `.env` 无 `DATABASE_URL`,`get_checkpointer` 回退 InMemorySaver,无法验 PG 持久/多 worker)。

> 进程内已验证(共享 InMemorySaver,等价单 worker 单进程):端点形状 `{checkpoints:[...]}` / `{values,next}`、字段映射(step/checkpoint_id/ts/next/node 全非 null)、403、state 详情。HTTP SSE `started.thread_id` 回传为 2 行代码改动,逻辑简单但未在 HTTP 层实跑。

## 收尾

- [x] README 更新日志表加 2026-07-02 行。
- [x] `changelog/2026-07-02.md` 新增。
- [x] `tasks/lessons.md` #20(跨层契约 + langgraph 字段)。
- [x] design.md 同步实际实现(node 来源 / 风险2 实证)。
- [ ] `task.py finish` + archive(待用户确认是否 commit)。
