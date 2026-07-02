# Design — eval 全机可用

## 决策快照

| 维度 | 决策 |
|---|---|
| 题库进镜像 | `.dockerignore` 加 `!eval/questions.md` 等**反向例外**，不动现有 `*.md` 与锚定规则 |
| 无源码机怎么跑 | host 只放一个**薄壳 `scripts/run-eval.sh`**，`docker exec` 进 `<tenant>-api` 容器跑（依赖/`eval/`/env 现成） |
| 分发薄壳 | 复用 ci-fanout `image-deploy` 的 tar 同步（现已在传 `stop-tenant.sh`/`check-tenant.sh`） |
| 裁判模型 key | 容器已有 `DATABASE_URL`/`MODEL_*`；仅裁判 `OPENAI_API_KEY` 等经 `secrets/eval.env` + `docker exec -e` 注入 |
| 检索 eval 路径 bug | 改默认 `--ground-truth` 指向 `eval/ground_truth_30q.json` |

## 关键事实（含文件:行）

- `Dockerfile:55` `COPY eval/ ./eval/` —— eval 脚本已在镜像；`Dockerfile:54` `COPY scripts/ ./scripts/`。
- `.dockerignore:31` `*.md`（裸名递归）→ `eval/questions.md`、`eval/README_DEEPEVAL.md` 被排除。
- `.dockerignore:24-30` 锚定规则（`/coach_mode.png` 等）——**lessons #11**：前导 `/` 锚定根，裸名会递归误删 `src/.../static/cocah.html`。本次只**追加反向例外**，绝不改既有锚定行。
- `eval/run_conversation_eval.py:598` `--app-url` required；`:607` `--questions` 默认 `eval/questions.md`（`:641`），缺失则回退 `ground_truth_30q.json`。
- `scripts/run_retrieval_eval.py:412-413` 默认 `--ground-truth = scripts/ground_truth_30q.json`（错，文件在 `eval/`）。
- 租户 api 容器名规律：`sales-agent-<tenant>-api`（render 产物，实测 `docker ps` 可见）。

## 改动 1（已撤回）：`.dockerignore` 不需要改

实现期实测推翻了「缺口 1」前提：`.dockerignore` 的 `*.md` **只匹配根级 `.md`**（filepath.Match 的 `*` 不跨 `/`），**不排除嵌套的 `eval/questions.md`**。lessons #11 说的「裸名递归」只适用于无通配符的裸名（如 `cocah.html`），不适用于 `*.md`。

验证（fresh 最小 build，用原始 `.dockerignore`）：`/app/eval/questions.md` 存在（11958 字节）；运行中的 taishan-api 容器里也有。故 `eval/questions.md` 本就在镜像里，**无需改 `.dockerignore`**（已 git checkout 还原）。`eval/README_DEEDEVAL.md` 同理在镜像里。

> 教训记入 lessons：别凭「调研 agent 说被排除」就改 `.dockerignore`，先最小 build 实测。

## 改动 2：`scripts/run-eval.sh`（新增薄壳）

用法：

```
run-eval.sh <tenant> <conversation|deepeval|retrieval> [-- judge-args...]
选项: --app-url URL | --env-file secrets/eval.env | --results-dir ./eval-results
```

核心逻辑（伪代码）：

```bash
TENANT="$1"; KIND="$2"; shift 2
CTR="sales-agent-${TENANT}-api"
docker ps --format '{{.Names}}' | grep -qx "$CTR" || { echo "容器 $CTR 未运行"; exit 1; }

# 容器已自带 DATABASE_URL / MODEL_* (来自 tenant env_file)；
# 仅裁判 key 等额外 env 从 --env-file 读出，经 docker exec -e 透传。
EXTRA_ENV=()
if [[ -n "$ENV_FILE" ]]; then
  while IFS='=' read -r k v; do
    [[ -z "$k" || "$k" =~ ^# ]] && continue
    EXTRA_ENV+=( -e "$k=$v" )
  done < "$ENV_FILE"
fi

case "$KIND" in
  conversation)
    docker exec -i "${EXTRA_ENV[@]}" "$CTR" \
      bash -lc "cd /app && python eval/run_conversation_eval.py --app-url ${APP_URL} $*" ;;
  deepeval)
    docker exec -i "${EXTRA_ENV[@]}" "$CTR" \
      bash -lc "cd /app && python eval/deepeval_eval.py $*" ;;
  retrieval)
    docker exec -i "${EXTRA_ENV[@]}" "$CTR" \
      bash -lc "cd /app && python scripts/run_retrieval_eval.py -t ${TENANT} -m hybrid -r local $*" ;;
esac

# 结果拷回 host
docker cp "$CTR:/app/eval/results/." "${RESULTS_DIR:-./eval-results}/" 2>/dev/null || true
```

- 实现期改用 `docker exec -w /app`（设工作目录）+ 数组形式传参，**不用 `bash -lc` 字符串**，避免注入与引号问题。PYTHONPATH 已在容器 ENV。
- **conversation eval 的 `--app-url` 默认 `http://127.0.0.1:8000`（容器视角）**：eval 在 `<tenant>-api` 容器内跑，容器内 app 监听 8000；宿主机映射端口（如 8003）容器内访问不到。实测冒烟时传了宿主机端口导致 `ConnectError`，遂改为默认容器内地址。
- **解析 bug 修复**：初版 `--` 分支用内层 `while` 耗尽参数后，外层 `shift` 对空参数返回非零 → `set -e` 提前退出（在 echo 前，故无输出）。改为 `--) shift; break` + 分离 post-dash 参数。

## 改动 3：`scripts/run_retrieval_eval.py` 默认路径修正

`run_retrieval_eval.py:412-413`：

```python
# before:  default=str(Path(__file__).resolve().parent / "ground_truth_30q.json")  # = scripts/，错
# after:
default=str(Path(__file__).resolve().parents[1] / "eval" / "ground_truth_30q.json")  # = eval/，对
```

- 容器内 `/app/scripts/run_retrieval_eval.py` → `parents[1]` = `/app` → `/app/eval/ground_truth_30q.json` ✓。
- 本地仓库根同理（`scripts/` 的 parent = repo root → `eval/ground_truth_30q.json`）✓。

## 改动 4：ci-fanout tar 同步加 `run-eval.sh`

`ci-fanout.sh:79` 当前：

```bash
tar -C "${REPO_DIR}/scripts" -cf - stop-tenant.sh check-tenant.sh | ssh ...
```

改为：

```bash
tar -C "${REPO_DIR}/scripts" -cf - stop-tenant.sh check-tenant.sh run-eval.sh | ssh ...
```

并在远端 `chmod +x` 列表加 `run-eval.sh`（`:81`）。**只多传这一个薄壳**，不传整个 `eval/`（题库已走镜像）。

## 改动 5：eval env 模板 + 文档

新增 `deploy/eval.env.example`（gitignored 实例放各机 `secrets/eval.env`）：

```
# 裁判模型（DeepEval 用；run_conversation_eval 不需要）
OPENAI_API_KEY=
DEEPEVAL_MODEL=deepseek-chat
OPENAI_BASE_URL=https://api.deepseek.com
# 注：DATABASE_URL / MODEL_API_KEY / MODEL_BASE_URL / MODEL_CHAT_MODEL / MODEL_EMBEDDING_MODEL
# 已在租户 api 容器 env_file 里，docker exec 自动继承，无需在此重复。
```

文档：在 `eval/README_DEEPEVAL.md`（或 `docs/deploy/`）补「无源码机跑 eval」一节，指向 `run-eval.sh`。

## 数据流

```
[无源码机 host]
  run-eval.sh <tenant> conversation --app-url ...
    └─ docker exec sales-agent-<tenant>-api
         (容器内 /app: eval/ 全套 + 依赖 + DATABASE_URL/MODEL_* 已在)
         python eval/run_conversation_eval.py --app-url ...
    └─ docker cp /app/eval/results/. → host ./eval-results/
```

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| `.dockerignore` 反向例外没生效（顺序/写法） | build 后断言 `questions.md` 在镜像；加单测/冒烟 |
| 误删锚定文件（lessons #11） | 只追加 `!` 行，不动既有行；回归断言 `cocah.html` 在 |
| 容器内 `docker exec` 缺裁判 key | `--env-file secrets/eval.env` 透传；conversation eval 不需要 |
| 多 worker 容器名后缀 | 容器名固定 `sales-agent-<tenant>-api`（无 worker 后缀），grep 精确匹配 |
| 结果在容器内丢（容器重建） | `docker cp` 即时拷回 host |

## 实现期观察（非 C1 范围，仅记录）

- **taishan 租户 app 返回 HTTP 500 `"dictionary changed size during iteration"`**（run-eval.sh 冒烟时，eval 连上 app 后 `/agent/chat` 报此错）。这是 app 自身 bug（疑似当前 `feat/graph-debug-time-travel` 分支的图/checkpointer 在该租户数据上的问题），**不是 wrapper/eval 基建问题**——run-eval.sh 全链路正常（连上 app、记录错误、落盘、拷回）。属另一议题，不在 C1。
- `tests/unit/test_render_multitenant_deploy.py` 有 2 个 neo4j 端口断言**预存在失败**（stash C1 改动后仍失败）→ `render-multitenant-deploy.py` 的 neo4j 端口渲染有 bug，**C2 渲染 `compose-prod3.yml` 时需修**。

## 不改的东西

- app 镜像 entrypoint、alembic 行为、prompt 解析、eval 算法/指标。
- 不在 host 装 Python venv（坚持容器内跑）。
