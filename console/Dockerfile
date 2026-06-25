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
# nginx 独立前端模式：API 走 /api 前缀，由 nginx 代理到后端（见 nginx.conf.template）。
# .env.production 默认留空（供「后端镜像内嵌 SPA 同源」模式用）；此处用 ENV 覆盖为 /api，
# dotenv 不会覆盖已存在的 process.env，故该值在 npm run build 时生效。
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
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
