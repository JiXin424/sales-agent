"""deploy-release.sh 冒烟测试：--env-file neo4j.env 注入。"""
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy-release.sh"


def test_bash_syntax_ok():
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()


def test_conditional_env_file_injection():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "--env-file" in text
    assert "secrets/neo4j.env" in text
    # 必须是条件注入（文件存在才加），不能无条件硬加
    assert "if [ -f \"secrets/neo4j.env\" ]" in text
