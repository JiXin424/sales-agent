# Implement — eval 全机可用

> 执行顺序自上而下。每个 Gate 须绿再继续。改动遵循「最小影响」，每步给高层总结。

## 进度（实现期更新）

- ✅ Step 2/3/4/5/6 已完成并过 Gate（prod2 本地验证）。
- ❌ Step 1 **撤回**：实测 `eval/questions.md` 本就在镜像里（`.dockerignore` 的 `*.md` 只匹配根级），无需改。
- ⏳ Step 7（test 端到端）待 CI push。
- ⏳ Step 8（changelog/README/lessons）待收尾。

## Step 1 — `.dockerignore` 反向例外【已撤回，无需做】

实测推翻前提：`eval/questions.md` 本就在镜像里（`.dockerignore` 的 `*.md` 只匹配根级，不递归）。已 `git checkout` 还原。详见 design.md「改动 1（已撤回）」。

## Step 2 — 修 `run_retrieval_eval.py` 默认路径

- [ ] `scripts/run_retrieval_eval.py:412-413` 默认 `--ground-truth` 改为 `parents[1]/"eval"/"ground_truth_30q.json"`。
- **Gate 2**：
  ```bash
  python -c "from pathlib import Path; p=Path('scripts/run_retrieval_eval.py').resolve(); print((p.parents[1]/'eval'/'ground_truth_30q.json').exists())"  # True
  pytest tests/unit -k retrieval -q 2>/dev/null || true   # 若有相关测
  ```

## Step 3 — 新增 `scripts/run-eval.sh`

- [ ] 按 design.md 改动 2 实现；`chmod +x`。
- [ ] `bash -n scripts/run-eval.sh` 语法检查。
- **Gate 3**（prod2 本地实测，taishan 容器在跑）：
  ```bash
  scripts/run-eval.sh taishan conversation --app-url http://127.0.0.1:8010 2>&1 | tail
  ls eval-results/   # 应有结果文件
  ```
  能产出结果文件即过。

## Step 4 — ci-fanout tar 同步加 `run-eval.sh`

- [ ] `scripts/ci-fanout.sh:79` tar 列表加 `run-eval.sh`；`:81` chmod 列表加 `run-eval.sh`。
- **Gate 4**：`bash -n scripts/ci-fanout.sh`；人工 review diff 只增一行同步项。

## Step 5 — eval env 模板 + 文档

- [ ] 新增 `deploy/eval.env.example`（design.md 改动 5）。
- [ ] `eval/README_DEEPEVAL.md` 补「无源码机跑 eval」节（指向 `run-eval.sh` + 各机自备变量清单）。
- 注意：`README_DEEPEVAL.md` 进镜像与否不影响功能，仅 host 文档；保持 `.dockerignore` 例外一致即可。

## Step 6 — 测试覆盖

- [ ] 加冒烟/单测：
  - `.dockerignore` 不回归（build 后 `cocah.html` 在、`questions.md` 在）——可放 `tests/unit/test_dockerignore.py` 或 `scripts/smoke-image.sh`。
  - `run_retrieval_eval.py` 默认路径指向 `eval/`。
- **Gate 5**：`pytest tests/unit -q` 全绿。

## Step 7 — 端到端（无源码机 test）

- [ ] push → CI build → fan-out 到 test；SSH test 确认 `run-eval.sh` 已同步到 `/root/code/sales-agent/scripts/`。
- [ ] test 上 `run-eval.sh fuduoduo conversation --app-url http://127.0.0.1:<port> 2>&1 | tail` 跑通。
- **Gate 6**：test 无源码机产出 eval 结果文件。

## Step 8 — 收尾

- [ ] `changelog/2026-07-02.md` 追加一条（对象=eval/CI，类型=增强，影响=无源码机，明细+原因）。
- [ ] README「产品文档对照」「更新日志」节同步。
- [ ] 自检：资深工程师会批准吗？时间旅行在此任务不涉及（属父任务验证项）。

## Rollback

- `.dockerignore` 改动：删掉追加的 `!eval/...` 两行即可（无破坏性）。
- `run-eval.sh`：删除文件 + 撤销 ci-fanout 同步行；镜像内 eval 能力不受影响。
- `run_retrieval_eval.py` 路径：显式传 `-g` 即可绕过，回滚安全。
