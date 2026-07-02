#!/usr/bin/env bash
# run-eval.sh 冒烟：语法 + 关键逻辑断言 + --help（不依赖 docker）。
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT/scripts/run-eval.sh"

bash -n "$SCRIPT"

# 关键逻辑必须存在
grep -q 'sales-agent-${TENANT}-api' "$SCRIPT" || { echo "缺容器发现"; exit 1; }
grep -q 'docker exec' "$SCRIPT"            || { echo "缺 docker exec"; exit 1; }
grep -q -- '-w /app' "$SCRIPT"             || { echo "缺 -w /app 工作目录"; exit 1; }
grep -q -- 'EXTRA_ENV+=( -e' "$SCRIPT"     || { echo "缺 env-file 透传(-e)"; exit 1; }
grep -q 'docker cp' "$SCRIPT"              || { echo "缺结果拷回(docker cp)"; exit 1; }
grep -q 'run_conversation_eval.py' "$SCRIPT" || { echo "缺 conversation 分支"; exit 1; }
grep -q 'deepeval_eval.py' "$SCRIPT"       || { echo "缺 deepeval 分支"; exit 1; }
grep -q 'run_retrieval_eval.py' "$SCRIPT"  || { echo "缺 retrieval 分支"; exit 1; }

# conversation 默认 app-url 指向容器内 8000（不是宿主机端口）
grep -q 'http://127.0.0.1:8000' "$SCRIPT"  || { echo "缺默认 app-url"; exit 1; }

# --help 应正常退出（不触发 docker）
"$SCRIPT" --help >/dev/null

# 缺 tenant/kind 应非零退出
if "$SCRIPT" >/dev/null 2>&1; then
  echo "缺参数时应失败，却成功了"; exit 1
fi

# run_retrieval_eval.py 默认 ground_truth 路径修复（指向 eval/，非 scripts/）
RET="$ROOT/scripts/run_retrieval_eval.py"
grep -q 'parents\[1\] / "eval" / "ground_truth_30q.json"' "$RET" \
  || { echo "run_retrieval_eval.py 默认 ground_truth 未指向 eval/"; exit 1; }

echo "run-eval.sh + retrieval 路径 冒烟 OK"
