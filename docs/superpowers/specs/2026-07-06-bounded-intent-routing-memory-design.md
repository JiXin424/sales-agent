# 有限自主意图路由与 Topic Memory 设计

**日期：** 2026-07-06
**状态：** 已确认
**范围：** 在线消息的语义归一化、Topic 关系判断、澄清闭环、意图与知识策略路由、短期 Topic Memory

## 1. 背景

当前系统已经具备规则优先、LLM 分类兜底、任务类型、RAG/Ontology 检索、会话消息落库、会话摘要模型和 LangGraph Store 等基础设施，但生产主链路仍存在四个关键问题：

1. 任务意图与是否检索被耦合；当前 `TASK_DEFAULT_RETRIEVAL` 对全部任务设为真，并且 LLM 返回的 `needs_retrieval` 会被再次强制改为真，实际没有“简单回复或知识库查询”的决策空间。
2. Router 主要处理单条消息，缺少句内否定、自我修正、目标替换和实体保留的结构化表达。
3. 钉钉使用长期稳定的 `conversation_id`，但没有独立 `topic_id`，不同话题可能共享最近消息并互相污染。
4. richer `context_loader`、`ConversationSummary` 与 Store 已存在，但 Chat Graph 当前只读取同一 conversation 最近若干条消息；摘要与长期 Memory 尚未形成主链路读写闭环。

本设计引入“Context Resolver + Intent/Evidence Router”的两级有限自主架构。模型负责受约束的语义理解，代码负责枚举校验、Topic 状态变更、知识策略护栏、澄清与降级。

## 2. 设计目标

- 正确处理一句话中的否定、自我修正、放弃、替换、补充和对比关系。
- 区分当前输入是承接、修订、切换、新 Topic，还是无法判断。
- 无法判断时显式询问用户，并在用户回答“继续”或“新问题”后恢复原始待处理消息完成闭环。
- 将“用户想做什么”与“回答是否依赖企业知识”拆为独立维度。
- 模型只能输出预定义结构，不能返回任意 Graph 节点、工具或执行代码。
- 普通 Topic 30 分钟无活动后关闭；24 小时内允许明确恢复。
- 企业事实类问题不允许在无证据时由模型凭常识补全。
- 建立可评测、可观测、可降级的路由系统。

## 3. 非目标

- 不在本期引入可自由选择任意工具的通用 ReAct Agent。
- 不把用户全部历史对话或全部长期 Memory 注入 Context Resolver。
- 不在本期建设完整的长期用户 Memory 写入、遗忘、纠错和管理界面。
- 不改变已经确认的四类 Guided Flow 内部步骤；本设计只规定它们与 Topic/澄清路由的优先级。
- 不把企业知识库当作对话 Memory。

## 4. 核心概念

### 4.1 渠道会话

HTTP 或钉钉中的稳定通信容器。钉钉可以继续使用 `dingtalk_single:{tenant_id}:{user_id}`，但它不再等同于语义话题。

### 4.2 Topic

用户围绕一个可辨识目标进行的语义会话，例如“了解福多多产品”“比较福多多竞品”“准备某客户拜访”。Topic 有独立 ID、摘要、关键实体、当前目标和活跃期限。

### 4.3 Guided Flow

访前准备、访后复盘、小赢欣赏、卡点破框等明确多轮流程。它具有比普通 Topic Router 更高的路由优先级，状态生命周期沿用其独立设计。

### 4.4 Pending Clarification

当系统无法判断当前消息与 Topic 的关系时保存的待判定状态。用户的下一条“继续”“新问题”或补充内容用于解决该状态，而不是作为普通业务消息直接执行。

## 5. 总体架构

```text
Incoming message
        |
        v
Deterministic Gate
  guided-flow trigger / reset / exit / help
        |
        v
Pending Clarification?
  yes -> Clarification Resolver -> restore pending message
  no  -> current message
        |
        v
Context Resolver
  discourse correction + reference resolution + turn relation
        |
        +-- ambiguous -> persist pending clarification -> ask user -> END
        |
        v
Topic Manager
  continue / revise / switch / new / explicit restore
        |
        v
Intent / Evidence Router
  task intent + direct/retrieve/clarify + knowledge policy
        |
        v
Policy Guard
  validate enum + enforce enterprise-fact rules + fallback
        |
        +-- direct -> generation
        +-- retrieve -> RAG/Ontology -> evidence gate -> generation
        `-- clarify -> content clarification -> END
        |
        v
Risk / logging / topic summary update
```

这是受控 Routing Workflow，不是开放式 Agent。LLM 决定语义标签和建议策略，代码拥有最终执行权。

## 6. 两级有限自主路由

### 6.1 Context Resolver

#### 输入

- 当前用户原始消息。
- 当前 active Topic 最近 4～8 轮消息。
- 当前 Topic 的短结构化摘要。
- 当前 Topic 的关键实体与目标。
- Pending Clarification（存在时由 Clarification Resolver 先处理）。
- Guided Flow 的只读占用状态，用于避免普通 Router 抢走流程回答。

不输入：

- 其他 Topic 的完整对话。
- 用户全部历史消息。
- 企业知识库全文。
- 未筛选的长期用户 Memory。

#### 输出 Schema

```json
{
  "turn_relation": "continue | revise | switch | new | ambiguous",
  "standalone_query": "查询福多多的主要竞品及对比资料",
  "retained_entities": ["福多多"],
  "retracted_goals": ["查询福多多产品"],
  "missing_references": [],
  "confidence": 0.86,
  "reason_code": "within_turn_correction"
}
```

#### 职责

- 识别句内修正和语篇关系。
- 消解“它、这个、刚才那个”等指代。
- 保留新目标仍需要的实体与约束。
- 撤销被“算了、不对、改成、还是”等明确替换的目标。
- 生成不依赖原始上下文也能执行的 `standalone_query`。
- 判断当前输入与 active Topic 的关系。

不得决定：

- 是否调用知识库。
- 使用哪个 Retriever。
- 执行哪个任意工具或 Graph 节点。

### 6.2 Intent / Evidence Router

只接收已消歧的 `standalone_query`、少量稳定身份信息和当前业务配置。

#### 输出 Schema

```json
{
  "intent": "knowledge_qa",
  "response_mode": "direct | retrieve | clarify",
  "knowledge_policy": "none | optional | required",
  "knowledge_scope": ["competitor"],
  "retrieval_query": "福多多的主要竞品及对比资料",
  "confidence": 0.91,
  "reason_code": "company_specific_fact"
}
```

#### 三种知识策略

- `none`：寒暄、情绪确认、流程确认、纯通用回应，不调用知识库。
- `optional`：知识可能改善回答，但不是正确回答的前提；检索失败后可提供明确标注为通用建议的内容。
- `required`：产品、价格、政策、案例、竞品、公司事实、合同与交付承诺等必须有企业证据。

`task_type` 不再固定映射到 `needs_retrieval`。同一个 `script_generation` 可以是 `none`、`optional` 或 `required`。

## 7. 句内自我修正

示例：

```text
帮我找一下福多多的产品，算了，还是找一下竞品吧。
```

语义拆解：

- 撤销动作：查询福多多产品。
- 保留参照实体：福多多。
- 新动作：查询其竞品。
- 最终独立问题：查询福多多的主要竞品及相关对比信息。

不能采用简单“最后子句覆盖全部字段”的策略；覆盖动作时必须保留仍被新动作引用的参数、实体和约束。

需覆盖的修正信号包括：

- 否定：不是、不要、别。
- 修正：不对、准确地说、改成。
- 放弃：算了、先不看。
- 替换：还是、换成、转而。
- 补充：另外、还有、顺便。
- 对比：与它相比、竞品、类似产品。

## 8. Turn Relation 与 Topic 变更

| Relation | Topic 操作 | 上下文操作 |
|---|---|---|
| `continue` | 保持 `topic_id` | 使用当前 Topic 上下文 |
| `revise` | 保持 `topic_id` | 更新目标/约束，记录撤销内容 |
| `switch` | 新建 Topic，写 `parent_topic_id` | 只携带明确选中的实体/约束 |
| `new` | 新建无 parent 的 Topic | 不携带旧 Topic 内容 |
| `ambiguous` | 暂不修改 Topic | 保存原消息并发起关系澄清 |

示例：

- “再短一点” → `continue`。
- “不对，改成面向工会客户” → `revise`。
- 讨论福多多后说“那看看东方福利网” → `switch`。
- “帮我准备明天的客户拜访” → `new`。

## 9. Topic 生命周期

### 9.1 自动关闭

- active Topic 连续 30 分钟无有效用户消息后关闭。
- 每次被正常接受的用户消息刷新 `last_active_at`。
- Topic 关闭后，普通新消息默认创建新 Topic，不自动携带旧上下文。

### 9.2 显式恢复

- Topic 关闭后 24 小时内，用户可通过“继续刚才、接着上次、刚才福多多那个”等明确表达恢复。
- Context Resolver 必须确认指代对象；找到唯一 Topic 才能恢复。
- 多个候选 Topic 时必须列出简短候选并澄清。
- 超过 24 小时后不自动恢复；要求用户补充具体对象，并创建新 Topic。

### 9.3 重置

“新话题、重新开始、清空上下文”等命令立即：

- 关闭 active Topic。
- 取消 Pending Clarification。
- 清除当前短期上下文选择。
- 下一条业务消息创建新 Topic。

## 10. 关系澄清闭环

### 10.1 创建 Pending Clarification

当 `turn_relation=ambiguous` 时保存：

```json
{
  "id": "clarification-id",
  "type": "turn_relation",
  "original_message": "这个方案怎么样？",
  "previous_topic_id": "topic-001",
  "previous_topic_summary": "正在讨论福多多产品方案",
  "candidate_query": "评价这个方案",
  "attempts": 1,
  "status": "pending",
  "expires_at": "..."
}
```

向用户询问：

```text
你是想继续刚才关于福多多方案的话题，还是开始一个新问题？
```

### 10.2 Clarification Resolver

下一条用户消息优先进入澄清解析：

```json
{
  "resolution": "continue | new | replace | cancel | ambiguous",
  "supplemental_message": "重点看价格",
  "confidence": 0.95
}
```

该消息不是独立业务问题。系统必须取回 `original_message` 后再执行：

- `continue`：恢复原 Topic，上下文限定在原 Topic；结合补充消息重新判断最终 `continue` 或 `revise`。
- `new`：不加载旧 Topic；用原消息和补充消息重新判断最终 `new`，必要时进入内容澄清。
- `replace`：用户给出了完整新问题，用新内容替换待处理原消息并重新运行 Context Resolver。
- `cancel`：取消 pending，不执行原消息。
- `ambiguous`：再次询问；第二次仍无法判断时默认 `new`。

澄清答案“继续”不能直接成为 `standalone_query`，也不能直接送进知识库。

### 10.3 内容澄清

关系已确定但问题自身仍不完整时，进入独立 `content_clarification`。例如用户选择“新问题”，但原消息只是“这个怎么样”，系统询问具体对象。关系澄清和内容澄清必须使用不同状态与指标。

### 10.4 优先级

存在 pending 时：

1. 明确 Guided Flow 触发：取消 pending，进入新流程。
2. 重置/退出：取消 pending 和当前 Topic。
3. 明确继续/新问题：解决 pending。
4. 完整新问题：`replace` 并重新解析。
5. 仍含糊：第二次澄清；再失败默认新 Topic。

### 10.5 幂等

- Pending Clarification 有唯一 ID。
- 用户澄清消息绑定该 ID 与渠道 event ID。
- 成功恢复并执行后标记 `resolved`。
- 重复 event ID 不得再次执行原消息。
- Guided Flow、重置或用户取消后标记 `cancelled`。

## 11. Memory 分层与使用边界

### 11.1 当前句语义状态

当前消息内的修正、实体、保留约束和撤销目标。生命周期仅限本轮，但结构化结果写入 trace。

### 11.2 Topic 短期 Memory

- 当前 Topic 最近 4～8 轮。
- 当前 Topic 摘要。
- 关键实体、当前目标和有效约束。
- Pending Clarification。

用于 Context Resolver、Intent Router 和生成。

### 11.3 用户长期 Memory

本期不作为 Context Resolver 的输入。后续只允许保存：

- 用户明确表达且稳定的偏好。
- 角色、负责区域等稳定身份资料。
- 用户明确要求记住的信息。
- 有来源、时间、可删除状态的事实。

不保存临时情绪、一次性的客户判断、模型推断的性格标签。长期 Memory 只在 Topic 和 Intent 已确定后按任务选择性读取。

### 11.4 企业知识库

产品、政策、案例、竞品和客户资料属于回答证据，不是 Memory。仅在 Evidence Router 决定 `optional/required` 后查询。

## 12. 数据模型

新增 `conversation_topics`：

```text
id
tenant_id
agent_id
user_id
channel
conversation_id
parent_topic_id
status                 active | closed
summary
key_entities_json
current_goal
active_constraints_json
retracted_goals_json
pending_clarification_json
clarification_attempts
last_active_at
closed_at
expires_at
created_at
updated_at
```

给 `conversation_messages` 增加可空 `topic_id`，旧数据保持兼容。

约束：

- 同一 `(tenant_id, agent_id, user_id, channel)` 最多一个 active Topic。
- Topic 更新、pending 状态与消息写入在同一数据库事务中。
- `parent_topic_id` 仅用于 `switch`；`new` 不设置 parent。
- pending JSON 必须有版本字段，以支持后续 Schema 演进。

## 13. Policy Guard

Router 输出必须经过本地 Schema 与策略校验：

- 未知枚举、非法 scope、缺失 required 字段：拒绝并重试一次。
- 产品参数、价格、政策、案例、竞品、公司事实、合同和交付承诺：最低 `required`。
- 寒暄、情绪确认、流程确认与澄清控制语：强制 `none`。
- 低置信度且可能涉及企业事实：降级为 `required`。
- LLM Router 不可用且无法规则判定：进入内容澄清；疑似企业事实时优先 `required`。
- `required` 检索无证据：明确说明知识库暂无依据，不得生成未经支持的企业事实。
- `optional` 检索无证据：可以输出通用建议，但不得声称具体企业事实。

## 14. 异常处理

### 14.1 Context Resolver

- 结构化输出失败：同模型重试一次。
- 再失败：明确继续词尝试恢复、明确新话题词创建新 Topic，其他进入关系澄清。
- 不得以失败为由把全部历史拼入生成 Prompt。

### 14.2 Clarification Resolver

- 状态过期：按普通新消息处理，并关闭 pending。
- 找不到原 Topic：进入内容澄清，不猜测。
- 第二次仍含糊：默认新 Topic。

### 14.3 Retrieval

- `required` 无结果、低相关度或引擎异常：输出证据不足状态。
- `optional` 异常：继续通用回答并记录降级原因。
- `none`：禁止隐式调用 Retriever。

### 14.4 日志与事务

- 用户已经收到回答但日志失败时，不回滚已完成 Topic 语义状态到旧阶段。
- Topic 状态写入失败时，不继续执行依赖该状态的下一轮；提示用户重试。
- 钉钉重复事件沿用 event ID 幂等并覆盖澄清恢复路径。

## 15. 当前系统迁移

### 阶段一：恢复真正的检索决策

- 移除 `route_task()` 对 `llm_result.needs_retrieval=True` 的无条件覆盖。
- 将全部任务默认检索改为 `knowledge_policy` 三态。
- 保留现有 `task_type` 用于 Prompt 与业务统计。
- 增加 Policy Guard，避免 Router 漏判企业事实。

### 阶段二：Topic 与 Context Resolver

- 新增 Topic 表及 message.topic_id。
- 将 Context Resolver 放在任务 Router 前。
- Chat Graph 不再按稳定钉钉 conversation ID 无差别读取最近消息，只加载 active Topic。
- 接入现有 summary 能力，但摘要按 Topic 存储和更新。

### 阶段三：澄清闭环

- 持久化 Pending Clarification。
- 增加 Clarification Resolver 与两次澄清上限。
- 支持 30 分钟自动关闭和 24 小时显式恢复。
- 将关系澄清与内容澄清分开观测。

### 阶段四：长期 Memory（后续独立设计）

- 定义可写入事实类型、来源、TTL、用户查看与删除。
- 只在路由完成后选择性读取。
- 不把长期 Memory 用作 Topic 承接的主要证据。

## 16. 可观测性

每轮 trace 至少记录：

```json
{
  "topic_id": "topic-002",
  "previous_topic_id": "topic-001",
  "turn_relation": "switch",
  "standalone_query": "查询东方福利网的公司背景",
  "intent": "knowledge_qa",
  "knowledge_policy": "required",
  "router_type": "llm",
  "reason_code": "company_specific_fact",
  "clarification_state": "none",
  "context_resolver_ms": 180,
  "intent_router_ms": 120
}
```

不得记录模型隐藏思维链。只保存结构化输出、简短 `reason_code`、模型/Prompt 版本、耗时与降级原因。

## 17. 测试与评测

### 17.1 数据集类别

1. 句内修正：放弃前一动作但保留实体。
2. 明确追问：代词、省略、继续优化。
3. 同 Topic 修订：改变目标、语气、对象范围。
4. Topic 切换：共享部分实体但改变主要对象。
5. 全新 Topic：与旧话题无关。
6. 关系含糊与澄清：继续、新问题、替换、取消、仍含糊。
7. 30 分钟过期与 24 小时显式恢复。
8. 多候选旧 Topic 的恢复澄清。
9. 简单回复与知识查询边界。
10. 企业事实类漏判与无证据降级。

### 17.2 指标

- `turn_relation` 准确率与各类别混淆矩阵。
- `standalone_query` 语义正确率。
- 保留实体与撤销目标准确率。
- 澄清回答解析准确率。
- 澄清后原消息最终完成率。
- Topic 上下文串线率。
- `required` 检索漏判率。
- 不必要检索率。
- Context Resolver 与 Router P50/P95 延迟。
- 每轮新增模型调用和 Token 成本。

### 17.3 上线门槛

- 硬规则入口、退出和重置：100%。
- 澄清状态机事务与幂等测试：100%。
- `turn_relation` 验证集准确率不低于 90%。
- 企业事实类 `required` 漏判率低于 2%。
- Topic 串线率低于 1%。
- 所有 `required` 无证据用例不得输出无来源企业事实。

## 18. 验收标准

- 句内自我修正能撤销旧动作并保留新目标需要的实体。
- Router 处理的是独立 `standalone_query`，不是原始含糊文本。
- Topic、渠道 conversation 和 Guided Flow 三种状态边界清晰。
- Topic 30 分钟过期；24 小时内仅可明确恢复。
- 无法判断新旧话题时询问用户，并在澄清后恢复原消息完成执行。
- 澄清两次仍不明确时默认新 Topic，不携带旧上下文。
- `task_type` 与 `knowledge_policy` 解耦。
- 简单回复不检索，企业事实必须检索并通过证据门。
- 主链路按 Topic 加载短期上下文与摘要。
- 长期 Memory 不参与本期 Topic 关系判断。
- 所有路由决策可追踪、可评测、可降级。

## 19. 参考模式

- Anthropic, [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)：区分预定义 Workflow 与模型动态控制的 Agent，并将 Routing 作为可组合工作流模式。
- LangGraph, [Workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)：使用结构化输出驱动条件边。
- LangChain, [Router](https://docs.langchain.com/oss/python/langchain/multi-agent/router)：区分无状态 Router、有状态 Router 与持续多轮编排。
- OpenAI Agents SDK, [Agent orchestration](https://openai.github.io/openai-agents-python/multi_agent/)：混合 LLM 决策与代码编排，用结构化输出提高确定性。
