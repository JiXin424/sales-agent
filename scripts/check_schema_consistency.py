#!/usr/bin/env python3
"""部署后 DB schema 一致性校验：DB 实际 schema 是否包含 ORM model 定义的所有表/列。

捕获 ``init_db`` stamp-head 兜底导致的「幽灵漂移」——``alembic_version`` 标到 head，
但 ``create_all`` 不 ALTER 已存在表、migration 的 ``add_column`` 又被 stamp 跳过，于是
model 里有的列 DB 里没有（典型：prod3 的 ``conversation_messages.topic_id``）。

校验基准用 ORM ``Base.metadata``（应用真正会查询的列），而非解析 migration 文件——
这样天然忽略「migration 加了但 model 没同步」的列（如 ``retrieval_profiles.entity_limit``，
应用实际从 config 读），只报「model 有、DB 没有」的真实缺失。

部署脚本在 ``compose up`` 后调用本脚本：
- exit 0 = DB 包含 model 所有表/列
- exit 1 = 有缺失（部署应失败，需 backfill migration 补齐）

无 ``DATABASE_URL`` 时 dry-run（exit 0）；连 DB 失败也 exit 0（交 health check 兜底，
校验本身不该把 DB 未就绪误判为漂移）。
"""

import asyncio
import os
import sys


def load_expected() -> dict[str, set[str]]:
    """返回 {table: {column_names}}，来自 ORM ``Base.metadata``。

    会触发 ``sales_agent.models`` 的导入（与 ``init_db`` 一致，含钉钉延迟导入模型）。
    """
    from sales_agent.core.database import Base
    import sales_agent.models  # noqa: F401  注册所有 model
    sales_agent.models._import_dingtalk_models()
    return {t.name: {c.name for c in t.columns} for t in Base.metadata.sorted_tables}


async def fetch_actual(url: str):
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        actual: dict[str, set[str]] = {}
        for r in await conn.fetch(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema='public'"
        ):
            actual.setdefault(r["table_name"], set()).add(r["column_name"])
    finally:
        await conn.close()
    return version, actual


def main() -> int:
    try:
        expected = load_expected()
    except Exception as e:  # noqa: BLE001
        print(f"[check-schema] ❌ 无法加载 ORM model：{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    n_cols = sum(len(v) for v in expected.values())
    print(f"[check-schema] ORM model 期望：{len(expected)} 表 / {n_cols} 列")

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("[check-schema] 无 DATABASE_URL，dry-run（exit 0）")
        return 0
    # asyncpg 不认 postgresql+asyncpg://，规范化成 postgresql://
    url = url.replace("postgresql+asyncpg://", "postgresql://", 1)

    try:
        version, actual = asyncio.run(fetch_actual(url))
    except Exception as e:  # noqa: BLE001
        print(
            f"[check-schema] ⚠️ 连 DB 失败，跳过校验（{type(e).__name__}: {e}），"
            "交由 health check 兜底",
            file=sys.stderr,
        )
        return 0

    print(f"[check-schema] DB alembic_version={version}，实际 {len(actual)} 表")

    missing: list[tuple[str, str]] = []
    for table, cols in sorted(expected.items()):
        actual_cols = actual.get(table)
        if actual_cols is None:
            missing.append((table, "<整表缺失>"))
            continue
        for c in sorted(cols):
            if c not in actual_cols:
                missing.append((table, c))

    if missing:
        print("[check-schema] ❌ DB 缺失（model 有但 DB 没有 = 幽灵漂移）：", file=sys.stderr)
        for t, c in missing:
            print(f"  - {t}.{c}", file=sys.stderr)
        print(
            "[check-schema] ❌ 校验失败：有 migration 的 DDL 未落地，需 backfill migration 补齐",
            file=sys.stderr,
        )
        return 1
    print("[check-schema] ✅ 通过：DB 包含 ORM model 所有表/列")
    return 0


if __name__ == "__main__":
    sys.exit(main())
