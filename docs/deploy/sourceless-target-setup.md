# 无源码目标机初始化手册

> 适用：test及未来**不能放源码**的部署目标机。设计见
> [`docs/superpowers/specs/2026-06-25-sourceless-deploy-design.md`](../superpowers/specs/2026-06-25-sourceless-deploy-design.md)。

无源码目标机**只保留**：docker + docker compose、registry 登录、CA 证书、`secrets/`。compose 与部署脚本由 CI 打进 `sales-agent-deploy` 镜像传输，目标机 `docker run` 它完成部署。

## 1. 首次初始化（一次性）

```bash
# 1. 装 docker + compose plugin（略）

# 2. 允许 HTTP registry（走 HTTP + htpasswd，无 TLS）+ 登录
#    在 /etc/docker/daemon.json 加 { "insecure-registries": ["registry.internal:5000"] }（已有则合并）
systemctl restart docker
docker login registry.internal:5000 -u salesagent   # 凭证见主控 infra/registry-password.txt

# 4. /etc/hosts 解析 registry.internal → 主控可达 IP
#   （跨地域走公网 IP；同 VPC 走私网。具体见 cicd-gitea.md §2）
echo "<主控IP> registry.internal" >> /etc/hosts

# 5. 创建 secrets（从 example 模板，本地填真实凭证，绝不进 git）
mkdir -p /root/code/sales-agent/secrets && cd /root/code/sales-agent/secrets
#   从主控或仓库拿模板：secrets/example.env、secrets/neo4j.env.example
#   复制为租户 env + neo4j.env：
cp example.env fuduoduo.env        # 改名成你的租户 id
$EDITOR fuduoduo.env               # 填 MODEL_API_KEY / DINGTALK / KNOWLEDGE_ENGINE=ontology_neo4j 等
cp neo4j.env.example neo4j.env
$EDITOR neo4j.env                  # 设 NEO4J_PASSWORD（三台一致）
chmod 600 *.env

# 6. 若该机用 traefik 反代网关：建共享网络（compose 里 shared_network 引用）
#   docker network create taishan-network   # 名字与 inventory 的 traefik.shared_network 一致
```

test跨地域拉镜像要主控**两层防火墙**放行 TCP:5000（阿里云安全组 + 主控 ufw），见 `cicd-gitea.md` §3。

## 2. 瘦身（删源码）

无源码目标机不需要 src/scripts/tests/docs/.git 等，只留 `secrets/`（+ `data/` `logs/` 持久卷目录）：

```bash
cd /root/code/sales-agent
# 先备份，验证部署 OK 再删
mv src src.bak
rm -rf scripts tests docs console eval config .git Dockerfile *.md pyproject.toml alembic.ini *.zip
# 保留：secrets/ data/ logs/
```

## 3. 日常部署（全自动，无需手动）

代码在 prod2 改、merge 进 main、`git push origin main` → prod3 Gitea 自动触发 CI：
1. build app 镜像 + **deploy 镜像**（含 `compose-<env>.yml` + `deploy-remote.sh`）→ push registry
2. fan-out 到本机：`docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v /root/code/sales-agent/secrets:/secrets:ro -e APP_IMAGE=... sales-agent-deploy:<sha> <env>`
3. deploy 镜像内：pull app 镜像 → retag → `compose up`（neo4j 密码从 `/secrets/neo4j.env` 插值）→ 等 api running

**你不用在目标机做任何事**，只要 secrets + registry 登录 + CA 在。

## 4. 验证

```bash
docker ps | grep -E 'fuduoduo-api|sales-agent-neo4j'   # app + neo4j 容器 running/healthy
docker exec sales-agent-fuduoduo-api python -c \
  "import urllib.request,json; print(json.dumps(json.loads(urllib.request.urlopen('http://localhost:8000/ready').read()),ensure_ascii=False))" \
  | grep -o '"neo4j_ready": *[a-z]*'                    # neo4j_ready:true
```

## 5. 回退 / 排错

- **回退到旧版本**：在 prod2 改 `deploy-targets.json` test的 deploy 镜像 tag（或 registry 里有历史 sha，ci-fanout `${IMAGE##*:}` 决定），重跑 CI。
- **secrets 缺失**：deploy 镜像启动即 exit（校验 `/secrets/neo4j.env`），本地补 `secrets/neo4j.env` 后重跑 CI。
- **compose 缺该 env**：`deploy-remote.sh` exit（`compose-<env>.yml` 不在镜像），说明 CI 没 render 该 env——检查 `deploy.yml` 的 `for env in test` 列表是否含你的 env。
- **跨 network 502**（traefik 网关）：compose 必须含 `shared_network` external 声明，见 `tasks/lessons.md` #10。
