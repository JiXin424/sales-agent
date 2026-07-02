#!/usr/bin/env bash
# 在任意服务器（含无源码 image-deploy 机）上跑 eval。
# 思路: host 只放这一个薄壳; 实际 eval 在运行中的 sales-agent-<tenant>-api 容器内执行
#       （依赖、eval/、DATABASE_URL/MODEL_* 都现成）。仅裁判 key 等额外 env 经 --env-file 透传。
# 结果默认从容器 /app/eval/{results,rounds} 拷回 host。
#
# 用法:
#   scripts/run-eval.sh [--app-url URL] [--env-file F] [--results-dir D] \
#                       <tenant> <conversation|deepeval|retrieval> [-- eval-args...]
#
# 示例:
#   scripts/run-eval.sh taishan conversation --app-url http://127.0.0.1:8010
#   scripts/run-eval.sh taishan deepeval --env-file secrets/eval.env -- --judge-model deepseek-chat
#   scripts/run-eval.sh taishan retrieval -- --round 01 --mode hybrid
#
# 注: --app-url 仅 conversation 用,默认 http://127.0.0.1:8000(容器视角,eval 在容器内跑);
#     不要传宿主机映射端口。deepeval/retrieval 忽略它。
#     --env-file 为 KEY=VALUE 行(忽略 # 注释); 通常放裁判 OPENAI_API_KEY/DEEPEVAL_MODEL/OPENAI_BASE_URL。
set -euo pipefail

usage() {
  cat <<USAGE
Usage: scripts/run-eval.sh [OPTIONS] <tenant> <conversation|deepeval|retrieval> [-- eval-args...]

Run eval inside the running sales-agent-<tenant>-api container (works on sourceless hosts).

Options:
  --app-url URL       App endpoint for 'conversation' (container-internal; eval runs inside
                      the api container, so defaults to http://127.0.0.1:8000). Do NOT pass
                      the host-mapped port (e.g. 8003) — unreachable from inside the container.
  --env-file F        Extra env (KEY=VALUE lines) passed via 'docker exec -e'
                      (e.g. judge OPENAI_API_KEY / DEEPEVAL_MODEL / OPENAI_BASE_URL).
  --results-dir D     Where to copy results back (default: ./eval-results).
  -h, --help          Show this help.

Everything after '--' is forwarded verbatim to the eval script.
USAGE
}

APP_URL=""; ENV_FILE=""; RESULTS_DIR=""
POSITIONAL=()
POSTDASH=()
SAW_DASH=0
while [ $# -gt 0 ]; do
  if [ "$1" = "--" ]; then
    shift; SAW_DASH=1; POSTDASH=("$@"); break
  fi
  case "$1" in
    --app-url)      shift; APP_URL="${1:-}" ;;
    --env-file)     shift; ENV_FILE="${1:-}" ;;
    --results-dir)  shift; RESULTS_DIR="${1:-}" ;;
    -h|--help)      usage; exit 0 ;;
    *)              POSITIONAL+=("$1") ;;
  esac
  shift
done

TENANT="${POSITIONAL[0]:-}"
KIND="${POSITIONAL[1]:-}"
if [ "$SAW_DASH" = "1" ]; then
  EVAL_ARGS=("${POSTDASH[@]}")
else
  EVAL_ARGS=("${POSITIONAL[@]:2}")
fi

if [ -z "$TENANT" ] || [ -z "$KIND" ]; then
  echo "错误: 需要 <tenant> <kind>" >&2
  usage >&2
  exit 2
fi

# ── 定位运行中的 api 容器 ──
CTR="sales-agent-${TENANT}-api"
if ! docker ps --format '{{.Names}}' 2>/dev/null | command grep -qx "$CTR"; then
  echo "错误: 容器 $CTR 未运行 (docker ps 看不到)" >&2
  echo "当前 sales-agent 租户:" >&2
  docker ps --format '{{.Names}}' 2>/dev/null \
    | command grep '^sales-agent-.*-api$' \
    | sed 's/^sales-agent-//; s/-api$//; s/^/    /' >&2 || echo "    (无)" >&2
  exit 1
fi

# ── 解析额外 env 文件 → docker exec -e KEY=VAL ... ──
EXTRA_ENV=()
if [ -n "$ENV_FILE" ]; then
  if [ ! -f "$ENV_FILE" ]; then
    echo "错误: env-file 不存在: $ENV_FILE" >&2; exit 1
  fi
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"          # 去前导空白
    [[ -z "$line" || "$line" == \#* ]] && continue   # 跳过空行/注释
    key="${line%%=*}"
    [[ -z "$key" || "$key" == "$line" ]] && continue # 无 '=' 或空 key
    val="${line#*=}"
    EXTRA_ENV+=( -e "$key=$val" )
  done < "$ENV_FILE"
fi

# ── 按 eval 类型组装命令(容器内路径) ──
case "$KIND" in
  conversation)
    # eval 在容器内执行 → app-url 是「容器视角」; 默认指向本容器内的 app(PORT 默认 8000)。
    # 注意: 不要传宿主机映射端口(如 8003),容器内访问不到。
    : "${APP_URL:=http://127.0.0.1:8000}"
    SCRIPT="eval/run_conversation_eval.py"
    SCRIPT_ARGS=( --app-url "$APP_URL" "${EVAL_ARGS[@]}" )
    ;;
  deepeval)
    SCRIPT="eval/deepeval_eval.py"
    SCRIPT_ARGS=( "${EVAL_ARGS[@]}" )
    ;;
  retrieval)
    # -m/-r 给默认值,用户可通过 eval-args 覆盖(argparse 取最后值)
    SCRIPT="scripts/run_retrieval_eval.py"
    SCRIPT_ARGS=( -t "$TENANT" -m hybrid -r local "${EVAL_ARGS[@]}" )
    ;;
  *)
    echo "错误: 未知 eval 类型 '$KIND' (可用: conversation|deepeval|retrieval)" >&2
    exit 2
    ;;
esac

# ── 执行(容器已自带 DATABASE_URL / MODEL_*; -w /app 设工作目录) ──
echo "[run-eval] $CTR → python $SCRIPT ${SCRIPT_ARGS[*]}"
set +e
docker exec "${EXTRA_ENV[@]}" -w /app "$CTR" python "$SCRIPT" "${SCRIPT_ARGS[@]}"
EXEC_RC=$?
set -e

# ── 结果拷回 host(best-effort) ──
RESULTS_DIR="${RESULTS_DIR:-./eval-results}"
mkdir -p "$RESULTS_DIR"
docker cp "$CTR:/app/eval/results/." "$RESULTS_DIR/" 2>/dev/null || true
docker cp "$CTR:/app/eval/rounds/."   "$RESULTS_DIR/" 2>/dev/null || true
echo "[run-eval] 结果已拷回 $RESULTS_DIR (eval rc=$EXEC_RC)"

exit "$EXEC_RC"
