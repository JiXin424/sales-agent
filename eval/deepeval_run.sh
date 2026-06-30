#!/bin/bash
# ──────────────────────────────────────────────────────────────────
# Sales Agent + DeepEval 一键评估脚本
#
# 用法:
#   # 1. 快速验证（5 题，单实例）
#   bash eval/deepeval_run.sh smoke
#
#   # 2. 单实例完整评估（Agent 已在运行）
#   bash eval/deepeval_run.sh eval http://localhost:8000 my_kb
#
#   # 3. KB 对比评估（两个实例都在运行）
#   bash eval/deepeval_run.sh compare http://localhost:8001 legacy_rag http://localhost:8002 ontology_neo4j
#
#   # 4. 自定义参数
#   bash eval/deepeval_run.sh eval http://localhost:8000 taishan "" "qwen-plus" 10
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
        APP_URL="${2:-http://localhost:8000}"
        LIMIT="${3:-5}"

        info "Smoke test: $LIMIT questions against $APP_URL"
        "$VENV_PYTHON" "$SCRIPT_DIR/deepeval_eval.py" \
            --app-url "$APP_URL" \
            --limit "$LIMIT" \
            --concurrency 1 \
            --output-dir "$SCRIPT_DIR/results/deepeval/"
        ok "Smoke test done. Check eval/results/deepeval/"
        ;;

    eval)
        # 单实例完整评估
        APP_URL="${2:?Usage: $0 eval <app-url> <label> [models] [limit]}"
        LABEL="${3:-eval}"
        MODELS="${4:-all}"
        LIMIT="${5:-0}"

        info "Single-instance eval: $LABEL @ $APP_URL"
        info "Models: $MODELS, Limit: ${LIMIT:-all}"

        if [ "$LIMIT" != "0" ] && [ -n "$LIMIT" ]; then
            "$VENV_PYTHON" "$SCRIPT_DIR/deepeval_eval.py" \
                --app-url "$APP_URL" \
                --models "$MODELS" \
                --limit "$LIMIT" \
                --concurrency 3 \
                --output-dir "$SCRIPT_DIR/results/deepeval/"
        else
            "$VENV_PYTHON" "$SCRIPT_DIR/deepeval_eval.py" \
                --app-url "$APP_URL" \
                --models "$MODELS" \
                --concurrency 3 \
                --output-dir "$SCRIPT_DIR/results/deepeval/"
        fi
        ok "Eval done. Check eval/results/deepeval/"
        ;;

    compare)
        # KB 对比评估
        # 用法: $0 compare <url1> <label1> <url2> <label2> [models] [limit] [tenant1] [tenant2]
        LEGACY_URL="${2:?Usage: $0 compare <url1> <label1> <url2> <label2> [models] [limit] [tenant1] [tenant2]}"
        LEGACY_LABEL="${3:-kb1}"
        ONTOLOGY_URL="${4:?}"
        ONTOLOGY_LABEL="${5:-kb2}"
        MODELS="${6:-all}"
        LIMIT="${7:-0}"
        TENANT1="${8:-taishan}"
        TENANT2="${9:-taishankaifa2}"

        info "KB comparison: $LEGACY_LABEL vs $ONTOLOGY_LABEL"
        info "  $LEGACY_LABEL: $LEGACY_URL (tenant=$TENANT1)"
        info "  $ONTOLOGY_LABEL: $ONTOLOGY_URL (tenant=$TENANT2)"

        if [ "$LIMIT" != "0" ] && [ -n "$LIMIT" ]; then
            "$VENV_PYTHON" "$SCRIPT_DIR/deepeval_eval.py" \
                --app-url-legacy "$LEGACY_URL" --label-legacy "$LEGACY_LABEL" \
                --tenant-id-legacy "$TENANT1" \
                --app-url-ontology "$ONTOLOGY_URL" --label-ontology "$ONTOLOGY_LABEL" \
                --tenant-id-ontology "$TENANT2" \
                --models "$MODELS" \
                --limit "$LIMIT" \
                --concurrency 3 \
                --output-dir "$SCRIPT_DIR/results/deepeval/"
        else
            "$VENV_PYTHON" "$SCRIPT_DIR/deepeval_eval.py" \
                --app-url-legacy "$LEGACY_URL" --label-legacy "$LEGACY_LABEL" \
                --tenant-id-legacy "$TENANT1" \
                --app-url-ontology "$ONTOLOGY_URL" --label-ontology "$ONTOLOGY_LABEL" \
                --tenant-id-ontology "$TENANT2" \
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
        echo "  smoke [app-url] [limit]"
        echo "      Quick smoke test (default: 5 questions, http://localhost:8000)"
        echo ""
        echo "  eval <app-url> <label> [models] [limit]"
        echo "      Full single-instance evaluation"
        echo "      Example: $0 eval http://localhost:8000 my_kb qwen-plus"
        echo ""
        echo "  compare <legacy-url> <legacy-label> <ontology-url> <ontology-label> [models] [limit]"
        echo "      Compare two KB instances"
        echo "      Example: $0 compare http://localhost:8001 legacy_rag http://localhost:8002 ontology_neo4j"
        echo ""
        echo "Prerequisites:"
        echo "  - OPENAI_API_KEY (or compatible) set in environment"
        echo "  - Agent instance(s) already running"
        echo "  - DeepEval installed in .venv"
        ;;
    *)
        err "Unknown command: $MODE"
        echo "Run '$0 help' for usage."
        exit 1
        ;;
esac
