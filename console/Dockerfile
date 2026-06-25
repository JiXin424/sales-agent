# ============================================================
# Console 前端容器 — nginx serving SPA + API 代理
# ============================================================
# 构建：docker build -t sales-agent-frontend:latest -f console/Dockerfile console/
# 运行：docker run -e BACKEND_HOST=taishan-api -p 11003:80 sales-agent-frontend:latest
# 运行时 BACKEND_HOST 必填（nginx 用它代理 /api */ 到后端容器）。
# ============================================================

# Stage 1: 构建 React SPA
FROM node:20-alpine AS frontend
WORKDIR /console
COPY package.json package-lock.json* ./
RUN npm config set registry https://registry.npmmirror.com \
    && npm ci || npm install
COPY . ./
RUN npm run build

# Stage 2: nginx serve
FROM nginx:1.27-alpine
RUN apk add --no-cache gettext
COPY --from=frontend /console/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/nginx.conf.template
COPY docker-entrypoint.sh /
RUN chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
