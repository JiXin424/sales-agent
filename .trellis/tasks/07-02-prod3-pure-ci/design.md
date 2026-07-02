# Design — prod3 去源码化部署（选项 A）

> 默认按 PRD「选项 A」：prod3 保留 songbai/taishanyanshi，部署改 image-deploy、去源码。若 review 选 B（迁出租户），rollout 段需替换为 DB 迁移计划。

## 决策快照

| 维度 | 决策 |
|---|---|
| prod3 租户去留 | **保留**（songbai/taishanyanshi 留在 prod3），仅改部署方式 |
| prod3 部署方式 | `deploy-release` → **`image-deploy`（local:true, env=prod3）** |
| compose 来源 | CI 渲染 `compose-prod3.yml` 烤进 deploy 镜像（镜像 `test` 做法） |
| ci-fanout local 分支 | **新增 image-deploy 支持**（现仅 remote 分支支持） |
| prod3 源码 | fan-out 不再 `git reset --hard`；保留**只读克隆**作参考（不删，降风险），后续可瘦身 |
| registry | 本就在 prod3(.210)；清理 .209 闲置副本 + 修注释 |
| prod2 fan-out | **建议改手动**（开发机不被自动 reset 打乱）；文档化，不强制 |

## 关键事实（含文件:行）

- `deploy-targets.json:4` prod3 = `{name:"prod3(主控)", host:"127.0.0.1", method:"deploy-release", local:true}`。
- `deploy.yml:67-80` 现仅 render `test`：`OVERRIDE_IMAGE=... python3 scripts/render-multitenant-deploy.py deploy/tenants.test.json --compose-out deploy/compose-test.yml`，随后 build deploy 镜像（`COPY compose-*.yml`）。
- `ci-fanout.sh:52-59` local 分支：`git stash; fetch; reset --hard` + `bash $script`（script 对 image-deploy 为空 → 当前不可用）。
- `ci-fanout.sh:69-86` remote image-deploy：tar 同步 ops 脚本 → `docker pull DEPLOY_IMG` → `docker run --rm -v docker.sock -v /root/.docker:ro -v $DIR:/workspace -e APP_IMAGE ... DEPLOY_IMG $env`。
- `deploy/tenants.test.json` 是已提交的 test inventory 模板；`deploy/tenants.json`（prod2 的）gitignored。
- prod3 上 REPO_DIR（runner ephemeral checkout，ci-fanout.sh 所在）≠ DIR（`/root/code/sales-agent` 持久克隆 + secrets）——local image-deploy 须挂 **DIR** 作 /workspace（secrets 在那）。

## 改动 1：`deploy/tenants.prod3.json`（新增，提交）

镜像 `tenants.test.json` 结构，内容 = prod3 现有 songbai + taishanyanshi（端口/域名/neo4j 等从 prod3 现 `tenants.json` 抄）。含 `_comment` 标明「prod3 (47.120.55.219) image-deploy 用，env_file 在目标机本地，CI render 用 --skip-validation」。traefik `shared_network` 按 prod3 现状填（lessons #10）。

> implement 时需 SSH prod3 取其现行 `tenants.json` 准确内容（端口/域名以线上为准）。

## 改动 2：`deploy.yml` render 循环加 prod3

`build-and-push` step 7（`:67-80`）改为循环渲染所有 image-deploy env：

```yaml
- name: Render per-env composes + build deploy image
  run: |
    set -e
    for env in test prod3; do
      OVERRIDE_IMAGE="${REGISTRY}/${IMAGE_NAME}:${SHA}" \
        python3 scripts/render-multitenant-deploy.py "deploy/tenants.${env}.json" \
          --skip-validation --compose-out "deploy/compose-${env}.yml"
    done
    docker build -t ${REGISTRY}/sales-agent-deploy:${SHA} -t ${REGISTRY}/sales-agent-deploy:latest \
      -f deploy/Dockerfile deploy/
    docker push ${REGISTRY}/sales-agent-deploy:${SHA}
```

- `deploy/Dockerfile:8` `COPY compose-*.yml` 会同时收入 `compose-test.yml` + `compose-prod3.yml`。

## 改动 3：ci-fanout local 分支支持 image-deploy

`ci-fanout.sh:52-59` local 分支扩展（伪代码）：

```bash
if [ "$local" = "True" ]; then
  if [ "$method" = "image-deploy" ]; then
    # 本地 image-deploy（prod3 runner 本机）
    DEPLOY_IMG="${REGISTRY_HOST:-registry.internal:5000}/sales-agent-deploy:${IMAGE##*:}"
    # 同步 ops 脚本到目标 dir（本地 cp，含 run-eval.sh，配合 C1）
    mkdir -p "${dir}/scripts"
    cp -f "${REPO_DIR}/scripts/stop-tenant.sh" "${REPO_DIR}/scripts/check-tenant.sh" \
          "${REPO_DIR}/scripts/run-eval.sh" "${dir}/scripts/" 2>/dev/null || true
    chmod +x "${dir}/scripts/"*.sh 2>/dev/null || true
    docker pull "${DEPLOY_IMG}" \
      && docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
           -v /root/.docker:/root/.docker:ro -v "${dir}:/workspace" \
           -e APP_IMAGE="${IMAGE}" "${DEPLOY_IMG}" "${env}" \
      || echo "⚠️ [$name] local image-deploy 失败" >&2
  elif [ "$has_source" = "True" ] && [ "$method" != "self-deploy" ]; then
    git -C "$REPO_DIR" stash 2>/dev/null
    git -C "$REPO_DIR" fetch origin main && git -C "$REPO_DIR" reset --hard origin/main
    REGISTRY_IMAGE="$IMAGE" FRONTEND_IMAGE="${FRONTEND_IMAGE:-}" bash "$script" $args
  fi
fi
```

- 关键：挂 `${dir}`（持久 /root/code/sales-agent，含 secrets）而非 `${REPO_DIR}`（ephemeral checkout）。
- 此改动同时让 C1 的 `run-eval.sh` 在 prod3 local 也被同步。

## 改动 4：`deploy-targets.json` prod3 切 image-deploy + 修注释

```json
{
  "_comment": "CI fan-out 目标清单。prod3=主控(CI)，业务租户走 image-deploy；prod2=开发机(本地源码)；test=无源码。",
  "registry": "registry.internal:5000/sales-agent",
  "targets": [
    { "name": "prod3(主控)", "host": "127.0.0.1", "user": "root", "dir": "/root/code/sales-agent", "method": "image-deploy", "local": true, "env": "prod3" },
    { "name": "prod2(开发)", "host": "172.25.186.209", "user": "root", "dir": "/root/code/sales-agent", "method": "deploy-release", "local": false },
    { "name": "test", "host": "47.118.16.235", "user": "root", "dir": "/root/code/sales-agent", "method": "image-deploy", "env": "test" }
  ]
}
```

## 改动 5：prod3 源码处理

- fan-out 不再 `git reset --hard /root/code/sales-agent`（改 image-deploy 后自然不再碰）。
- **保留** `/root/code/sales-agent` 作只读参考克隆（本期不删，降风险）；仅在文档标注「此克隆不再被部署维护，可能落后」。
- 真正瘦身（删 src/scripts/tests/docs/console/eval/config/.git 等，只留 secrets/ + data/ + logs/）列为**后续可选**，需单独验证 traefik/数据卷不受影响。

## 改动 6：registry/注释统一

- 确认 `.209` 上 `sales-agent-registry` 容器无引用（`docker ps` 看谁 push/pull 它；prod2 容器实际从 `registry.internal:5000`=.210 拉）→ `docker stop/rm sales-agent-registry` + 撤 `infra/cicd-compose.yml` 里本机副本相关注释。
- 文档（`docs/deploy/cicd-gitea.md` 等）写清：`registry.internal = prod3 私网 IP 172.25.186.210`。

## 改动 7（评估）：prod2 fan-out 改手动

- 现状：push → ci-fanout 对 prod2 `git stash; fetch; reset --hard`（`ci-fanout.sh:90`）。开发机 WIP 会被 stash（不丢，但乱）。
- **建议**：把 prod2 从 `deploy-targets.json` 摘掉（dev 自己 `bash scripts/deploy-release.sh --yes` 手动部署），或在 fanout 加 `manual: true` 字段跳过。
- 本期**只文档化建议**，是否执行由 review 定（避免与开发习惯冲突）。

## Rollout（低风险分步）

1. prod2 本地：build deploy 镜像（含 `compose-prod3.yml`）+ `bash -n` ci-fanout + render 冒烟（`compose-prod3.yml` 产出且含 songbai/taishanyanshi + shared_network）。
2. ci-fanout local image-deploy 分支：prod2 用临时容器模拟本地 image-deploy 冒烟（dummy deploy 镜像）。
3. 提交改动，push → CI build → **先观察 test 仍正常**（回归保护）。
4. 手动触发 fan-out 到 prod3（或等下次 push）：观察 songbai/taishanyanshi 经 image-deploy 重启、`/ready` 通、钉钉重连、控制台 + 时间旅行可用。
5. 稳定 24h 后再做改动 5/6 的源码瘦身与 registry 清理。

## Rollback

- prod3 target 改回 `deploy-release, local:true`（git 上一笔），fan-out 重新走源码部署——前提 prod3 `/root/code/sales-agent` 克隆还在（改动 5 保留了）。
- deploy 镜像按 sha 留历史，可指定旧 sha `sales-agent-deploy:<旧sha>` 回退（sourceless 设计 §8）。
- registry 清理（改动 6）可随时 `docker start` 恢复。

## 风险

| 风险 | 缓解 |
|---|---|
| prod3 image-deploy 把 songbai/taishanyanshi 搞挂 | 分步 rollout + 保留源码克隆回退 + 旧 sha deploy 镜像 |
| `tenants.prod3.json` 与线上端口/域名不一致 | implement 必从 prod3 现行 inventory 抄，不凭文档猜 |
| ci-fanout local 分支改动影响 prod3 既有 deploy-release | local 分支只在 `method==image-deploy` 走新路径，其它 method 不变 |
| traefik shared_network 漏配 → 502（lessons #10） | render 已支持，`tenants.prod3.json` 填对 |
| 删 .209 registry 误伤 | 先查引用再删；保留回退 |

## 不改的东西

- Gitea/runner/registry 进程、app 镜像 entrypoint/alembic、prompt 解析、租户数据。
