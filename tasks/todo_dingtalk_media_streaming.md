# Todo: 钉钉语音/图片走流式路径

## 背景 / 根因
- 现象：钉钉发文字能流式（互动卡片），发语音/图片不能流式（一次性 webhook 回复）。
- 根因（已查清，2 个子代理交叉确认 + 主代理亲验代码）：
  1. **门禁**：`stream_client.py:130-134` 的 `use_streaming` 硬要求 `message_type == "text"`。
  2. **接线缺失**：流式分支 `_handle_streaming`（`stream_client.py:183-265`）虽然接收
     `media_download_codes` / `raw_event` 参数，但函数体从未使用，也没调用
     `DingTalkMediaAdapter`，媒体转不出文字。是没接完的半成品。

## 目标（验收标准）
- 语音/图片在 `streaming_enabled=true` 且 `media_enabled=true` 时，走与文字相同的流式卡片路径。
- 预处理（ASR / 视觉）阻塞期间，单张卡片显示「正在识别语音/图片…」，转写完成后无缝接到
  Graph 的「分析中…」→ token 流式（或 finalize）。
- 文字路径行为零回归。
- 媒体识别失败 → 走与 `processor.py:118-136` 一致的兜底回复。
- 不夹带 token 级流式隐患（独立问题，单独标记）。

## 设计（5 处改动，最小影响）

### 改动 1 — 松门禁 `stream_client.py:130-134`
允许受支持的媒体类型（且 `media_enabled`）进入流式；快速命令判定只对 text 生效。
```python
is_media = message_type != "text" and supported_media_type(message_type)
use_streaming = self._config.streaming_enabled and (
    message_type == "text"
    or (is_media and self._config.media_enabled)
)
if use_streaming and message_type == "text":
    use_streaming = text_content.strip() not in _FAST_COMMANDS
```

### 改动 2 — `_handle_streaming` 透传 `message_type`
调用处（`stream_client.py:140-154`）与签名（`:183-199`）补 `message_type` 参数。

### 改动 3 — `_handle_streaming` 内接媒体适配（核心修复）
在调用 `handle_dingtalk_stream_via_graph` **之前**，若 `message_type != "text"`：
1. 取 `card_sender`（已有 `:221`），None 则维持现有非流式兜底（见改动 5）。
2. 先发过渡卡片：`card_sender.send_markdown_card(title="正在识别...", markdown_text="正在识别你的语音/图片…")`。
3. `await DingTalkMediaAdapter(self._config, settings).to_agent_text(...)` 转写（try/except，
   失败 → `reply_fn(无法识别兜底)` + return；finally `adapter.close()`）。逻辑对齐 `processor.py:109-138`。
4. 把转写文本作为 `message` 传入流式路径，并把过渡卡片 `card_id` 一并传入（见改动 4）复用，避免出现两张卡。

### 改动 4 — `handle_dingtalk_stream_via_graph` 接受可选 `card_id`
`graph_stream.py:94` 签名加 `card_id: str | None = None`：
- `None` → 现有行为，自建「分析中…」卡（文字路径不变）。
- 提供 → 复用该卡，跳过 `send_markdown_card`（`:165`）；流式首帧自然覆盖过渡文案。
  向后兼容，零回归。

### 改动 5 — 修 `_handle_streaming` 的 CardSender 不可用兜底
`stream_client.py:225-239` 现有兜底把 `message_type` 硬写成 `"text"` 且丢掉
`media_download_codes`/`raw_event`，媒体消息会无法转写。改为透传真实 `message_type` 与媒体字段，
落到 `handle_dingtalk_event`（它本身已有完整媒体分支）。

## 测试（TDD，先红后绿）
- [ ] `test_use_streaming_gate`：text/media/fast-command/media_enabled 关闭/streaming_enabled 关闭 组合，断言 `use_streaming` 取值。
- [ ] `test_streaming_media_calls_adapter`：mock `DingTalkMediaAdapter.to_agent_text` +
  `card_sender`，断言媒体消息进入流式分支并复用过渡卡。
- [ ] `test_streaming_media_failure_fallback`：转写抛异常 → 走兜底 `reply_fn`，不崩。
- [ ] `test_text_path_unchanged`：回归——文字仍自建卡、不调 adapter。
- [ ] `test_graph_stream_reuses_card_id`：传入 `card_id` 时不调 `send_markdown_card`。

## 验证（CLAUDE.md：验证永远走生产入口）
- worktree 内：`pytest` 跑相关测试红→绿。
- 合并到 dev 并 push 后（重建 kaifa2 tenant）：查 `<tenant>-stream` 容器日志确认 Stream 连上、
  无 crash；钉钉端实测发语音/图片看到流式卡片。HTTP `alembic_version` 非此次验证项（无 DB 变更）。

## 收尾
- [ ] 更新 `README.md` 产品文档对照节（流式支持范围：文字 + 语音 + 图片）。
- [ ] 新建 `changelog/2026-07-09.md` 记录本次改动。
- [ ] 单独标记 token 级流式隐患（`graph_stream.py` 调试日志 + `generate_node` 非流式调用），
      作为下一个任务，不在本次夹带。

## Review（完成后回填）
- 根因：流式门禁硬编码 `message_type == "text"` + 流式分支未接 `DingTalkMediaAdapter`（半成品）。
- 5 处改动全部落地，TDD 12 测试红→绿；dingtalk 111 + 相关 41 单测全过；全量 `tests/unit/` 的 6 failed/106 errors 经基线对比确认为既存环境问题（topic DB OSError / graph_debug brittle 计数 / extractor），零回归。
- 产物：`stream_client.py`（`_should_stream` + `_resolve_streaming_message` + `_handle_streaming` 接媒体 + 兜底修复）、`graph_stream.py`（`card_id` 复用）、新增 `test_stream_client.py`、README 钉钉章节 + 更新日志索引、`changelog/2026-07-09.md`。
- 遗留（独立任务，未夹带）：生成节点 `generate_node` 走非流式 `generate()`，token 级打字机可能未真正逐字。
- 待办：合并 push 到 dev 重建 kaifa2 后，查 `<tenant>-stream` 日志 + 钉钉实测验证。
