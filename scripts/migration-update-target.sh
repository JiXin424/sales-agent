#!/usr/bin/env bash
# ============================================================
# Sales Agent — CI/CD 迁移: 目标服务器更新脚本
# ============================================================
# 在 47.120.50.181(dev) 和 47.118.16.235(hangzhou) 上以 root 执行。
#
# 功能：
#   1. 更新 /etc/hosts → registry.internal 指向 benji (新主控)
#   2. 部署新 registry CA 证书
#   3. docker login 新 registry
#   4. (仅 47.120.50.181) 更新 git remote 指向 benji Gitea
#
# 用法:
#   # 在 47.120.50.181 上（同 VPC，用私网 + dev 模式）:
#   bash scripts/migration-update-target.sh --target heyuan
#
#   # 在 47.118.16.235 上（跨地域，用公网）:
#   bash scripts/migration-update-target.sh --target hangzhou
#
# 前置条件:
#   1. 新主控 (47.120.55.219) 的 bootstrap 已完成
#   2. 本机能 SSH 免密到新主控（用于 scp registry-ca.crt）
#      - 或手动将 registry-ca.crt 复制到本机
# ============================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ============================================================
# 参数解析
# ============================================================
TARGET=""
SKIP_SSH=0

while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="${2:-}"; shift ;;
    --skip-ssh) SKIP_SSH=1 ;;
    --help|-h)
      echo "Usage: $0 --target <heyuan|hangzhou> [--skip-ssh]"
      exit 0
      ;;
    *) err "未知参数: $1" ;;
  esac
  shift
done

if [ -z "$TARGET" ]; then
  # 尝试自动检测
  MY_IP=$(hostname -I 2>/dev/null | grep -oE '47\.120\.50\.181' || echo "")
  if [ -n "$MY_IP" ]; then
    TARGET="heyuan"
    log "自动检测到 heyuan (47.120.50.181)"
  else
    MY_IP=$(hostname -I 2>/dev/null | grep -oE '47\.118\.16\.235' || echo "")
    if [ -n "$MY_IP" ]; then
      TARGET="hangzhou"
      log "自动检测到 hangzhou (47.118.16.235)"
    fi
  fi
fi

if [ -z "$TARGET" ]; then
  err "无法自动检测目标，请用 --target <heyuan|hangzhou> 指定"
fi

# ============================================================
# 配置（按目标不同）
# ============================================================
NEW_MASTER_PUBLIC_IP="47.120.55.219"
NEW_MASTER_PRIVATE_IP="172.25.186.210"
REGISTRY_HOSTNAME="registry.internal"
REGISTRY_PORT="5000"
REGISTRY_USER="salesagent"

case "$TARGET" in
  heyuan|dev)
    TARGET_LABEL="heyuan (47.120.50.181)"
    REGISTRY_ROUTE="${NEW_MASTER_PRIVATE_IP}"     # 同 VPC 走私网
    UPDATE_GIT_REMOTE=1                           # dev 机需要改 git remote
    GIT_REMOTE_URL="http://${NEW_MASTER_PUBLIC_IP}:3002/gitea-admin/sales-agent.git"
    ;;
  hangzhou)
    TARGET_LABEL="hangzhou (47.118.16.235)"
    REGISTRY_ROUTE="${NEW_MASTER_PUBLIC_IP}"     # 跨地域走公网
    UPDATE_GIT_REMOTE=0                          # 杭州没有源码，不需改 git
    ;;
  *)
    err "未知目标: $TARGET (支持: heyuan, hangzhou)"
    ;;
esac

log "目标: ${TARGET_LABEL}"
log "Registry 路由: ${REGISTRY_HOSTNAME} → ${REGISTRY_ROUTE}"

# ============================================================
# 1. 更新 /etc/hosts
# ============================================================
log "=== 1/5 更新 /etc/hosts ==="

if grep -q "${REGISTRY_HOSTNAME}" /etc/hosts; then
  # 替换现有条目
  sed -i "s/.*${REGISTRY_HOSTNAME}.*/${REGISTRY_ROUTE} ${REGISTRY_HOSTNAME}  # → benji 新主控 (${TARGET})/" /etc/hosts
  log "已更新 /etc/hosts: ${REGISTRY_HOSTNAME} → ${REGISTRY_ROUTE}"
else
  echo "${REGISTRY_ROUTE} ${REGISTRY_HOSTNAME}  # → benji 新主控 (${TARGET})" >> /etc/hosts
  log "已添加 /etc/hosts: ${REGISTRY_HOSTNAME} → ${REGISTRY_ROUTE}"
fi

# ============================================================
# 2. 部署 Registry CA 证书
# ============================================================
log "=== 2/5 部署 Registry CA 证书 ==="

DOCKER_CERT_DIR="/etc/docker/certs.d/${REGISTRY_HOSTNAME}:${REGISTRY_PORT}"
mkdir -p "$DOCKER_CERT_DIR"

# 尝试从新主控复制 CA
if [ "$SKIP_SSH" -ne 1 ] && ssh -o BatchMode=yes -o ConnectTimeout=5 "root@${NEW_MASTER_PRIVATE_IP}" 'echo ok' 2>/dev/null; then
  log "从新主控 scp registry-ca.crt..."
  scp -o BatchMode=yes "root@${NEW_MASTER_PRIVATE_IP}:/root/code/sales-agent/infra/registry-ca.crt" "${DOCKER_CERT_DIR}/ca.crt" 2>/dev/null || {
    warn "scp 失败，尝试从公网 IP..."
    scp -o BatchMode=yes "root@${NEW_MASTER_PUBLIC_IP}:/root/code/sales-agent/infra/registry-ca.crt" "${DOCKER_CERT_DIR}/ca.crt" 2>/dev/null || {
      warn "无法自动获取 CA 证书"
      warn "请手动将 registry-ca.crt 复制到 ${DOCKER_CERT_DIR}/ca.crt"
    }
  }
else
  if [ -f "${DOCKER_CERT_DIR}/ca.crt" ]; then
    log "CA 证书已存在: ${DOCKER_CERT_DIR}/ca.crt"
  else
    warn "无法 SSH 到新主控且本地无 CA 证书"
    warn "请手动从新主控 ${NEW_MASTER_PUBLIC_IP}:/root/code/sales-agent/infra/registry-ca.crt 复制"
    warn "目标路径: ${DOCKER_CERT_DIR}/ca.crt"
  fi
fi

# 验证证书
if [ -f "${DOCKER_CERT_DIR}/ca.crt" ]; then
  log "CA 证书已部署: $(openssl x509 -in ${DOCKER_CERT_DIR}/ca.crt -noout -subject 2>/dev/null || echo '验证失败')"
fi

# ============================================================
# 3. Docker login 新 Registry
# ============================================================
log "=== 3/5 Docker login 新 Registry ==="

# 需要 registry 密码（从新主控获取或手动输入）
REGISTRY_PASS=""
if [ "$SKIP_SSH" -ne 1 ] && [ -n "${NEW_MASTER_PRIVATE_IP:-}" ] 2>/dev/null; then
  REGISTRY_PASS=$(ssh -o BatchMode=yes "root@${NEW_MASTER_PRIVATE_IP}" 'cat /root/code/sales-agent/infra/registry-password.txt' 2>/dev/null || echo "")
fi

if [ -z "$REGISTRY_PASS" ]; then
  warn "无法自动获取 registry 密码"
  echo -n "请输入 registry 用户 ${REGISTRY_USER} 的密码: "
  read -r REGISTRY_PASS
fi

if [ -n "$REGISTRY_PASS" ]; then
  echo "$REGISTRY_PASS" | docker login "${REGISTRY_HOSTNAME}:${REGISTRY_PORT}" -u "$REGISTRY_USER" --password-stdin 2>/dev/null && \
    log "Docker 登录成功: ${REGISTRY_HOSTNAME}:${REGISTRY_PORT}" || \
    warn "Docker 登录失败，请检查网络/TLS/password"
else
  warn "缺少 registry 密码，跳过 docker login"
fi

# ============================================================
# 4. (仅 dev 机) 更新 Git remote
# ============================================================
if [ "$UPDATE_GIT_REMOTE" -eq 1 ]; then
  log "=== 4/5 更新 Git remote ==="

  SALES_DIR="/root/code/sales-agent"
  if [ -d "${SALES_DIR}/.git" ]; then
    cd "$SALES_DIR"

    # 保存当前 remote（保留为 backup）
    OLD_ORIGIN=$(git remote get-url origin 2>/dev/null || echo "")
    if [ -n "$OLD_ORIGIN" ]; then
      git remote remove origin-old 2>/dev/null || true
      git remote add origin-old "$OLD_ORIGIN" 2>/dev/null || true
      log "旧 origin 保存为 origin-old: ${OLD_ORIGIN}"
    fi

    # 设置新 origin → benji Gitea
    git remote remove origin 2>/dev/null || true
    git remote add origin "$GIT_REMOTE_URL"
    log "新 Git remote (push 到 benji 触发 CI):"
    log "  origin: ${GIT_REMOTE_URL}"
    log ""
    log "  开发流程:"
    log "    git add -A && git commit -m '...' && git push origin main"
    log "  观察 CI: http://${NEW_MASTER_PUBLIC_IP}:3002/gitea-admin/sales-agent/actions"
  else
    warn "未找到 ${SALES_DIR}/.git，跳过 git remote 更新"
  fi
else
  log "=== 4/5 (跳过 — 此目标无需更新 git remote) ==="
fi

# ============================================================
# 5. 验证
# ============================================================
log "=== 5/5 验证 ==="

# 检查 hosts 解析
RESOLVED=$(getent hosts "${REGISTRY_HOSTNAME}" 2>/dev/null | awk '{print $1}' || echo "")
if [ "$RESOLVED" = "$REGISTRY_ROUTE" ]; then
  log "✅ ${REGISTRY_HOSTNAME} 解析正确 → ${REGISTRY_ROUTE}"
else
  warn "⚠️  ${REGISTRY_HOSTNAME} 解析为 ${RESOLVED}, 期望 ${REGISTRY_ROUTE}"
fi

# 检查 git remote
if [ "$UPDATE_GIT_REMOTE" -eq 1 ]; then
  CURRENT_ORIGIN=$(git -C /root/code/sales-agent remote get-url origin 2>/dev/null || echo "N/A")
  log "Git remote: ${CURRENT_ORIGIN}"
fi

# 检查 docker 连接
if docker pull "${REGISTRY_HOSTNAME}:${REGISTRY_PORT}/sales-agent:latest" 2>&1 | tail -1; then
  log "✅ Registry 可访问，镜像拉取成功"
else
  warn "⚠️  首次拉取可能失败（新 registry 还没有 latest 镜像），首次 CI 推送后重试"
fi

log ""
log "============================================================"
log "  ✅ ${TARGET_LABEL} 迁移配置完成"
log "============================================================"
log ""
log "验证方法："
log "  curl -sk -u ${REGISTRY_USER}:<pass> https://${REGISTRY_HOSTNAME}:${REGISTRY_PORT}/v2/sales-agent/tags/list"

if [ "$UPDATE_GIT_REMOTE" -eq 1 ]; then
  log ""
  log "⏳ 等待新主控首次 CI 完成后，运行:"
  log "  scripts/deploy-release.sh   # 拉取新镜像并部署"
fi
