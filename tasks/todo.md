# 图调试页：节点中文小字（收尾）+ 图占满屏幕布局重构

## 需求
1. 图结构里除 `__start__`/`__end__`/子图外的节点，用中文小字标功能
2. 图调试页 Mermaid 图放大、执行轨迹放下面、图占满整个屏幕（用户选「执行轨迹可折叠」方案）

## 需求1：节点中文小字 —— 已实现，仅收尾（无需改代码）

三方验证均已通过：
- **字符串层**（子代理1）：`_annotate_node_labels` + `node_metadata.py` 给三图 20 个应注解节点全部加上 `<font size='2' color='#888'>desc</font>`，`__start__`/`__end__`/子图入口正确跳过
- **单测层**：`tests/unit/graph/test_graph_debug.py` 断言 `<font>` 计数（online 7 / guided-flow 3 / chat 10）
- **渲染层**（mmdc 真渲染，lessons #33）：SVG 用 `<foreignObject>` 包 HTML `<div>`，`<font size="2" color="#888">` 在浏览器里渲染成灰色小字。online/guided-flow/chat 三图 SVG 各含 7/3/10 个 `<font>` + 等量 `#888`

唯一隐患：`src/sales_agent/graph/node_metadata.py` 是未跟踪文件（`??`），`graph_debug.py` 已 import 它。不 `git add` → CI 镜像缺失 → 部署崩溃（lessons #11/#14 同款「本地有、CI 没有」）。

- [x] `git add src/sales_agent/graph/node_metadata.py`

## 需求2：布局重构（执行轨迹可折叠）—— 主要工作

### 根因
1. `.gd-container` 的 `height: calc(100vh - 64px - 48px)` 漏算 AgentLayout `<Content>` 的 `padding:24`（上下共 48px）→ 容器比可用空间大 48px → 溢出/滚动
2. `.gd-left-body` 内 tabpane 非 flex 容器，`.gd-mermaid-wrap` 的 `height:100%` 与 NodeLegend/Collapse 争空间 → 图没干净撑满
3. `.gd-right` 执行轨迹 `flex: 0 0 36vh` 占较多，图被压缩

### 改动文件

#### 1. `console/src/layout/AgentLayout.tsx`（突破白卡片边距）
- 复用已有 `useLocation`（第 48 行），加 `const isGraphDebug = location.pathname.includes('/graph-debug');`
- `<Content>` style 条件化：graph-debug 路由下用 `{ margin: 0, padding: 0, background: 'transparent', minHeight: 0 }`，其余路由保持现有 `{ margin: 24, padding: 24, background: '#fff', borderRadius: 8, minHeight: 280 }`
- 只影响 graph-debug 路由，其它页面零影响

#### 2. `console/src/pages/Agents/GraphDebugPage.tsx`（折叠交互）
- 新增 state `const [traceCollapsed, setTraceCollapsed] = useState(false);`
- `gd-left-header`（第 537 行）：`图结构` 右侧加 toggle Button（展开时 `DownOutlined`「折叠轨迹」，折叠时 `UpOutlined`「展开轨迹」）
- `gd-right`（第 608 行）：`traceCollapsed` 时整体隐藏（`style={{ display: 'none' }}`），否则正常显示

#### 3. `console/src/pages/Agents/GraphDebugPage.css`（撑满 + 折叠样式）
- `.gd-container`: `height: calc(100vh - 64px)`（只减 header；Content 已无 padding/margin）
- `.gd-left-body > .ant-tabs ... > .ant-tabs-tabpane`: 改 `display:flex; flex-direction:column; min-height:0`（让图区 flex 撑满）
- `.gd-mermaid-wrap`: 去掉 `height:100%`，改 `flex:1; min-height:0`（真正撑满剩余空间）
- `.gd-right`: `flex: 0 0 32vh`（从 36vh 缩到 32vh，折叠由 tsx display:none 控制）
- `.gd-left-header` 加 toggle 按钮样式（按钮靠右）

### 布局结果
```
正常态:                          折叠态(图占满100%):
┌────────────────────┐           ┌────────────────────┐
│ 图结构      [▾折叠]│           │ 图结构      [▴展开]│
│ [chat][online][guided]        │ │ [chat][online][guided]        │
│ ┌──────────────────┐ │           │ ┌──────────────────┐ │
│ │   Mermaid 图     │ │           │ │                  │ │
│ │   (撑满主区)     │ │           │ │   Mermaid 图     │ │
│ └──────────────────┘ │           │ │   (占满整屏)     │ │
│ [输入框.........][发送]        │ │ └──────────────────┘ │
├────────────────────┤           │ [输入框.........][发送]        │
│执行轨迹 [trace][timeline]      │ └────────────────────┘
└────────────────────┘
```

## 验证清单
- [x] mmdc 真渲染三图（已通过：font 7/3/10 + 灰#888 + 子图橙#ff9800 + LLM蓝#1677ff 全命中）
- [x] `cd console && npm run build`（tsc -b + vite build）→ exit 0，29.15s
- [x] `uv run pytest tests/unit/graph/test_graph_debug.py -q` → 33 passed
- [x] README「产品文档对照」节加新条目（图占满屏幕 + 执行轨迹可折叠）
- [x] `changelog/2026-07-06.md` 追加本次变更记录
- [x] `git add src/sales_agent/graph/node_metadata.py`（A 已暂存）

## Review
**需求1（节点中文小字）**：后端 `_annotate_node_labels` + `node_metadata.py` 三方验证通过（子代理字符串层 + 单测 + mmdc 真渲染）。前端 mermaid 用 `<foreignObject>`+HTML `<font color='#888'>` 渲染灰色小字，非静默退化。唯一收尾：`node_metadata.py` 补 `git add`（原未跟踪，防 CI 崩溃）。

**需求2（图占满屏幕）**：根因是 `.gd-container` 漏算 Content padding 48px + tabpane 非 flex 容器致图被裁。修复：AgentLayout graph-debug 路由去 Content margin/padding（全屏）、gd-container height 改 `calc(100vh - 64px)`、tabpane 改 flex column、gd-mermaid-wrap 改 flex:1 撑满、执行轨迹 36vh→32vh + 可折叠（折叠后图占满 100%）。用户选定「执行轨迹可折叠」方案。

**验证**：前端 build exit 0 + 后端 33 单测通过 + mmdc 真渲染三方一致。AgentLayout 条件化只影响 graph-debug 路由，其它页面零回归。

**教训**：Bash 工具 cwd 不持久，每次命令必须带 `cd <dir> &&`——我反复把 cd 写进 description 而非 command，浪费多轮。
