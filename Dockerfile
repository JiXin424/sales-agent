# ============================================================
# Sales Agent — Dockerfile (多阶段：后端 + 控制台前端)
# ============================================================
# 构建：docker build -t sales-agent:latest .
# 运行：docker compose --profile taishan up -d
# 一个镜像 = 后端 API + 控制台前端（每实例一个 Agent，端口即控制台）
# ============================================================

# ---------- Stage 1: 构建控制台前端 ----------
FROM node:20-alpine AS frontend
WORKDIR /console
# 先复制依赖描述以利用缓存
COPY console/package.json console/package-lock.json* ./
RUN npm config set registry https://registry.npmmirror.com \
    && npm ci || npm install
COPY console/ ./
# 生产构建（.env.production → 同源 base url）
RUN npm run build

# ---------- Stage 2: 后端运行时 ----------
FROM python:3.10-slim

WORKDIR /app

# 国内 apt 镜像源（阿里云，兼容 bullseye sources.list 格式）
RUN if [ -f /etc/apt/sources.list ]; then \
      sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org/debian-security|mirrors.aliyun.com/debian-security|g' /etc/apt/sources.list; \
    fi && \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
    fi && \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 缓存
COPY pyproject.toml ./
COPY src/ ./src/
COPY config/ ./config/
COPY scripts/ ./scripts/
COPY eval/ ./eval/

# 安装依赖（阿里云 PyPI 镜像）
RUN pip install --no-cache-dir --upgrade pip \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    && pip install --no-cache-dir -e . \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com

# 控制台前端构建产物（打进镜像，FastAPI 托管）
COPY --from=frontend /console/dist /app/console/dist

# 默认环境变量（可在 docker-compose env_file 中覆盖）
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
# 前端静态目录 + 托管开关（main.py 据此挂载 SPA）
ENV APP__CONSOLE_DIST_DIR=/app/console/dist

# 暴露端口
EXPOSE 8000

# 数据和日志目录
RUN mkdir -p /data /logs

# 入口脚本：根据 PROCESS_ROLE 选择启动命令
COPY scripts/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# 启动命令
ENTRYPOINT ["/app/docker-entrypoint.sh"]
