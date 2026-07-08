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

## 安全提交规则

开发完成后，**安全地 commit 到本地 main 分支**，遵循以下流程：

1. **提交前先 fetch**：`git fetch origin main` 检查远程是否有新提交。
2. **Rebase 到最新**：`git rebase origin/main`，确保本地提交基于远程最新。
3. **有冲突立即停止**：rebase 过程中如果出现冲突，**不要尝试自动解决**，停止并报告：
   - 哪些文件冲突
   - 冲突内容摘要
   - 等待人工介入决策
4. **无冲突才 commit**：rebase 成功后，本地 commit 再 `git push origin main`。
5. **禁止 force push**：绝不使用 `git push --force` 到 `main`。
6. **禁止 `git reset --hard` / `git checkout -- .` / `git stash` 在主工作目录执行**（共享目录会误伤他人未提交改动）。
