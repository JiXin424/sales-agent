# Prompt 从 DB+py 迁到 YAML——设计文档

- **日期**：2026-07-10
- **状态**：已通过设计评审，待实现
- **作者**：gitea-admin（brainstorming 产出）
- **相关**：LLM 调用参数配置化（同一 YAML 模式的第二层——已完成）、`config/llm_call_defaults.yaml`

> 行号为设计调研时（2026-07-10）的近似值，实现前请用当前代码核对。

## 1. 背景与问题

仓库的 Prompt 系统当前是**DB 主力 + py 兜底**的三层架构：
- 第 1 层：Agent 绑定的 `agent_prompt_sets` 表（`models/agent_prompt_set.py`）
- 第 2 层：租户 active 版本 `prompt_versions` 表（`models/prompt.py`，REST API 在线编辑/版本化/激活/归档）
- 第 3 层：py 常量兜底 `prompts/*.py`（~25 个文件，39 个 `BuiltinPrompt` 条目，`prompt_defaults.py` 注册）

另有配套：REST API 9 端点（`api/routes/prompts.py`）、前端 6 个 prompt 管理页（`console/src/pages/Prompts/` + `Agents/AgentPromptsPage.tsx`）、~30 处 `resolve_*_prompt(db, ...)` 调用点、~18 个测试文件。

**问题**：这套 DB 系统对「只有开发者改 prompt」的场景太重——不需要版本化、在线编辑、多租户覆盖。直接用 YAML + git 管版本更简单，且和刚完成的参数 YAML 化（`config/llm_call_defaults.yaml`、`get_call_params()`）心智模型统一。

## 2. 目标与非目标

### 目标
- 39 个 prompt 模板收进**一个 YAML 文件**（`config/prompts.yaml`），结构化（`template` / `placeholders` / `description`），按 `category.key` 分组。
- 新增 `src/sales_agent/llm/prompt_loader.py`：启动加载 + 校验 + 内存缓存，运行时 `get_prompt(category, key)` 零 IO 返回 `PromptTemplate`。
- 删 `prompts/*.py`（~25 个文件）、删 `models/prompt.py` + `models/agent_prompt_set.py`、删 `services/prompt_registry.py`（367 行）+ `services/prompt_resolver_helper.py`。
- 删 `api/routes/prompts.py`（9 端点）、删 main.py 路由注册。
- 删 `prompt_versions` + `agent_prompt_sets` 两张 DB 表（Alembic migration）、删 `agent.prompt_set_id` 列。
- 删 console 前端 6 个 prompt 管理页 + API 封装 + 类型定义 + 路由/导航。
- ~30 处调用点：`resolve_*_prompt(db, ...)` → `get_prompt(category, key)`。
- `AgentCloneService` / `AgentMigration` / `ReleaseService` 等删除 `prompt_set` 引用。
- ~18 个测试文件改/删。

### 非目标
- **不动 `config/llm_call_defaults.yaml`** 和 `call_params.py`（参数层已完成，独立于本次）。
- **不动 prompt 模板内容本身**——值不变搬运（const → YAML）。不改任何模板正文/占位符/语义。
- **不做任何新功能**——纯迁移 + 删除 + 接线。

### 成功标准
- 39 个 prompt 在 YAML 中的 `template` 文本与现有 py 常量**逐字一致**（值不变）。
- 全量 unit 通过（回归：Prompt 生成的回答不变）。
- Stream 容器启动日志含 `Prompts loaded (stream runner)`（同 `LLM call params loaded` 风格），无 crash。
- DB 两表 + API + 前端全部消失。整个仓库不再有 `PromptRegistry`、`prompt_versions`、`agent_prompt_sets`、`resolve_prompt` 搜索命中（除 migration 历史）。

## 3. 方案选型

brainstorming 中确定的路径：
- **方案（采用）**：纯 YAML + 内存缓存，git 管版本，删除整个 DB/API/前端 prompt 系统。与 `call_params.py` 模式完全统一。
- 方案 1（已否决）：分两期——先 YAML 再删 DB。会导致两期之间 YAML 和 DB 并存、调用点支持双解析，复杂度翻倍。用户选「全做」一次性清理。
- 方案 2（已否决）：只动 prompt 模板、不动 DB/API/前端。留下不能用的 DB 表和死前端页面，不符合「废弃的部分要安全清除」原则。

## 4. 架构与数据流

```
启动时（一次性，stream_runner.py，紧接 call_params 后）：
  config/prompts.yaml  ──load_prompts()──▶  _PROMPTS: dict[str, PromptTemplate]
                                               （key = "category.key"，如 "router.evidence_router"）

运行时：
  evidence_router 需要 prompt
        │
        ▼
  p = get_prompt("router", "evidence_router")   ← 纯内存查找，零 IO
        │
        ▼
  p.template.format(message=msg)                 ← 调用方自己 format，和现有方式一致
```

**与参数层的对称**：

| | 参数（已完成） | Prompt（本次） |
|---|---|---|
| YAML 文件 | `config/llm_call_defaults.yaml` | `config/prompts.yaml` |
| Loader | `llm/call_params.py` | `llm/prompt_loader.py` |
| 返回值 | `CallParams(temperature, max_tokens)` | `PromptTemplate(template, placeholders, description)` |
| 启动加载 | `stream_runner.py` | 同位置追加 `load_prompts()` |
| 测试 fixture | `conftest.py` session autouse | 同 fixture 追加 |
| 运行时 | `get_call_params(site)` | `get_prompt(category, key)` |

### YAML 结构

```yaml
# config/prompts.yaml — 39 个 prompt，按 category 分组，结构化条目
task:
  knowledge_qa:
    template: |
      你是专业的销售陪跑助手。请根据以下信息回答用户问题。
      {context_block}
      {retrieval_content}
      用户问题：{message}
    placeholders: [message, context_block, retrieval_content]
    description: 知识问答

router:
  evidence_router:
    template: |
      你是一个意图路由器。根据用户输入判断任务类型...
    placeholders: []
    description: 证据路由判断

coach:
  coach_daily_eval:
    template: |
      请评估以下销售对话的质量...
      {conversation_block}
    placeholders: [conversation_block]
    description: 每日教练评估

# ... 全部 39 个（完整清单见 §7）
```

### Loader 接口

```python
@dataclass(frozen=True)
class PromptTemplate:
    template: str
    placeholders: tuple[str, ...]
    description: str

def load_prompts(path: str) -> None:
    """启动时调用：读 YAML、校验、缓存。失败即崩。"""

def get_prompt(category: str, key: str) -> PromptTemplate:
    """运行时读内存 dict。未知 key 抛 KeyError。"""
```

## 5. 调用点改动模式（before / after）

### 有 `resolve_*` helper 的（20 处）

**before**（`evidence_router.py:137`）：
```python
prompt = await resolve_router_prompt(
    db, "evidence_router", tenant_id, agent_id, default=EVIDENCE_ROUTER_PROMPT
)
```

**after**：
```python
from sales_agent.llm.prompt_loader import get_prompt
prompt = get_prompt("router", "evidence_router").template
```
（去掉 `db`、`tenant_id`、`agent_id`、`default=` 四个参数——YAML 里只有一份默认值。）

### 走 PromptRegistry 的（5 处）

**before**（`task_router.py:511`）：
```python
prompt = await PromptRegistry(db).resolve_prompt("router", "task_router", tenant_id, agent_id)
```

**after**：
```python
prompt = get_prompt("router", "task_router").template
```

### 带 `**fmt_kwargs` 的 knowledge 类（9 处）

**before**（`extractor.py:178`）：
```python
prompt = await resolve_knowledge_prompt(
    db, "entity_extraction", tenant_id, agent_id,
    default=ENTITY_EXTRACTION_PROMPT, content=chunk
)
# resolve_knowledge_prompt 内部做 .format(content=chunk)
```

**after**：
```python
prompt = get_prompt("knowledge", "entity_extraction").template.format(content=chunk)
```
（格式化移到调用方——和 router/risk 等保持一致，格式权交还给调用方。）

### 直接 import 常量的（4 处）

`memory/extractor.py`、`scenarios/matcher.py`、`media_adapter.py`（3 处）直接 import 常量 → 改调 `get_prompt(category, key).template`。

## 6. 删除清单

### 6.1 源文件删除

| 文件/目录 | 说明 |
|---|---|
| `src/sales_agent/prompts/` | 整个目录删除（~25 个 .py 文件） |
| `scenarios/prompt.py`、`ontology/extractor.py`、`ontology/answer_service.py`、`ontology/img_parser.py`、`ontology/retrieval_service.py`、`services/md_optimizer.py`、`integrations/dingtalk/media_adapter.py` 中的 prompt 常量 | 删常量定义 + 删对应的 BuiltinPrompt 注册。**这些文件本身保留**——它们包含非 prompt 的业务代码 |
| `src/sales_agent/models/prompt.py` | `PromptVersion` ORM |
| `src/sales_agent/models/agent_prompt_set.py` | `AgentPromptSet` ORM |
| `src/sales_agent/services/prompt_registry.py` | PromptRegistry 类（367 行） |
| `src/sales_agent/services/prompt_resolver_helper.py` | 5 个 resolve_* wrapper |
| `src/sales_agent/services/prompt_defaults.py` | BUILTIN_PROMPTS（YAML 替代） |
| `src/sales_agent/api/routes/prompts.py` | 9 个 REST 端点 |
| `console/src/api/prompts.ts` | prompt API 封装 |
| `console/src/pages/Prompts/PromptListPage.tsx` | 列表页 |
| `console/src/pages/Prompts/PromptEditPage.tsx` | 编辑页 |
| `console/src/pages/Agents/AgentPromptsPage.tsx` | Agent prompt 绑定页 |

### 6.2 源文件修改（删除引用）

| 文件 | 改动 |
|---|---|
| `models/agent.py:38` | 删 `prompt_set_id` 字段 |
| `models/__init__.py` | 删 `PromptVersion`、`AgentPromptSet` 导入 |
| `models/runtime_release.py:29` | 删 `prompt_set_id` 字段 |
| `services/agent_service.py` | 删 `prompt_set` 创建/序列化 |
| `services/agent_clone_service.py` | 删 `prompt_set` 深拷贝 |
| `services/agent_migration.py` | 删 `prompt_set` 迁移 |
| `services/agent_readiness_service.py` | 删 `prompt_set` 检查 |
| `services/release_service.py` | 删 manifest 中 `prompt_set` |
| `services/release_types.py` | 删 dataclass 字段 |
| `api/routes/agents.py` | 删 prompt 映射端点 |
| `api/schemas.py` | 删 PromptVersion/AgentPromptSet schemas |
| `main.py` | 删 prompts router 注册 |
| `eval/optimizer/tools/debug_prompt.py` | 改 `PromptRegistry` → `get_prompt` |
| `cli.py` | 改 `resolve_execution_prompts` → `get_prompt` |
| ~18 个测试文件 | 改/删（删 DB prompt 测试、改 loader 测试） |
| `console/src/api/agents.ts` | 删 `getAgentPrompts` / `bindPromptVersion` 等 |
| `console/src/api/types.ts` | 删 PromptVersion 等接口 |
| `console/src/App.tsx` | 删 prompt 路由 |
| `console/src/layout/AgentLayout.tsx` | 删 Prompt 选项卡 |
| `console/src/layout/Sidebar.tsx` | 删 "Prompt 管理" 导航 |

### 6.3 DB 迁移

新增 **Alembic migration**（`migrations/versions/XXXX_prompt_yaml_cleanup.py`）：

```python
def upgrade():
    # 删表（反向依赖先删）
    op.drop_table('agent_prompt_sets')
    op.drop_table('prompt_versions')
    # 删列
    with op.batch_alter_table('agents') as batch_op:
        batch_op.drop_column('prompt_set_id')
    with op.batch_alter_table('optimization_releases') as batch_op:
        batch_op.drop_column('prompt_set_id')

def downgrade():
    # 不能恢复（YAML 是新的唯一来源，DB 数据已丢）
    pass
```

### 6.4 前端删除

| 文件 | 改动 |
|---|---|
| `console/src/api/prompts.ts` | 删除 |
| `console/src/api/agents.ts` | 删 `getAgentPrompts()`、`bindPromptVersion()`、`unbindPromptVersion()` 及相关类型 |
| `console/src/api/types.ts` | 删 `PromptVersion`、`BuiltinPrompt`、`EffectivePrompt`、`PromptMapping`、`PromptFilters`、`PromptPreviewRequest`、`PromptPreviewResponse` |
| `console/src/pages/Prompts/PromptListPage.tsx` | 删除 |
| `console/src/pages/Prompts/PromptEditPage.tsx` | 删除 |
| `console/src/pages/Agents/AgentPromptsPage.tsx` | 删除 |
| `console/src/pages/Agents/GraphDebugPage.tsx` | 删 prompt 对照表部分 |
| `console/src/pages/Agents/AgentListPage.tsx:76-77` | 删 prompt 配置状态列 |
| `console/src/pages/Agents/AgentCloneWizardPage.tsx` | 删 `prompt_set` 复制/引用选项 |
| `console/src/pages/Readiness/ReadinessPage.tsx` | 删 prompt 活跃度检查 |
| `console/src/layout/AgentLayout.tsx:39` | 删 "Prompt" 选项卡 |
| `console/src/layout/Sidebar.tsx:21` | 删 "Prompt 管理" 导航项 |
| `console/src/App.tsx:78,107-109` | 删 `/prompts` + `/legacy/prompts*` 路由 |

## 7. Prompt 完整清单（39 个 → YAML）

| category | key | 占位符 | 来源文件 |
|---|---|---|---|
| task | memory_extraction | 无 | prompts/memory_extractor_prompt.py |
| task | emotional_support | message, context_block, retrieval_block | prompts/emotional_support.py |
| task | knowledge_qa | message, context_block, retrieval_content | prompts/knowledge_qa.py |
| task | script_generation | message, context_block, retrieval_block | prompts/script_generation.py |
| task | objection_handling | message, context_block, retrieval_content | prompts/objection_handling.py |
| task | conversation_review | message, context_block, retrieval_block | prompts/conversation_review.py |
| task | general_sales_coaching | message, context_block, retrieval_block | prompts/general_coaching.py |
| task | visit_preparation | message, context_block, retrieval_block | prompts/visit_preparation.py |
| task | follow_up_planning | message, context_block, retrieval_block | prompts/follow_up_planning.py |
| task | customer_context_summary | message, context_block, retrieval_block | prompts/customer_context_summary.py |
| task | deal_advancement | message, context_block, retrieval_block | prompts/deal_advancement.py |
| task | conversation_scoring | message, context_block, retrieval_block | prompts/conversation_scoring.py |
| task | post_visit_review | message, context_block, retrieval_block | prompts/post_visit_review.py |
| system | system_constraint | 无 | prompts/system.py |
| router | task_router | message | prompts/task_router_prompt.py |
| router | context_resolver | 无 | prompts/context_resolver_prompt.py |
| router | clarification_resolver | 无 | prompts/clarification_resolver_prompt.py |
| router | evidence_router | 无 | prompts/evidence_router_prompt.py |
| router | topic_restore_resolver | 无 | prompts/topic_restore_resolver_prompt.py |
| router | scenario_matcher | questions_json | scenarios/prompt.py |
| risk | risk_check | message, answer | prompts/risk_check_prompt.py |
| coach | coach_daily_eval | conversation_block | prompts/coach_daily_evaluation.py |
| coach | coach_daily_eval_system | 无 | prompts/coach_quick.py |
| coach | coach_sw_system | 无 | prompts/coach_quick.py |
| coach | coach_sb_system | 无 | prompts/coach_quick.py |
| coach | coach_sw_card | small_win, strength, gratitude, energy_sentence | prompts/coach_quick.py |
| coach | coach_sb_split | sales_input, user_split | prompts/coach_quick.py |
| coach | coach_sb_card | sales_input, split_text, possibilities_attempt | prompts/coach_quick.py |
| web | web_analysis | user_question, search_results | prompts/web_analysis_prompt.py |
| system | media_vision_system | 无 | integrations/dingtalk/media_adapter.py |
| task | media_vision_user | 无 | integrations/dingtalk/media_adapter.py |
| task | media_audio_transcribe | 无 | integrations/dingtalk/media_adapter.py |
| knowledge | entity_extraction | content | ontology/extractor.py |
| knowledge | fact_extraction | content, entities_json | ontology/extractor.py |
| knowledge | image_interpret | 无 | ontology/img_parser.py |
| knowledge | md_optimize_system | 无 | services/md_optimizer.py |
| knowledge | md_optimize_user | content | services/md_optimizer.py |
| knowledge | ontology_term_extractor | question | ontology/retrieval_service.py |
| knowledge | ontology_response | graph_json, question, task_type | ontology/answer_service.py |

> YAML 中的 `template` 值取**当前 py 常量的字符串内容**逐字复制——值不变迁移。

## 8. 测试与回归保障

### 新建
- `tests/unit/llm/test_prompt_loader.py`：合法性校验（缺文件、缺 key、缺字段、template 空 → fail loud）、按 category+key 取值正确。
- loader 验证测试：`test_load_real_prompts_all_39_keys()`——断言全部 39 个 category+key 组合可获取且 template 非空。

### 回归保障
- 全量 unit：所有 `resolve_*` 调用点改完后，跑 `tests/unit/` 确认无新增失败（只看 prompt 相关模块）。
- 值不变验证：YAML 写入时逐字抄 py 常量，实现计划阶段由脚本自动抽取 py 常量写入 YAML（避免手抄出错）。
- Stream 日志：部署后 `docker logs <tenant>-stream` 确认 `Prompts loaded` 且无 `KeyError` / `ImportError` / crash。

### 渐进接入
- 第一阶段：YAML + loader + 启动加载 + unit 测试。
- 第二阶段：调用点批量改接（按 category 分批，每批一个 commit）。
- 第三阶段：删 py 文件 + 删 DB 表 + Alembic migration。
- 第四阶段：删 API + 前端 + 测试文件。

## 9. 错误处理

| 场景 | 行为 |
|---|---|
| YAML 文件不存在 | 启动失败（prompt 是核心依赖） |
| YAML 缺 category 或 key | 启动校验失败 |
| `template` 为空 | 启动校验失败 |
| `placeholders` 列表与模板实际占位符不匹配 | **不校验**——YAML 的 placeholders 字段是文档/辅助，运行时由调用方 `.format()` 决定成败，loader 不做占位符校验 |
| 运行时请求不存在 category/key | `KeyError`，fail loud |
| 运行时 `get_prompt` 在 `load_prompts` 之前调 | `RuntimeError` |

## 10. 不做

- 不动 `config/llm_call_defaults.yaml` / `call_params.py`（参数层已完成）。
- 不新建 prompt 编辑页面（删光前端，纯开发者改）。
- 不保留 DB 表/API 的任何兼容层（一次性删光）。

## 11. 后续

本设计审阅通过后，转 writing-plans 出实现计划。
