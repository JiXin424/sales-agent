#!/bin/sh
set -e
# 用 envsubst 将 nginx 模板里的 ${BACKEND_HOST} 替换为实际值
envsubst '${BACKEND_HOST}' < /etc/nginx/nginx.conf.template > /etc/nginx/conf.d/default.conf
exec "$@"
