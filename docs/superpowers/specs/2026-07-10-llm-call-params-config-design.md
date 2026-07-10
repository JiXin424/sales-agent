# LLM 调用参数配置化 - 设计文档

- **日期**：2026-07-10
- **状态**：已通过设计评审，待实现
- **作者**：gitea-admin（brainstorming 产出）
- **相关**：PromptRegistry（参数版解耦）、生产路由架构、eval/deepeval 回归

> 行号为设计调研时（2026-07-10）的近似值，实现前请用当前代码逐点核对。

## 1. 背景与问题

仓库内所有 LLM 调用的 `temperature` / `max_tokens` 都是**硬编码字面量**，散落在约 22~24 个调用点。现状（子代理已核实，逐条带证据）：

- **`temperature`**：在全部调用点都是硬编码字面量（evidence_router 0.0、task_router 0.1、agent_executor 0.3、coach_flows 0.2/0.4……）。虽然存在「半套」解耦机制（`ModelConfig.temperature`、models.json、shared 模式 DB config_json），但被 per-call 字面量**完全遮蔽**——实例默认温度实际只在 `health.py:104` 诊断 ping 一处生效，等于摆设。
- **`max_tokens`**：**零解耦**。不在 `ModelConfig`、不在 models.json、不在任何 DB 表、不在任何 env 变量里，纯粹散落成几十个魔法数字（100/200/300/500/600/700/800/900/1600/2000/4096/5000/6000……）。
- **`prompt`**：已较大程度解耦（DB 两表 `prompt_versions` + `agent_prompt_sets` + REST API `/tenants/{id}/prompts/*` + 三层解析 Agent 绑定->租户 active->py 兜底），但**有 7 个遗漏点**绕过注册表、仍只在 py 里（详见 §6）。
- **附带 bug**：`services/md_optimizer.py:146` 调 `self.chat_model.chat(...)`，但 `ChatModel` / `OpenAICompatibleChat` 只有 `generate` / `stream_generate`，运行时会抛 `AttributeError`（知识库 ingest 链路上的 latent crash）。

> 用户最初设想「prompt 在 py 里、迁去 YAML、做 DB 快照」。经核实纠正：prompt 主要在 DB（py 仅兜底），迁去 YAML 反而降级；版本/回滚/防误删由 git 全包，DB 快照与 git 重复。最终决定：**参数收进 YAML，仅开发者改，不做 DB、不做后台 API、不做前端**。

## 2. 目标与非目标

### 目标
- 把 22~24 处硬编码 `temperature` / `max_tokens` 收进**一个 YAML 文件**，按调用点 key 聚合。
- runtime 启动时加载并校验 YAML，缓存进内存；运行时零 IO 直接读内存 dict。
- 改参数 = 改 YAML -> commit -> push -> 重建容器（复用现有 main push 三台 force-recreate 流程）。版本管理、回滚、防误删全交 git。
- 补齐 7 个漏网 prompt 进 PromptRegistry（修复 prompt 解耦漏洞）。
- 修 `md_optimizer.py` 的 `.chat()` bug。

### 非目标
- **不做 DB**：不建表、不写 Alembic migration、不存快照、不做版本/草稿/激活机制。
- **不建后台 API / 不建前端**：参数只给开发者改。
- **不动现有 prompt 系统**：`prompt_versions` / `agent_prompt_sets` 表、`/tenants/{id}/prompts/*` API 保持原样，那是另一套已上线在用的系统。
- **不做按租户覆盖**：参数全局唯一（之前因「不做 DB」自动简化，不再需要 tenant_id / db 上下文）。
- **不纳入 `top_p` / `presence_penalty` / `frequency_penalty` / `repetition_penalty`**：全仓库零使用，YAGNI。
- **不纳入 `response_format` 等结构性参数**：如 `daily_evaluator.py:607` 的 `response_format={"type":"json_object"}` 留在代码里（它是结构性约束、非调参 knob）。YAML 只管 `temperature` / `max_tokens` 两个标量。
- **不纳入模型名**：模型名解耦（models.json -> env -> DB config）已完成，不重复造。

### 成功标准
- 重构后**所有调用点的 `temperature` / `max_tokens` 值与重构前完全一致**（YAML 填入的就是原字面量）——行为零变化的纯结构重构。
- 改一处参数只需改 YAML 一个 key，不再碰 .py。
- 部署后 stream 容器（`docker logs <tenant>-stream`）连上 Online Graph、无 crash、无 `KeyError` / `RuntimeError`。
- 跑 `eval/deepeval_run.sh` 对比改动前后报告，分数无系统性变化（仅允许温度随机带来的微小抖动）。

## 3. 方案选型

brainstorming 中考虑过的候选：

- **方案 0（采用）**：纯 YAML + 内存缓存，git 管版本。最简，零新基建，与「只给开发者改 + git 管版本回滚」目标完全匹配。
- 方案 1（已否决）：YAML 默认 + DB 覆盖层（混合，复刻 prompt 模式）。用户最初倾向此方案，但因「不要运营改、不做后台 API」后否决——没有运营改，DB 覆盖层无消费者，纯增基建无收益。
- 方案 2（已否决）：YAML + DB 快照（热回滚）。用户判定「快速回滚」走 git revert + 重建容器即可接受，DB 快照与 git 重复，否决。

**采用方案 0。**

## 4. 架构与数据流

```
启动时（一次性，与 initialize_online_runtime() 一起）：
  config/llm_call_defaults.yaml  ──load_call_params()──▶  模块级内存 dict _PARAMS
                                                              （含类型/范围校验，失败即崩）

运行时（每个调用点）：
  evidence_router 想调 LLM
        │
        ▼
  get_call_params("evidence_router")   ← 纯内存 dict 查找，零 IO
        │
        ▼
  返回 CallParams(temperature=0.0, max_tokens=500)
        │
        ▼
  self._model.generate(messages, temperature=0.0, max_tokens=500)
```

无 DB、无两层解析、无 tenant。加载时机：进程启动序列里（`stream_runner.py`），与 `initialize_online_runtime()` 同阶段调用一次 `load_call_params()`。YAML 打进 Docker 镜像（COPY），生产机不存在「手滑删文件」问题。

### 关键决定

| 决定 | 选择 | 理由 |
|---|---|---|
| 默认值层格式 | `config/llm_call_defaults.yaml`，按调用点 key 聚合 | 用户选定；标量 knob 用 YAML 干净；prompt 默认值继续留 py（多行带占位符模板，YAML 难写）——按数据形状分工，不算两套乱机制 |
| 覆盖层 | **不做** | 不要运营改、不做后台 API；版本/回滚交 git |
| 粒度 | 按**调用点 key**（非按 model） | router 200 / 生成 2000 / 抽取 6000，必须按调用点分；同文件多处不同参数用不同 key |
| 参数范围 | 只 `temperature` + `max_tokens` | top_p 等全仓库零用，YAGNI；response_format 等结构性参数留代码 |
| 校验哲学 | fail loud，尽早暴露 | 缺文件/缺 key/类型错/温度超范围/max_tokens≤0 -> 启动即崩；运行时未知 key -> KeyError |

## 5. 组件与改动面

### 5.1 新增：YAML 文件 `config/llm_call_defaults.yaml`

每个调用点一个 key，含 `temperature` + `max_tokens`。同一 .py 多处不同参数用不同 key。完整清单见 §7（实现前由子代理逐行核实当前值与行号，spec 内值为调研时近似值）。

### 5.2 新增：loader 模块 `src/sales_agent/llm/call_params.py`

职责：
- `CallParams` frozen dataclass（`temperature: float` / `max_tokens: int`）。
- `load_call_params(path: str) -> None`：启动时读 YAML、校验、缓存进模块级 `_PARAMS`。
- `get_call_params(call_site: str) -> CallParams`：运行时读内存 dict，未加载抛 `RuntimeError`，未知 key 抛 `KeyError`。
- 校验规则：`0 ≤ temperature ≤ 2`；`max_tokens > 0`；类型必须可转 float/int；缺任一字段失败。

### 5.3 新增：启动接入

在 `stream_runner.py` 启动序列里，`initialize_online_runtime()` 之后调一次 `load_call_params(settings.llm_call_defaults_path)`。配置路径走 `get_settings()`（与 models.json 同风格）。

### 5.4 改动：22~24 个调用点

每处把写死的字面量换成 `get_call_params("<call_site>")`。before/after 示例：

```python
# 改之前（evidence_router.py:150，写死）
self._model.generate(messages, temperature=0.0, max_tokens=500)

# 改之后
from sales_agent.llm.call_params import get_call_params
p = get_call_params("evidence_router")
self._model.generate(messages, temperature=p.temperature, max_tokens=p.max_tokens)
```

特殊点处理：
- `health.py:104` 原不传温度（落回实例默认 0.3）-> 改成显式给 `health_ping: {temperature: 0.3, max_tokens: 10}`，顺手消除「实例默认遮蔽」困惑。
- `daily_evaluator.py:607` 的 `response_format={"type":"json_object"}` **保留在代码**，只把 temperature/max_tokens 抽出。
- `media_adapter.py:155/199` 用独立 vision/audio model -> 同样进 YAML（key `media_vision` / `media_audio`）。

### 5.5 小修：7 个漏网 prompt 补进注册表

在 `src/sales_agent/services/prompt_defaults.py` 的 `BUILTIN_PROMPTS` 注册以下 prompt，使其获得 DB 覆盖路径（三层解析）：
- `MEMORY_EXTRACTOR_PROMPT`（`prompts/memory_extractor_prompt.py`，现 `memory/extractor.py:32` 直接用、未注册）
- `TOPIC_RESTORE_RESOLVER_PROMPT`（`prompts/topic_restore_resolver_prompt.py`，`topic_restore.py:201` 唯一来源）
- `SCENARIO_MATCHER_PROMPT`（`scenarios/prompt.py`，`scenarios/matcher.py:44` 未注册）
- 重复的 `_ENTITY_EXTRACTION_PROMPT`（`graph/retrieval/ontology_graph.py:23` 是 `ontology/retrieval_service.py:19` 的重复副本，需统一）
- `media_adapter.py:160/165/205` 三个函数内联 prompt（纯字面量，对注册表不可见）

### 5.6 小修：md_optimizer `.chat()` bug

`services/md_optimizer.py:146` 的 `self.chat_model.chat(...)` 改为 `self.chat_model.generate(...)`（`ChatModel` 接口只有 `generate` / `stream_generate`）。

> §5.5 / §5.6 可独立成 commit，也可并入本设计主体。实现计划里标清边界。

## 6. 错误处理

| 场景 | 行为 |
|---|---|
| YAML 文件不存在 | 启动失败，明确报错（参数是核心依赖，不能缺）|
| YAML 缺某个 key | 不报错--loader 只校验「文件中已写的 key 合法」，不要求 24 个 key 全在（见 §8 渐进接入）|
| 值类型错 / `temperature` 超 [0,2] / `max_tokens ≤ 0` | 启动失败 |
| 运行时请求不存在的 call_site | `KeyError`，fail loud（编程 bug，越早炸越好）|
| 运行时 `get_call_params` 在 `load_call_params` 之前调 | `RuntimeError` |
| YAML 解析成功 | 缓存进内存，之后零 IO |

## 7. 调用点清单（YAML key 全表）

实现前由子代理逐行核实当前值与行号；下表为调研时近似值。

| key | 文件:行 | temperature | max_tokens |
|---|---|---|---|
| `evidence_router` | services/evidence_router.py:150 | 0.0 | 500 |
| `task_router` | services/task_router.py:429 | 0.1 | 200 |
| `agent_executor` | services/agent_executor.py:194 | 0.3 | 2000 |
| `context_resolver` | services/context_resolver.py:149 | 0.0 | 500 |
| `topic_manager` | services/topic_manager.py:571 | 0.0 | 500 |
| `topic_restore` | services/topic_restore.py:215 | 0.0 | 500 |
| `risk_checker` | services/risk_checker.py:281 | 0.1 | 300 |
| `memory_extractor` | services/memory/extractor.py:37 | 0.0 | 700 |
| `md_optimizer` | services/md_optimizer.py:146 | 0.3 | 4096 |
| `ontology_retrieval` | ontology/retrieval_service.py:68 | 0.0 | 100 |
| `ontology_answer` | ontology/answer_service.py:283 | 0.2 | 1600 |
| `ontology_entity_extraction` | ontology/extractor.py:185 | 0.1 | 5000 |
| `ontology_fact_extraction` | ontology/extractor.py:276 | 0.1 | 6000 |
| `ontology_graph` | graph/retrieval/ontology_graph.py:75 | 0.0 | 100 |
| `web_fallback` | graph/retrieval/web_fallback.py:71 | 0.2 | 800 |
| `scenario_matcher` | scenarios/matcher.py:54 | 0.0 | 200 |
| `coach_small_win` | graph/guided_flow/handlers/coach_flows.py:106 | 0.4 | 600 |
| `coach_block_split` | graph/guided_flow/handlers/coach_flows.py:204 | 0.2 | 400 |
| `coach_reframe` | graph/guided_flow/handlers/coach_flows.py:217 | 0.4 | 900 |
| `daily_evaluator` | coach/daily_evaluator.py:607 | 0.1 | 2000 |
| `prompt_preview` | api/routes/prompts.py:358 | 0.3 | 2000 |
| `health_ping` | api/routes/health.py:104 | 0.3 | 10 |
| `media_vision` | integrations/dingtalk/media_adapter.py:155 | 0.1 | 800 |
| `media_audio` | integrations/dingtalk/media_adapter.py:199 | 0.0 | 800 |

## 8. 测试与回归保障

### 单元测试（新增 `tests/llm/test_call_params.py`）
- loader 合法 YAML 加载成功，`get_call_params` 返回正确值。
- 缺文件 -> 启动报错。
- 已写的 key 缺字段 / 类型错 / 温度超范围 / max_tokens≤0 -> 启动报错。
- 未加载就调 `get_call_params` -> `RuntimeError`。
- 未知 call_site -> `KeyError`。
- 每个已接入 key：`get_call_params("<key>")` 返回值 = 原硬编码字面量（值不变断言）。

### 回归保障（证明行为没变）
- 重构是**值不变的搬运**：YAML 填入的就是原字面量。
- 走生产入口验证（CLAUDE.md 要求）：跑 `eval/deepeval_run.sh`，对比改动前后 `eval/results/fuduoduo/deepeval_*.html` 报告，分数无系统性变化（仅允许温度随机微小抖动）。
- 部署后查 stream 容器日志（`docker logs <tenant>-stream`）确认连上 Online Graph、无 crash、无 `KeyError` / `RuntimeError`。

### 渐进接入策略（避免一次改 24 处出事）
- loader + YAML 先建好，全部 key 就位（值 = 原字面量）。
- 调用点**分批改**：先路由层 3 个（evidence_router / task_router / context_resolver）-> 跑评测验证不退化 -> 再生成层（agent_executor）-> 再其余。每批一个 commit，便于二分定位。
- 未接入的调用点：其 key 可暂不在 YAML（避免校验「多余 key」误报；loader 只校验「存在的 key 合法」，不校验「每个 key 都有调用点」——key 不会被消费是开发期自查项）。

## 9. 明确不做 DB migration

本次不碰数据库，**无 Alembic migration**。特此写明，免得后续以为要建表。

## 10. 后续

本设计审阅通过后，转 writing-plans 出实现计划。
