# Prompt 从 DB+py 迁到 YAML — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 39 个 prompt 模板从 DB+py 迁到 `config/prompts.yaml`（单文件、结构化、按 category 嵌套），删 2 张 DB 表 + 9 个 API 端点 + 6 个前端页面 + ~25 个 py 文件 + PromptRegistry 整套，~30 调用点改走 `get_prompt(category, key)`。值不变（py 常量 → YAML template 逐字复制）。与已完成的 `get_call_params()` 模式统一。

**Architecture:** 复用 `call_params.py` 模式。新增 `src/sales_agent/llm/prompt_loader.py`（`load_prompts` 启动加载+YAML 校验、`get_prompt(category, key)` 运行时零 IO 读内存 dict，返回 `PromptTemplate(template, placeholders, description)`）。启动序列在 `call_params` 之后追加。~30 调用点把 `resolve_*_prompt(db, tenant_id, agent_id, default=CONST)` 改为 `get_prompt(category, key).template`。调用方自己 `.format(**kwargs)`（保持现有格式化方式）。

**Tech Stack:** Python 3.11+ / Pydantic BaseModel（Settings）/ PyYAML / pytest / Alembic。所有删除都是明确的：删文件、删表、删路由、删前端页。

## Global Constraints

- **值不变**：39 个 prompt 的 `template` 文本从 py 常量逐字复制到 YAML，**不修改任何字符**。这是回归安全的根基。
- **不动 `config/llm_call_defaults.yaml`** 和 `call_params.py`（参数层已完成，独立）。启动序列追加在 `call_params` 之后。测试 fixture 同 `conftest.py` 追加。
- **DB 迁移不可逆**：Alembic upgrade 删 2 表 + 1 列；downgrade 为 no-op（YAML 是新的唯一来源，无法恢复 DB 数据）。
- **前端全部删光**：不保留任何 prompt 管理页面、API 封装、类型定义。console build 必须通过（删了引用后 tsc+vite 不报错）。
- **别名兼容**：`_DEFAULT_PROMPTS` 删掉（它不是公开 API，纯历史兼容）。`PromptRegistry`、`resolve_prompt` 等搜索命中在全仓应为零（除 migration 历史）。
- **测试**：`PYTHONPATH=$(pwd)/src /root/code/sales-agent/.venv/bin/python -m pytest`；worktree 内必须 `PYTHONPATH=$(pwd)/src`。实现前先 EnterWorktree 隔离。
- **每个 Task 一个 commit**，便于二分定位。

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `config/prompts.yaml` | 39 个 prompt（结构化，按 category 嵌套） | 新建 |
| `src/sales_agent/llm/prompt_loader.py` | `PromptTemplate` + `load_prompts` + `get_prompt` | 新建 |
| `tests/unit/llm/test_prompt_loader.py` | loader 单测 | 新建 |
| `src/sales_agent/roles/stream_runner.py` | 启动调 `load_prompts()` | 改 |
| `tests/conftest.py` | 测试 fixture 追加 `load_prompts` | 改 |
| ~30 个调用点文件 | `resolve_*_prompt` → `get_prompt` | 改 |
| `src/sales_agent/prompts/` | 整个目录 | 删 |
| `src/sales_agent/models/prompt.py` | `PromptVersion` ORM | 删 |
| `src/sales_agent/models/agent_prompt_set.py` | `AgentPromptSet` ORM | 删 |
| `src/sales_agent/services/prompt_registry.py` | PromptRegistry 类 | 删 |
| `src/sales_agent/services/prompt_resolver_helper.py` | 5 个 resolve_* wrapper | 删 |
| `src/sales_agent/services/prompt_defaults.py` | BUILTIN_PROMPTS | 删 |
| `src/sales_agent/api/routes/prompts.py` | 9 端点 | 删 |
| `src/sales_agent/models/agent.py` | 删 `prompt_set_id` | 改 |
| `migrations/versions/` | 新 migration：删表+删列 | 新建 |
| `console/src/` | 删 6 页面 + API 封装 + 类型 + 路由/导航 | 改/删 |
| ~15 个服务文件 | 删 prompt_set 引用 | 改 |
| ~18 个测试文件 | 改调用 / 删废弃测试 | 改/删 |

---

### Task 1: 创建 YAML 文件（39 prompt，值不变）

**Files:**
- Create: `config/prompts.yaml`

**说明**：从 39 个 py 常量逐字复制模板文本到 YAML，**不改任何字符**。关键是正确提取每个常量的字符串内容 + 占位符列表 + 描述。

- [ ] **Step 1: 用脚本提取 39 个 prompt 到 YAML**

自动化避免手抄错。在 worktree 内运行（`PYTHONPATH=$(pwd)/src`）以下 Python 脚本，扫描 BUILTIN_PROMPTS 注册表 + 外部常量，写入 `config/prompts.yaml`：

```python
"""提取所有 BUILTIN_PROMPTS 到 config/prompts.yaml。运行后手动审查 YAML 内容。"""
import yaml, sys
sys.path.insert(0, "src")
from sales_agent.services.prompt_defaults import BUILTIN_PROMPTS
from collections import defaultdict

data = defaultdict(dict)
for p in BUILTIN_PROMPTS:
    data[p.category][p.key] = {
        "template": p.template,
        "placeholders": list(p.required_placeholders),
        "description": p.description or "",
    }

with open("config/prompts.yaml", "w", encoding="utf-8") as f:
    yaml.dump(dict(data), f, allow_unicode=True, default_flow_style=False, sort_keys=True, width=120)
print(f"Written {sum(len(v) for v in data.values())} prompts to config/prompts.yaml")
```

- [ ] **Step 2: 手动审查 YAML 内容**

检查：
- 总数 = 39（`grep -c "template:" config/prompts.yaml` 或等价检查）
- 抽样 5 个 prompt（task.knowledge_qa / router.evidence_router / coach.coach_daily_eval / knowledge.entity_extraction / system.system_constraint）——打开对应 py 常量文件逐字对比，确认 YAML template 与 py 常量**逐字一致**（空格、换行、缩进都不差）
- 占位符列表与 py 常量中 `{...}` 匹配

- [ ] **Step 3: Commit**

```bash
git add config/prompts.yaml
git commit -m "feat(prompt): 新增 config/prompts.yaml（39 prompt，py 常量逐字复制）"
```

---

### Task 2: prompt_loader 模块（TDD）+ 启动接入 + conftest

**Files:**
- Create: `src/sales_agent/llm/prompt_loader.py`
- Create: `tests/unit/llm/test_prompt_loader.py`
- Modify: `src/sales_agent/roles/stream_runner.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces: `PromptTemplate(template: str, placeholders: tuple[str,...], description: str)`；`load_prompts(path) -> None`；`get_prompt(category, key) -> PromptTemplate`
- Consumes: 无（基础模块）

- [ ] **Step 1: 写 loader**

`src/sales_agent/llm/prompt_loader.py`：

```python
"""Prompt 模板加载器 — YAML → 内存缓存，运行时零 IO。
启动时 load_prompts() 读 config/prompts.yaml；运行时 get_prompt(category, key) 零 IO。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_PROMPTS: dict[tuple[str, str], "PromptTemplate"] | None = None


@dataclass(frozen=True)
class PromptTemplate:
    template: str
    placeholders: tuple[str, ...]
    description: str


def _coerce_and_validate(raw: dict, category: str, key: str) -> PromptTemplate:
    if not isinstance(raw, dict):
        raise ValueError(f"prompt {category}.{key} 的值必须是 dict: {raw!r}")
    template = raw.get("template", "")
    if not template or not isinstance(template, str):
        raise ValueError(f"prompt {category}.{key} 的 template 不能为空")
    placeholders = tuple(raw.get("placeholders", []) or [])
    description = raw.get("description", "") or ""
    return PromptTemplate(template=template, placeholders=placeholders, description=description)


def load_prompts(path: str) -> None:
    """启动时调用：读 YAML、校验、缓存进 _PROMPTS。失败即崩。"""
    global _PROMPTS
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Prompt 配置文件不存在: {path}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Prompt 配置文件顶层必须是 dict: {path}")
    prompts: dict[tuple[str, str], PromptTemplate] = {}
    for category, cat_dict in raw.items():
        if not isinstance(cat_dict, dict):
            raise ValueError(f"category {category} 的值必须是 dict")
        for key, val in cat_dict.items():
            prompts[(category, key)] = _coerce_and_validate(val, category, key)
    _PROMPTS = prompts


def get_prompt(category: str, key: str) -> PromptTemplate:
    """运行时读内存 dict。未知 (category,key) 抛 KeyError。"""
    if _PROMPTS is None:
        raise RuntimeError("Prompt 未加载，请先在启动序列调 load_prompts()")
    k = (category, key)
    if k not in _PROMPTS:
        raise KeyError(f"未知 prompt '{category}.{key}'，请检查 config/prompts.yaml")
    return _PROMPTS[k]
```

- [ ] **Step 2: 写单元测试**

`tests/unit/llm/test_prompt_loader.py`：

```python
import pytest, yaml
from sales_agent.llm.prompt_loader import PromptTemplate, load_prompts, get_prompt

def _write_yaml(tmp_path, data):
    p = tmp_path / "prompts.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True))
    return str(p)

def test_load_valid_and_get(tmp_path):
    data = {"router": {"test_key": {"template": "hello {name}", "placeholders": ["name"], "description": "test"}}}
    load_prompts(_write_yaml(tmp_path, data))
    p = get_prompt("router", "test_key")
    assert p.template == "hello {name}"
    assert p.placeholders == ("name",)
    assert p.description == "test"

def test_missing_file_raises(tmp_path):
    with pytest.raises((FileNotFoundError, RuntimeError)):
        load_prompts(str(tmp_path / "nope.yaml"))

def test_empty_template_raises(tmp_path):
    data = {"task": {"x": {"template": "", "placeholders": [], "description": ""}}}
    with pytest.raises(ValueError):
        load_prompts(_write_yaml(tmp_path, data))

def test_missing_template_field_raises(tmp_path):
    data = {"task": {"x": {"placeholders": [], "description": ""}}}
    with pytest.raises(ValueError):
        load_prompts(_write_yaml(tmp_path, data))

def test_get_before_load_raises():
    import sales_agent.llm.prompt_loader as m
    m._PROMPTS = None
    with pytest.raises(RuntimeError):
        get_prompt("router", "x")

def test_unknown_key_raises(tmp_path):
    data = {"router": {"test_key": {"template": "hello", "placeholders": [], "description": ""}}}
    load_prompts(_write_yaml(tmp_path, data))
    with pytest.raises(KeyError):
        get_prompt("router", "nonexistent")

def test_load_real_prompts_all_39_keys(tmp_path):
    """加载真实 config/prompts.yaml，验证全部 39 个 category+key 可获取且 template 非空。"""
    from pathlib import Path
    real = Path("config/prompts.yaml")
    if not real.exists():
        pytest.skip("config/prompts.yaml not found")
    load_prompts(str(real))
    expected_categories = {
        "task": ["memory_extraction", "emotional_support", "knowledge_qa", "script_generation",
                 "objection_handling", "conversation_review", "general_sales_coaching",
                 "visit_preparation", "follow_up_planning", "customer_context_summary",
                 "deal_advancement", "conversation_scoring", "post_visit_review",
                 "media_vision_user", "media_audio_transcribe"],
        "system": ["system_constraint", "media_vision_system"],
        "router": ["task_router", "context_resolver", "clarification_resolver", "evidence_router",
                   "topic_restore_resolver", "scenario_matcher"],
        "risk": ["risk_check"],
        "coach": ["coach_daily_eval", "coach_daily_eval_system", "coach_sw_system",
                  "coach_sb_system", "coach_sw_card", "coach_sb_split", "coach_sb_card"],
        "web": ["web_analysis"],
        "knowledge": ["entity_extraction", "fact_extraction", "image_interpret",
                      "md_optimize_system", "md_optimize_user", "ontology_term_extractor",
                      "ontology_response"],
    }
    count = 0
    for cat, keys in expected_categories.items():
        for key in keys:
            p = get_prompt(cat, key)
            assert p.template, f"{cat}.{key} template is empty"
            count += 1
    assert count == 39, f"Expected 39 prompts, got {count}"
```

- [ ] **Step 3: 跑测试确认通过**

Run: `cd <worktree> && PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/llm/test_prompt_loader.py -v`
Expected: 7 passed（先确保 loader 单测全绿；加载真实 YAML 的测试依赖 Task 1 的 YAML 文件）

- [ ] **Step 4: 启动接入**

`src/sales_agent/roles/stream_runner.py`，在 `load_call_params(...)` 和 `logger.info("LLM call params loaded ...")` 之后追加：

```python
    # 加载 LLM 调用参数默认值（temperature/max_tokens）
    from sales_agent.llm.call_params import load_call_params
    load_call_params(settings.llm_call_defaults_path)
    logger.info("LLM call params loaded (stream runner)")

    # 加载 Prompt 模板
    from sales_agent.llm.prompt_loader import load_prompts
    load_prompts(settings.prompts_path)
    logger.info("Prompts loaded (stream runner)")

    # 加载 TenantRuntime
    runtime = get_tenant_runtime()
```

同时 Settings 加字段（`src/sales_agent/core/config.py`，紧接 `llm_call_defaults_path` 之后）：
```python
    llm_call_defaults_path: str = "config/llm_call_defaults.yaml"
    prompts_path: str = "config/prompts.yaml"
```

- [ ] **Step 5: conftest fixture 追加**

`tests/conftest.py`，在 autouse fixture 中 `load_call_params(...)` 之后追加：
```python
    from sales_agent.llm.prompt_loader import load_prompts
    load_prompts(get_settings().prompts_path)
```

- [ ] **Step 6: 跑 loader 全量测试确认**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/llm/test_prompt_loader.py -v`
Expected: 7 passed（含 `test_load_real_prompts_all_39_keys`）

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/llm/prompt_loader.py tests/unit/llm/test_prompt_loader.py src/sales_agent/roles/stream_runner.py src/sales_agent/core/config.py tests/conftest.py
git commit -m "feat(prompt): 新增 prompt_loader（YAML 加载+校验+内存缓存）+ 启动+conftest 接入"
```

---

### Task 3: 调用点改造——Router 类（6 处）

**Files:**
- Modify: `services/task_router.py:511`
- Modify: `services/context_resolver.py:139`
- Modify: `services/evidence_router.py:137`
- Modify: `services/topic_manager.py:561`
- Modify: `services/topic_restore.py:197`
- Modify: `scenarios/matcher.py:44`

每处：去掉 `db, tenant_id, agent_id, default=CONST` 参数，改为 `get_prompt(category, key).template`。

before（例：`evidence_router.py:137`）：
```python
prompt = await resolve_router_prompt(
    db, "evidence_router", tenant_id, agent_id, default=EVIDENCE_ROUTER_PROMPT
)
```
after：
```python
from sales_agent.llm.prompt_loader import get_prompt
prompt = get_prompt("router", "evidence_router").template
```

⚠️ `scenarios/matcher.py` 现直接 import `SCENARIO_MATCHER_PROMPT` → 改 `get_prompt("router", "scenario_matcher").template.format(questions_json=...)`。

- [ ] Step 1-6: 逐文件改（每文件 grep 确认行号 → 改 → 跑相关单测）
- [ ] Step 7: 跑 router 相关测试 `PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/ -k "router or task_router or evidence or context_resolver or topic or scenario" -v 2>&1 | tail -20`
- [ ] Step 8: Commit: `refactor(prompt): router 类 6 处调用点接入 get_prompt`

---

### Task 4: 调用点改造——Task/System 类（5 处）

**Files:**
- Modify: `graph/chat/nodes/generation.py:88`
- Modify: `graph/guided_flow/handlers/visit_flows.py:193`
- Modify: `cli.py:164,297`
- Modify: `graph/guided_flow/handlers/coach_flows.py:396,418`

每处：`resolve_execution_prompts(db, agent_id, tenant_id, task_type)` → 分别调 `get_prompt("task", task_type).template` + `get_prompt("system", "system_constraint").template`。

coach_flows: `resolve_quick_session_prompts(db, tenant_id, agent_id)` → 逐个调 `get_prompt("coach", key).template`（5 个 key）。

- [ ] Step 1-5: 逐文件改
- [ ] Step 6: Commit: `refactor(prompt): task/system 类调用点接入 get_prompt`

---

### Task 5: 调用点改造——Risk/Coach/Web/Knowledge 类（~10 处）

**Files:**
- Modify: `graph/chat/nodes/risk_check.py:101` → `get_prompt("risk", "risk_check").template.format(message=..., answer=...)`
- Modify: `coach/daily_evaluator.py:595-596` → `get_prompt("coach", key).template`
- Modify: `graph/retrieval/web_fallback.py:65` → `get_prompt("web", "web_analysis").template.format(user_question=..., search_results=...)`
- Modify: `ontology/extractor.py:178,267` → `get_prompt("knowledge", key).template.format(...)`
- Modify: `ontology/ingestion_service.py:382`
- Modify: `services/md_optimizer.py:120,127`
- Modify: `ontology/retrieval_service.py:60`
- Modify: `graph/retrieval/ontology_graph.py:66`
- Modify: `ontology/answer_service.py:265`
- Modify: `api/routes/ontology.py:408,454`

每处：`resolve_*_prompt(db, key, tenant_id, agent_id, default=CONST, **fmt_kwargs)` → `get_prompt(category, key).template.format(**fmt_kwargs)`

⚠️ knowledge 类：格式化从 resolver 内部移到调用方，注意 `**fmt_kwargs` 的传递。

- [ ] Steps 1-10: 逐文件改
- [ ] Step 11: Commit: `refactor(prompt): risk/coach/web/knowledge 类调用点接入 get_prompt`

---

### Task 6: 直接 import 常量 → get_prompt（4 处）

**Files:**
- Modify: `services/memory/extractor.py:32` → `get_prompt("task", "memory_extraction").template`
- Modify: `integrations/dingtalk/media_adapter.py:160,165,205` → `get_prompt("system", "media_vision_system").template` 等

- [ ] Step 1-2: 逐文件改
- [ ] Step 3: Commit: `refactor(prompt): 直接 import 常量 4 处改接 get_prompt`

---

### Task 7: 删除 py 文件 + models + 注册表（src/ 清理）

**Files:**
- Delete: `src/sales_agent/prompts/`（整个目录）
- Delete: `src/sales_agent/models/prompt.py`
- Delete: `src/sales_agent/models/agent_prompt_set.py`
- Delete: `src/sales_agent/services/prompt_registry.py`
- Delete: `src/sales_agent/services/prompt_resolver_helper.py`
- Delete: `src/sales_agent/services/prompt_defaults.py`
- Modify: `src/sales_agent/models/agent.py`（删 `prompt_set_id` 字段）
- Modify: `src/sales_agent/models/__init__.py`（删 PromptVersion、AgentPromptSet 导入）
- Modify: 删除 `scenarios/prompt.py` 中的 `SCENARIO_MATCHER_PROMPT` 常量（保留文件其余内容，或整文件删——确认该文件只含此常量）

- [ ] **Step 1: 全仓搜索残留引用**

Run: `grep -rn "from sales_agent.prompts\|from sales_agent.services.prompt_registry\|from sales_agent.services.prompt_resolver_helper\|from sales_agent.services.prompt_defaults\|from sales_agent.models.prompt\|from sales_agent.models.agent_prompt_set" src/ --include="*.py" | grep -v "/test_" | grep -v __pycache__`

Expected: ZERO 命中（所有引用已在 Task 3-6 清理）。
若有残留 → 逐个改，确保零引用后再删文件。

- [ ] **Step 2: 删除文件**
- [ ] **Step 3: 跑全量 import 冒烟**

Run: `PYTHONPATH=$(pwd)/src /root/code/sales-agent/.venv/bin/python -c "from sales_agent.main import app; print('import ok')"`
Expected: `import ok`（无 `ModuleNotFoundError` / `ImportError`）

- [ ] **Step 4: Commit**

```bash
git add -A src/sales_agent/prompts/ src/sales_agent/models/ src/sales_agent/services/ src/sales_agent/scenarios/
git commit -m "refactor(prompt): 删 prompts/*.py + PromptRegistry + models + prompt_defaults"
```

---

### Task 8: Alembic migration（删 DB 表 + 列）

**Files:**
- Create: `migrations/versions/XXXX_prompt_yaml_cleanup.py`（`alembic revision -m "prompt_yaml_cleanup"`）

- [ ] **Step 1: 生成 migration 骨架**

Run: `cd <worktree> && .venv/bin/alembic revision -m "prompt_yaml_cleanup"`

- [ ] **Step 2: 填写 upgrade**

```python
def upgrade():
    op.drop_table('agent_prompt_sets')
    op.drop_table('prompt_versions')
    with op.batch_alter_table('agents') as batch_op:
        batch_op.drop_column('prompt_set_id')
    with op.batch_alter_table('optimization_releases') as batch_op:
        batch_op.drop_column('prompt_set_id')

def downgrade():
    # 不可逆：YAML 是新的唯一 prompt 来源，DB 数据已丢弃
    pass
```

- [ ] **Step 3: 验证 migration**

Run: `.venv/bin/alembic upgrade head`（测试 DB `localhost:5432`，确认不报错）
Run: `.venv/bin/alembic downgrade -1` 确认不报错（no-op）
Run: `.venv/bin/alembic upgrade head` 再次确认

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/XXXX_prompt_yaml_cleanup.py
git commit -m "refactor(db): 删 prompt_versions + agent_prompt_sets 表 + agent.prompt_set_id（prompt YAML 化 cleanup）"
```

---

### Task 9: 清理服务文件中的 prompt_set 引用

**Files:**
- Modify: `services/agent_service.py` — 删 `prompt_set` 创建/序列化
- Modify: `services/agent_clone_service.py` — 删 `prompt_set` 深拷贝
- Modify: `services/agent_migration.py` — 删 `prompt_set` 迁移
- Modify: `services/agent_readiness_service.py` — 删 `prompt_set` 检查
- Modify: `services/release_service.py` — 删 manifest `prompt_set`
- Modify: `services/release_types.py` — 删 `prompt_set_id` dataclass 字段
- Modify: `models/runtime_release.py` — 删 `prompt_set_id` 字段
- Modify: `api/routes/agents.py` — 删 prompt 映射端点（`GET/PUT /agents/{id}/prompts`）
- Modify: `api/schemas.py` — 删 PromptVersion/AgentPromptSet Pydantic schemas
- Modify: `main.py` — 删 prompts router 注册
- Modify: `eval/optimizer/tools/debug_prompt.py` — 改 `PromptRegistry` → `get_prompt`
- Modify: `api/routes/ontology.py` — 删 PromptRegistry 引用（已在 Task 5 改接）

- [ ] **Step 1-12: 逐文件改**
- [ ] **Step 2: 全量 import 冒烟** → `import ok`
- [ ] **Step 3: 全仓 grep 确认零残留**

Run: `grep -rn "PromptRegistry\|prompt_versions\|agent_prompt_sets\|prompt_set_id\|resolve_prompt\|resolve_execution_prompts\|resolve_router_prompt\|resolve_risk_prompt\|resolve_quick_session_prompts\|resolve_knowledge_prompt\|resolve_coach_prompt\|BuiltinPrompt\|BUILTIN_PROMPTS" src/ --include="*.py" | grep -v __pycache__ | grep -v "migrations/" | grep -v "#"`
Expected: ZERO
若有残留 → 逐个清理

- [ ] **Step 4: Commit**

```bash
git add -A src/sales_agent/services/ src/sales_agent/api/ src/sales_agent/models/ src/sales_agent/main.py eval/
git commit -m "refactor(prompt): 清理 prompt_set 引用 + 删 prompts API 路由 + main.py 注册"
```

---

### Task 10: 删前端 prompt 页面 + 改测试

**Files:**
- Delete: `console/src/api/prompts.ts`
- Delete: `console/src/pages/Prompts/PromptListPage.tsx`
- Delete: `console/src/pages/Prompts/PromptEditPage.tsx`
- Delete: `console/src/pages/Agents/AgentPromptsPage.tsx`
- Modify: `console/src/api/agents.ts` — 删 `getAgentPrompts`/`bindPromptVersion`/`unbindPromptVersion`
- Modify: `console/src/api/types.ts` — 删 PromptVersion 等接口
- Modify: `console/src/App.tsx` — 删 prompt 路由
- Modify: `console/src/layout/AgentLayout.tsx` — 删 Prompt 选项卡
- Modify: `console/src/layout/Sidebar.tsx` — 删 "Prompt 管理" 导航
- Modify: `console/src/pages/Agents/GraphDebugPage.tsx` — 删 prompt 对照表部分
- Modify: `console/src/pages/Agents/AgentListPage.tsx` — 删 prompt 配置状态列
- Modify: `console/src/pages/Agents/AgentCloneWizardPage.tsx` — 删 `prompt_set` 选项
- Modify: `console/src/pages/Readiness/ReadinessPage.tsx` — 删 prompt 检查

- [ ] **Step 1: 删文件 + 改引用**
- [ ] **Step 2: console build 验证**

Run: `cd console && npm run build 2>&1 | tail -10`
Expected: 无 TypeScript 编译错误、无 import 错误。若有 import 残留 → 修完再跑。

- [ ] **Step 3: Commit**

```bash
git add -A console/src/
git commit -m "refactor(frontend): 删 prompt 管理 6 页面 + API 封装 + 类型 + 路由"
```

---

### Task 11: 删/改测试文件

**需要删的测试文件**（这些文件只测 DB prompt 系统，YAML loader 替代后无用）：
- `tests/integration/test_prompt_api.py` — 删除
- `tests/unit/test_prompt_registry.py` — 删除
- `tests/unit/test_prompt_registry_category.py` — 删除
- `tests/unit/test_prompt_templates.py` — 删除
- `tests/unit/test_prompts_preview_render.py` — 删除
- `tests/unit/test_prompt_resolver_helper.py` — 删除
- `tests/unit/test_coach_prompts.py` — 删除
- `tests/unit/test_web_analysis_prompt.py` — 删除
- `tests/unit/test_risk_checker_prompt_injection.py` — 删除
- `tests/unit/test_task_router_prompt_injection.py` — 删除
- `tests/unit/test_ontology_response_prompt.py` — 删除
- `tests/unit/test_evidence_router_prompt.py` — 删除
- `tests/unit/test_visit_post_visit_placeholders.py` — 删除

**需要改的测试文件**（引用了 resolve_*_prompt 或 PromptRegistry）：
- `tests/unit/test_agent_runtime.py` — 删 PromptRegistry 引用
- `tests/unit/test_agent_migration.py` — 删 `prompt_set_id` 引用
- `tests/integration/test_agents_api.py` — 删 PromptVersion 等引用
- `tests/unit/test_out_of_domain_web_routing.py` — 改 `resolve_*` → mock `get_prompt`
- `tests/unit/services/test_prompt_defaults.py` — 删除（BUILTIN_PROMPTS 已删）
- `tests/console/src/tests/api/agents.test.ts` — 删 `prompt_set: 'copy'` 断言

- [ ] **Step 1: 删除废弃测试文件**
- [ ] **Step 2: 改保留的测试文件**（搜索 `resolve_prompt\|PromptRegistry\|BuiltinPrompt\|prompt_set` 逐文件改）
- [ ] **Step 3: 跑全量 unit 确认无崩溃**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/ -q 2>&1 | tail -10`
Expected: 无 ModuleNotFoundError / ImportError（仅允许 pre-existing 失败）

- [ ] **Step 4: Commit**

```bash
git add -A tests/
git commit -m "test(prompt): 删废弃 DB prompt 测试 + 改残留 PromptRegistry 引用"
```

---

### Task 12: 全量回归 + 部署验证

- [ ] **Step 1: 全量 unit**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/ -q 2>&1 | tail -15`
目标：无新增失败（对比 main 基线）。

- [ ] **Step 2: import 全链路冒烟**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -c "from sales_agent.main import app; print('OK')"`
Expected: OK

- [ ] **Step 3: stream_runner 启动冒烟**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -c "
from sales_agent.llm.call_params import load_call_params
from sales_agent.llm.prompt_loader import load_prompts
from sales_agent.core.config import get_settings
s = get_settings()
load_call_params(s.llm_call_defaults_path)
load_prompts(s.prompts_path)
print('Both loaders OK')
print('Params:', len(__import__('sales_agent.llm.call_params', fromlist=['_PARAMS'])._PARAMS), 'keys')
print('Prompts:', len(__import__('sales_agent.llm.prompt_loader', fromlist=['_PROMPTS'])._PROMPTS), 'prompts')
"`

Expected: `Both loaders OK` / `Params: 24 keys` / `Prompts: 39 prompts`

- [ ] **Step 4: 全仓 grep 确认零残留**

Run: `grep -rni "resolve_prompt\|PromptRegistry\|prompt_versions\|agent_prompt_sets\|prompt_set_id\|BUILTIN_PROMPTS\|BuiltinPrompt\|prompt_defaults\|prompt_registry\|prompt_resolver_helper\|route.*prompts" src/ --include="*.py" | grep -v __pycache__ | grep -v "migrations/" | grep -v "\.pyc"`
Expected: ZERO（仅允许 `config/prompts.yaml` 路径字符串和 `get_prompt` 函数调用）

- [ ] **Step 5: changelog + README 更新**

追加 `changelog/2026-07-10.md` 条目，更新 `README.md` 更新日志表。

- [ ] **Step 6: 合并到 main + ExitWorktree**

（完成后按 finishing 流程操作）

---

## Self-Review

**1. Spec 覆盖**：§4 YAML 结构 -> Task 1 ✅ / §5 loader -> Task 2 ✅ / §5 调用点 -> Task 3-6 ✅ / §6 删除 -> Task 7-10 ✅ / §6 DB -> Task 8 ✅ / §8 测试 -> Task 11 ✅ / §8 回归 -> Task 12 ✅

**2. 占位符**：Task 1 用脚本自动提取 YAML 而非手写——消除手抄错风险。Task 9 的残留 grep 搜索覆盖所有已知的 API 名和变量名。任务大小适中，可独立 review。

**3. 类型一致**：`get_prompt(category, key) -> PromptTemplate` / `PromptTemplate.template` / `PromptTemplate.placeholders` / `PromptTemplate.description` 在 Task 2 定义，Task 3-6 消费一致。
