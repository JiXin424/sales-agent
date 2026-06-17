"""Prompt Registry 单元测试。"""

from __future__ import annotations

import json
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.prompt import PromptVersion
from sales_agent.services.prompt_registry import (
    PromptRegistry,
    _validate_task_type,
    _validate_template,
    _DEFAULT_PROMPTS,
)
from sales_agent.services.task_router import ALL_TASK_TYPES


# --- Validation tests ---


class TestValidation:
    def test_validate_task_type_accepts_known_types(self):
        """所有合法 task_type 都应通过校验。"""
        for tt in ALL_TASK_TYPES:
            _validate_task_type(tt)  # should not raise

    def test_validate_task_type_rejects_unknown(self):
        with pytest.raises(ValueError, match="Invalid task_type"):
            _validate_task_type("unknown_type")

    def test_validate_template_accepts_message_placeholder(self):
        _validate_template("Hello {message} world")  # should not raise

    def test_validate_template_rejects_missing_message(self):
        with pytest.raises(ValueError, match="missing required placeholder"):
            _validate_template("No placeholder here")

    def test_validate_template_accepts_full_template(self):
        template = "{message} {context_block} {retrieval_block} {retrieval_content}"
        _validate_template(template)  # should not raise


# --- Registry integration tests ---


class TestPromptRegistry:
    @pytest.mark.asyncio
    async def test_default_resolution_for_all_task_types(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """没有 active 版本时，resolve 回退到 Python 默认 prompt。"""
        registry = PromptRegistry(db_session)
        for task_type in ALL_TASK_TYPES:
            text = await registry.resolve(sample_tenant, task_type)
            assert text == _DEFAULT_PROMPTS[task_type], (
                f"Default prompt mismatch for {task_type}"
            )

    @pytest.mark.asyncio
    async def test_tenant_override_takes_precedence(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """租户级 active 版本应覆盖默认 prompt。"""
        registry = PromptRegistry(db_session)

        # 创建 draft 并激活
        pv = await registry.create_version(
            tenant_id=sample_tenant,
            task_type="knowledge_qa",
            template_text="Custom {message} prompt",
        )
        activated = await registry.activate_version(sample_tenant, pv.id)

        # resolve 应返回自定义 prompt
        text = await registry.resolve(sample_tenant, "knowledge_qa")
        assert text == "Custom {message} prompt"
        assert activated.status == "active"

    @pytest.mark.asyncio
    async def test_single_active_enforcement(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """同一 tenant+task_type 最多一个 active 版本。"""
        registry = PromptRegistry(db_session)

        # 创建并激活两个版本
        v1 = await registry.create_version(
            tenant_id=sample_tenant,
            task_type="emotional_support",
            template_text="V1 {message}",
        )
        await registry.activate_version(sample_tenant, v1.id)

        v2 = await registry.create_version(
            tenant_id=sample_tenant,
            task_type="emotional_support",
            template_text="V2 {message}",
        )
        await registry.activate_version(sample_tenant, v2.id)

        # v1 应变为 archived
        v1_refreshed = await registry.get_version(sample_tenant, v1.id)
        assert v1_refreshed.status == "archived"

        # v2 应为 active
        v2_refreshed = await registry.get_version(sample_tenant, v2.id)
        assert v2_refreshed.status == "active"

        # resolve 应返回 v2
        text = await registry.resolve(sample_tenant, "emotional_support")
        assert text == "V2 {message}"

    @pytest.mark.asyncio
    async def test_create_version_rejects_invalid_task_type(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """创建版本时应拒绝不合法的 task_type。"""
        registry = PromptRegistry(db_session)
        with pytest.raises(ValueError, match="Invalid task_type"):
            await registry.create_version(
                tenant_id=sample_tenant,
                task_type="nonexistent",
                template_text="Hello {message}",
            )

    @pytest.mark.asyncio
    async def test_create_version_rejects_missing_placeholder(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """创建版本时应拒绝缺少 {message} 的模板。"""
        registry = PromptRegistry(db_session)
        with pytest.raises(ValueError, match="missing required placeholder"):
            await registry.create_version(
                tenant_id=sample_tenant,
                task_type="knowledge_qa",
                template_text="No message placeholder",
            )

    @pytest.mark.asyncio
    async def test_lifecycle_draft_activate_archive(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """完整的 draft → active → archived 生命周期。"""
        registry = PromptRegistry(db_session)

        # 创建 draft
        pv = await registry.create_version(
            tenant_id=sample_tenant,
            task_type="script_generation",
            template_text="Draft {message}",
        )
        assert pv.status == "draft"

        # 更新 draft
        updated = await registry.update_draft(
            sample_tenant, pv.id,
            template_text="Updated {message}",
        )
        assert updated.template_text == "Updated {message}"

        # 激活
        activated = await registry.activate_version(sample_tenant, pv.id)
        assert activated.status == "active"

        # 不能更新已激活的版本
        with pytest.raises(ValueError, match="Only draft versions"):
            await registry.update_draft(
                sample_tenant, pv.id,
                template_text="Should fail",
            )

        # 归档
        archived = await registry.archive_version(sample_tenant, pv.id)
        assert archived.status == "archived"

    @pytest.mark.asyncio
    async def test_list_versions(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """list_versions 应正确分页和过滤。"""
        registry = PromptRegistry(db_session)

        # 创建 3 个版本
        for i in range(3):
            await registry.create_version(
                tenant_id=sample_tenant,
                task_type="objection_handling",
                template_text=f"Version {i} {{message}}",
            )

        # 列出所有
        versions, total = await registry.list_versions(
            sample_tenant, task_type="objection_handling"
        )
        assert total == 3
        assert len(versions) == 3

        # 按 status 过滤
        drafts, draft_total = await registry.list_versions(
            sample_tenant, status="draft"
        )
        assert draft_total == 3

        # 分页
        page1, _ = await registry.list_versions(
            sample_tenant, task_type="objection_handling", limit=2, offset=0
        )
        assert len(page1) == 2

    @pytest.mark.asyncio
    async def test_tenant_isolation(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """不同租户的 prompt 不应互相可见。"""
        registry = PromptRegistry(db_session)

        # 创建租户 A 的版本
        pv = await registry.create_version(
            tenant_id=sample_tenant,
            task_type="knowledge_qa",
            template_text="Tenant A {message}",
        )

        # 租户 B 的 get_version 应返回 None
        result = await registry.get_version("other_tenant_999", pv.id)
        assert result is None

    @pytest.mark.asyncio
    async def test_default_fallback_different_tenant(
        self, db_session: AsyncSession, sample_tenant: str
    ):
        """没有 active 版本的租户应回退到默认 prompt。"""
        registry = PromptRegistry(db_session)
        text = await registry.resolve("brand_new_tenant", "knowledge_qa")
        assert text == _DEFAULT_PROMPTS["knowledge_qa"]
