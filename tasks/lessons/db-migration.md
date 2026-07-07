# Lessons · DB / SQLAlchemy / Alembic 迁移

> 详情文件;索引见 `tasks/lessons.md`。#编号稳定。

## #2 SQLAlchemy:先 flush 子对象再设外键,否则外键可能丢失
- **教训**:新增子对象(如 `AgentPromptSet`)并绑定到父对象外键时,**先 `flush` 子对象拿到稳定 id,再设父外键,再 `flush`**。`add + 赋值 + 一次 flush` 的组合会导致外键丢失(与 identity map / dirty 追踪时序有关)。
- **正确范式**:
  ```python
  db.add(ps); await db.flush()      # 先持久化 ps
  agent.prompt_set_id = ps.id
  await db.flush()                  # 再持久化外键
  ```
- **检查**:`_resolve_agent_prompt_version` 返回 None 时,打印 `agent.prompt_set_id` 确认是否持久化。
- **相关**:#5

## #3 create_all 不处理已有表的加列,必须用 Alembic
- **教训**:生产 DB schema 变更**必须走 Alembic migration**(CLAUDE.md 强制要求)。`create_all` 只建新表,不改已有表结构。**baseline 策略**:对已有生产库用 `alembic stamp head` 标记当前状态(不执行 DDL),再 `alembic upgrade head` 跑增量 migration;新库可直接 `upgrade head`(建表仍由 create_all 完成)。
- **相关**:#28

## #5 测试用 _make_agent 而非 ensure_default_agent_for_tenant 建 Agent
- **教训**:`ensure_default_agent_for_tenant` 会自己创建 prompt_set 并绑定 agent,测试中覆写 `prompt_set_id` 时行为异常(配合 #2 的 flush 时序问题)。需要精确控制 agent 的 prompt_set 绑定的测试,用 `AgentService.create_agent`(不预绑 prompt_set)+ 手动建 set + 设外键,可控性更好。
- **相关**:#2

## #28 init_db 里 create_all 跑在 alembic upgrade 之前 → migration 同时含 create_table+add_column 时必漂移:幽灵漂移(版本号=head 但列缺失)
- **教训**:① **`create_all` 和 `alembic upgrade` 不是顺序无关的**:当 migration 同时含建表+加列、且要建的表已在 ORM model 里,`create_all` 会抢建,让 upgrade 撞 `DuplicateTableError`。正确顺序是 **先 upgrade(create_all 还没预建,create_table 不撞)→ 后 create_all(幂等补 model-only 表)**。② **`upgrade 失败 → stamp head` 兜底是「谎言式容错」**:把 `alembic_version` 标到 head,掩盖了失败 revision 的 add_column 没执行。「版本号到位、schema 没到位」的幽灵漂移极难发现——要等业务查询缺列 crash(且报的是次生 `InFailedSQLTransactionError`,不是原始 `UndefinedColumn`)。③ **`InFailedSQLTransactionError: current transaction is aborted` 永远是次生错误**,真正的根因(如 `UndefinedColumn`)在它前面的日志里。④ 同镜像在不同环境行为不同 → 先怀疑环境状态(DB schema/secrets/env),不是代码;**`alembic_version` 相同 ≠ schema 相同**。⑤ CI 部署 DB 不能只靠运行时 `init_db` 自动迁移;应加「部署后 schema 一致性校验」+ migration 预演(临时 pgvector 库 dry-run upgrade)。新 ORM 表必须同一 PR 配 `create_table` migration(model 先进、migration 后补 = 埋漂移)。
- **检查**:某环境报 `InFailedSQLTransactionError` 而其它正常 → `SELECT column_name FROM information_schema.columns WHERE table_name='X'` 跨环境 diff → 缺列则查 `alembic_version`(若版本号=正常但列缺 = 幽灵漂移)→ 写幂等 backfill migration(`ADD COLUMN IF NOT EXISTS`,**不要含 create_table**,否则又会被 stamp 跳过)补齐 → 同时修 `init_db` 顺序。事务 dry-run(`BEGIN; <migration SQL>; ROLLBACK;`)可无风险验证 SQL 兼容性。
- **相关**:#3 #25 #29
