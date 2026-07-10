"""Agent 克隆服务。

把一个既有 Agent 的"产品配置"复制成一个新的 draft Agent。
强制规则：
  - 绝不复制明文密钥 / webhook 凭证 / 渠道 secret。
  - 绝不复制运行历史（会话、追踪、反馈、告警、报告、eval 运行结果）。
  - 每次克隆写一条 agent_clone_manifests 记录 copied/referenced/reset/skipped。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.agent import Agent
from sales_agent.models.agent_channel_config import AgentChannelConfig
from sales_agent.models.agent_clone_manifest import AgentCloneManifest
from sales_agent.models.agent_knowledge_scope import AgentKnowledgeScope
from sales_agent.models.agent_risk_policy import AgentRiskPolicy
from sales_agent.models.eval import EvalSuite

logger = logging.getLogger(__name__)

# 安全：绝不复制/引用的明文密钥字段名（用于断言）
_SECRET_FIELD_NAMES = {"api_key", "api_key_env", "secret", "secret_refs", "token", "authorization", "webhook_secret"}


class AgentCloneService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def clone(self, source_agent_id: str, options: dict[str, Any]) -> dict[str, Any]:
        """执行克隆，返回 {agent: <dict>, manifest: <dict>}。"""
        source = (
            await self.db.execute(select(Agent).where(Agent.id == source_agent_id))
        ).scalar_one_or_none()
        if source is None:
            raise ValueError(f"Source agent not found: {source_agent_id}")

        target_tenant = options.get("tenant_id") or source.tenant_id
        cross_tenant = target_tenant != source.tenant_id

        # 资源选项（带默认）
        opt_prompt = options.get("prompt_set", "copy")
        opt_risk = options.get("risk_policy", "copy")
        opt_knowledge = options.get("knowledge_scope", "reference")
        opt_eval = options.get("eval_suite", "copy")
        opt_channel = options.get("channel_config", "shell_only")
        opt_model = options.get("model_config_choice", options.get("model_config", "reference"))

        # 跨 tenant 时知识强制清空（决策 1）
        if cross_tenant and opt_knowledge == "reference":
            opt_knowledge = "empty"

        copied: dict[str, Any] = {}
        referenced: dict[str, Any] = {}
        reset: dict[str, Any] = {}
        skipped: dict[str, Any] = {}

        # --- 1. 创建 target Agent（draft） ---
        target = Agent(
            tenant_id=target_tenant,
            name=options["name"],
            agent_type=source.agent_type,
            description=options.get("description") or source.description,
            status="draft",
            source_agent_id=source.id,
            model_config_ref="runtime",
            feature_flags_json=source.feature_flags_json or "{}",
            is_tenant_default=False,
            created_by=options.get("created_by"),
        )
        self.db.add(target)
        await self.db.flush()
        copied["metadata"] = {"source_agent_id": source.id, "name": options["name"]}

        # --- 2. Prompt set ---
        target.prompt_set_id = await self._clone_prompt_set(
            source, target, opt_prompt, copied, referenced, skipped
        )

        # --- 3. Risk policy ---
        target.risk_policy_id = await self._clone_risk_policy(
            source, target, opt_risk, copied, referenced, skipped
        )

        # --- 4. Knowledge scope ---
        target.knowledge_scope_id = await self._clone_knowledge_scope(
            source, target, opt_knowledge, cross_tenant, copied, referenced, skipped
        )

        # --- 5. Eval suite ---
        target.eval_suite_id = await self._clone_eval_suite(
            source, target, opt_eval, copied, referenced, skipped
        )

        # --- 6. Channel config（绝不复制 secret） ---
        await self._clone_channel(
            source, target, opt_channel, copied, skipped
        )

        # --- 7. Model config（绝不复制密钥） ---
        if opt_model == "reference":
            target.model_config_ref = source.model_config_ref
            referenced["model_config"] = {"model_config_ref": source.model_config_ref}
        else:  # "new"
            target.model_config_ref = "runtime"  # 占位，待 setup 配置
            copied["model_config"] = {"model_config_ref": "runtime"}

        # --- 8. Runtime history 永远重置 ---
        reset["runtime_history"] = [
            "conversations", "conversation_messages", "conversation_summaries",
            "retrieval_logs", "agent_runs", "agent_run_steps", "feedback",
            "review_items", "knowledge_gaps", "alerts", "pilot_reports",
            "eval_runs", "model_call_logs",
        ]

        await self.db.flush()

        # --- 安全断言：manifest 中不得出现明文密钥字段 ---
        _assert_no_plaintext_secrets(copied, referenced)

        # --- 9. 写 manifest ---
        manifest = AgentCloneManifest(
            source_agent_id=source.id,
            target_agent_id=target.id,
            tenant_id=target_tenant,
            options_json=json.dumps(options, ensure_ascii=False),
            copied_resources_json=json.dumps(copied, ensure_ascii=False),
            referenced_resources_json=json.dumps(referenced, ensure_ascii=False),
            reset_resources_json=json.dumps(reset, ensure_ascii=False),
            skipped_resources_json=json.dumps(skipped, ensure_ascii=False),
            created_by=options.get("created_by"),
        )
        self.db.add(manifest)
        await self.db.flush()

        from sales_agent.services.agent_service import _agent_to_dict
        return {"agent": _agent_to_dict(target), "manifest": _manifest_to_dict(manifest)}

    # ---- 各资源克隆实现 ----

    async def _clone_prompt_set(
        self, source: Agent, target: Agent, opt: str, copied, referenced, skipped
    ) -> str | None:
        src_ps = None
        if source.prompt_set_id:
            src_ps = (
                await self.db.execute(
                    select(AgentPromptSet).where(AgentPromptSet.id == source.prompt_set_id)
                )
            ).scalar_one_or_none()
        if src_ps is None:
            skipped["prompt_set"] = "source has no prompt set"
            return None
        try:
            src_map = json.loads(src_ps.task_prompt_versions_json or "{}")
        except json.JSONDecodeError:
            src_map = {}

        if opt == "reference":
            new_map = dict(src_map)
            new_ps = AgentPromptSet(
                agent_id=target.id, tenant_id=target.tenant_id, name=src_ps.name,
                task_prompt_versions_json=json.dumps(new_map, ensure_ascii=False),
            )
            self.db.add(new_ps)
            await self.db.flush()
            referenced["prompt_set"] = {"prompt_version_ids": list(new_map.values())}
            return new_ps.id

        # copy：复制每个映射到的 PromptVersion 为新的 draft 行（独立可编辑）
        new_map: dict[str, str] = {}
        for task_type, version_id in src_map.items():
            pv = (
                await self.db.execute(
                    select(PromptVersion).where(PromptVersion.id == version_id)
                )
            ).scalar_one_or_none()
            if pv is None:
                continue
            new_pv = PromptVersion(
                tenant_id=target.tenant_id,
                agent_id=target.id,
                task_type=pv.task_type,
                version=pv.version,
                status="draft",
                template_text=pv.template_text,
                description=pv.description,
                metadata_json=pv.metadata_json or "{}",
            )
            self.db.add(new_pv)
            await self.db.flush()
            new_map[task_type] = new_pv.id
        new_ps = AgentPromptSet(
            agent_id=target.id, tenant_id=target.tenant_id, name=src_ps.name,
            task_prompt_versions_json=json.dumps(new_map, ensure_ascii=False),
        )
        self.db.add(new_ps)
        await self.db.flush()
        copied["prompt_set"] = {
            "duplicated_versions": {
                old: new for old, new in zip(src_map.values(), new_map.values())
            },
        }
        return new_ps.id

    async def _clone_risk_policy(
        self, source: Agent, target: Agent, opt: str, copied, referenced, skipped
    ) -> str | None:
        if not source.risk_policy_id:
            skipped["risk_policy"] = "source has no risk policy"
            return None
        src_rp = (
            await self.db.execute(
                select(AgentRiskPolicy).where(AgentRiskPolicy.id == source.risk_policy_id)
            )
        ).scalar_one_or_none()
        if src_rp is None:
            skipped["risk_policy"] = "source risk policy missing"
            return None

        if opt == "reference":
            referenced["risk_policy"] = {"risk_policy_id": src_rp.id}
            return src_rp.id

        new_rp = AgentRiskPolicy(
            agent_id=target.id, tenant_id=target.tenant_id,
            rules_json=src_rp.rules_json or "{}",
        )
        self.db.add(new_rp)
        await self.db.flush()
        copied["risk_policy"] = {"source_id": src_rp.id, "new_id": new_rp.id}
        return new_rp.id

    async def _clone_knowledge_scope(
        self, source: Agent, target: Agent, opt: str, cross_tenant, copied, referenced, skipped
    ) -> str | None:
        if not source.knowledge_scope_id:
            skipped["knowledge_scope"] = "source has no knowledge scope"
            return None
        src_scope = (
            await self.db.execute(
                select(AgentKnowledgeScope).where(
                    AgentKnowledgeScope.id == source.knowledge_scope_id
                )
            )
        ).scalar_one_or_none()
        if src_scope is None:
            skipped["knowledge_scope"] = "source scope missing"
            return None

        if opt == "reference":
            referenced["knowledge_scope"] = {"knowledge_scope_id": src_scope.id}
            return src_scope.id

        if opt == "copy_subset":
            new_scope = AgentKnowledgeScope(
                agent_id=target.id, tenant_id=target.tenant_id, mode=src_scope.mode,
                document_ids_json=src_scope.document_ids_json or "[]",
                source_file_ids_json=src_scope.source_file_ids_json or "[]",
            )
            self.db.add(new_scope)
            await self.db.flush()
            copied["knowledge_scope"] = {
                "mode": src_scope.mode,
                "document_ids": json.loads(src_scope.document_ids_json or "[]"),
            }
            return new_scope.id

        # empty：独立空作用域
        new_scope = AgentKnowledgeScope(
            agent_id=target.id, tenant_id=target.tenant_id,
            mode="document_subset", document_ids_json="[]", source_file_ids_json="[]",
        )
        self.db.add(new_scope)
        await self.db.flush()
        copied["knowledge_scope"] = {"mode": "document_subset", "document_ids": []}
        return new_scope.id

    async def _clone_eval_suite(
        self, source: Agent, target: Agent, opt: str, copied, referenced, skipped
    ) -> str | None:
        if not source.eval_suite_id:
            skipped["eval_suite"] = "source has no eval suite"
            return None
        src_es = (
            await self.db.execute(
                select(EvalSuite).where(EvalSuite.id == source.eval_suite_id)
            )
        ).scalar_one_or_none()
        if src_es is None:
            skipped["eval_suite"] = "source eval suite missing"
            return None

        if opt == "reference":
            referenced["eval_suite"] = {"eval_suite_id": src_es.id}
            return src_es.id

        if opt == "empty":
            skipped["eval_suite"] = "user chose empty"
            return None

        # copy：复制 suite 元数据（fixture_path 共享；cases 从 fixture 加载）
        new_es = EvalSuite(
            tenant_id=target.tenant_id, agent_id=target.id,
            name=src_es.name, description=src_es.description,
            fixture_path=src_es.fixture_path, case_count=src_es.case_count,
            status="active",
        )
        self.db.add(new_es)
        await self.db.flush()
        copied["eval_suite"] = {"source_id": src_es.id, "new_id": new_es.id,
                                "fixture_path": src_es.fixture_path}
        return new_es.id

    async def _clone_channel(
        self, source: Agent, target: Agent, opt: str, copied, skipped
    ) -> None:
        if opt == "skip":
            skipped["channel_config"] = "user chose skip"
            return
        # shell_only：新建空渠道配置，绝不复制任何 secret / webhook 凭证
        src_channel = (
            await self.db.execute(
                select(AgentChannelConfig).where(AgentChannelConfig.agent_id == source.id)
            )
        ).scalar_one_or_none()
        channel = src_channel.channel if src_channel else "dingtalk"
        new_cfg = AgentChannelConfig(
            agent_id=target.id, tenant_id=target.tenant_id,
            channel=channel, status="not_configured",
            config_json="{}", secret_refs_json="{}",  # 空：无任何密钥
        )
        self.db.add(new_cfg)
        await self.db.flush()
        copied["channel_config"] = {
            "channel": channel, "status": "not_configured",
            "note": "shell only; no secrets or webhook credentials copied",
        }

def _manifest_to_dict(m: AgentCloneManifest) -> dict[str, Any]:
    return {
        "id": m.id,
        "source_agent_id": m.source_agent_id,
        "target_agent_id": m.target_agent_id,
        "tenant_id": m.tenant_id,
        "options": json.loads(m.options_json or "{}"),
        "copied_resources": json.loads(m.copied_resources_json or "{}"),
        "referenced_resources": json.loads(m.referenced_resources_json or "{}"),
        "reset_resources": json.loads(m.reset_resources_json or "{}"),
        "skipped_resources": json.loads(m.skipped_resources_json or "{}"),
        "created_at": m.created_at or "",
    }


def _assert_no_plaintext_secrets(copied: dict, referenced: dict) -> None:
    """扫描 manifest 内容，确保不含明文密钥字段。"""
    blob = json.dumps({"copied": copied, "referenced": referenced}, ensure_ascii=False)
    lowered = blob.lower()
    for forbidden in _SECRET_FIELD_NAMES:
        # 允许出现 "secret_refs" 作为 key 名（值为空 dict），
        # 但绝不允许出现形如 "secret":"xxxxx" 的明文值。这里用宽松检查：
        # 若出现 _value_ 形态（冒号后非空、非 { } 的字符串值）则告警。
        if forbidden == "secret_refs":
            continue
        # 简单启发：检查 "forbidden":"<非空非对象>" 模式
        import re
        pattern = re.compile(rf'"{forbidden}"\s*:\s*"([^"{{}}]+)"', re.IGNORECASE)
        m = pattern.search(blob)
        if m:
            raise AssertionError(
                f"Clone manifest contains plaintext secret field {forbidden!r}: "
                f"value leak detected"
            )
