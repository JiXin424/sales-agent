#!/bin/bash
# ============================================================
# Sales Agent — Let's Encrypt SSL 证书初始化
# ============================================================
# 首次部署运行此脚本申请 SSL 证书
#
# 前提：
#   1. 域名 DNS 已解析到本机 IP
#   2. 阿里云安全组已放行 80 和 443 端口
#   3. 80 端口无其他服务占用
#
# 用法：
#   bash scripts/init-letsencrypt.sh --email your@email.com
# ============================================================

set -e

DOMAIN="qiyelongxia.com.cn"
EMAIL=""
CERT_DIR="./nginx/certbot-etc"
WEBROOT="./nginx/certbot-webroot"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# 解析参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        --email)  EMAIL="$2"; shift 2 ;;
        --domain) DOMAIN="$2"; shift 2 ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

if [ -z "$EMAIL" ]; then
    echo "请提供邮箱用于 Let's Encrypt 通知（证书过期提醒）："
    echo "用法: bash $0 --email your@email.com"
    exit 1
fi

echo "============================================"
echo "  Let's Encrypt SSL 证书初始化"
echo "  域名: $DOMAIN"
echo "  邮箱: $EMAIL"
echo "============================================"

# 1. 停止可能占用 80 端口的服务
echo ""
echo "[1/5] 检查并释放 80 端口..."
if command -v systemctl &>/dev/null; then
    sudo systemctl stop nginx 2>/dev/null || true
fi
docker rm -f sales-agent-nginx 2>/dev/null || true

# 2. 创建必要目录
echo "[2/5] 创建证书目录..."
mkdir -p "$CERT_DIR"
mkdir -p "$WEBROOT"

# 3. 检查是否已有有效证书
if [ -f "$CERT_DIR/live/$DOMAIN/cert.pem" ]; then
    echo "[3/5] 检测到已有证书，验证有效期..."
    if openssl x509 -checkend 2592000 -noout -in "$CERT_DIR/live/$DOMAIN/cert.pem" 2>/dev/null; then
        echo "  ✅ 证书仍然有效（>30天），无需重新申请"
        echo ""
        echo "如需强制续期，运行："
        echo "  docker compose --profile nginx-setup run --rm certbot renew --force-renewal"
        exit 0
    fi
fi

echo "[3/5] 使用 certbot standalone 模式申请证书..."

# 4. 用 certbot Docker 容器的 standalone 模式申请证书
#    standalone 模式：certbot 自己启动临时 HTTP 服务器完成验证
docker compose --profile nginx-setup run --rm --no-deps \
    -p 80:80 \
    certbot certonly \
    --standalone \
    -d "$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    --non-interactive

echo ""
echo "[4/5] 设置证书目录权限..."
chmod -R 755 "$CERT_DIR" 2>/dev/null || sudo chmod -R 755 "$CERT_DIR"

# 5. 设置自动续期 cron
echo "[5/5] 设置自动续期（每月 1 号凌晨 3 点）..."
CRON_CMD="cd $PROJECT_DIR && docker compose --profile nginx-setup run --rm --no-deps certbot renew --quiet && docker compose exec nginx nginx -s reload"
CRON_LINE="0 3 1 * * $CRON_CMD"

if crontab -l 2>/dev/null | grep -q "certbot renew"; then
    echo "  已存在 certbot 续期 cron 条目，跳过"
else
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    echo "  ✅ 已添加 cron 条目"
fi

echo ""
echo "============================================"
echo "  ✅ SSL 证书初始化完成！"
echo "============================================"
echo ""
echo "现在可以启动服务："
echo "  docker compose --profile taishan up -d"
echo ""
echo "验证 HTTPS："
echo "  curl -I https://$DOMAIN/integrations/dingtalk/health"
