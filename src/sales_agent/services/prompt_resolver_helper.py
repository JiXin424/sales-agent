"""Prompt 解析公共 helper。

为 agent 执行链路（Chat Graph / 钉钉流式 / CLI）统一解析 task + system prompt，
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
    """解析风险检查 prompt（供 Chat Graph / cli 的风险检查段调用）。"""
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


async def resolve_router_prompt(
    db: AsyncSession | None,
    key: str,
    tenant_id: str | None,
    agent_id: str | None,
    *,
    default: str,
) -> str:
    """解析 router 类 prompt（context_resolver / clarification_resolver / evidence_router）。

    三级回退（Agent 绑定 → tenant active → 内置默认）；当 *db* 为空、
    *tenant_id* 为空、或 (category,key) 解析抛错时返回 *default* 常量，
    保证向后兼容（service 单测不传 db 仍走旧常量路径）。

    Parameters
    ----------
    db :
        数据库会话；None 时直接回退 default。
    key :
        router 类下的具体标识（如 ``"context_resolver"``）。
    tenant_id, agent_id :
        租户 / Agent 标识；tenant_id 为空时直接回退 default。
    default :
        解析失败时的回退常量（通常是模块级 prompt 常量）。
    """
    if db is None or not tenant_id:
        return default
    try:
        return await PromptRegistry(db).resolve_prompt(
            "router", key, tenant_id, agent_id
        )
    except Exception:  # noqa: BLE001
        return default


class _KeepMissingDict(dict):
    """``str.format_map`` 安全映射：未知占位符原样保留为 ``{key}``。

    知识库子系统 prompt（entity_extraction / fact_extraction / md_optimize_user /
    ontology_term_extractor / ontology_response）含三类花括号：

    1. 真占位符（如 ``{content}``）—— 由调用方通过 ``**fmt_kwargs`` 注入；
    2. 字面 JSON 示例的 escape（``{{...}}``）—— ``str.format`` 自动还原为 ``{...}``；
    3. 运营在后台编辑器里漏 escape 的字面花括号（如 ``{task_type}`` 误写）。

    普通 ``.format(**kwargs)`` 把第 3 类当未知占位符抛 ``KeyError``（lessons #30）。
    本映射让未知 key 原样保留，三类花括号都能正确处理。
    """

    def __missing__(self, key: str) -> str:  # noqa: D401
        return "{" + key + "}"


async def resolve_knowledge_prompt(
    db: AsyncSession | None,
    key: str,
    tenant_id: str | None,
    agent_id: str | None = None,
    *,
    default: str,
    **fmt_kwargs: str,
) -> str:
    """解析 knowledge 类 prompt 并按需 ``format_map`` 注入占位符。

    三级回退（Agent 绑定 → tenant active → 内置默认）；当 *db* 为空、
    *tenant_id* 为空、或 ``("knowledge", key)`` 解析抛错时回退到 *default*。

    若给出 ``**fmt_kwargs``：用 ``format_map(_KeepMissingDict(fmt_kwargs))``
    注入占位符（lessons #30 SafeDict，未知 key 原样保留 ``{key}``，避免运营
    在后台把字面 JSON ``{{...}}`` 误改成 ``{...}`` 后崩 LLM 调用）。无
    ``fmt_kwargs`` 时直接返回模板，不 ``format``（适用于无占位符的
    ``image_interpret`` / ``md_optimize_system``）。

    Parameters
    ----------
    db :
        数据库会话；None 时直接回退 default。
    key :
        knowledge 类下的具体标识（如 ``"entity_extraction"``）。
    tenant_id, agent_id :
        租户 / Agent 标识；tenant_id 为空时直接回退 default。
    default :
        解析失败时的回退常量（通常是模块级 prompt 常量）。
    **fmt_kwargs :
        模板占位符注入值（如 ``content=chunk``）。
    """
    if db is None or not tenant_id:
        template: str = default
    else:
        try:
            template = await PromptRegistry(db).resolve_prompt(
                "knowledge", key, tenant_id, agent_id
            )
        except Exception:  # noqa: BLE001
            template = default
    if fmt_kwargs:
        return template.format_map(_KeepMissingDict(fmt_kwargs))
    return template
