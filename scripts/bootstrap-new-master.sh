#!/usr/bin/env bash
# ============================================================
# Sales Agent — 新主控 Bootstrap 脚本
# ============================================================
# 在 47.120.55.219 (prod3) 上以 root 执行，一键搭建：
#   Gitea (web :3002) + 私有 Docker Registry (:5000) + act_runner
#
# 前置条件：
#   1. Docker + docker compose plugin 已安装
#   2. 端口 3002 / 5000 未被占用
#   3. 防火墙放行 TCP :3002 :5000
#
# 用法：bash scripts/bootstrap-new-master.sh
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
# 0. 配置变量（按需修改）
# ============================================================
GITEA_DOMAIN="47.120.55.219"
GITEA_WEB_PORT="3002"
REGISTRY_PORT="5000"
REGISTRY_HOSTNAME="registry.internal"

# 网络地址（用于 TLS SAN）
BENJI_PUBLIC_IP="47.120.55.219"
BENJI_PRIVATE_IP="172.25.186.210"
LOOPBACK="127.0.0.1"

# Gitea 管理员
GITEA_ADMIN_USER="gitea-admin"
GITEA_ADMIN_EMAIL="admin@sales-agent.local"
GITEA_ADMIN_PASS=$(openssl rand -base64 18 | tr -d '/+=' | head -c 16)
GITEA_REPO_OWNER="gitea-admin"
GITEA_REPO_NAME="sales-agent"

# Registry 用户
REGISTRY_USER="salesagent"
REGISTRY_PASS=$(openssl rand -base64 18 | tr -d '/+=' | head -c 16)

# Runner
ACT_RUNNER_VERSION="0.6.1"
ACT_RUNNER_LABELS="ubuntu-latest:host"

# 目录
SALES_AGENT_DIR="/root/code/sales-agent"
INFRA_DIR="${SALES_AGENT_DIR}/infra"
OLD_GITEA_URL="http://47.120.50.181:3002"

# ============================================================
# 1. 环境检查
# ============================================================
log "=== 1/10 环境检查 ==="

need_cmd() { command -v "$1" >/dev/null 2>&1 || err "需要 $1 但未安装"; }
need_cmd docker
need_cmd openssl
need_cmd curl
need_cmd htpasswd || { log "安装 apache2-utils (htpasswd)"; apt-get update -qq && apt-get install -y -qq apache2-utils; }

docker compose version >/dev/null 2>&1 || err "需要 docker compose plugin"

# 检查端口
for port in "$GITEA_WEB_PORT" "$REGISTRY_PORT"; do
  if ss -tlnp 2>/dev/null | grep -q ":${port} "; then
    err "端口 ${port} 已被占用，请先释放"
  fi
done

# 创建目录
mkdir -p "$INFRA_DIR"
mkdir -p "$SALES_AGENT_DIR"/{deploy,scripts,secrets,data,logs,config}

log "环境检查通过"

# ============================================================
# 2. 生成 TLS 证书
# ============================================================
log "=== 2/10 生成 TLS 证书 (SAN: registry.internal,$BENJI_PRIVATE_IP,$BENJI_PUBLIC_IP,$LOOPBACK) ==="

cd "$INFRA_DIR"

# 生成 CA 私钥 + 证书
openssl genrsa -out ca.key 4096 2>/dev/null
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
  -subj "/CN=Sales Agent Registry CA" \
  -out registry-ca.crt 2>/dev/null

# 生成 Registry 私钥
openssl genrsa -out domain.key 4096 2>/dev/null

# SAN 配置
cat > /tmp/registry-san.cnf <<EOF
[req]
default_bits = 4096
default_md = sha256
distinguished_name = req_distinguished_name
req_extensions = req_ext
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = ${REGISTRY_HOSTNAME}

[req_ext]
subjectAltName = @alt_names

[v3_req]
subjectAltName = @alt_names

[alt_names]
DNS.1 = ${REGISTRY_HOSTNAME}
IP.1 = ${BENJI_PRIVATE_IP}
IP.2 = ${BENJI_PUBLIC_IP}
IP.3 = ${LOOPBACK}
EOF

# 生成 CSR + 签发证书
openssl req -new -key domain.key -out domain.csr -config /tmp/registry-san.cnf 2>/dev/null
openssl x509 -req -in domain.csr -CA registry-ca.crt -CAkey ca.key \
  -CAcreateserial -out domain.crt -days 3650 \
  -extfile /tmp/registry-san.cnf -extensions v3_req 2>/dev/null

rm -f domain.csr /tmp/registry-san.cnf
log "TLS 证书生成完成"
log "  CA:       ${INFRA_DIR}/registry-ca.crt"
log "  Cert:     ${INFRA_DIR}/domain.crt"
log "  Key:      ${INFRA_DIR}/domain.key"

# ============================================================
# 3. 生成 Registry 认证
# ============================================================
log "=== 3/10 Registry htpasswd ==="

echo "$REGISTRY_PASS" | htpasswd -iBc "${INFRA_DIR}/htpasswd" "$REGISTRY_USER" 2>/dev/null
echo "$REGISTRY_PASS" > "${INFRA_DIR}/registry-password.txt"
chmod 600 "${INFRA_DIR}/registry-password.txt"

log "Registry 用户: ${REGISTRY_USER}"
log "Registry 密码: 已保存到 ${INFRA_DIR}/registry-password.txt"

# ============================================================
# 4. 生成 infra/cicd-compose.yml
# ============================================================
log "=== 4/10 写入 infra/cicd-compose.yml ==="

cat > "${INFRA_DIR}/cicd-compose.yml" <<COMPOSE
# CI/CD 基础设施 — 新主控 47.120.55.219
# Gitea (web :${GITEA_WEB_PORT}) + Registry (:${REGISTRY_PORT})
services:
  registry:
    image: registry:2
    container_name: sales-agent-registry
    restart: unless-stopped
    ports:
      - "0.0.0.0:${REGISTRY_PORT}:5000"
    environment:
      REGISTRY_AUTH: htpasswd
      REGISTRY_AUTH_HTPASSWD_REALM: Sales-Agent Registry
      REGISTRY_AUTH_HTPASSWD_PATH: /auth/htpasswd
      REGISTRY_HTTP_TLS_CERTIFICATE: /certs/domain.crt
      REGISTRY_HTTP_TLS_KEY: /certs/domain.key
    volumes:
      - ./htpasswd:/auth/htpasswd:ro
      - ./domain.crt:/certs/domain.crt:ro
      - ./domain.key:/certs/domain.key:ro
      - registry-data:/var/lib/registry

  gitea:
    image: gitea/gitea:1.24
    container_name: gitea
    restart: unless-stopped
    environment:
      USER_UID: "1000"
      USER_GID: "1000"
      GITEA__security__INSTALL_LOCK: "true"
      GITEA__server__DOMAIN: "${GITEA_DOMAIN}"
      GITEA__server__ROOT_URL: "http://${GITEA_DOMAIN}:${GITEA_WEB_PORT}/"
      GITEA__server__START_SSH_SERVER: "false"
      GITEA__server__DISABLE_SSH: "true"
      GITEA__actions__ENABLED: "true"
    ports:
      - "${GITEA_WEB_PORT}:3000"
    volumes:
      - gitea-data:/data
      - /etc/timezone:/etc/timezone:ro
      - /etc/localtime:/etc/localtime:ro

volumes:
  registry-data:
  gitea-data:
COMPOSE

log "cicd-compose.yml 写入完成"

# ============================================================
# 5. 启动 Gitea + Registry
# ============================================================
log "=== 5/10 启动 Gitea + Registry ==="

cd "$INFRA_DIR"
docker compose -f cicd-compose.yml up -d

log "等待 Gitea 就绪..."
for i in $(seq 1 30); do
  if curl -sSf "http://127.0.0.1:${GITEA_WEB_PORT}/api/v1/version" >/dev/null 2>&1; then
    log "Gitea 就绪 (${i}x2s)"
    break
  fi
  [ "$i" -eq 30 ] && err "Gitea 启动超时"
  sleep 2
done

# ============================================================
# 6. 创建 Gitea 管理员 + Token
# ============================================================
log "=== 6/10 创建 Gitea 管理员 ==="

docker exec -u git gitea gitea admin user create \
  --username "$GITEA_ADMIN_USER" \
  --password "$GITEA_ADMIN_PASS" \
  --email "$GITEA_ADMIN_EMAIL" \
  --admin \
  --must-change-password=false 2>/dev/null || log "admin 用户可能已存在，跳过创建"

# 生成 API token
GITEA_TOKEN=$(docker exec -u git gitea gitea admin user generate-access-token \
  -u "$GITEA_ADMIN_USER" \
  --scopes all \
  --raw 2>/dev/null)

echo "$GITEA_TOKEN" > "${INFRA_DIR}/gitea-admin-token.txt"
echo "$GITEA_ADMIN_PASS" > "${INFRA_DIR}/gitea-admin-password.txt"
chmod 600 "${INFRA_DIR}/gitea-admin-password.txt" "${INFRA_DIR}/gitea-admin-token.txt"

log "Gitea admin: ${GITEA_ADMIN_USER}"
log "Gitea token: ${INFRA_DIR}/gitea-admin-token.txt"
log "Gitea web:  http://${GITEA_DOMAIN}:${GITEA_WEB_PORT}/"

# ============================================================
# 7. 创建 Gitea 仓库
# ============================================================
log "=== 7/10 创建 Gitea 仓库 ==="

# 通过 API 创建仓库
curl -sS -X POST "http://127.0.0.1:${GITEA_WEB_PORT}/api/v1/user/repos" \
  -H "Authorization: token ${GITEA_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"${GITEA_REPO_NAME}\",\"private\":false,\"default_branch\":\"main\"}" 2>&1 | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    if 'id' in d:
        print(f'Repo created: {d[\"full_name\"]} ({d[\"html_url\"]})')
    else:
        print(f'Response: {json.dumps(d, indent=2)}')
except Exception as e:
    print(f'Parse error: {e}')
" || log "仓库可能已存在，继续..."

# ============================================================
# 8. 克隆源码到新主控
# ============================================================
log "=== 8/10 克隆源码 ==="

if [ -d "${SALES_AGENT_DIR}/.git" ]; then
  log "${SALES_AGENT_DIR} 已有 .git，执行 git pull"
  cd "$SALES_AGENT_DIR"
  git fetch origin 2>/dev/null || true
else
  # 从原 master Gitea 克隆
  log "从 ${OLD_GITEA_URL} 克隆源码..."
  git clone "${OLD_GITEA_URL}/${GITEA_REPO_OWNER}/${GITEA_REPO_NAME}.git" "$SALES_AGENT_DIR" 2>/dev/null || {
    warn "无法从旧 Gitea 克隆；尝试从当前目录复制..."
    warn "请确保源码已存在于 ${SALES_AGENT_DIR}，跳过克隆"
  }
fi

# 配置 git remote — push 到本地 Gitea (触发 CI)
cd "$SALES_AGENT_DIR"
git remote remove origin 2>/dev/null || true
git remote add origin "http://127.0.0.1:${GITEA_WEB_PORT}/${GITEA_REPO_OWNER}/${GITEA_REPO_NAME}.git"

# 设置 push URL（带 PAT 认证）
GITEA_PAT="$GITEA_TOKEN"
if [ -z "${GITEA_PAT}" ]; then
  GITEA_PAT=$(cat "${INFRA_DIR}/gitea-admin-token.txt" 2>/dev/null || echo "")
fi
if [ -n "${GITEA_PAT}" ]; then
  git remote set-url origin "http://${GITEA_ADMIN_USER}:${GITEA_PAT}@127.0.0.1:${GITEA_WEB_PORT}/${GITEA_REPO_OWNER}/${GITEA_REPO_NAME}.git"
fi

log "Git remote: $(git remote get-url origin)"

# ============================================================
# 9. 安装 act_runner + 注册到 Gitea
# ============================================================
log "=== 9/10 安装 act_runner v${ACT_RUNNER_VERSION} ==="

ARCH=$(uname -m)
case "$ARCH" in
  x86_64)  RUNNER_ARCH="amd64" ;;
  aarch64) RUNNER_ARCH="arm64" ;;
  *)       err "不支持的架构: $ARCH" ;;
esac

RUNNER_BIN="/usr/local/bin/act_runner"

if ! command -v act_runner >/dev/null 2>&1; then
  log "下载 act_runner v${ACT_RUNNER_VERSION} (${RUNNER_ARCH})..."
  RUNNER_URL="https://dl.gitea.com/act_runner/${ACT_RUNNER_VERSION}/act_runner-${ACT_RUNNER_VERSION}-linux-${RUNNER_ARCH}"
  curl -sSL "$RUNNER_URL" -o "$RUNNER_BIN" || {
    warn "从 dl.gitea.com 下载失败，尝试 GitHub..."
    curl -sSL "https://github.com/nektos/act/releases/download/v${ACT_RUNNER_VERSION}/act_Linux_${RUNNER_ARCH}" -o "$RUNNER_BIN" || \
    err "act_runner 下载失败，请手动安装"
  }
  chmod +x "$RUNNER_BIN"
  log "act_runner 安装完成: $($RUNNER_BIN --version 2>&1 | head -1)"
else
  log "act_runner 已安装: $(act_runner --version 2>&1 | head -1)"
fi

# 注册 runner 到 Gitea (获取 token)
RUNNER_TOKEN=$(curl -sS "http://127.0.0.1:${GITEA_WEB_PORT}/api/v1/admin/runners/registration-token" \
  -H "Authorization: token ${GITEA_TOKEN}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('token',''))" 2>/dev/null)

if [ -z "$RUNNER_TOKEN" ]; then
  # 备选：repo 级别 registration token
  RUNNER_TOKEN=$(curl -sS "http://127.0.0.1:${GITEA_WEB_PORT}/api/v1/repos/${GITEA_REPO_OWNER}/${GITEA_REPO_NAME}/actions/runners/registration-token" \
    -H "Authorization: token ${GITEA_TOKEN}" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null)
fi

if [ -z "$RUNNER_TOKEN" ]; then
  err "无法获取 runner registration token；检查 Gitea Actions 是否已启用"
fi

# 注册 runner
log "注册 act_runner 到 Gitea..."
act_runner register \
  --no-interactive \
  --instance "http://127.0.0.1:${GITEA_WEB_PORT}" \
  --token "$RUNNER_TOKEN" \
  --name "prod3-host-runner" \
  --labels "$ACT_RUNNER_LABELS" 2>/dev/null || {
    # 旧版语法兼容
    act_runner register \
      --instance "http://127.0.0.1:${GITEA_WEB_PORT}" \
      --token "$RUNNER_TOKEN" \
      --name "prod3-host-runner" \
      --labels "$ACT_RUNNER_LABELS" 2>/dev/null || \
    err "act_runner 注册失败"
  }

# 创建 systemd 服务
log "创建 gitea-runner systemd 服务..."

cat > /etc/systemd/system/gitea-runner.service <<SYSTEMD
[Unit]
Description=Gitea Act Runner (host mode, prod3 master)
After=network.target docker.service

[Service]
Type=simple
User=root
ExecStart=${RUNNER_BIN} daemon
Restart=on-failure
RestartSec=5
Environment="HOME=/root"

[Install]
WantedBy=multi-user.target
SYSTEMD

systemctl daemon-reload
systemctl enable gitea-runner.service
systemctl restart gitea-runner.service

sleep 2
if systemctl is-active --quiet gitea-runner.service; then
  log "gitea-runner 服务运行中"
else
  warn "gitea-runner 未成功启动，检查: systemctl status gitea-runner"
  systemctl status gitea-runner.service --no-pager || true
fi

# ============================================================
# 10. 配置 hosts + Docker 证书 + 首次推送
# ============================================================
log "=== 10/10 配置 hosts + Docker 证书 + 首次推送 ==="

# 10a: /etc/hosts — 本机 registry.internal 指向自己
if ! grep -q "registry.internal" /etc/hosts; then
  echo "${LOOPBACK} registry.internal  # prod3 主控 (本地)" >> /etc/hosts
  log "/etc/hosts 已添加: 127.0.0.1 registry.internal"
else
  # 更新现有条目
  sed -i "s/.*registry.internal.*/${LOOPBACK} registry.internal  # prod3 主控 (本地)/" /etc/hosts
  log "/etc/hosts 已更新 registry.internal → 127.0.0.1"
fi

# 10b: Docker TLS 证书（本机也需信任自己的 registry CA）
DOCKER_CERT_DIR="/etc/docker/certs.d/${REGISTRY_HOSTNAME}:${REGISTRY_PORT}"
mkdir -p "$DOCKER_CERT_DIR"
cp "${INFRA_DIR}/registry-ca.crt" "${DOCKER_CERT_DIR}/ca.crt"
log "Registry CA 已部署到 ${DOCKER_CERT_DIR}/ca.crt"

# 10c: docker login
log "登录 registry..."
echo "$REGISTRY_PASS" | docker login "${REGISTRY_HOSTNAME}:${REGISTRY_PORT}" -u "$REGISTRY_USER" --password-stdin 2>/dev/null || {
  warn "docker login 失败（可能是 Docker socket 权限问题），稍后手动执行"
}

# 10d: 确保 workflow 目录
mkdir -p "${SALES_AGENT_DIR}/.gitea/workflows"

# 10e: 拉取 pgvector 基础镜像并推入本地 registry（如果有 Docker socket）
if docker info >/dev/null 2>&1; then
  log "预拉取 pgvector 基础镜像..."
  docker pull registry.internal:5000/pgvector/pgvector:pg16 2>/dev/null && \
    log "pgvector 镜像可用" || \
    warn "pgvector 镜像暂不可用，首次 CI 可能需要从原 registry 获取"
fi

# ============================================================
# 完成
# ============================================================
log ""
log "============================================================"
log "  🎉 新主控 Bootstrap 完成！"
log "============================================================"
log ""
log "📍 新主控信息："
log "   Gitea Web:      http://${GITEA_DOMAIN}:${GITEA_WEB_PORT}/"
log "   Gitea Admin:    ${GITEA_ADMIN_USER}"
log "   Gitea Token:    ${INFRA_DIR}/gitea-admin-token.txt"
log "   Registry:        ${REGISTRY_HOSTNAME}:${REGISTRY_PORT}"
log "   Registry 用户:   ${REGISTRY_USER}"
log "   Registry 密码:   ${INFRA_DIR}/registry-password.txt"
log "   源码目录:         ${SALES_AGENT_DIR}"
log "   CI Actions:      http://${GITEA_DOMAIN}:${GITEA_WEB_PORT}/${GITEA_REPO_OWNER}/${GITEA_REPO_NAME}/actions"
log ""
log "📋 后续步骤："
log "   1. 在本机完成后续文件配置后 git push 触发首次 CI"
log "   2. 在 47.120.50.181 上运行 scripts/migration-update-target.sh"
log "   3. 在 47.118.16.235 上运行 scripts/migration-update-target.sh"
log "   4. 确认 registry CA 已分发到所有目标机"
log ""
log "⚠️  请保存 ${INFRA_DIR} 下的凭证文件（均已在 .gitignore 中）"
