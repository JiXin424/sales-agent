# eval 全机可用: 脚本同步 + 运行入口 + 题库进镜像

> 父任务: `07-02-ci-topology-eval-everywhere`

## Goal

让每台服务器（重点是**无源码的 image-deploy 机**，如 test）都能正常跑 eval：题库在镜像里、脚本可达、依赖齐、env 可配。本期只确保「能跑」，不做自动调度。

## Background（eval 调研结论）

- `eval/` 已 `COPY` 进 app 镜像（`Dockerfile:55`），含 `run_conversation_eval.py`、`deepeval_*.py`、`merge_results.py`、`ground_truth_30q.json`、`questions.md` 等。
- ~~缺口 1~~（**已证伪**）：原以为 `.dockerignore` 的 `*.md` 排除了 `eval/questions.md`，实测 `*.md` 只匹配根级、不递归，`eval/questions.md` **本就在镜像里**（运行中的 taishan-api 容器含 11958 字节的它）。故无需改 `.dockerignore`。
- **缺口 2**：无源码机 host 上没有 `eval/`（sourceless 瘦身会删），用户没法在 host 直接 `python eval/...`；唯一带齐 Python 依赖（deepeval、langgraph 等）和 `eval/` 的是 **app 容器**。
- **缺口 3**：`scripts/run_retrieval_eval.py:413` 默认 `--ground-truth` 路径指 `scripts/ground_truth_30q.json`（错），实际在 `eval/ground_truth_30q.json`，必须显式 `-g`。
- DeepEval / 检索 eval 需 `DATABASE_URL` + `MODEL_API_KEY/BASE_URL/CHAT_MODEL/EMBEDDING_MODEL` + `OPENAI_API_KEY`（裁判，可 `DEEPEVAL_MODEL` + `OPENAI_BASE_URL` 转向非 OpenAI）。`run_conversation_eval.py` 仅需一个跑着的 app `--app-url`。
- `eval/merge_results.py` 已存在 →「各机分别跑、再合并结果」本就是设计过的用法。

## Requirements

- ~~**R1**~~（**撤销**）：原要改 `.dockerignore` 让 `eval/questions.md` 进镜像；实测它本就在镜像里，无需改。
- **R2** 提供一个**运行入口脚本**（`scripts/run-eval.sh`），让无源码机一条命令跑 eval：内部 `docker exec` 进运行中的 `<tenant>-api` 容器（依赖、`eval/`、env 现成），host 只需这一个薄壳脚本（沿用 ci-fanout tar 同步 `stop-tenant.sh` 的机制分发）。
- **R3** 修 `run_retrieval_eval.py` 默认 ground_truth 路径 bug（指向 `eval/` 而非 `scripts/`）。
- **R4** 提供每机 eval env 模板与清单（`DATABASE_URL` / `MODEL_*` / `OPENAI_API_KEY` / 可选 `DEEPEVAL_MODEL`、`OPENAI_BASE_URL`），说明在无源码机如何注入（`docker exec -e` 或容器 env_file）。
- **R5** ci-fanout `image-deploy` 分支同步运维脚本时，一并同步 `run-eval.sh`（仅这一个薄壳，不传整个 `eval/`）。
- **R6** 结果落盘可取：eval 结果默认写容器内 `/app/eval/results/`，`run-eval.sh` 支持把结果拷回 host（或挂载卷）。

## Constraints

- 不在 host 裸跑 eval（避免给每台无源码机配 Python venv + 全套依赖）。
- 凭证不进 git；eval env 各机本地维护（可放 `secrets/eval.env`，gitignored）。
- `run-eval.sh` 必须能识别目标租户容器名（`sales-agent-<tenant>-api`）。

## Out of Scope

- 自动 eval 调度（cron / 侧车容器 / API 触发）——留后续。
- 跨机结果自动汇总 pipeline（`merge_results.py` 已在，手动用即可）。
- 改 eval 算法/指标本身。

## Acceptance Criteria

- [x] app 镜像里 `/app/eval/questions.md` 存在且非空（实测：无需改 `.dockerignore`，本就在）。
- ~~`.dockerignore` 改动后 cocah.html 仍在~~（撤销：未改 `.dockerignore`）。
- [ ] test（无源码）host 上有 `run-eval.sh`，执行能跑通 `run_conversation_eval.py` 并产出结果文件。（prod2 本地已验证 wrapper 全链路；test 端到端待 Step 7 CI）
- [x] `run_retrieval_eval.py` 不带 `-g` 也能找到 `eval/ground_truth_30q.json`（默认路径已修正并验证）。
- [x] 提供了 eval env 模板（`deploy/eval.env.example`）+ 文档（`eval/README_DEEPEVAL.md` 场景 9），列出每机自备变量。
- [x] 冒烟脚本 `tests/unit/test_run_eval.sh` 覆盖 run-eval.sh 逻辑 + `run_retrieval_eval.py` 默认路径（`.dockerignore` 部分因撤销已不适用）。
- [ ] changelog 当日记一条；README 同步（Step 8 待做）。

## Notes

- 详细运行入口与 env 注入设计见 `design.md`；执行步骤见 `implement.md`。
