# LLM 调用参数配置化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把仓库内 22~24 处硬编码 `temperature`/`max_tokens` 收进 `config/llm_call_defaults.yaml`，runtime 启动加载并校验、内存缓存零 IO 读取，仅开发者改、git 管版本；顺带补 6 个漏网 prompt 进注册表、修 `md_optimizer` 的 `.chat()` bug。

**Architecture:** 纯 YAML + 内存缓存（方案 0）。新增 `src/sales_agent/llm/call_params.py` loader（`load_call_params` 启动加载+校验、`get_call_params` 运行时读内存 dict），启动序列在 `initialize_online_runtime()` 后调一次。24 个调用点把字面量换成 `get_call_params("<call_site>")`。值不变纯结构重构。不碰 DB、不建后台 API/前端、不做 Alembic migration。

**Tech Stack:** Python 3.11+ / asyncio / Pydantic BaseModel（Settings）/ PyYAML / pytest。ChatModel 接口 `async generate(messages, temperature=None, max_tokens=None, response_format=None) -> str`。

## Global Constraints

- **值不变**：YAML 填入的 `temperature`/`max_tokens` 必须等于重构前该调用点的原字面量（见各 Task before 行）。这是回归安全的根基。
- **不碰数据库**：无 Alembic migration、无新表、无快照。
- **不建后台 API / 前端**：参数只给开发者改。
- **只抽 `temperature` + `max_tokens` 两个标量**；`response_format` 等结构性参数留代码；`top_p` 等不纳入。
- **fail loud**：YAML 缺文件/已写 key 缺字段/类型错/温度超 [0,2]/max_tokens≤0 -> 启动即崩；运行时未知 call_site -> `KeyError`。
- **后端 CommonJS 风格不适用**（本项目是 Python 后端）；测试用 `$PROJECT_DIR/.venv/bin/python -m pytest`，worktree 内需 `PYTHONPATH=$(pwd)/src`（editable install 可能指向别的 worktree）。
- **实现前先 EnterWorktree 隔离**（CLAUDE.md 要求）：主工作目录有并发会话会 `git reset --hard`，worktree 物理隔离保成果。完成后 commit 到 worktree 分支 -> 合回 main -> ExitWorktree。
- **每个 Task 一个 commit**，便于二分定位。
- **行号为 2026-07-10 核实值**，实现前用当前代码再确认一次（`grep -n`）。

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/sales_agent/llm/call_params.py` | `CallParams` dataclass + `load_call_params` + `get_call_params` | 新建 |
| `tests/unit/llm/__init__.py` | 包标识 | 新建（空） |
| `tests/unit/llm/test_call_params.py` | loader 单测 | 新建 |
| `config/llm_call_defaults.yaml` | 24 个调用点参数默认值 | 新建 |
| `src/sales_agent/core/config.py` | `Settings.llm_call_defaults_path` 字段 | 改 |
| `src/sales_agent/roles/stream_runner.py` | 启动调 `load_call_params` | 改 |
| 24 个调用点 .py | 字面量 -> `get_call_params` | 改 |
| `src/sales_agent/services/md_optimizer.py` | `.chat()` bug + 解析 + 参数接入 | 改 |
| `src/sales_agent/services/prompt_defaults.py` | 注册 6 个漏网 prompt | 改 |

---

### Task 1: CallParams loader 模块（TDD 基础）

**Files:**
- Create: `src/sales_agent/llm/__init__.py`（空）
- Create: `src/sales_agent/llm/call_params.py`
- Create: `tests/unit/llm/__init__.py`（空）
- Create: `tests/unit/llm/test_call_params.py`

**Interfaces:**
- Produces: `CallParams(temperature: float, max_tokens: int)`；`load_call_params(path: str) -> None`；`get_call_params(call_site: str) -> CallParams`。后续所有 Task 消费这些。

- [ ] **Step 1: 写失败测试**

`tests/unit/llm/test_call_params.py`：

```python
import pytest
import yaml

from sales_agent.llm.call_params import CallParams, load_call_params, get_call_params


def _write_yaml(tmp_path, data):
    p = tmp_path / "llm_call_defaults.yaml"
    p.write_text(yaml.safe_dump(data))
    return str(p)


def test_load_valid_yaml_and_get(tmp_path):
    path = _write_yaml(tmp_path, {"evidence_router": {"temperature": 0.0, "max_tokens": 500}})
    load_call_params(path)
    p = get_call_params("evidence_router")
    assert p == CallParams(temperature=0.0, max_tokens=500)


def test_missing_file_raises(tmp_path):
    with pytest.raises((FileNotFoundError, RuntimeError)):
        load_call_params(str(tmp_path / "nope.yaml"))


def test_missing_field_raises(tmp_path):
    path = _write_yaml(tmp_path, {"evidence_router": {"temperature": 0.0}})  # 缺 max_tokens
    with pytest.raises((ValueError, RuntimeError)):
        load_call_params(path)


def test_bad_temperature_range_raises(tmp_path):
    path = _write_yaml(tmp_path, {"x": {"temperature": 3.0, "max_tokens": 100}})
    with pytest.raises((ValueError, RuntimeError)):
        load_call_params(path)


def test_bad_max_tokens_raises(tmp_path):
    path = _write_yaml(tmp_path, {"x": {"temperature": 0.0, "max_tokens": 0}})
    with pytest.raises((ValueError, RuntimeError)):
        load_call_params(path)


def test_bad_type_raises(tmp_path):
    path = _write_yaml(tmp_path, {"x": {"temperature": "hot", "max_tokens": 100}})
    with pytest.raises((ValueError, RuntimeError, TypeError)):
        load_call_params(path)


def test_get_before_load_raises():
    # 用一个全新 call_site 触发「未加载」分支（依赖模块状态，置于末尾）
    load_call_params.__wrapped__ if hasattr(load_call_params, "__wrapped__") else None
    import sales_agent.llm.call_params as m
    m._PARAMS = None
    with pytest.raises(RuntimeError):
        get_call_params("evidence_router")


def test_unknown_call_site_raises(tmp_path):
    path = _write_yaml(tmp_path, {"evidence_router": {"temperature": 0.0, "max_tokens": 500}})
    load_call_params(path)
    with pytest.raises(KeyError):
        get_call_params("nonexistent_call_site")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd <worktree> && PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/llm/test_call_params.py -v`
Expected: FAIL（`ModuleNotFoundError: sales_agent.llm.call_params`）

- [ ] **Step 3: 写最小实现**

`src/sales_agent/llm/call_params.py`：

```python
"""LLM 调用参数（temperature/max_tokens）默认值加载器。

启动时 load_call_params() 读 config/llm_call_defaults.yaml 并校验，缓存进内存；
运行时 get_call_params(call_site) 零 IO 读取。仅开发者改，版本/回滚交 git。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_PARAMS: dict[str, "CallParams"] | None = None


@dataclass(frozen=True)
class CallParams:
    temperature: float
    max_tokens: int


def _coerce_and_validate(raw: dict) -> CallParams:
    if "temperature" not in raw or "max_tokens" not in raw:
        raise ValueError(f"调用点缺 temperature/max_tokens 字段: {raw}")
    try:
        temperature = float(raw["temperature"])
    except (TypeError, ValueError) as e:
        raise ValueError(f"temperature 不是数字: {raw['temperature']!r}") from e
    try:
        max_tokens = int(raw["max_tokens"])
    except (TypeError, ValueError) as e:
        raise ValueError(f"max_tokens 不是整数: {raw['max_tokens']!r}") from e
    if not (0.0 <= temperature <= 2.0):
        raise ValueError(f"temperature 超出 [0,2]: {temperature}")
    if max_tokens <= 0:
        raise ValueError(f"max_tokens 必须 > 0: {max_tokens}")
    return CallParams(temperature=temperature, max_tokens=max_tokens)


def load_call_params(path: str) -> None:
    """启动时调用：读 YAML、校验、缓存进模块级 _PARAMS。失败即抛。"""
    global _PARAMS
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"LLM 调用参数文件不存在: {path}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"LLM 调用参数文件顶层必须是 dict: {path}")
    params: dict[str, CallParams] = {}
    for key, val in raw.items():
        if not isinstance(val, dict):
            raise ValueError(f"调用点 {key} 的值必须是 dict: {val!r}")
        params[key] = _coerce_and_validate(val)
    _PARAMS = params


def get_call_params(call_site: str) -> CallParams:
    """运行时读内存 dict。未加载抛 RuntimeError，未知 key 抛 KeyError。"""
    if _PARAMS is None:
        raise RuntimeError("LLM 调用参数未加载，请先在启动序列调 load_call_params()")
    if call_site not in _PARAMS:
        raise KeyError(f"未知 LLM 调用点 '{call_site}'，请检查 config/llm_call_defaults.yaml")
    return _PARAMS[call_site]
```

`src/sales_agent/llm/__init__.py` 和 `tests/unit/llm/__init__.py`：空文件。

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/llm/test_call_params.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/llm/__init__.py src/sales_agent/llm/call_params.py tests/unit/llm/__init__.py tests/unit/llm/test_call_params.py
git commit -m "feat(llm): 新增 CallParams loader（YAML 加载+校验+内存缓存）"
```

---

### Task 2: settings 字段 + YAML 文件 + 启动接入

**Files:**
- Modify: `src/sales_agent/core/config.py`（`Settings` 类加字段，约第 254 行 `user_profile_memory` 之后）
- Create: `config/llm_call_defaults.yaml`
- Modify: `src/sales_agent/roles/stream_runner.py:41`（`logger.info("Online runtime initialized (stream runner)")` 之后插入）

**Interfaces:**
- Consumes: Task 1 的 `load_call_params`
- Produces: 进程启动后 `_PARAMS` 已填充；`get_settings().llm_call_defaults_path` 可用。

- [ ] **Step 1: Settings 加字段**

`src/sales_agent/core/config.py`，在 `Settings` 类内 `user_profile_memory: UserProfileMemoryConfig = UserProfileMemoryConfig()` 这行之后加：

```python
    user_profile_memory: UserProfileMemoryConfig = UserProfileMemoryConfig()

    # LLM 调用参数（temperature/max_tokens）默认值文件路径，开发者维护、git 管版本
    llm_call_defaults_path: str = "config/llm_call_defaults.yaml"
```

- [ ] **Step 2: 创建 YAML 文件**

`config/llm_call_defaults.yaml`（值 = 各调用点原字面量，2026-07-10 核实）：

```yaml
# LLM 调用参数默认值（开发者维护，走 git；版本/回滚由 git 负责）
# 改这里 = commit + push + 重建容器。值变更需跑 eval/deepeval_run.sh 回归。
# 仅 temperature + max_tokens 两个标量；response_format 等结构性参数留代码。

evidence_router:        # services/evidence_router.py
  temperature: 0.0
  max_tokens: 500
task_router:            # services/task_router.py
  temperature: 0.1
  max_tokens: 200
agent_executor:         # services/agent_executor.py
  temperature: 0.3
  max_tokens: 2000
context_resolver:       # services/context_resolver.py
  temperature: 0.0
  max_tokens: 500
topic_manager:          # services/topic_manager.py
  temperature: 0.0
  max_tokens: 500
topic_restore:          # services/topic_restore.py
  temperature: 0.0
  max_tokens: 500
risk_checker:           # services/risk_checker.py
  temperature: 0.1
  max_tokens: 300
memory_extractor:       # services/memory/extractor.py
  temperature: 0.0
  max_tokens: 700
md_optimizer:           # services/md_optimizer.py
  temperature: 0.3
  max_tokens: 4096
ontology_retrieval:     # ontology/retrieval_service.py
  temperature: 0.0
  max_tokens: 100
ontology_answer:        # ontology/answer_service.py
  temperature: 0.2
  max_tokens: 1600
ontology_entity_extraction:  # ontology/extractor.py（经 _generate_with_retry）
  temperature: 0.1
  max_tokens: 5000
ontology_fact_extraction:    # ontology/extractor.py（经 _generate_with_retry）
  temperature: 0.1
  max_tokens: 6000
ontology_graph:         # graph/retrieval/ontology_graph.py
  temperature: 0.0
  max_tokens: 100
web_fallback:           # graph/retrieval/web_fallback.py
  temperature: 0.2
  max_tokens: 800
scenario_matcher:       # scenarios/matcher.py
  temperature: 0.0
  max_tokens: 200
coach_small_win:        # graph/guided_flow/handlers/coach_flows.py（经 _llm_generate）
  temperature: 0.4
  max_tokens: 600
coach_block_split:      # graph/guided_flow/handlers/coach_flows.py（经 _llm_generate）
  temperature: 0.2
  max_tokens: 400
coach_reframe:          # graph/guided_flow/handlers/coach_flows.py（经 _llm_generate）
  temperature: 0.4
  max_tokens: 900
daily_evaluator:        # coach/daily_evaluator.py（另带 response_format，留代码）
  temperature: 0.1
  max_tokens: 2000
prompt_preview:         # api/routes/prompts.py
  temperature: 0.3
  max_tokens: 2000
health_ping:            # api/routes/health.py（原不传温度，显式补 0.3）
  temperature: 0.3
  max_tokens: 10
media_vision:           # integrations/dingtalk/media_adapter.py（走原生 SDK create）
  temperature: 0.1
  max_tokens: 800
media_audio:            # integrations/dingtalk/media_adapter.py（走原生 SDK create）
  temperature: 0.0
  max_tokens: 800
```

- [ ] **Step 3: 启动序列接入**

`src/sales_agent/roles/stream_runner.py`，在 `logger.info("Online runtime initialized (stream runner)")` 之后、`# 加载 TenantRuntime` 之前插入：

```python
    await initialize_online_runtime()
    logger.info("Online runtime initialized (stream runner)")

    # 加载 LLM 调用参数默认值（temperature/max_tokens）
    from sales_agent.llm.call_params import load_call_params
    load_call_params(settings.llm_call_defaults_path)
    logger.info("LLM call params loaded (stream runner)")

    # 加载 TenantRuntime
    runtime = get_tenant_runtime()
```

- [ ] **Step 4: 写加载验证测试**

追加到 `tests/unit/llm/test_call_params.py`：

```python
def test_load_real_defaults_file_all_keys():
    """加载真实 config/llm_call_defaults.yaml，验证全部 24 个 key 就位且值正确。"""
    from sales_agent.core.config import get_settings
    load_call_params(get_settings().llm_call_defaults_path)
    expected = {
        "evidence_router": (0.0, 500), "task_router": (0.1, 200),
        "agent_executor": (0.3, 2000), "context_resolver": (0.0, 500),
        "topic_manager": (0.0, 500), "topic_restore": (0.0, 500),
        "risk_checker": (0.1, 300), "memory_extractor": (0.0, 700),
        "md_optimizer": (0.3, 4096), "ontology_retrieval": (0.0, 100),
        "ontology_answer": (0.2, 1600), "ontology_entity_extraction": (0.1, 5000),
        "ontology_fact_extraction": (0.1, 6000), "ontology_graph": (0.0, 100),
        "web_fallback": (0.2, 800), "scenario_matcher": (0.0, 200),
        "coach_small_win": (0.4, 600), "coach_block_split": (0.2, 400),
        "coach_reframe": (0.4, 900), "daily_evaluator": (0.1, 2000),
        "prompt_preview": (0.3, 2000), "health_ping": (0.3, 10),
        "media_vision": (0.1, 800), "media_audio": (0.0, 800),
    }
    for key, (t, m) in expected.items():
        p = get_call_params(key)
        assert p.temperature == t, f"{key} temperature={p.temperature} != {t}"
        assert p.max_tokens == m, f"{key} max_tokens={p.max_tokens} != {m}"
```

- [ ] **Step 5: 跑测试确认通过**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/llm/test_call_params.py::test_load_real_defaults_file_all_keys -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add config/llm_call_defaults.yaml src/sales_agent/core/config.py src/sales_agent/roles/stream_runner.py tests/unit/llm/test_call_params.py
git commit -m "feat(llm): 新增 llm_call_defaults.yaml + Settings 字段 + 启动加载"
```

---

### Task 3: 路由层 3 点接入（第一批）

**Files:**
- Modify: `src/sales_agent/services/evidence_router.py:150`
- Modify: `src/sales_agent/services/task_router.py:429`
- Modify: `src/sales_agent/services/context_resolver.py:149`

**Interfaces:**
- Consumes: Task 1/2 的 `get_call_params`

每个调用点：在文件顶部 import 区加 `from sales_agent.llm.call_params import get_call_params`，把 `temperature=X, max_tokens=Y` 字面量替换为 `temperature=p.temperature, max_tokens=p.max_tokens`（`p = get_call_params("<key>")`）。

- [ ] **Step 1: evidence_router.py:150**

`grep -n "generate(" src/sales_agent/services/evidence_router.py` 确认行号。

before（约 :150）：
```python
        chat_model.generate(messages, temperature=0.0, max_tokens=500)
```
after：
```python
        p = get_call_params("evidence_router")
        chat_model.generate(messages, temperature=p.temperature, max_tokens=p.max_tokens)
```
文件顶部加 import。

- [ ] **Step 2: task_router.py:429**

`grep -n "generate(" src/sales_agent/services/task_router.py` 确认行号（`_llm_route` 内）。

before（约 :429）：
```python
        chat_model.generate(messages, temperature=0.1, max_tokens=200)
```
after：
```python
        p = get_call_params("task_router")
        chat_model.generate(messages, temperature=p.temperature, max_tokens=p.max_tokens)
```

- [ ] **Step 3: context_resolver.py:149**

`grep -n "generate(" src/sales_agent/services/context_resolver.py` 确认行号。

before（约 :149）：
```python
        chat_model.generate(messages, temperature=0.0, max_tokens=500)
```
after：
```python
        p = get_call_params("context_resolver")
        chat_model.generate(messages, temperature=p.temperature, max_tokens=p.max_tokens)
```

- [ ] **Step 4: 跑现有路由相关单测确认不破**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/services/ tests/unit/graph/ -k "router or context or evidence" -v 2>&1 | tail -30`
Expected: 全绿（或与改动前同等状态，无新增 fail）。

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/services/evidence_router.py src/sales_agent/services/task_router.py src/sales_agent/services/context_resolver.py
git commit -m "refactor(llm): 路由层 3 点接入 get_call_params"
```

---

### Task 4: 生成层 agent_executor 接入（第二批）

**Files:**
- Modify: `src/sales_agent/services/agent_executor.py:194`

- [ ] **Step 1: 接入**

`grep -n "generate(" src/sales_agent/services/agent_executor.py` 确认行号。

before（约 :194）：
```python
        chat_model.generate(messages, temperature=0.3, max_tokens=2000)
```
after：
```python
        p = get_call_params("agent_executor")
        chat_model.generate(messages, temperature=p.temperature, max_tokens=p.max_tokens)
```
顶部加 import。

- [ ] **Step 2: 跑生成相关单测**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/ -k "executor or generate or chat" -v 2>&1 | tail -30`
Expected: 无新增 fail。

- [ ] **Step 3: Commit**

```bash
git add src/sales_agent/services/agent_executor.py
git commit -m "refactor(llm): agent_executor 接入 get_call_params"
```

---

### Task 5: 话题/风险/记忆层接入（第三批）

**Files:**
- Modify: `src/sales_agent/services/topic_manager.py:571`
- Modify: `src/sales_agent/services/topic_restore.py:215`
- Modify: `src/sales_agent/services/risk_checker.py:281`
- Modify: `src/sales_agent/services/memory/extractor.py:37`

每个点同样模式：顶部 import + 字面量替换。key 见下。

- [ ] **Step 1: topic_manager.py:571**

`grep -n "generate(" src/sales_agent/services/topic_manager.py`。before：`chat_model.generate(messages, temperature=0.0, max_tokens=500)` -> after 用 `get_call_params("topic_manager")`。

- [ ] **Step 2: topic_restore.py:215**

`grep -n "generate(" src/sales_agent/services/topic_restore.py`。before：`temperature=0.0, max_tokens=500` -> `get_call_params("topic_restore")`。

- [ ] **Step 3: risk_checker.py:281**

`grep -n "generate(" src/sales_agent/services/risk_checker.py`。before：`temperature=0.1, max_tokens=300` -> `get_call_params("risk_checker")`。

- [ ] **Step 4: memory/extractor.py:37**

`grep -n "generate(" src/sales_agent/services/memory/extractor.py`。before：`chat_model.generate(messages, temperature=0.0, max_tokens=700)` -> `get_call_params("memory_extractor")`。

- [ ] **Step 5: 跑相关单测**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/memory/ tests/unit/services/ -k "topic or risk or memory" -v 2>&1 | tail -30`
Expected: 无新增 fail。

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/services/topic_manager.py src/sales_agent/services/topic_restore.py src/sales_agent/services/risk_checker.py src/sales_agent/services/memory/extractor.py
git commit -m "refactor(llm): 话题/风险/记忆层接入 get_call_params"
```

---

### Task 6: 知识抽取层接入（第四批）

**Files:**
- Modify: `src/sales_agent/ontology/retrieval_service.py:68`
- Modify: `src/sales_agent/ontology/answer_service.py:283`
- Modify: `src/sales_agent/ontology/extractor.py:129`（`_generate_with_retry` helper 内，被 :185/:276 调用）
- Modify: `src/sales_agent/graph/retrieval/ontology_graph.py:75`
- Modify: `src/sales_agent/graph/retrieval/web_fallback.py:71`

⚠️ `extractor.py` 两个调用点（实体抽取 :185、事实抽取 :276）经同一 helper `_generate_with_retry`（generate 在 :129），但两处 max_tokens 不同（5000 vs 6000）。**不能在 helper 内统一读一个 key**。方案：helper 增加参数 `call_site: str`，由两个业务点传入不同 key，helper 内 `get_call_params(call_site)`。

- [ ] **Step 1: ontology/retrieval_service.py:68**

`grep -n "generate(" src/sales_agent/ontology/retrieval_service.py`。该调用另带 `response_format={"type":"json_object"}`，**保留 response_format**，只替换 temperature/max_tokens。

before（约 :68）：
```python
        self.chat_model.generate(messages, temperature=0, max_tokens=100, response_format={"type": "json_object"})
```
after：
```python
        p = get_call_params("ontology_retrieval")
        self.chat_model.generate(messages, temperature=p.temperature, max_tokens=p.max_tokens, response_format={"type": "json_object"})
```

- [ ] **Step 2: ontology/answer_service.py:283**

before：`temperature=0.2, max_tokens=1600` -> `get_call_params("ontology_answer")`。

- [ ] **Step 3: ontology/extractor.py helper + 两业务点**

`grep -n "_generate_with_retry\|generate(" src/sales_agent/ontology/extractor.py`。在 `_generate_with_retry` 签名加 `call_site: str` 参数，内部 generate 用 `get_call_params(call_site)`：

helper 内 before（约 :129）：
```python
        chat_model.generate(messages, temperature=0.1, max_tokens=...)
```
helper 内 after：
```python
        p = get_call_params(call_site)
        chat_model.generate(messages, temperature=p.temperature, max_tokens=p.max_tokens)
```
实体抽取调用点（:185 附近）传 `call_site="ontology_entity_extraction"`，事实抽取（:276 附近）传 `call_site="ontology_fact_extraction"`。删除两业务点原本传给 helper 的 `temperature=`/`max_tokens=` 字面量。

- [ ] **Step 4: graph/retrieval/ontology_graph.py:75**

before：`temperature=0, max_tokens=100` -> `get_call_params("ontology_graph")`。

- [ ] **Step 5: graph/retrieval/web_fallback.py:71**

before：`temperature=0.2, max_tokens=800` -> `get_call_params("web_fallback")`。

- [ ] **Step 6: 跑知识抽取相关单测**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/ontology/ tests/unit/graph/ -v 2>&1 | tail -40`
Expected: 无新增 fail。

- [ ] **Step 7: Commit**

```bash
git add src/sales_agent/ontology/retrieval_service.py src/sales_agent/ontology/answer_service.py src/sales_agent/ontology/extractor.py src/sales_agent/graph/retrieval/ontology_graph.py src/sales_agent/graph/retrieval/web_fallback.py
git commit -m "refactor(llm): 知识抽取层接入 get_call_params（extractor helper 加 call_site 参数）"
```

---

### Task 7: 教练/场景层接入（第五批）

**Files:**
- Modify: `src/sales_agent/scenarios/matcher.py:54`
- Modify: `src/sales_agent/graph/guided_flow/handlers/coach_flows.py:183`（`_llm_generate` helper，被 :106/:204/:217 调用）
- Modify: `src/sales_agent/coach/daily_evaluator.py:607`

⚠️ `coach_flows.py` 三个调用点经同一 helper `_llm_generate`（generate 在 :183），三处参数不同（0.4/600、0.2/400、0.4/900）。同 Task 6 extractor 方案：helper 加 `call_site: str` 参数，业务点传不同 key。

- [ ] **Step 1: scenarios/matcher.py:54**

`grep -n "generate(" src/sales_agent/scenarios/matcher.py`。before：`temperature=0.0, max_tokens=200` -> `get_call_params("scenario_matcher")`。

- [ ] **Step 2: coach_flows.py helper + 三业务点**

`grep -n "_llm_generate\|generate(" src/sales_agent/graph/guided_flow/handlers/coach_flows.py`。helper `_llm_generate`（:176 定义、:183 generate）加 `call_site: str` 参数，内部用 `get_call_params(call_site)`；三业务点（:106 小赢卡传 `coach_small_win`、:204 破框拆解传 `coach_block_split`、:217 破框出卡传 `coach_reframe`）删除原 `temperature=`/`max_tokens=` 字面量。

- [ ] **Step 3: coach/daily_evaluator.py:607**

`grep -n "generate(" src/sales_agent/coach/daily_evaluator.py`。该调用另带 `response_format={"type":"json_object"}`，**保留**。

before（约 :607）：
```python
        chat_model.generate(messages, temperature=0.1, max_tokens=2000, response_format={"type": "json_object"})
```
after：
```python
        p = get_call_params("daily_evaluator")
        chat_model.generate(messages, temperature=p.temperature, max_tokens=p.max_tokens, response_format={"type": "json_object"})
```

- [ ] **Step 4: 跑教练/场景相关单测**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/coach/ tests/unit/ -k "scenario or coach" -v 2>&1 | tail -40`
Expected: 无新增 fail。

- [ ] **Step 5: Commit**

```bash
git add src/sales_agent/scenarios/matcher.py src/sales_agent/graph/guided_flow/handlers/coach_flows.py src/sales_agent/coach/daily_evaluator.py
git commit -m "refactor(llm): 教练/场景层接入 get_call_params（coach_flows helper 加 call_site）"
```

---

### Task 8: API/健康/媒体层接入（第六批）

**Files:**
- Modify: `src/sales_agent/api/routes/prompts.py:358`
- Modify: `src/sales_agent/api/routes/health.py:104`
- Modify: `src/sales_agent/integrations/dingtalk/media_adapter.py:155` 和 `:199`

⚠️ `media_adapter` 两点走原生 SDK `client.chat.completions.create(model=..., ...)`，非 `.generate()`。读 `get_call_params` 后传给 `create(temperature=..., max_tokens=...)`。
⚠️ `health.py:104` 原本不传 temperature（落回实例默认 0.3），改成显式读 `health_ping`（YAML 已补 0.3），消除「实例默认遮蔽」。

- [ ] **Step 1: api/routes/prompts.py:358**

`grep -n "generate(" src/sales_agent/api/routes/prompts.py`。before：`model_provider.chat.generate(messages, temperature=0.3, max_tokens=2000)` -> `get_call_params("prompt_preview")`。

- [ ] **Step 2: api/routes/health.py:104**

`grep -n "generate(" src/sales_agent/api/routes/health.py`。

before（约 :104，不传 temperature）：
```python
        runtime.model_provider.chat.generate(messages, max_tokens=10)
```
after：
```python
        p = get_call_params("health_ping")
        runtime.model_provider.chat.generate(messages, temperature=p.temperature, max_tokens=p.max_tokens)
```

- [ ] **Step 3: media_adapter.py:155（图片理解）**

`grep -n "chat.completions.create" src/sales_agent/integrations/dingtalk/media_adapter.py`。该调用 `client.chat.completions.create(model=self._config.vision_model, messages=..., temperature=0.1, max_tokens=800, ...)`。

before（约 :155，关键参数）：
```python
        response = client.chat.completions.create(
            model=self._config.vision_model,
            messages=messages,
            temperature=0.1,
            max_tokens=800,
            ...
        )
```
after：
```python
        p = get_call_params("media_vision")
        response = client.chat.completions.create(
            model=self._config.vision_model,
            messages=messages,
            temperature=p.temperature,
            max_tokens=p.max_tokens,
            ...
        )
```

- [ ] **Step 4: media_adapter.py:199（语音转写）**

同 Step 3 模式，`model=self._config.audio_model`，key 用 `media_audio`。

before：`temperature=0.0, max_tokens=800` -> `p = get_call_params("media_audio"); temperature=p.temperature, max_tokens=p.max_tokens`。

- [ ] **Step 5: 跑 API/媒体相关单测**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/dingtalk/ tests/integration/dingtalk/ -v 2>&1 | tail -30`
Expected: 无新增 fail。

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/api/routes/prompts.py src/sales_agent/api/routes/health.py src/sales_agent/integrations/dingtalk/media_adapter.py
git commit -m "refactor(llm): API/健康/媒体层接入 get_call_params（media 走原生 SDK create）"
```

---

### Task 9: md_optimizer `.chat()` bug 修复 + 参数接入

**Files:**
- Modify: `src/sales_agent/services/md_optimizer.py:146` 及附近 :155-159 response 解析

⚠️ 此调用点既有 `.chat()` bug（运行时 AttributeError），接入时一并修。`generate()` 是 **async** 且返回纯 `str`，而现有代码按 response 对象取 `.content`/`.choices[0].message.content`。须：① `await self.chat_model.generate(...)` ② 删除 response 对象解析，直接用返回字符串 ③ 参数读 `get_call_params("md_optimizer")`。先读该函数完整上下文确认调用是否已在 `async` 函数内。

- [ ] **Step 1: 读函数上下文确认 async**

Run: `grep -n "def \|\.chat(\|\.content\|\.choices" src/sales_agent/services/md_optimizer.py | head -30`
确认 :146 所在函数是否 `async def`。若非 async，需把该函数改成 `async def` 并追踪调用方加 `await`（调用方在 `knowledge_ingestor.py:260` 附近，核实已提）。

- [ ] **Step 2: 改调用 + 解析 + 参数**

before（约 :146 + :155-159，模式）：
```python
        response = self.chat_model.chat(messages, temperature=0.3, max_tokens=4096)
        # 按 response 对象解析
        if hasattr(response, "choices"):
            text = response.choices[0].message.content
        else:
            text = response.content
```
after：
```python
        p = get_call_params("md_optimizer")
        text = await self.chat_model.generate(messages, temperature=p.temperature, max_tokens=p.max_tokens)
```
（删除原 response 对象解析分支，`generate` 直接返回 `str`。）

若所在函数非 async：改为 `async def`，并到调用方（`knowledge_ingestor.py` 调用处）加 `await`。顶部加 `from sales_agent.llm.call_params import get_call_params`。

- [ ] **Step 3: 跑 md_optimizer / ingest 相关单测**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/ -k "optimizer or ingest or knowledge" -v 2>&1 | tail -30`
Expected: 无新增 fail；若原无测试，至少 `PYTHONPATH=$(pwd)/src .venv/bin/python -c "from sales_agent.services.md_optimizer import MdOptimizer; print('import ok')"` 通过。

- [ ] **Step 4: Commit**

```bash
git add src/sales_agent/services/md_optimizer.py
# 若改了调用方
git add src/sales_agent/services/knowledge_ingestor.py 2>/dev/null || true
git commit -m "fix(llm): md_optimizer 的 .chat() bug + 接入 get_call_params（generate 返回 str，同步改解析）"
```

---

### Task 10: 6 个漏网 prompt 补进 PromptRegistry

**Files:**
- Modify: `src/sales_agent/services/prompt_defaults.py`（`BUILTIN_PROMPTS` 加条目）
- Modify: 3 个调用点改走 resolve（`memory/extractor.py:32`、`topic_restore.py:201`、`scenarios/matcher.py:44`）
- Modify: `integrations/dingtalk/media_adapter.py`（3 个内联 prompt 抽常量 + 注册 + 调用点改走 resolve）

`BuiltinPrompt` 字段（已核实）：`(category, key, template, required_placeholders, description)`，字段名是 `template`（非 `template_text`），无 `task_type`。先读各 prompt 常量的占位符。

- [ ] **Step 1: 读 6 个 prompt 的占位符**

Run:
```bash
grep -n "{.*}" src/sales_agent/prompts/memory_extractor_prompt.py
grep -n "{.*}" src/sales_agent/prompts/topic_restore_resolver_prompt.py
grep -n "{.*}" src/sales_agent/scenarios/prompt.py
grep -n '"""' src/sales_agent/integrations/dingtalk/media_adapter.py | head
```
记录每个 prompt 的 `{placeholder}` 列表，作为 `required_placeholders`。

- [ ] **Step 2: 在 BUILTIN_PROMPTS 注册 6 个**

`src/sales_agent/services/prompt_defaults.py`，在对应分类构造函数里加 `BuiltinPrompt(...)` 条目。参考现有写法（位置参数）：

```python
from sales_agent.prompts.memory_extractor_prompt import MEMORY_EXTRACTOR_PROMPT
from sales_agent.prompts.topic_restore_resolver_prompt import TOPIC_RESTORE_RESOLVER_PROMPT
from sales_agent.scenarios.prompt import SCENARIO_MATCHER_PROMPT

# 在 _task_entries（或合适分类）里加：
BuiltinPrompt("task", "memory_extraction", MEMORY_EXTRACTOR_PROMPT, ("<按 Step1 占位符>",), "记忆候选提取"),
# 在 _system_router_risk_entries（router 类）里加：
BuiltinPrompt("router", "topic_restore_resolver", TOPIC_RESTORE_RESOLVER_PROMPT, ("<占位符>",), "话题恢复判断"),
BuiltinPrompt("router", "scenario_matcher", SCENARIO_MATCHER_PROMPT, ("questions_json",), "销售场景识别"),
```
media_adapter 3 个内联 prompt：先在 `media_adapter.py` 顶部抽成模块级常量（如 `MEDIA_VISION_SYSTEM_PROMPT` / `MEDIA_VISION_USER_PROMPT` / `MEDIA_AUDIO_TRANSCRIBE_PROMPT`），import 进 prompt_defaults.py 注册：

```python
from sales_agent.integrations.dingtalk.media_adapter import (
    MEDIA_VISION_SYSTEM_PROMPT, MEDIA_VISION_USER_PROMPT, MEDIA_AUDIO_TRANSCRIBE_PROMPT,
)
BuiltinPrompt("system", "media_vision_system", MEDIA_VISION_SYSTEM_PROMPT, (), "图片理解 system"),
BuiltinPrompt("task", "media_vision_user", MEDIA_VISION_USER_PROMPT, (), "图片理解 user"),
BuiltinPrompt("task", "media_audio_transcribe", MEDIA_AUDIO_TRANSCRIBE_PROMPT, (), "语音转写"),
```
（category 按内容定，占位符按 Step1 实际填。）

- [ ] **Step 3: 调用点改走 resolve（可选增强）**

对 `memory/extractor.py:32`、`topic_restore.py:201`、`scenarios/matcher.py:44`：若调用点已有 db/tenant 上下文，改走 `PromptRegistry(...).resolve_prompt(category, key, ...)` 替代直接 import 常量；若无上下文，保持直接 import 常量（注册仅为获得 DB 覆盖路径，不强制改调用点）。media_adapter 调用点无 db/tenant 上下文，保持直接用常量。

> 判断：本步「改调用点走 resolve」是增强项，若调用点无 db/tenant 上下文则跳过（注册本身已让 prompt 进三层解析的兜底层，DB 有覆盖时未来接入 resolve 才生效）。最小改动优先。

- [ ] **Step 4: 写注册验证测试**

`tests/unit/services/test_prompt_defaults.py`（若已有则追加）：

```python
def test_builtin_prompts_contains_new_six():
    from sales_agent.services.prompt_defaults import get_builtin
    keys = [
        ("task", "memory_extraction"),
        ("router", "topic_restore_resolver"),
        ("router", "scenario_matcher"),
        ("system", "media_vision_system"),
        ("task", "media_vision_user"),
        ("task", "media_audio_transcribe"),
    ]
    for category, key in keys:
        p = get_builtin(category, key)
        assert p is not None, f"BUILTIN_PROMPTS 缺 {category}/{key}"
        assert p.template  # 非空
```

- [ ] **Step 5: 跑测试**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/services/test_prompt_defaults.py -v 2>&1 | tail -20`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add src/sales_agent/services/prompt_defaults.py src/sales_agent/integrations/dingtalk/media_adapter.py src/sales_agent/services/memory/extractor.py src/sales_agent/services/topic_restore.py src/sales_agent/scenarios/matcher.py tests/unit/services/test_prompt_defaults.py
git commit -m "feat(prompt): 补 6 个漏网 prompt 进 BUILTIN_PROMPTS（memory/topic_restore/scenario/media×3）"
```

---

### Task 11: 全量回归 + 合回 main

**Files:** 无代码改动，纯验证 + 合并。

- [ ] **Step 1: 全量 unit 测试**

Run: `PYTHONPATH=$(pwd)/src .venv/bin/python -m pytest tests/unit/ -v 2>&1 | tail -40`
Expected: 无新增 fail（与 main 基线对比）。

- [ ] **Step 2: deepeval 回归（值不变验证）**

Run: `cd /root/code/sales-agent && bash eval/deepeval_run.sh`（按脚本默认参数，租户 fuduoduo）
对比 `eval/results/fuduoduo/deepeval_*.html` 改动前后报告：avg Faithfulness / Answer Relevancy / Task Completion 无系统性下降（仅允许温度随机微小抖动，±0.02 内）。
若显著下降 -> 某调用点值搬错，回该 Task 用 `git diff` 核对 YAML 值 vs 原字面量。

- [ ] **Step 3: 部署到 dev 并查 stream 日志（生产入口验证）**

按 [[dev-deploy-verify-flow]]：dev push 只重建本机 prod2（taishan+taishankaifa2）。push 后：
```bash
docker logs --tail 50 taishan-stream 2>&1 | grep -iE "LLM call params loaded|Online runtime initialized|error|traceback"
docker logs --tail 50 taishankaifa2-stream 2>&1 | grep -iE "LLM call params loaded|Online runtime initialized|error|traceback"
```
Expected: 看到 `LLM call params loaded (stream runner)` + `Online runtime initialized`，无 `KeyError`/`RuntimeError`/`AttributeError`/crash。

- [ ] **Step 4: 记录 changelog**

`changelog/2026-07-10.md`（不存在则新建），新增条目：改动对象=LLM 调用参数配置化；类型=重构+bug 修复；影响范围=全部 LLM 调用点（22~24）+ md_optimizer + 6 个漏网 prompt；明细=YAML 集中 + loader + 启动加载 + 调用点接入 + .chat() bug 修复 + prompt 注册；原因=参数硬编码散落、无法集中管理、md_optimizer latent crash。

- [ ] **Step 5: 更新 README 产品文档对照**

检查 `README.md`「产品文档对照」节，若涉及 LLM 调参/配置说明，更新条目/日期。

- [ ] **Step 6: 合回 main + ExitWorktree**

```bash
# 在 worktree 内确认全部 commit
git log --oneline main..HEAD
# 合回 main
git checkout main && git merge --no-ff <worktree-branch> -m "merge: LLM 调用参数配置化"
```
然后调 ExitWorktree（action: remove）退出 worktree。

- [ ] **Step 7: 部署 main 三台 force-recreate + 最终 stream 验证**

main push 后按 [[dev-deploy-verify-flow]]：三台应用容器 force-recreate（postgres/neo4j 隔离）。再查三台 stream 日志确认 `LLM call params loaded` + 无 crash。

---

## Self-Review

**1. Spec 覆盖**：
- §2 目标「YAML 收拢」-> Task 1/2 ✅
- §2「补 6 个漏网 prompt」-> Task 10 ✅
- §2「修 md_optimizer bug」-> Task 9 ✅
- §5.4 调用点接入（含 media_adapter SDK / 间接调用 helper / response_format 保留 / health 显式补温）-> Task 3-9 ✅
- §5.5 6 个 prompt 注册 -> Task 10 ✅
- §5.6 md_optimizer .chat + 解析 -> Task 9 ✅
- §7 24 个 key -> Task 2 YAML 全列 ✅
- §8 单测 + deepeval 回归 + stream 日志 + 渐进分批 -> Task 1/2/11 + 各 Task 间分批 commit ✅
- §9 不做 DB migration -> Global Constraints + 全程无 migration ✅

**2. 占位符扫描**：Task 10 Step 2 有 `<按 Step1 占位符>` -- 这是依赖 Step1 grep 结果动态填的占位符，非偷懒；但为消除歧义，实现者须先跑 Step1 grep 再填。其余无 TBD/TODO。✅（可接受，因占位符内容由前一 step 产出）

**3. 类型一致性**：`CallParams(temperature: float, max_tokens: int)`、`get_call_params(str) -> CallParams`、`load_call_params(str) -> None` 在 Task 1 定义，Task 2-9 消费一致；`p.temperature`/`p.max_tokens` 全程统一。`_llm_generate`/`_generate_with_retry` helper 加 `call_site: str` 参数在 Task 6/7 一致。✅
