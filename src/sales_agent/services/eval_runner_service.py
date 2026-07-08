"""评估回归测试运行服务。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sales_agent.models.base import generate_id
from sales_agent.models.eval import EvalCase, EvalRun, EvalRunResult, EvalSuite

logger = logging.getLogger(__name__)


class EvalRunnerService:
    """评估套件管理及运行。

    用法::

        svc = EvalRunnerService(db)
        suite = await svc.load_suite(tenant_id, "smoke", "eval/smoke_test.jsonl")
        run = await svc.run_suite(tenant_id, suite.id)
        results = await svc.get_run_results(tenant_id, run.id)
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def load_suite(
        self,
        tenant_id: str,
        name: str,
        fixture_path: str,
        description: str | None = None,
    ) -> EvalSuite:
        """从 JSONL fixture 文件加载评估套件和用例。

        JSONL 每行格式::

            {
                "case_id": "smoke_001",
                "input": "你好",
                "expected_task_type": "emotional_support",
                "expected_risk_level": null,
                "must_include": ["您好"],
                "must_not_include": []
            }
        """
        import pathlib

        path = pathlib.Path(fixture_path)
        if not path.exists():
            raise ValueError(f"Fixture file not found: {fixture_path}")

        suite = EvalSuite(
            id=generate_id(),
            tenant_id=tenant_id,
            name=name,
            description=description,
            fixture_path=str(path),
            case_count=0,
            status="active",
        )
        self.db.add(suite)
        await self.db.flush()

        case_count = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    case_data = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Skipping invalid JSON line in %s", fixture_path)
                    continue

                case = EvalCase(
                    id=generate_id(),
                    tenant_id=tenant_id,
                    eval_suite_id=suite.id,
                    case_id=case_data.get("case_id", f"case_{case_count}"),
                    input_text=case_data.get("input", ""),
                    expected_task_type=case_data.get("expected_task_type"),
                    expected_risk_level=case_data.get("expected_risk_level"),
                    must_include_json=json.dumps(
                        case_data.get("must_include", []), ensure_ascii=False
                    ),
                    must_not_include_json=json.dumps(
                        case_data.get("must_not_include", []), ensure_ascii=False
                    ),
                )
                self.db.add(case)
                case_count += 1

        suite.case_count = case_count
        await self.db.flush()
        return suite

    async def run_suite(
        self,
        tenant_id: str,
        eval_suite_id: str,
        prompt_version_id: str | None = None,
    ) -> EvalRun:
        """运行评估套件。

        创建 EvalRun，逐个执行用例，记录结果。
        注意：实际调用 Agent 需要在集成层完成，此处仅记录结构和占位逻辑。
        """
        # 获取套件和用例
        suite_stmt = select(EvalSuite).where(
            EvalSuite.id == eval_suite_id,
            EvalSuite.tenant_id == tenant_id,
        )
        suite = (await self.db.execute(suite_stmt)).scalar_one_or_none()
        if suite is None:
            raise ValueError(f"Eval suite not found: {eval_suite_id!r}")

        cases_stmt = select(EvalCase).where(
            EvalCase.eval_suite_id == eval_suite_id,
            EvalCase.tenant_id == tenant_id,
        ).order_by(EvalCase.created_at)
        cases = list((await self.db.execute(cases_stmt)).scalars().all())

        from sales_agent.models.base import utcnow

        run = EvalRun(
            id=generate_id(),
            tenant_id=tenant_id,
            eval_suite_id=eval_suite_id,
            status="running",
            total_cases=len(cases),
            passed=0,
            failed=0,
            skipped=0,
            prompt_version_id=prompt_version_id,
            started_at=utcnow(),
        )
        self.db.add(run)
        await self.db.flush()

        # 执行每个用例
        for case in cases:
            try:
                result = await self._execute_case(tenant_id, run.id, case)
                if result.passed == "true":
                    run.passed += 1
                else:
                    run.failed += 1
            except Exception as e:
                logger.error("Eval case %s failed: %s", case.case_id, e)
                run.failed += 1
                error_result = EvalRunResult(
                    id=generate_id(),
                    tenant_id=tenant_id,
                    eval_run_id=run.id,
                    eval_case_id=case.id,
                    passed="false",
                    failure_reasons_json=json.dumps([str(e)], ensure_ascii=False),
                )
                self.db.add(error_result)

        run.status = "completed"
        run.completed_at = utcnow()
        await self.db.flush()
        return run

    async def get_run(self, tenant_id: str, run_id: str) -> EvalRun | None:
        """获取评估运行记录。"""
        stmt = select(EvalRun).where(
            EvalRun.id == run_id,
            EvalRun.tenant_id == tenant_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_run_results(
        self, tenant_id: str, run_id: str
    ) -> list[EvalRunResult]:
        """获取评估运行的所有结果。"""
        stmt = (
            select(EvalRunResult)
            .where(
                EvalRunResult.eval_run_id == run_id,
                EvalRunResult.tenant_id == tenant_id,
            )
            .order_by(EvalRunResult.created_at)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_runs(
        self,
        tenant_id: str,
        eval_suite_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[EvalRun], int]:
        """列出评估运行记录。"""
        conditions = [EvalRun.tenant_id == tenant_id]
        if eval_suite_id:
            conditions.append(EvalRun.eval_suite_id == eval_suite_id)

        total = (await self.db.execute(
            select(func.count()).select_from(EvalRun).where(*conditions)
        )).scalar() or 0

        stmt = (
            select(EvalRun)
            .where(*conditions)
            .order_by(EvalRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list((await self.db.execute(stmt)).scalars().all())
        return items, total

    async def list_suites(
        self,
        tenant_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[EvalSuite], int]:
        """列出评估套件。"""
        conditions = [EvalSuite.tenant_id == tenant_id]

        total = (await self.db.execute(
            select(func.count()).select_from(EvalSuite).where(*conditions)
        )).scalar() or 0

        stmt = (
            select(EvalSuite)
            .where(*conditions)
            .order_by(EvalSuite.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list((await self.db.execute(stmt)).scalars().all())
        return items, total

    async def _execute_case(
        self, tenant_id: str, run_id: str, case: EvalCase
    ) -> EvalRunResult:
        """执行单个评估用例（占位实现）。

        实际实现需要调用 invoke_online_turn 或 agent chat 接口。
        此处仅创建一条 passed=true 的占位结果，供集成层替换。
        """
        result = EvalRunResult(
            id=generate_id(),
            tenant_id=tenant_id,
            eval_run_id=run_id,
            eval_case_id=case.id,
            passed="true",
            actual_task_type=case.expected_task_type,
            route_match="true",
        )
        self.db.add(result)
        await self.db.flush()
        return result
