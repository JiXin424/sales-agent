"""
合成节点：调用 deepeval_synthesize.py 从知识库生成 Golden 题目。

复用已有脚本，以子进程方式调用，隔离 monkey-patch 副作用。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_synthesize(
    docs_dir: str,
    output_dir: str,
    limit_per_doc: int = 3,
    max_goldens: int = 0,
    *,
    cwd: str | Path | None = None,
) -> tuple[str, int]:
    """运行 deepeval_synthesize.py，返回 (golden_file_path, question_count)。

    Args:
        docs_dir: 知识库文档目录
        output_dir: 输出目录（golden 文件写入此目录）
        limit_per_doc: 每篇文档最多几题
        max_goldens: 总上限（0 = 不限制）
        cwd: 工作目录（默认项目根）

    Returns:
        (golden_file_path, question_count)
    """
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    if cwd is None:
        cwd = repo_root

    script = repo_root / "eval" / "deepeval_synthesize.py"
    cmd = [
        sys.executable, str(script),
        "--docs-dir", str(docs_dir),
        "--output", str(output_dir),
        "--limit-per-doc", str(limit_per_doc),
        "--max-goldens", str(max_goldens),
    ]

    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=900,  # 15 分钟超时
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"deepeval_synthesize.py failed (rc={result.returncode}):\n"
            f"STDOUT:\n{result.stdout[-2000:]}\n"
            f"STDERR:\n{result.stderr[-2000:]}"
        )

    # 从输出中解析 golden 数量和文件路径
    stdout = result.stdout
    golden_count = 0
    json_path = ""
    for line in stdout.splitlines():
        if "Golden 数量" in line:
            try:
                golden_count = int(line.split("：")[-1].strip())
            except (ValueError, IndexError):
                golden_count = int(line.split(":")[-1].strip())
        if "JSON 文件" in line:
            json_path = line.split("：")[-1].strip() if "：" in line else line.split(":")[-1].strip()

    if not json_path:
        # 回退：猜测路径
        json_path = str(Path(output_dir) / "goldens.json")

    return json_path, golden_count
