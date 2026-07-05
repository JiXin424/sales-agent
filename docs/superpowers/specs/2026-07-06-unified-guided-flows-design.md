# 统一在线引导流程 Graph 设计

**日期：** 2026-07-06
**状态：** 已确认
**范围：** 在线 Chat、钉钉快捷入口、四类引导流程与现有 LangGraph 边界

## 1. 背景与目标

当前项目在调试注册表中暴露 Chat、Ontology Retrieval、Quick Session 和 Daily Evaluation 四个 Graph，但它们并不是四条同等成熟的生产链路：Chat 已接入 HTTP 与钉钉，Ontology 是 Chat 内部动态调用的检索子图，而 Quick Session 与 Daily Evaluation Graph 仍是未接入业务的占位实现。与此同时，真实的小赢欣赏和卡点破框逻辑仍由 `coach/quick_session.py` 与 `quick_sessions` 表维护，访前准备和访后复盘则通过钉钉 H5 发送整组问题后进入普通 Chat。

本设计把在线交互收敛为一个统一入口，并将以下四个功能统一为“一问一答、完成后出卡”的引导流程：

1. 访前准备
2. 访后复盘
3. 小赢欣赏
4. 卡点破框

设计目标：

- 四个功能共享相同的流程生命周期和抢占规则。
- 快捷入口与明确短语触发使用同一条业务链路。
- 同一用户同一时间只有一个活跃流程；新触发立即覆盖旧流程。
- 每次请求只消费一条用户消息、推进一个步骤。
- 流程状态不跨服务重启、不跨自然日保留。
- 没有活跃流程时，保持现有普通 Chat 行为。
- Daily Evaluation 继续由定时器或管理 API 独立运行。

非目标：

- 不使用 LLM 或模糊语义识别自动触发流程。
- 不在本次设计中重写 Daily Evaluation 的完整业务实现。
- 不立即删除 `quick_sessions` 表或历史数据。
- 不改变四类卡片已经验证过的业务文案与规则，除非为逐轮交互所必需。

## 2. 架构决策

采用“在线会话根图 + 通用引导流程子图 + 独立日常评估图”的结构。

```text
HTTP / DingTalk message
          |
          v
Online Conversation Graph
  |-- explicit trigger -> Guided Flow Subgraph
  |-- active flow      -> Guided Flow Subgraph
  `-- no active flow   -> Chat Pipeline
                            `-- Ontology Retrieval Subgraph

Scheduler / Admin API
          `-------------> Daily Evaluation Graph
```

不采用四个独立引导 Graph，避免重复实现流程初始化、步骤推进、抢占、退出、状态清理和异常降级。不将所有节点压平到 Chat Graph，避免 Chat 状态与引导流程状态互相污染。

### 2.1 Graph 边界

- **Online Conversation Graph**：所有在线消息的唯一入口，负责规范化输入、显式触发识别、流程优先级和普通 Chat 分流。
- **Guided Flow Subgraph**：四类引导功能共用的一轮推进引擎，通过流程定义提供差异。
- **Chat Pipeline**：保留现有校验、租户解析、上下文、任务路由、检索、生成、风控和日志能力。
- **Ontology Retrieval Subgraph**：作为 Chat 的检索子图复用，不在每次节点调用时重新构建和编译。
- **Daily Evaluation Graph**：由 scheduler 或管理 API 调用，生命周期、幂等与事务均独立于在线消息。

## 3. 钉钉入口与四个流程

钉钉界面仍可保留三个顶层快捷入口：

- “教练模式”打开选择页，页面内提供“访前准备”和“访后复盘”。
- “小赢欣赏”直接触发小赢流程。
- “卡点破框”直接触发破框流程。

后端接收的四种 action 地位完全平等：

| Flow ID | 显示名称 | 入口 | 最终产物 |
|---|---|---|---|
| `visit_preparation` | 访前准备 | 教练模式选择页或明确短语 | 访前作战卡 |
| `post_visit_review` | 访后复盘 | 教练模式选择页或明确短语 | 机会推进卡 |
| `small_win_appreciation` | 小赢欣赏 | 独立快捷入口或明确短语 | 小赢卡 |
| `sales_block_breakthrough` | 卡点破框 | 独立快捷入口或明确短语 | 破框卡 |

快捷入口和文本触发均转换为同一个规范化字段 `requested_flow`，下游不区分入口来源，只记录 `trigger_source=button|keyword` 用于观测。

## 4. 触发与路由规则

在线根图按以下固定优先级处理消息：

1. 新流程触发
2. 退出指令
3. 推进当前活跃流程
4. 普通 Chat

### 4.1 明确短语匹配

- 对消息做首尾空白清理、英文大小写规范化和全角标点规范化。
- 仅匹配配置中登记的完整短语，不使用 LLM 意图识别。
- 不使用任意子串命中，避免普通业务表达误抢占当前流程。
- 初始触发短语至少包含四个功能的标准名称；新增别名通过配置和测试显式登记。

### 4.2 抢占与重新开始

- 命中新流程时，无条件丢弃旧流程的临时状态并初始化新流程。
- 即使命中的是当前同名流程，也从第一步重新开始。
- 触发消息仅用于启动流程，不作为第一题答案。
- 新流程初始化后立即返回第一问，本轮结束。

### 4.3 退出与完成

- `退出`、`取消`、`结束` 等明确指令清空当前流程并返回确认语。
- 流程完成并成功生成卡片后清空当前流程。
- 流程中未命中触发或退出的普通消息，默认作为当前步骤答案。

## 5. 状态模型

在线根图状态分为身份、输入、流程和输出四组：

```text
identity:
  tenant_id
  agent_id
  channel
  user_id
  current_date

input:
  message
  entry_action
  requested_flow

flow:
  active_flow
  current_step
  collected_answers
  clarification_count
  started_at
  trigger_source

output:
  reply
  completed_card
  response_kind
```

状态键使用：

```text
tenant_id + agent_id + channel + user_id + current_date
```

其中 `agent_id` 缺失时使用解析后的默认 Agent ID，不能直接使用空值形成共享键。

状态由进程级单例 `InMemorySaver` 保存，禁止在每次请求中重新创建 Saver。自然日进入状态键，从而跨天自动从新状态开始；服务进程重启后内存状态自然清空。当前部署如果未来扩展为多进程或多副本，必须先引入同一运行期共享且带启动 epoch 的状态后端，否则不能假设请求会命中同一进程。

不再使用 `quick_sessions` 表保存活跃流程。最终卡片和用户/助手消息仍进入现有 Conversation/Message 日志，使历史查询和 Daily Evaluation 可以继续使用这些数据。

## 6. 通用流程定义

四个功能通过注册式 `FlowDefinition` 描述：

```text
FlowDefinition:
  id
  label
  trigger_phrases
  steps[]
  card_generator
  fallback_renderer

FlowStep:
  id
  question
  validator
  normalizer
  clarification_prompt
```

公共引擎只负责：

- 初始化和覆盖流程状态。
- 校验当前步骤回答。
- 保存规范化回答。
- 决定下一步或出卡。
- 调用卡片生成器及降级模板。
- 清理完成或退出的流程状态。

业务差异保留在流程定义及少量显式钩子中，不能在公共引擎里堆积基于 Flow ID 的大型 `if/elif`。

### 6.1 访前准备

建议步骤：

1. 客户对象：要见谁、客户角色或组织。
2. 客户现状：已知需求、关系阶段和背景。
3. 本次目标：希望本次沟通推进到哪一步。
4. 根据三轮输入生成访前作战卡。

### 6.2 访后复盘

建议步骤：

1. 客户表达：客户主要说了什么。
2. 客户态度：积极、观望、异议或拒绝及其证据。
3. 下一步约定：是否已约定动作、责任人和时间。
4. 根据三轮输入生成机会推进卡。

### 6.3 小赢欣赏

保持现役成熟逻辑：

1. 今天的小赢或小进展。
2. 小赢背后的个人优势。
3. 想感谢的人、事或自身行动。
4. 给自己的能量句。
5. 生成小赢卡。

### 6.4 卡点破框

保持现役成熟逻辑：

1. 描述客户反馈、发生的事情、当前判断和已做动作。
2. 区分事实、解释与担心，并展示结构化拆分。
3. 探索其他解释和更小的可行动作。
4. 生成破框卡。

## 7. 单轮数据流

```text
START
  -> normalize_input
  -> resolve_explicit_trigger
  -> route_turn
       new_flow
         -> initialize_flow
         -> ask_first_question
         -> END

       cancel_flow
         -> clear_flow
         -> render_cancel_ack
         -> END

       active_flow
         -> validate_current_answer
            invalid -> ask_clarification -> END
            valid   -> collect_answer
                      -> route_step
                         more -> ask_next_question -> END
                         done -> render_card
                              -> clear_flow
                              -> log_response
                              -> END

       normal_chat
         -> existing Chat Pipeline
         -> END
```

任何一次调用最多推进一个用户回答步骤。图中不存在从第一问自动连续运行到出卡的边，也不依赖长时间挂起的 `interrupt()` 来等待下一条钉钉消息。

## 8. 错误处理与降级

- 回答过短或无效时不推进步骤，返回该步骤的补充提示。
- 每个步骤最多补问一次；第二次仍不理想时接受原文，避免锁死用户。
- LLM 卡片生成失败时，调用该流程的确定性模板生成降级卡片并正常结束。
- 普通节点异常时保留已收集答案，提示用户重试当前步骤。
- 状态结构损坏或无法读取时清空该用户当日流程，提示从头开始。
- 钉钉重复事件必须在进入 Graph 前或首节点处按 event ID 幂等，不能重复推进步骤。
- 新触发优先于退出和当前步骤消费，确保流程抢占行为确定。
- 流程完成后的日志写入失败不能重新激活已完成流程；日志层应独立重试或记录告警。

## 9. Checkpointer 与编译生命周期

- Online Conversation Graph、Guided Flow Subgraph 和 Ontology Retrieval Subgraph 均在应用启动期构建或按配置缓存，不能按消息重复编译。
- 在线流程使用进程级 `InMemorySaver` 单例。
- Runtime context 继续承载每请求的数据库会话、模型、租户运行时和观测对象，不能将这些不可序列化对象写入 Graph State。
- Daily Evaluation 可使用独立 checkpointer；其持久化选择不影响在线流程“重启即清空”的约束。
- Graph Debug 注册表先展示 Online Conversation Graph、Guided Flow 和 Ontology Retrieval；Daily Evaluation 只有在真实业务迁移完成后才重新展示，迁移前隐藏占位图。

## 10. Daily Evaluation 边界

Daily Evaluation 继续由以下入口调用：

- 定时器按 Agent 配置时间触发。
- 管理 API 手动触发。

它不进入在线会话根图，原因包括：

- 触发源是时间或管理动作，不是用户消息。
- 状态、批量粒度、幂等键和数据库事务与在线对话不同。
- 失败隔离要求按用户继续执行，不能阻塞在线消息。

现有 `graph/coach/daily_eval_graph.py` 是占位实现，不能作为生产迁移基础。后续应把 `DailyEvaluationService` 的真实聚合、校验、幂等写分、里程碑和奖励逻辑逐步拆成节点，并保持现有 API 契约。

## 11. 迁移策略

### 阶段一：建立统一入口

1. 增加 Online Conversation Graph 和通用 Guided Flow 引擎。
2. 建立四个 Flow Definition。
3. 从现有 `quick_session.py` 提取小赢和卡点的成熟规则、Prompt 和降级模板。
4. 将访前准备和访后复盘改为逐轮步骤定义。

### 阶段二：接入渠道

1. 钉钉四种 action 统一转换为 `requested_flow`。
2. 普通消息先执行明确短语匹配，再判断活跃流程。
3. HTTP Chat 使用同一根图，保持渠道无关的流程语义。
4. 启用租户级特性开关，允许回退到原普通 Chat 路由。

### 阶段三：清理旧实现

1. 停止创建和读取 `quick_sessions`。
2. 保留表和历史数据一个观察版本，不立即执行破坏性迁移。
3. 删除未接入且逻辑不完整的 `quick_session_graph.py`。
4. 从生产 Chat 模块中移出纯调试注册耦合，使用独立 Graph Registry。
5. Daily Evaluation 完成真实 Graph 迁移前，删除或明确禁用占位 Graph，避免调试页展示假流程。

## 12. 测试策略

### 12.1 单元测试

- 四个标准短语和四种 action 映射到正确 Flow ID。
- 非完整短语、普通销售表达不会误触发。
- 路由优先级满足新触发、退出、活跃流程、普通 Chat 的顺序。
- 同名重触发和异名抢占都重置步骤与答案。
- 四个 Flow Definition 的步骤顺序、问题和最终渲染正确。
- 无效回答只补问一次。
- LLM 失败使用正确的流程降级模板。

### 12.2 Graph 状态测试

- 每次调用只推进一步。
- 完成和退出均清空 `active_flow`。
- 不同租户、Agent、渠道、用户和日期之间状态隔离。
- 新建 Saver 模拟服务重启后，旧流程不可恢复。
- 自然日变化后从第一步开始。

### 12.3 集成测试

- 三个钉钉顶层入口可正确触达四个流程。
- 点击与关键词触发产生相同的后端状态和第一问。
- 活跃流程可被任意其他流程立即抢占。
- 重复钉钉 event ID 不会重复推进。
- 无活跃流程时，现有 HTTP、钉钉 Chat、RAG、Ontology 和风控行为不变。
- 最终卡片写入会话历史，并能被 Daily Evaluation 聚合。

## 13. 验收标准

- 四个功能均为逐轮一问一答流程，并使用统一生命周期。
- 用户点击入口或输入明确短语即可启动对应流程。
- 同一用户同一日期只有一个活跃流程，新触发必定覆盖旧流程。
- 完成、退出、跨天和服务重启后不再继续旧流程。
- 在线请求每轮最多消费一个流程步骤。
- `quick_sessions` 不再参与新流程运行。
- 普通 Chat、Ontology Retrieval 和 Daily Evaluation 的职责边界清晰且回归通过。
- 调试界面不再把占位 Graph 当作已接入生产的工作流。

## 14. 风险与约束

- 进程内状态要求同一用户消息在单进程实例中处理；扩容到多进程或多副本前必须重新设计运行期共享状态。
- 抢占会主动丢弃未完成答案，产品文案应清楚提示“已切换到新流程”。
- 访前和访后从一次性收集改为逐轮交互后，钉钉 H5 与提示文案需要同步更新。
- 旧 `quick_sessions` 数据不应直接转换为新状态，因为产品要求新机制从头开始且不跨重启恢复。
