# Pilot Onboarding Guide — 新租户上线指南

**版本:** Phase D
**更新日期:** 2026-06-11

## 1. 概述

本指南面向运营人员和管理员，描述如何从零开始为一个新企业客户配置和启动 Sales Agent Pilot。

**前置条件：**
- Sales Agent 服务已部署运行
- 数据库已初始化
- 管理控制台可访问

## 2. 租户配置

### 2.1 创建租户

1. 打开管理控制台
2. 在顶部租户选择器中点击「创建租户」
3. 填写：
   - **租户 ID**：唯一标识，如 `enterprise_acme`
   - **名称**：企业名称，如「ACME 集团」
   - **配置**：使用默认配置或自定义

### 2.2 模型配置

在租户配置中设置 LLM 模型参数：

| 参数 | 说明 | 推荐值 |
|------|------|--------|
| provider | 模型提供商 | `openai_compatible` |
| base_url | API 地址 | 根据实际部署 |
| chat_model | 对话模型 | `qwen-plus` |
| embedding_model | 向量模型 | `text-embedding-v3` |
| temperature | 温度参数 | `0.3` |

**安全提示：** API Key 通过环境变量配置（`api_key_env`），不存储在配置中。

## 3. 钉钉集成

### 3.1 Webhook 模式（HTTP）

1. 在钉钉开放平台创建机器人
2. 获取 Webhook URL 和签名密钥
3. 在配置文件中设置：
   - `DINGTALK_ENABLED=true`
   - `DINGTALK_MESSAGE_MODE=http`
   - `DINGTALK_WEBHOOK_URL=<webhook_url>`

### 3.2 Stream 模式（推荐）

1. 在钉钉开放平台创建企业内部应用
2. 获取 Client ID 和 Client Secret
3. 配置 Stream 模式：
   - `DINGTALK_MESSAGE_MODE=stream`
   - `DINGTALK_CLIENT_ID=<client_id>`
   - `DINGTALK_CLIENT_SECRET=<client_secret>`

### 3.3 验证

发送测试消息到钉钉机器人，确认收到回复。检查管理控制台「对话记录」页面是否有记录。

## 4. 知识库上传

### 4.1 文档格式

- 支持 Markdown（`.md`）格式
- 推荐使用清晰的标题层级（`#`, `##`, `###`）
- 每个主题一个文件，避免单文件过大

### 4.2 上传流程

1. 打开管理控制台 → 「知识库」
2. 点击「上传文件」
3. 选择 Markdown 文件
4. 等待导入任务完成
5. 在「文档」列表中确认状态为 `active`

### 4.3 验证知识

在管理控制台使用 Prompt 预览功能，输入与上传知识相关的问题，确认回答引用了正确的知识。

## 5. Prompt 审查

### 5.1 检查活跃 Prompt

1. 打开「Prompt 管理」
2. 查看每个任务类型的活跃 Prompt
3. 确认内容符合企业客户需求

### 5.2 任务类型清单

| 任务类型 | 说明 |
|----------|------|
| emotional_support | 情绪安抚与激励 |
| knowledge_qa | 知识问答 |
| script_generation | 话术生成 |
| objection_handling | 异议处理 |
| conversation_review | 对话复盘 |
| general_sales_coaching | 通用销售辅导 |
| visit_preparation | 拜访准备 |
| follow_up_planning | 跟进规划 |
| customer_context_summary | 客户画像 |
| deal_advancement | 推进成交 |
| conversation_scoring | 对话评分 |

## 6. 评估套件

### 6.1 运行冒烟测试

1. 打开「Eval 回归」页面
2. 点击套件旁的「运行」按钮
3. 等待运行完成
4. 查看通过率，确保核心用例全部通过

### 6.2 基线建立

首次运行的结果作为后续变更的基线（baseline）。

## 7. 销售用户使用指南

### 7.1 通过钉钉使用

1. 在钉钉中找到已配置的机器人
2. 直接发送消息即可开始对话
3. 常见使用场景：
   - 「客户说价格太高怎么办？」→ 异议处理
   - 「帮我准备明天拜访张总的方案」→ 拜访准备
   - 「这个客户的关键信息有哪些？」→ 客户画像

### 7.2 反馈机制

- 对满意的回答点 👍
- 对不满意的回答点 👎 并补充说明
- 管理员会根据反馈持续改进

## 8. 管理员日常操作

### 8.1 每日质量审查

1. 打开「质量审查」页面
2. 点击「立即扫描」检查新增审查条目
3. 逐条审查：
   - 查看对话内容
   - 标记根因分类
   - 创建知识缺口（如适用）
   - 更新状态为已解决或忽略

### 8.2 每周报告

1. 打开「Pilot 报告」页面
2. 选择时间范围
3. 生成周报
4. 下载 Markdown 格式报告用于汇报

### 8.3 告警监控

1. 打开「运维告警」页面
2. 检查活跃告警
3. 确认或解决告警

## 9. 故障排查

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| 钉钉无响应 | Webhook 配置错误 | 检查 URL 和签名 |
| 回答质量差 | 知识缺失 | 上传相关文档 |
| 延迟高 | 模型响应慢 | 检查模型服务状态 |
| 导入失败 | 文件格式错误 | 确认为 Markdown |
| 告警频发 | 阈值过低 | 调整告警规则阈值 |

## 10. 上线检查清单

- [ ] 租户已创建
- [ ] 模型配置已验证
- [ ] 钉钉集成已测试
- [ ] 知识库已上传
- [ ] Prompt 已审查
- [ ] 评估套件已运行
- [ ] 默认告警规则已创建
- [ ] 销售用户已告知使用方式
- [ ] 首次质量审查已完成

---

*Phase D — Pilot Validation and Quality Loop*
