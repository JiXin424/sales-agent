"""Prompt 解析公共 helper。

为 agent 执行链路（chat_pipeline / 钉钉流式 / CLI）统一解析 task + system prompt，
避免每个调用方各自构造 ``PromptRegistry``。所有调用点经此 helper 解析后，即接入
DB 版本管理（钉钉流式、CLI 之前直接用代码常量，运营在后台改 prompt 不生效）。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.services.prompt_registry import PromptRegistry


async def resolve_execution_prompts(
    db: AsyncSession,
    agent_id: str | None,
    tenant_id: str,
    task_type: str,
) -> tuple[str, str]:
    """一次性解析 task prompt 与 system prompt。

    Returns:
        (task_prompt, system_prompt)，均经 registry 三级回退解析
        （Agent 绑定 → tenant active → 内置默认）。
    """
    reg = PromptRegistry(db)
    task_prompt = await reg.resolve_prompt("task", task_type, tenant_id, agent_id)
    system_prompt = await reg.resolve_prompt(
        "system", "system_constraint", tenant_id, agent_id
    )
    return task_prompt, system_prompt


async def resolve_risk_prompt(
    db: AsyncSession,
    tenant_id: str,
    agent_id: str | None = None,
) -> str:
    """解析风险检查 prompt（供 chat_pipeline / cli 的风险检查段调用）。"""
    return await PromptRegistry(db).resolve_prompt(
        "risk", "risk_check", tenant_id, agent_id
    )


async def resolve_quick_session_prompts(
    db: AsyncSession,
    tenant_id: str,
    agent_id: str | None = None,
) -> dict[str, str]:
    """解析快速会话（小赢欣赏 / 卡点破框）的 5 个 prompt。

    返回 {sw_system, sw_card, sb_system, sb_split, sb_card}；单个解析失败时缺省，
    底层函数回退到内置常量。
    """
    reg = PromptRegistry(db)
    keys = {
        "sw_system": ("coach", "coach_sw_system"),
        "sw_card": ("coach", "coach_sw_card"),
        "sb_system": ("coach", "coach_sb_system"),
        "sb_split": ("coach", "coach_sb_split"),
        "sb_card": ("coach", "coach_sb_card"),
    }
    out: dict[str, str] = {}
    for name, (cat, key) in keys.items():
        try:
            out[name] = await reg.resolve_prompt(cat, key, tenant_id, agent_id)
        except Exception:  # noqa: BLE001
            pass  # 缺省 → 底层用内置常量
    return out
