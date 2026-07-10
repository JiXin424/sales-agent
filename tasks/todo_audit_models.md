# 审计 models/ 目录的 mock/占位代码与死代码

## 范围
`src/sales_agent/models/` 下所有 32 个 .py 文件，只读审计。

## 步骤
- [x] 1. 列出全部 model 文件、__init__.py re-export、migrations/versions 列表
- [x] 2. 程序化抽取每个文件的 model 类名 + __tablename__ + 非模型函数
- [x] 3. 对每个 model 类 grep 全仓库（src+tests+migrations+eval）确认引用
- [x] 4. 对每个非模型函数检查是否 mock/占位（pass/NotImplementedError/写死返回/Stub）
- [x] 5. 汇总分类（MOCK / 需 DBA 复核 / 需复核 / LIVE）

## 规则
- model 类一律不标「可安全删除」，最多标「需 DBA 复核」
- 排除 .claude/worktrees/、__pycache__/、docs/、changelog/
- 保守优先

## 结论
- MOCK/占位：0（models/ 下无非模型 stub；base.py 的 generate_id/utcnow、conversation_topic.py 的 utc_datetime 均为真实实现）
- 需 DBA 复核：6 个 model（RetrievalTrace / RetrievalTraceHit / OptimizationCommandAudit / CandidateEvalRun / TenantModelConfig / QuickSession）
- 关键背景：baseline migration 是 no-op，所有表由 `Base.metadata.create_all()` 在运行时建表，因此「表存在」不等于「被使用」
