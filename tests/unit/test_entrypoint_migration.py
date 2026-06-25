"""docker-entrypoint.sh 的冒烟测试：语法 + 关键逻辑断言。

shell 脚本不做完整 TDD；用 bash -n + 内容断言保证 migration 接入正确。
"""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
ENTRY = ROOT / "scripts" / "docker-entrypoint.sh"


def test_bash_syntax_ok():
    r = subprocess.run(["bash", "-n", str(ENTRY)], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()


def test_api_role_runs_migrations_with_gate():
    text = ENTRY.read_text(encoding="utf-8")
    assert "alembic upgrade head" in text
    assert "RUN_MIGRATIONS" in text
    # 必须在 api 分支内（"  api)" 到其 ;; 之间），且在 exec serve 之前
    api_block = text.split("  api)")[1].split(";;")[0]
    assert "alembic upgrade head" in api_block
    assert "RUN_MIGRATIONS" in api_block
    assert api_block.index("alembic upgrade head") < api_block.index("exec sales-agent serve")


def test_stream_worker_do_not_run_migrations():
    text = ENTRY.read_text(encoding="utf-8")
    # 实际 migration 调用（命令行）只出现一次：仅 api 分支（echo 消息不计）
    assert text.count("alembic upgrade head ||") == 1
