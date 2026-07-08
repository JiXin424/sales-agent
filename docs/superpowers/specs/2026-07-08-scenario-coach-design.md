# 场景教练（Scenario Coach）设计

**Status:** Draft pending review
**日期:** 2026-07-08
**数据源:** `销冠智慧教练手册_第6-25页.md`（福多多 · 销售赋能 · 2026 年 4 月版）

## 1. 目标

针对单租户实例，新增"场景教练"能力：根据用户提问，识别其是否与预设的 6 个销售场景（共 11 个代表性问题）中某一个高度重合；若命中，直接返回该问题对应的预设答案（含文末来源归属《销冠智慧教练手册·2026年4月版》），跳过 RAG/生成；若未命中，原样放行正常 Online Graph 管线，行为零变化。

三个硬约束：

1. **解耦**：功能自包含，可整体新增/移除而不触碰现有节点内部。
2. **实例级开关**：本机两个 agent 实例，可给其一配备、另一个不添加。
3. **快速迁移**：可快速部署到另一台服务器（CI/CD, prod3）的某个实例。

## 2. 范围

### In scope

- 一个自包含包 `src/sales_agent/scenarios/`，承载场景数据加载、匹配、节点逻辑。
- 场景数据以 md 文件入库，随镜像分发，无需数据库、无需 Alembic migration、无需 seed。
- LLM 分类匹配（11 选 1 + none），confidence 阈值判定。
- Online Graph 早插节点，命中即短路返回预设 `answer_dict`，文末带手册来源。
- 复用既有 `*_ENABLED` env flag 模式实现实例级开关与跨机部署。
- 单测 + fixture（11 代表问题 + 同义改写）+ 集成测试。

### Out of scope（YAGNI，留作升级路径）

- 向量余弦预筛 / 混合匹配（方案 B/C）——见 §12 升级路径。
- DB 表 / 运行时改答案 / admin UI 编辑场景——当前答案随代码提交，改答案=改 md+重新部署。
- 多套场景集 / 按租户定制内容——单租户、内容随镜像统一。
- per-agent（实例内多 agent 差异化）粒度——当前需求是 per-instance。

## 3. 设计前提（已确认决策）

1. **匹配粒度 = 问题级**：用户问题匹配到 Q01–Q11 中的某一个，返回该 Q 的预设答案；6 场景仅作分组。手册实际结构为 6 场景 / 11 问题（场景二含 Q03/04/05 三个问题，其余场景 1–2 个）。
2. **答案格式**：命中的 `ScenarioQuestion` 映射成既有 `answer_dict {summary, sections}` 契约，钉钉渲染器无需改动。
3. **不匹配放行**：未命中或功能关闭时，行为与现状完全一致。
4. **开关**：env flag（`SCENARIO_COACH_ENABLED`），默认关，走 `secrets/<tenant>.env`，复用成熟的 `*_ENABLED` 模式。
5. **匹配策略**：方案 A（LLM 分类），temp=0，复用 `evidence_router` 形态（`parse_model_json` + pydantic + retry + 正则 fallback）。

## 4. 架构与解耦

新增自包含包 `src/sales_agent/scenarios/`，目录结构：

```
src/sales_agent/scenarios/
  __init__.py
  data/
    销冠智慧教练手册.md          # 唯一数据源（用户提供的 md，原样入库）
  loader.py                       # md → ScenarioRegistry（单例缓存）
  models.py                       # Scenario / ScenarioQuestion / AnswerSection
  matcher.py                      # ScenarioMatcher.match() → ScenarioMatchDecision
  prompt.py                       # LLM 分类 prompt（system + few-shot + 11 题枚举）
  node.py                         # graph 节点薄封装（调 matcher，命中设 answer_dict）
```

对外依赖仅三样，均为既有设施：

- `settings.scenario_coach.enabled` / `.confidence_threshold`（§9 新增的 config 子节）。
- `chat_model`：经 LangGraph `runtime.context["chat_model"]` 取，与 `evidence_routing` 节点同源（`graph/online/nodes.py:486`）。
- 图状态契约 `answer_dict {summary, sections, sources}` + `response_kind`（既有，`fast_commands` 已用同样契约）。

**解耦保证**：功能关闭 = `route_online_message` 边直接走原路径，根本不进入新节点，零行为变化、零开销。移除功能 = 删 `scenarios/` 包 + 一处节点注册 + 一处边分支 + 一段 config，不触及任何现有节点内部逻辑。

## 5. 场景数据（配置文件入库）

### 5.1 数据源

用户提供的 md 放到 `src/sales_agent/scenarios/data/销冠智慧教练手册.md`，**原样入库作为唯一数据源**。日后改答案 = 改这个 md + 重新部署生效。内容随镜像打进 `src/`，任何开了 flag 的实例自动拿到同样 6 场景 11 问题——这是"快速迁移、无 DB"的核心。

### 5.2 数据模型（`models.py`）

```
Scenario:
  id: str            # "S1".."S6"
  name: str          # "客户嫌贵 / 比价"
  subtitle: str      # "价格异议、说别家便宜"
  questions: list[ScenarioQuestion]

ScenarioQuestion:
  id: str            # "Q01".."Q11"
  text: str          # 代表问题原文，如"友商配赠更高、价格更低……我们凭什么赢？"
  tag: str           # "需判断" | "流程型"   （取自问题标题下 blockquote）
  answer_summary: str # 一句话摘要，供 answer_dict.summary
  answer_sections: list[AnswerSection]

AnswerSection:
  heading: str       # "价值还没立住" | "步骤 1：先在微信里建立价值感" …
  body: str          # 方向/话术/做法 的拼接文本
```

### 5.3 Loader（`loader.py`）

- 进程级单例 `ScenarioRegistry`，首次访问时解析 md 并缓存；解析结果不变则不重复解析。
- 解析锚点用两级标题：`## 场景X`（场景边界）、`### QNN`（问题边界）。问题标题到下一个 `###` / `##` / 文末 `---` 之间的内容为该问题答案体。该规则对两种内部写法（"需判断 · 先判断再应对" vs "流程型 · 照步骤走"）均鲁棒。
- 答案体内部按 `####` 或 `**步骤 N` 切成 `AnswerSection`；无子标题时整段作为一个 section。
- **来源名提取**：从 md 顶部（标题行 `# 销冠智慧教练手册（第 6–25 页）` + 副标题 `第一版 · 2026 年 4 月版`）解析出 `source_name = "销冠智慧教练手册·2026年4月版"`，存入 registry metadata；解析失败用此默认值。该名用于命中时文末来源归属（§8）。
- **失败兜底**：加载或解析失败 → 记 error 日志、registry 标记不可用、功能自动降级为关闭（绝不 crash stream），其余实例不受影响。

## 6. 匹配逻辑（LLM 分类）

### 6.1 接口

`ScenarioMatcher.match(user_message: str) -> ScenarioMatchDecision`

```
ScenarioMatchDecision (pydantic):
  matched_question_id: str | None   # "Q01".."Q11" 或 None
  confidence: float                  # 0.0–1.0
  reason: str                        # 简述判定理由（日志/可观测用）
```

### 6.2 Prompt（`prompt.py`）

- **system**：说明任务——判断用户问题是否与某预设销售场景问题"意图高度重合"；强调看意图而非字面，同义改写应命中，泛泛/无关问题应返回 none。
- **few-shot**：2 例（一例明确匹配 + 标 confidence 高、一例明确 none），复用 `evidence_router_prompt.py:52-62` 的 few-shot 形态。
- **枚举**：注入当前 registry 的 11 个 `{id, text}`。
- **user**：用户消息。
- **输出**：JSON `{matched_question_id, confidence, reason}`，temp=0。

### 6.3 解析与判定

- 复用 `parse_model_json(raw, ScenarioMatchDecision)`（`services/structured_router_output.py:20`），自带 json-repair + 2 次 retry + 正则兜底，与 `evidence_router.py:148` 一致。
- `chat_model` 从 `runtime.context["chat_model"]` 取，调用形态同 `services/evidence_router.py:150`（`chat_model.generate(messages=..., temperature=0, response_format=...)`）。
- **命中判定**：`matched_question_id is not None 且 confidence ≥ settings.scenario_coach.confidence_threshold`（默认 0.8，env 可调）。否则视为 none。

### 6.4 Fail-open

LLM 调用失败 / 超时 / JSON 解析彻底失败 → `matched=None`，放行正常管线，记 warning。**绝不阻断对话**——本功能是增强，不是关键路径。

## 7. 图接线（Online Graph 早插，全路径拦截）

插入位置：Online Graph 中 `normalize_turn` 之后、`context_resolution` / `direct_evidence_routing` 之前——统一拦截 `chat` 与 `direct_chat` 两条路径，在任何 LLM 调用（context resolver / evidence router / generation）之前完成，命中即省成本省延迟。

### 7.1 新节点

`scenario_coach`（`graph/online/nodes/scenario_coach.py`，薄封装）：

1. 功能关闭 / registry 不可用 / 消息为空或非文本 → 直接透传，不匹配。
2. 调 `ScenarioMatcher.match(message)`。
3. 命中：把命中的 `ScenarioQuestion` 映射成 `answer_dict`（§7.2），设 `response_kind="scenario"`，记命中日志（question_id / confidence）。
4. 未命中：不改 state，仅记 debug 日志。

### 7.2 answer_dict 映射

```
answer_dict = {
  "summary": question.answer_summary,
  "sections": [{"heading": s.heading, "body": s.body} for s in question.answer_sections],
  "sources": [ <手册来源条目, 见 §8> ],
}
response_kind = "scenario"
```

契约与 `fast_commands`（`chat/nodes/fast_commands.py:50`）一致，下游钉钉渲染器（`integrations/dingtalk/processor.py:143-158`）无需改动。

### 7.3 边改动

- 扩展 `route_online_message`（`graph/online/edges.py:8`）：当 `settings.scenario_coach.enabled` 且消息路由属 `chat` / `direct_chat` 时，把原下游目的地（`context_resolution` / `direct_evidence_routing`）暂存进 state（如 `state["scenario_pending_route"]`），返回 `"scenario_coach"`；`duplicate` / `guided_flow` 等路径不变。
- 新增条件边 `route_after_scenario`：
  - `response_kind == "scenario"`（命中）→ 会话记录 → END。跳过 context_resolution / evidence_routing / retrieval / generation，直接出预设答案。**必须将预设答案作为本轮回复落库到会话消息表**（与正常 `generate` 一致，否则会话历史缺这条）；具体落库方式（复用既有 `log_control_response` 节点，或在 `scenario_coach` 节点内自记）由实现计划定。
  - 否则 → state 中暂存的原下游（`context_resolution` 或 `direct_evidence_routing`），行为与现状完全一致。
- 在 `build_online_graph`（`graph/online/graph.py:49`）注册新节点与边。

## 8. 来源归属（本次新增需求）

命中场景回答后，文末来源需显示《销冠智慧教练手册·2026年4月版》。

**完全复用既有来源机制，无需新渲染逻辑**：

- `answer_dict["sources"]` 为 `list[dict]`，`integrations/dingtalk/citation.py:format_citation_block(sources)` 已在回答末尾拼接"📖 引用来源"块，每行 `[n] {title} · {label}`，最多 3 条、按 title 去重；`processor.py:149/158` 自动消费。
- 命中时 `answer_dict["sources"]` 设为单元素：

```
{
  "title": "销冠智慧教练手册·2026年4月版",
  "display_title": "销冠智慧教练手册·2026年4月版",
  "source_type": "scenario_coach"
}
```

  `title` 取自 registry 的 `source_name`（§5.3），非硬编码。

- 在 `citation.py` 的 `_SOURCE_TYPE_LABELS` 增加一条映射 `"scenario_coach": "教练手册"`，使文末 label 贴切（默认未知类型会落"知识库"）。最终文末显示形如：

  ```
  ──────────
  📖 引用来源
  [1] 销冠智慧教练手册·2026年4月版 · 教练手册
  ```

- 未命中 / 功能关闭时 sources 维持原管线产生的值，来源块不受影响。

## 9. 配置与迁移

### 9.1 Config（复用成熟 `*_ENABLED` 模式）

- `src/sales_agent/core/config.py` 新增子配置类 `ScenarioCoachConfig{enabled: bool = False, confidence_threshold: float = 0.8}`。
- `Settings.from_yaml` 增加 env 覆盖块（照搬 `config.py:362-366` 的 `GUIDED_FLOWS_ENABLED` 块）：`SCENARIO_COACH_ENABLED`（`{"1","true","yes","on"}`→True）、`SCENARIO_COACH_CONFIDENCE_THRESHOLD`（float 解析）。
- `config/default.yaml` 增加 `scenario_coach:` 段（`enabled: false`, `confidence_threshold: 0.8`）。
- `.env.example` 与 `deploy/tenant.env.example` 增加默认值条目以便发现。

### 9.2 实例级开关（实例 A 开 / 实例 B 关）

- 编辑各租户的 env 文件（git-ignored，宿主机 `secrets/` 下）：`secrets/taishan.env` 加 `SCENARIO_COACH_ENABLED=true`；`secrets/taishankaifa2.env` 不加 / 设 `false`。
- 无需改 `render-multitenant-deploy.py`——flag 走 `env_file: ./secrets/<tenant>.env`，已对每个 `{tenant}-api` / `-stream` / `-worker` 服务接好，三角色共享同一 env_file，实例内一致。
- push main → CI fanout → `docker compose up -d` 仅重建受影响容器。

### 9.3 部署到 prod3（CI/CD 服务器）

- 在 `deploy/tenants.prod3.json` 对应租户的 `secrets/<id>.env` 设 flag（env 文件在目标机上，render 跑 `--skip-validation`）。
- push main → `deploy.yml` 重渲染 `compose-prod3.yml` + 重建 deploy 镜像 + `ci-fanout.sh` 下发；`deploy-remote.sh` 拉镜像 + `docker compose up -d`。
- 场景内容随镜像走（md 在 `src/`），开了 flag 的实例自动获得同样 6 场景 11 问题。

### 9.4 验证（走生产入口，依 CLAUDE.md 规则）

- `docker logs sales-agent-<tenant>-stream` 确认无 crash，且启动日志打印 `scenario_coach loaded 6 scenarios / 11 questions`（功能开启时）或 `scenario_coach disabled`（关闭时）。
- 钉钉发一条场景代表问题 → 确认回预设答案且文末有手册来源块。
- 钉钉发一条无关问题 → 确认正常走 AI 生成、无来源块异常。
- `deploy-remote.sh:60-83` 已 gate `api /health` 200 + schema 一致性，但 stream 日志是规定的人工复核项。

## 10. 错误处理

| 失败 | 处理 |
|---|---|
| md 加载/解析失败 | registry 标记不可用，功能自动降级为关闭，记 error，不 crash |
| LLM 调用失败/超时 | matched=None 放行，记 warning |
| JSON 解析彻底失败（含 retry+正则兜底后） | matched=None 放行，记 warning |
| 空消息 / 非文本消息 | 跳过匹配，放行 |
| confidence 低于阈值 | 视为 none 放行 |
| 功能关闭 | 边直接走原路径，不进节点 |

所有路径 fail-open；stream 永不因本功能挂。

## 11. 测试

### 11.1 单测

- **loader**：fixture md → 断言解析出 6 场景 / 11 问题；每个 Q 的 text/tag/summary/sections 非空；`source_name == "销冠智慧教练手册·2026年4月版"`；构造一份畸形 md 断言 fail-open（registry 不可用、不抛）。
- **matcher**：mock `chat_model.generate` 返回各分支 JSON，断言 match / none / confidence 边界（=阈值命中、<阈值放行）/ 解析失败 fail-open。
- **node**：mock matcher，断言命中→`answer_dict` 正确（含 sources + response_kind="scenario"）；未命中→state 无 answer_dict 改动、透传暂存路由。
- **citation**：`source_type="scenario_coach"` → label=="教练手册"；sources 含手册条目 → 文末块含手册标题。

### 11.2 Fixture

- 11 个代表问题原文 + 一组同义改写用户问句（每个 Q 1–2 条）+ 一组明确无关问句。断言正确命中 id 与 confidence ≥ 阈值；无关问句返回 none。

### 11.3 集成

- 启用 flag 构建 Online Graph：命中问句 → `answer_dict` = 预设且 `response_kind="scenario"`，不进 retrieval/generation；未命中问句 → 落到 `chat` 正常生成。
- 关闭 flag：所有问句行为与现状一致（回归）。

## 12. 升级路径

日后单租户量增大、每轮多一次 LLM 调用成本显现时，可在 matcher 前加 embedding 预筛（方案 C）：启动时预计算 11 题 embedding 缓存，每轮先 embed 用户问题 + 内存余弦，高阈值直接匹配、灰区交 LLM 确认、否则放行。`ScenarioMatcher.match` 接口不变，仅内部实现切换，图与 config 无需改动。复用既有 `OpenAICompatibleEmbedding.embed`（`llm/openai_compatible.py:176`）。

## 13. 风险与权衡

- **每轮多一次小 LLM 调用（含未命中轮）**：prompt 短、temp=0、模型便宜；功能默认关、只在启用实例跑、单租户量不大，可接受。量大了走 §12。
- **LLM 误匹配**：confidence 阈值默认 0.8 + few-shot 约束 + fail-open 放行；阈值 env 可调。
- **md 解析对格式敏感**：以两级标题为锚点，对内部写法差异鲁棒；畸形文件 fail-open 不阻断。改 md 后部署前应跑 loader 单测。
- **来源 label 新增映射**：仅在 `citation.py` 加一行 dict 项，对既有来源类型零影响。
