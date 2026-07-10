from sales_agent.services.sales_actions.detector import detect_fast_action_intent


def test_detect_explicit_create_phrase():
    assert detect_fast_action_intent("半小时后提醒我给张总回电话") == "create_action"


def test_detect_complete_phrase():
    assert detect_fast_action_intent("张总那个电话我打完了") == "complete_action"


def test_detect_cancel_phrase():
    assert detect_fast_action_intent("取消明天给王总发资料的提醒") == "cancel_action"


def test_detect_list_phrase():
    assert detect_fast_action_intent("我今天还有哪些任务") == "list_actions"


def test_non_action_chat_returns_none():
    assert detect_fast_action_intent("客户说价格贵怎么回") == "none"
