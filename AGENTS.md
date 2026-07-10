<!-- TRELLIS:START -->
# Trellis Instructions

These instructions are for AI assistants working in this project.

This project is managed by Trellis. The working knowledge you need lives under `.trellis/`:

- `.trellis/workflow.md` — development phases, when to create tasks, skill routing
- `.trellis/spec/` — package- and layer-scoped coding guidelines (read before writing code in a given layer)
- `.trellis/workspace/` — per-developer journals and session traces
- `.trellis/tasks/` — active and archived tasks (PRDs, research, jsonl context)

If a Trellis command is available on your platform (e.g. `/trellis:finish-work`, `/trellis:continue`), prefer it over manual steps. Not every platform exposes every command.

If you're using Codex or another agent-capable tool, additional project-scoped helpers may live in:
- `.agents/skills/` — reusable Trellis skills
- `.codex/agents/` — optional custom subagents

Managed by Trellis. Edits outside this block are preserved; edits inside may be overwritten by a future `trellis update`.

<!-- TRELLIS:END -->

## 开发与部署规则（dev-first）

日常开发**一律在 dev 分支**，提交只 push 到 dev，避免误触发三台服务器全量重建。

### 日常开发（默认走 dev）

1. **确认在 dev 分支**：开发前 `git branch` 确认当前在 dev，不在则 `git checkout dev && git pull origin dev`。所有改动都在 dev 上做。
2. **只 push origin dev**：自测后 `git push origin dev`。dev 的 CI（`.gitea/workflows/deploy-dev.yml`）**只重建本机 prod2 的应用容器**（taishan + taishankaifa2），**不碰 test、不碰 prod3**。
3. **push 后立即测试**：dev 部署完按 CLAUDE.md 走生产入口验证——`docker logs taishan-stream` / `taishankaifa2-stream` 确认 Stream 连上、无 crash，钉钉端实测功能。
4. **禁止随意 push origin main**：main 的 CI 会 **fan-out 重建 prod3 + prod2 + test 三台全部应用容器**。只有确认发布、dev 已合入 main 时才 push（见下「发布到 main」）。

### 提交安全（dev 上）

5. **提交前 fetch + rebase**：`git fetch origin dev && git rebase origin/dev`，基于远程最新。
6. **冲突立即停止**：rebase 出现冲突**不要自动解决**，停止并报告冲突文件与摘要，等人工决策。
7. **禁止 force push**：绝不 `git push --force` 到 dev 或 main。
8. **禁止在主工作目录 `git reset --hard` / `git checkout -- .` / `git stash`**（共享目录误伤他人未提交改动；隔离用 worktree）。

### 发布到 main（三台全量重建，谨慎）

dev 自测通过、确认发布时：`git checkout main && git merge dev && git push origin main`。⚠️ 此操作**重建三台全部应用容器**，prod3/test 切到 main 版本——发布前确认 dev 已是期望内容。
