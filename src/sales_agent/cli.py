"""CLI 入口：sales-agent ingest / chat / eval。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import typer

app = typer.Typer(
    name="sales-agent",
    help="ToB 销售陪跑 Agent v0 CLI",
)


def _get_db_and_settings():
    """获取数据库 session 和配置。"""
    from sales_agent.core.config import get_settings
    from sales_agent.core.database import get_session_factory, init_db

    settings = get_settings()
    return settings


@app.command()
def ingest(
    tenant: str = typer.Option(..., "--tenant", "-t", help="租户 ID"),
    path: str = typer.Option(..., "--path", "-p", help="知识库目录路径"),
    rebuild: bool = typer.Option(False, "--rebuild", help="是否重建索引"),
):
    """导入企业知识库。"""
    asyncio.run(_ingest(tenant, path, rebuild))


async def _ingest(tenant: str, path: str, rebuild: bool):
    from sales_agent.core.config import get_settings
    from sales_agent.core.database import init_db, get_session_factory
    from sales_agent.services.tenant_resolver import TenantResolver
    from sales_agent.services.knowledge_ingestor import KnowledgeIngestor

    settings = get_settings()
    await init_db()

    factory = get_session_factory()
    async with factory() as db:
        try:
            # 解析租户
            resolver = TenantResolver(db)
            tenant_info = await resolver.resolve(tenant)
            provider = resolver.get_model_provider(tenant_info)

            # 导入
            ingestor = KnowledgeIngestor(db=db, embedding_model=provider.embedding, chat_model=provider.chat)
            result = await ingestor.ingest_directory(
                tenant_id=tenant,
                directory=path,
                rebuild_index=rebuild,
            )
            await db.commit()

            typer.echo(f"导入完成：")
            typer.echo(f"  文件扫描：{result['documents_seen']}")
            typer.echo(f"  文件导入：{result['documents_ingested']}")
            typer.echo(f"  文本块数：{result['chunks_created']}")
            if result["warnings"]:
                typer.echo(f"  警告：{len(result['warnings'])}")
                for w in result["warnings"]:
                    typer.echo(f"    - {w}")
            if result["errors"]:
                typer.echo(f"  错误：{len(result['errors'])}")
                for e in result["errors"]:
                    typer.echo(f"    - {e}")

        except Exception as e:
            typer.echo(f"导入失败：{e}", err=True)
            sys.exit(1)


@app.command()
def chat(
    tenant: str = typer.Option(..., "--tenant", "-t", help="租户 ID"),
    user: str = typer.Option("cli_user", "--user", "-u", help="用户 ID"),
):
    """交互式聊天。"""
    asyncio.run(_chat(tenant, user))


async def _chat(tenant: str, user: str):
    from sales_agent.core.config import get_settings
    from sales_agent.core.database import init_db, get_session_factory
    from sales_agent.services.tenant_resolver import TenantResolver
    from sales_agent.services.request_validator import validate_chat_request
    from sales_agent.services.task_router import route_task
    from sales_agent.services.retriever import Retriever
    from sales_agent.services.agent_executor import execute_agent
    from sales_agent.services.risk_checker import RiskChecker
    from sales_agent.services import conversation_logger
    from sales_agent.services.context_loader import is_reset_command
    from sales_agent.services.response_formatter import format_text_output, format_sales_visible_sources
    from sales_agent.models.base import generate_id

    settings = get_settings()
    await init_db()

    factory = get_session_factory()
    async with factory() as db:
        # 解析租户
        resolver = TenantResolver(db)
        try:
            tenant_info = await resolver.resolve(tenant)
        except Exception as e:
            typer.echo(f"租户错误：{e}", err=True)
            sys.exit(1)

        provider = resolver.get_model_provider(tenant_info)
        conversation_id = generate_id()

        typer.echo(f"Sales Agent v0 - 租户: {tenant}")
        typer.echo(f"输入销售问题开始对话，输入 /reset 重置，输入 /quit 退出")
        typer.echo("")

        while True:
            try:
                message = input(f"{tenant} > ").strip()
            except (EOFError, KeyboardInterrupt):
                typer.echo("\n再见！")
                break

            if not message:
                continue
            if message in ("/quit", "/exit", "quit", "exit"):
                typer.echo("再见！")
                break

            # 处理重置
            if is_reset_command(message):
                conversation_id = generate_id()
                typer.echo("已开启新话题。你可以直接说当前要处理的销售问题。\n")
                continue

            try:
                # 任务路由
                route_result = await route_task(
                    message, provider.chat, db=db, tenant_id=tenant
                )

                # RAG 检索
                retrieval_result = None
                if route_result.needs_retrieval:
                    retriever = Retriever(db, provider.embedding)
                    retrieval_result = await retriever.retrieve_for_task(
                        tenant_id=tenant,
                        message=message,
                        task_type=route_result.task_type,
                    )

                # Prompt 解析（YAML 全局加载）
                from sales_agent.llm.prompt_loader import get_prompt as _get_prompt
                _task_prompt = _get_prompt("task", route_result.task_type).template
                _system_prompt = _get_prompt("system", "system_constraint").template

                # Agent 执行
                answer_dict = await execute_agent(
                    chat_model=provider.chat,
                    task_type=route_result.task_type,
                    message=message,
                    retrieval_result=retrieval_result,
                    tenant_style=tenant_info.get("config", {}),
                    prompt_text=_task_prompt,
                    system_prompt_text=_system_prompt,
                )

                # 风险检查
                answer_text = json.dumps(answer_dict, ensure_ascii=False)
                risk_checker = RiskChecker()
                risk_result = risk_checker.check_output(answer_text)

                # 格式化输出
                text_output = format_text_output(answer_dict)
                typer.echo(f"\n[{route_result.task_type}]")
                typer.echo(text_output)

                if retrieval_result and retrieval_result.has_results:
                    sources_text = format_sales_visible_sources(
                        [s.to_source_item() for s in retrieval_result.sources]
                    )
                    if sources_text:
                        typer.echo(f"\n{sources_text}")

                if risk_result.action == "warn":
                    typer.echo(f"\n⚠️ {risk_result.notice}")

                typer.echo("")

                # 记录日志
                await conversation_logger.log_conversation(
                    db,
                    tenant_id=tenant,
                    user_id=user,
                    channel="cli",
                    conversation_id=conversation_id,
                    message=message,
                    task_type=route_result.task_type,
                    task_confidence=route_result.confidence,
                    answer_dict=answer_dict,
                    risk_dict=risk_result.to_dict(),
                )
                await db.commit()

            except Exception as e:
                typer.echo(f"\n错误：{e}\n", err=True)


@app.command()
def eval(
    tenant: str = typer.Option(..., "--tenant", "-t", help="租户 ID"),
    file: str = typer.Option(..., "--file", "-f", help="评估集 JSONL 文件路径"),
):
    """运行评估集。"""
    asyncio.run(_eval(tenant, file))


async def _eval(tenant: str, file: str):
    from sales_agent.core.config import get_settings
    from sales_agent.core.database import init_db, get_session_factory
    from sales_agent.services.tenant_resolver import TenantResolver
    from sales_agent.services.request_validator import validate_chat_request
    from sales_agent.services.task_router import route_task
    from sales_agent.services.retriever import Retriever
    from sales_agent.services.agent_executor import execute_agent
    from sales_agent.services.risk_checker import RiskChecker
    from sales_agent.models.base import generate_id

    settings = get_settings()
    await init_db()

    # 读取评估集
    path = Path(file)
    if not path.exists():
        typer.echo(f"评估集文件不存在: {file}", err=True)
        sys.exit(1)

    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))

    typer.echo(f"加载评估集：{len(cases)} 条")

    factory = get_session_factory()
    async with factory() as db:
        resolver = TenantResolver(db)
        try:
            tenant_info = await resolver.resolve(tenant)
        except Exception as e:
            typer.echo(f"租户错误：{e}", err=True)
            sys.exit(1)

        provider = resolver.get_model_provider(tenant_info)

        results = []
        for i, case in enumerate(cases, 1):
            input_msg = case.get("input", "")
            expected_type = case.get("expected_task_type", "")
            must_include = case.get("must_include", [])
            must_not_include = case.get("must_not_include", [])
            expected_risk = case.get("risk_level", "")

            typer.echo(f"[{i}/{len(cases)}] {input_msg[:50]}...")

            try:
                route_result = await route_task(
                    input_msg, provider.chat, db=db, tenant_id=tenant
                )

                retrieval_result = None
                if route_result.needs_retrieval:
                    retriever = Retriever(db, provider.embedding)
                    retrieval_result = await retriever.retrieve_for_task(
                        tenant_id=tenant,
                        message=input_msg,
                        task_type=route_result.task_type,
                    )

                # Prompt 解析（YAML 全局加载）
                from sales_agent.llm.prompt_loader import get_prompt as _get_prompt
                _task_prompt = _get_prompt("task", route_result.task_type).template
                _system_prompt = _get_prompt("system", "system_constraint").template

                answer_dict = await execute_agent(
                    chat_model=provider.chat,
                    task_type=route_result.task_type,
                    message=input_msg,
                    retrieval_result=retrieval_result,
                    prompt_text=_task_prompt,
                    system_prompt_text=_system_prompt,
                )

                answer_text = json.dumps(answer_dict, ensure_ascii=False)

                # 检查指标
                task_match = route_result.task_type == expected_type if expected_type else None
                must_include_hits = sum(1 for kw in must_include if kw in answer_text)
                must_not_violations = [kw for kw in must_not_include if kw in answer_text]

                results.append({
                    "case_id": case.get("case_id", f"case_{i}"),
                    "task_match": task_match,
                    "predicted_type": route_result.task_type,
                    "expected_type": expected_type,
                    "must_include_hits": f"{must_include_hits}/{len(must_include)}",
                    "must_not_violations": must_not_violations,
                    "confidence": route_result.confidence,
                })

            except Exception as e:
                results.append({
                    "case_id": case.get("case_id", f"case_{i}"),
                    "error": str(e),
                })

        # 输出汇总
        typer.echo("\n=== 评估汇总 ===")
        task_correct = sum(1 for r in results if r.get("task_match") is True)
        task_total = sum(1 for r in results if r.get("task_match") is not None)
        violations = sum(1 for r in results if r.get("must_not_violations"))

        if task_total > 0:
            typer.echo(f"任务分类准确率：{task_correct}/{task_total} = {task_correct/task_total:.1%}")
        typer.echo(f"违规数量：{violations}")

        # 输出详细结果
        output_path = path.stem + "_results.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        typer.echo(f"\n详细结果已保存到：{output_path}")


if __name__ == "__main__":
    app()


# ============================================================
# 进程角色命令：serve / stream / worker
# ============================================================


@app.command("serve")
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="监听地址"),
    port: int = typer.Option(8000, "--port", "-p", help="监听端口"),
):
    """启动 API 服务（role=api）。不启动钉钉 Worker。"""
    os.environ["PROCESS_ROLE"] = "api"
    os.execvp(
        "uvicorn",
        ["uvicorn", "sales_agent.main:app", "--host", host, "--port", str(port)],
    )


@app.command("stream")
def stream():
    """启动钉钉 Stream 长连接运行器（role=stream）。"""
    os.environ["PROCESS_ROLE"] = "stream"
    from sales_agent.roles.stream_runner import main as _stream_main
    _stream_main()


@app.command("worker")
def worker():
    """启动后台 Worker（role=worker）。不绑定 API 端口。"""
    os.environ["PROCESS_ROLE"] = "worker"
    from sales_agent.roles.worker_runner import main as _worker_main
    _worker_main()


# ── Optimization sub-commands ──────────────────────────────────────────

from sales_agent.cli_optimization import app as opt_app
app.add_typer(opt_app, name="iteration", help="Knowledge optimization iteration operations")
app.add_typer(opt_app, name="release", help="Release management operations")
app.add_typer(opt_app, name="checkpoint", help="Checkpoint replay and fork operations")
