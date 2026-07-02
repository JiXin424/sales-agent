#!/bin/bash
# ──────────────────────────────────────────────────────────────────
# Sales Agent + DeepEval 一键评估脚本
#
# 直接走 ChatPipeline（与钉钉用户相同的代码路径），不再依赖 HTTP API。
#
# 用法:
#   # 1. 快速冒烟（5 题）
#   bash eval/deepeval_run.sh smoke
#
#   # 2. 单租户完整评估
#   bash eval/deepeval_run.sh eval taishan
#
#   # 3. 两租户对比
#   bash eval/deepeval_run.sh compare taishan taishankaifa2
#
#   # 4. 用 Synthesizer 生成的 golden 文件评估
#   bash eval/deepeval_run.sh golden taishan eval/datasets/taishankaifa2/goldens.md
#
#   # 5. 自定义参数
#   bash eval/deepeval_run.sh eval taishan "qwen-plus" 10
# ──────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"

cd "$PROJECT_DIR"

# ── 颜色 ──────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ── 自动加载 .env 文件 ───────────────────────────────────────
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a  # 自动导出所有变量
    source "$PROJECT_DIR/.env"
    set +a
    info "Loaded env vars from .env"
elif [ -f "$PROJECT_DIR/.env.eval" ]; then
    set -a
    source "$PROJECT_DIR/.env.eval"
    set +a
    info "Loaded eval env vars from .env.eval"
fi

# ── 检查 Python 环境 ─────────────────────────────────────────
if [ ! -f "$VENV_PYTHON" ]; then
    err "Virtual env not found at $VENV_PYTHON"
    exit 1
fi

# 检查 deepeval 是否已安装
if ! "$VENV_PYTHON" -c "import deepeval" 2>/dev/null; then
    err "DeepEval not installed. Run: source .venv/bin/activate && pip install deepeval"
    exit 1
fi

# 检查 OPENAI_API_KEY（DeepEval 需要 LLM 当裁判）
if [ -z "${OPENAI_API_KEY:-}" ]; then
    warn "OPENAI_API_KEY not set. DeepEval metrics need an LLM to judge answers."
    warn "Set it: export OPENAI_API_KEY=sk-..."
    warn "Or use another model via DEEPEVAL_MODEL env var."
fi

# ── 命令分发 ─────────────────────────────────────────────────
MODE="${1:-help}"
case "$MODE" in
    smoke)
        # 快速冒烟测试：5 题，验证流程是否跑通
        TENANT_ID="${2:-taishan}"
        LIMIT="${3:-5}"
        export TENANT_ID="$TENANT_ID"

        info "Smoke test: $LIMIT questions against tenant=$TENANT_ID"
        "$VENV_PYTHON" "$SCRIPT_DIR/deepeval_eval.py" \
            --tenant-id "$TENANT_ID" \
            --limit "$LIMIT" \
            --concurrency 1 \
            --output-dir "$SCRIPT_DIR/results/deepeval/"
        ok "Smoke test done. Check eval/results/deepeval/"
        ;;

    eval)
        # 单租户完整评估
        TENANT_ID="${2:?Usage: $0 eval <tenant-id> [models] [limit]}"
        export TENANT_ID="$TENANT_ID"
        MODELS="${3:-all}"
        LIMIT="${4:-0}"

        info "Single-tenant eval: tenant=$TENANT_ID"
        info "Models: $MODELS, Limit: ${LIMIT:-all}"

        if [ "$LIMIT" != "0" ] && [ -n "$LIMIT" ]; then
            "$VENV_PYTHON" "$SCRIPT_DIR/deepeval_eval.py" \
                --tenant-id "$TENANT_ID" \
                --models "$MODELS" \
                --limit "$LIMIT" \
                --concurrency 3 \
                --output-dir "$SCRIPT_DIR/results/deepeval/"
        else
            "$VENV_PYTHON" "$SCRIPT_DIR/deepeval_eval.py" \
                --tenant-id "$TENANT_ID" \
                --models "$MODELS" \
                --concurrency 3 \
                --output-dir "$SCRIPT_DIR/results/deepeval/"
        fi
        ok "Eval done. Check eval/results/deepeval/"
        ;;

    risk)
        # 风险检测评估
        TENANT_ID="${2:-taishan}"

        info "Risk detection eval: tenant=$TENANT_ID"
        "$VENV_PYTHON" "$SCRIPT_DIR/deepeval_risk_eval.py" \
            --tenant-id "$TENANT_ID" \
            --output-dir "$SCRIPT_DIR/results/risk/"
        ok "Risk eval done. Check eval/results/risk/"
        ;;

    golden)
        # 用 Synthesizer 生成的 golden 文件评估
        TENANT_ID="${2:?Usage: $0 golden <tenant-id> <golden-file> [models] [limit]}"
        export TENANT_ID="$TENANT_ID"
        GOLDEN_FILE="${3:?Usage: $0 golden <tenant-id> <golden-file> [models] [limit]}"
        MODELS="${4:-all}"
        LIMIT="${5:-0}"

        if [ ! -f "$GOLDEN_FILE" ]; then
            err "Golden file not found: $GOLDEN_FILE"
            exit 1
        fi

        info "Golden eval: tenant=$TENANT_ID file=$GOLDEN_FILE"
        info "Models: $MODELS, Limit: ${LIMIT:-all}"

        if [ "$LIMIT" != "0" ] && [ -n "$LIMIT" ]; then
            "$VENV_PYTHON" "$SCRIPT_DIR/deepeval_eval.py" \
                --tenant-id "$TENANT_ID" \
                --golden-file "$GOLDEN_FILE" \
                --models "$MODELS" \
                --limit "$LIMIT" \
                --concurrency 3 \
                --output-dir "$SCRIPT_DIR/results/deepeval/"
        else
            "$VENV_PYTHON" "$SCRIPT_DIR/deepeval_eval.py" \
                --tenant-id "$TENANT_ID" \
                --golden-file "$GOLDEN_FILE" \
                --models "$MODELS" \
                --concurrency 3 \
                --output-dir "$SCRIPT_DIR/results/deepeval/"
        fi
        ok "Golden eval done. Check eval/results/deepeval/"
        ;;

    compare)
        # 两租户对比
        TENANT_ID="${2:?Usage: $0 compare <tenant-id-1> <tenant-id-2> [models] [limit]}"
        TENANT_ID_2="${3:?}"
        LABEL1="${4:-$TENANT_ID}"
        LABEL2="${5:-$TENANT_ID_2}"
        MODELS="${6:-all}"
        LIMIT="${7:-0}"

        info "Tenant comparison: $LABEL1 vs $LABEL2"
        info "  $LABEL1: tenant=$TENANT_ID"
        info "  $LABEL2: tenant=$TENANT_ID_2"

        if [ "$LIMIT" != "0" ] && [ -n "$LIMIT" ]; then
            "$VENV_PYTHON" "$SCRIPT_DIR/deepeval_eval.py" \
                --tenant-id "$TENANT_ID" --label "$LABEL1" \
                --tenant-id-2 "$TENANT_ID_2" --label-2 "$LABEL2" \
                --models "$MODELS" \
                --limit "$LIMIT" \
                --concurrency 3 \
                --output-dir "$SCRIPT_DIR/results/deepeval/"
        else
            "$VENV_PYTHON" "$SCRIPT_DIR/deepeval_eval.py" \
                --tenant-id "$TENANT_ID" --label "$LABEL1" \
                --tenant-id-2 "$TENANT_ID_2" --label-2 "$LABEL2" \
                --models "$MODELS" \
                --concurrency 3 \
                --output-dir "$SCRIPT_DIR/results/deepeval/"
        fi
        ok "Comparison done. Check eval/results/deepeval/"
        ;;

    help|--help|-h)
        echo "Usage: $0 <command> [args...]"
        echo ""
        echo "Commands:"
        echo "  smoke [tenant-id] [limit]"
        echo "      Quick smoke test (default: 5 questions, tenant=taishan)"
        echo ""
        echo "  eval <tenant-id> [models] [limit]"
        echo "      Full single-tenant evaluation"
        echo "      Example: $0 eval taishan qwen-plus 10"
        echo ""
        echo "  risk [tenant-id]"
        echo "      Evaluate risk detection (block/miss/false-alarm)"
        echo "      Example: $0 risk taishan"
        echo ""
        echo "  golden <tenant-id> <golden-file> [models] [limit]"
        echo "      Evaluate using Synthesizer-generated golden file"
        echo "      Supports .json / .csv / .md format"
        echo "      Example: $0 golden taishan eval/datasets/taishankaifa2/goldens.md"
        echo ""
        echo "  compare <tenant-id-1> <tenant-id-2> [label1] [label2] [models] [limit]"
        echo "      Compare two tenants"
        echo "      Example: $0 compare taishan taishankaifa2 泰山 泰山开发"
        echo ""
        echo "Prerequisites:"
        echo "  - .env file with DATABASE_URL, MODEL_* vars"
        echo "  - OPENAI_API_KEY (or DEEPEVAL_MODEL) for judge LLM"
        echo "  - DeepEval installed in .venv"
        echo "  - DB accessible (same DB as running Agent)"
        ;;
    *)
        err "Unknown command: $MODE"
        echo "Run '$0 help' for usage."
        exit 1
        ;;
esac
