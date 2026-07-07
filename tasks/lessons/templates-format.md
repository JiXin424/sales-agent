# Lessons · 模板渲染 / str.format / DB prompt 花括号

> 详情文件;索引见 `tasks/lessons.md`。#编号稳定。

## #1 str.format 双花括号是转义,不是占位符
- **教训**:Python `str.format` 中 `{{` → 字面 `{`,`{{message}}` **不会**被替换,会原样输出 `{message}`。JSON 示例区用双花括号是对的(要输出字面 JSON 结构),但**变量占位符必须用单花括号** `{message}`。
- **检查方法**:`string.Formatter().parse()` 对 `{{message}}` 也识别为含字段 `message`,所以 `"{message}" in prompt` 这种子串校验**无法发现此 bug**。必须对 `.format()` **渲染后的结果**做断言(值是否真的注入)。见 `tests/unit/test_visit_post_visit_placeholders.py`。
- **相关**:#30

## #30 DB 版 prompt 含字面花括号(`{群聊/私聊}`)→ str.format 当未知占位符抛 KeyError,LLM 路由每次必崩;用 format_map + SafeDict 兼容三类花括号。+ Edit 被 hook 静默 revert,必须 git diff 验证而非 Read
- **教训**:① **`str.format` 不适合渲染「外部数据源(DB/配置)的模板」**:模板作者无法保证所有字面花括号都 escape 成 `{{}}`。改用 `format_map` + `__missing__` 返回 `"{" + key + "}"` 的 SafeDict——未知占位符原样保留,三类花括号全正确:真占位符替换、`{{}}` escape 还原、字面 `{key}` 保留。不改 DB 数据、不破坏 escape 语义。② **`.replace("{message}", msg)` 是错解**:它跳过 `.format`,导致 `{{...}}` escape 不被还原、原样发给 LLM → LLM 模仿输出双花括号 JSON → 下游解析又炸(`JSONDecodeError`)。format_map 才是正解。③ **Edit 可能被 hook 静默 revert**:`Read` 显示的是缓存/旧状态看不出问题,只有 `git diff` 才权威显示「改动没在」。**验证改动落盘一律用 `git diff`,不要用 Read**。④ `PromptRegistry` 解析后的 prompt 仍是「待 format 的模板」:规范——DB prompt 一律按 `.format` 语义 escape(字面花括号写 `{{}}`),调用方一律用 `format_map`+SafeDict(双保险:即便作者漏 escape 也不炸)。
- **检查**:LLM 路由/任何 `prompt.format(...)` 报 `KeyError: '<中文/非占位符词>'` → 该词是 DB prompt 里的字面花括号(作者漏 escape)→ 改 `.format` 为 `format_map(_KeepMissingDict(...))`,`__missing__` 返回 `"{" + key + "}"`。验证改动用 `git diff`。
- **相关**:#1 #27 #31
