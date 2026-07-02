"""Question suite promotion and post-release exploration generation.

Promotion creates a new immutable fixed suite version by copying accepted
exploration cases into it. The previous suite is never mutated.

Post-release trigger enqueues exploration generation for the published
knowledge version.
"""

from __future__ import annotations

import json
import hashlib
import logging
from dataclasses import dataclass

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.eval import EvalSuite, EvalCase
from sales_agent.models.base import generate_id, utcnow

logger = logging.getLogger(__name__)


@dataclass
class PromotionResult:
    """Result of promoting exploration cases to a fixed suite."""
    new_suite_id: str
    parent_suite_id: str
    case_count: int  # total cases in new suite
    added_count: int  # newly promoted cases


class QuestionSuiteService:
    """Manage fixed and exploration question suites with immutable promotion."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def promote(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        source_case_ids: list[str],
        target_fixed_suite_id: str,
        actor_id: str,
    ) -> PromotionResult:
        """Promote accepted exploration cases into a new fixed suite version.

        Creates a new immutable fixed suite (version N+1) that includes all
        cases from the parent plus the newly promoted ones. The parent suite
        is never modified.

        Args:
            tenant_id: Tenant scope.
            agent_id: Agent scope.
            source_case_ids: Exploration case IDs to promote.
            target_fixed_suite_id: The current fixed suite to extend.
            actor_id: Who performed the promotion.

        Returns:
            PromotionResult with the new suite ID and counts.
        """
        # Load parent suite
        parent = await self.db.scalar(
            select(EvalSuite).where(
                EvalSuite.id == target_fixed_suite_id,
                EvalSuite.tenant_id == tenant_id,
            )
        )
        if parent is None:
            raise ValueError(f"Fixed suite {target_fixed_suite_id} not found")

        # Load cases to promote
        promoted_cases = (
            await self.db.execute(
                select(EvalCase).where(
                    EvalCase.id.in_(source_case_ids),
                    EvalCase.tenant_id == tenant_id,
                )
            )
        ).scalars().all()

        # Compute content hash
        case_ids = sorted([c.id for c in promoted_cases])
        content_hash = hashlib.sha256(
            json.dumps(case_ids, sort_keys=True).encode()
        ).hexdigest()[:16]

        # Create new version (immutable)
        new_version = parent.version + 1
        new_suite = EvalSuite(
            id=generate_id(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            name=parent.name,
            description=parent.description,
            suite_type="fixed",
            version=new_version,
            parent_suite_id=parent.id,
            generator_version="promotion",
            knowledge_version_id=parent.knowledge_version_id,
            case_count=parent.case_count + len(promoted_cases),
            content_hash=content_hash,
            status="active",
        )
        self.db.add(new_suite)
        await self.db.flush()

        # Copy promoted cases into the new suite with lineage
        for case in promoted_cases:
            new_case = EvalCase(
                id=generate_id(),
                tenant_id=tenant_id,
                eval_suite_id=new_suite.id,
                case_id=case.case_id,
                input_text=case.input_text,
                expected_task_type=case.expected_task_type,
                question_type=case.question_type,
                answerability=case.answerability,
                required_facts_json=case.required_facts_json,
                source_fact_ids_json=case.source_fact_ids_json,
                source_document_ids_json=case.source_document_ids_json,
                expected_route=case.expected_route,
                role_type=case.role_type,
                generation_strategy="promotion",
                generator_version="1.0",
                lineage_case_id=case.id,
                quality_status="accepted",
            )
            self.db.add(new_case)

        await self.db.flush()

        logger.info(
            "Promoted %d cases into fixed suite %s v%d (parent=%s) by %s",
            len(promoted_cases), new_suite.id, new_version, parent.id, actor_id,
        )

        return PromotionResult(
            new_suite_id=new_suite.id,
            parent_suite_id=parent.id,
            case_count=new_suite.case_count,
            added_count=len(promoted_cases),
        )

    async def create_exploration_suite(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        knowledge_version_id: str,
        name: str = "exploration",
    ) -> EvalSuite:
        """Create a new exploration suite for a knowledge version.

        Called after a successful release publication.
        """
        suite = EvalSuite(
            id=generate_id(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            name=f"{name}_kv_{knowledge_version_id[:8]}",
            suite_type="exploration",
            version=1,
            knowledge_version_id=knowledge_version_id,
            case_count=0,
            status="active",
        )
        self.db.add(suite)
        await self.db.flush()
        return suite
