"""快捷入口多轮状态机的回归测试（小赢欣赏 / 卡点破框）。

卡点破框出卡走 LLM；这里用 stub 模型覆盖 LLM 路径，用 chat_model=None 覆盖规则回退。
"""

from __future__ import annotations

import asyncio

from sales_agent.coach import quick_session as qs


class _StubChat:
    """最小 LLM stub：按用户提示内容返回不同合成结果。"""

    def __init__(self, responder):
        self._responder = responder
        self.calls = 0

    async def generate(self, *, messages, temperature, max_tokens, response_format=None):
        self.calls += 1
        return self._responder(messages[-1]["content"])


# ---------------- 小赢欣赏：规则回退（无 LLM）----------------

def test_small_win_full_flow():
    stage, payload, reply = qs._sw_start()
    assert stage == "small_win" and "小赢欣赏" in reply

    stage, payload, reply, done = asyncio.run(
        qs._sw_advance(None, stage, payload, "今天主动联系了一个一直没回的客户")
    )
    assert stage == "strength" and not done

    stage, payload, reply, done = asyncio.run(qs._sw_advance(None, stage, payload, "我敢主动开口，没退缩"))
    assert stage == "gratitude" and not done

    stage, payload, reply, done = asyncio.run(qs._sw_advance(None, stage, payload, "客户终于回了消息"))
    assert stage == "energy" and not done

    stage, payload, reply, done = asyncio.run(qs._sw_advance(None, stage, payload, "AI代写"))
    assert stage == "completed" and done
    assert "小赢卡" in reply


def test_small_win_clarification_loop():
    stage, payload, _ = qs._sw_start()
    stage, payload, reply, done = asyncio.run(qs._sw_advance(None, stage, payload, "还行"))
    assert stage == "small_win" and not done and "补充" in reply
    stage, payload, reply, done = asyncio.run(qs._sw_advance(None, stage, payload, "还行吧"))
    assert stage == "strength"


# ---------------- 小赢欣赏：LLM 路径 ----------------

def test_small_win_uses_llm():
    def responder(_user_prompt):
        return (  # 第 4 步出卡：LLM 合成的小赢卡
            "## 小赢卡\n\n"
            "**今天的小赢**\n你今天主动联系了一个一直没回的客户。\n\n"
            "**我欣赏你的是**\n你表现出了主动，比如你敢先开口联系。\n\n"
            "**这件事的意义**\n它说明你正在从等待走向主动连接，哪怕只是一步。\n\n"
            "**今天值得感谢**\n感谢客户愿意回复。\n\n"
            "**给自己的能量句**\n敢开口，就已经迈出去了。\n\n"
            "小赢不是小事，它是状态恢复和持续成长的起点。"
        )

    chat = _StubChat(responder)
    stage, payload, _ = qs._sw_start()
    stage, payload, _, _ = asyncio.run(qs._sw_advance(chat, stage, payload, "今天主动联系了一个一直没回的客户"))
    assert stage == "strength"
    stage, payload, _, _ = asyncio.run(qs._sw_advance(chat, stage, payload, "我敢主动开口"))
    assert stage == "gratitude"
    stage, payload, _, _ = asyncio.run(qs._sw_advance(chat, stage, payload, "客户回了消息"))
    assert stage == "energy"
    stage, payload, reply, done = asyncio.run(qs._sw_advance(chat, stage, payload, "AI代写"))
    assert stage == "completed" and done
    assert reply.startswith("## 小赢卡")  # 用的是 LLM 出卡，不是规则模板
    assert "敢开口" in reply  # LLM 合成的具体内容（规则模板版不会有这句）
    assert chat.calls == 1  # 只在出卡调一次 LLM（前三轮无 LLM）


# ---------------- 卡点破框：LLM 路径 ----------------

def test_sales_block_uses_llm():
    def responder(user_prompt):
        if "拆成三类" in user_prompt:  # 第 2 步拆分
            return "**事实**：客户嫌贵不回\n**解释**：他没预算\n**担心**：被拒绝"
        return (  # 第 4 步出卡
            "1. 事实是什么：客户嫌贵不回\n"
            "2. 旧框是什么：他把不回当成没兴趣\n"
            "3. 新框是什么：可能怕买错\n"
            "4. 还可以怎么做：\n- 问一句\n- 给理由\n"
            "5. 今天的最小行动：发一个小问题\n"
            "6. 推荐话术：你方便说下担心预算还是效果？\n"
            "7. 承诺和回传：你准备选哪个动作？几点前做？"
        )

    chat = _StubChat(responder)
    stage, payload, _ = qs._sb_start()  # awaiting_blocker
    # 第 1 轮：blocker -> split（问 SB_SECOND，无 LLM）
    stage, payload, reply, done = asyncio.run(qs._sb_advance(chat, stage, payload, "客户嫌贵一直不回"))
    assert stage == "awaiting_split" and not done
    # 第 2 轮：split -> possibilities（LLM 拆分被展示）
    stage, payload, reply, done = asyncio.run(
        qs._sb_advance(chat, stage, payload, "事实是他嫌贵；我判断没戏，担心被拒")
    )
    assert stage == "awaiting_possibilities" and not done
    assert "我帮你拆一下" in reply and "客户嫌贵不回" in reply
    # 第 3 轮：possibilities -> completed（LLM 出卡）
    stage, payload, reply, done = asyncio.run(qs._sb_advance(chat, stage, payload, "可能怕买错，先降风险"))
    assert stage == "completed" and done
    assert reply.startswith("1. 事实是什么")  # 用的是 LLM 出卡，不是规则版
    assert chat.calls == 2  # 两次 LLM 调用（拆分 + 出卡；第 1 轮无 LLM）


# ---------------- 卡点破框：规则回退（无模型）----------------

def test_sales_block_fallback_no_model():
    stage, payload, _ = qs._sb_start()
    stage, payload, reply, done = asyncio.run(qs._sb_advance(None, stage, payload, "客户嫌贵一直不回"))
    assert stage == "awaiting_split" and not done
    stage, payload, reply, done = asyncio.run(
        qs._sb_advance(None, stage, payload, "事实是他嫌贵；我判断没戏，担心被拒")
    )
    assert stage == "awaiting_possibilities" and not done
    assert "拆" in reply  # 回退也展示拆分
    stage, payload, reply, done = asyncio.run(qs._sb_advance(None, stage, payload, "可能怕买错，先降风险"))
    assert stage == "completed" and done
    assert "最小行动" in reply  # 规则版 _sb_final_reply


# ---------------- 分发 / 工具 ----------------

def test_labels_and_cancel():
    assert qs.label_of("small_win_appreciation") == "小赢欣赏"
    assert qs.label_of("sales_block_breakthrough") == "卡点破框"
    assert set(qs.VALID_TYPES) == {"small_win_appreciation", "sales_block_breakthrough"}
    assert qs._is_cancel("退出") and qs._is_cancel("cancel")
    assert not qs._is_cancel("下午好")
