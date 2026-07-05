"""Seed eval_cases from goldens.json for the fixed-regression-v1 suite.

Usage: python3 -m sales_agent.optimization.seed_eval_cases [--tenant taishan] [--limit 50]
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy import select

from sales_agent.core.config import get_settings
from sales_agent.core.database import get_session_factory
from sales_agent.models.base import generate_id
from sales_agent.models.eval import EvalCase, EvalSuite

logger = logging.getLogger(__name__)

_DEFAULT_SUITE_NAME = "fixed-regression-v1"
_DEFAULT_SUITE_ID = "f085432cd19c45d6"  # existing suite for taishan


async def seed_from_goldens_json(
    json_path: str,
    tenant_id: str = "taishan",
    *,
    suite_id: str = _DEFAULT_SUITE_ID,
    suite_name: str = _DEFAULT_SUITE_NAME,
    limit: int = 0,
    clear_existing: bool = True,
) -> dict:
    """Import questions from a goldens.json file into eval_cases.

    JSON format: list of objects with keys: input, expected_output, context, ...

    Args:
        json_path: Path to goldens.json
        tenant_id: Target tenant
        suite_id: Eval suite ID to assign cases to
        suite_name: Suite name (used if suite doesn't exist)
        limit: Max cases to import (0 = all)
        clear_existing: Delete existing cases for this suite before import

    Returns:
        Dict with counts: imported, skipped, errors
    """
    settings = get_settings()
    factory = get_session_factory()

    # Load JSON
    with open(json_path, "r", encoding="utf-8") as f:
        questions = json.load(f)

    if limit > 0:
        questions = questions[:limit]

    logger.info("Loaded %d questions from %s", len(questions), json_path)

    async with factory() as db:
        # Ensure suite exists
        suite = await db.scalar(
            select(EvalSuite).where(EvalSuite.id == suite_id)
        )
        if suite is None:
            suite = EvalSuite(
                id=suite_id,
                tenant_id=tenant_id,
                name=suite_name,
                status="active",
            )
            db.add(suite)
            await db.flush()
            logger.info("Created eval suite: %s (%s)", suite_name, suite_id)

        # Clear existing cases for this suite
        if clear_existing:
            result = await db.execute(
                select(EvalCase).where(
                    EvalCase.tenant_id == tenant_id,
                    EvalCase.eval_suite_id == suite_id,
                )
            )
            existing = result.scalars().all()
            for case in existing:
                await db.delete(case)
            if existing:
                logger.info("Cleared %d existing cases", len(existing))

        imported = 0
        skipped = 0
        errors = 0

        for i, q in enumerate(questions):
            try:
                input_text = str(q.get("input", "")).strip()
                if not input_text:
                    skipped += 1
                    continue

                expected_output = str(q.get("expected_output", "") or "").strip()
                context = q.get("context", "") or ""
                source_file = str(q.get("source_file", "") or "")

                # Parse expected constraints from context/expected_output
                must_include = []
                must_not_include = []

                case_id = f"gt_{(i + 1):04d}"
                metadata = {
                    "source_file": source_file,
                    "source": "goldens.json",
                    "index": i,
                }
                if context:
                    metadata["context"] = str(context)[:500]

                eval_case = EvalCase(
                    id=generate_id(),
                    tenant_id=tenant_id,
                    eval_suite_id=suite_id,
                    case_id=case_id,
                    input_text=input_text,
                    expected_answer=expected_output,
                    expected_task_type="knowledge_qa",
                    must_include_json=json.dumps(must_include, ensure_ascii=False),
                    must_not_include_json=json.dumps(must_not_include, ensure_ascii=False),
                    metadata_json=json.dumps(metadata, ensure_ascii=False),
                    quality_status="pending",
                )
                db.add(eval_case)
                imported += 1

            except Exception as e:
                logger.error("Failed to import question %d: %s", i, e)
                errors += 1

        await db.commit()
        logger.info(
            "Seed complete: imported=%d skipped=%d errors=%d",
            imported, skipped, errors,
        )

        return {
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "total": len(questions),
        }


async def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Seed eval_cases from goldens.json")
    parser.add_argument("--json-path", default="eval/datasets/taishan/goldens.json")
    parser.add_argument("--tenant", default="taishan")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-clear", action="store_true")
    args = parser.parse_args()

    result = await seed_from_goldens_json(
        json_path=args.json_path,
        tenant_id=args.tenant,
        limit=args.limit,
        clear_existing=not args.no_clear,
    )
    print(f"Seeded: {result}")


if __name__ == "__main__":
    asyncio.run(main())
