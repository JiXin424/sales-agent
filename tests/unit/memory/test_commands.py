import pytest

from sales_agent.services.memory.commands import detect_memory_command
from sales_agent.services.memory.contracts import MemoryScope


def test_detect_remember_region_command():
    command = detect_memory_command("记住我负责华东区")
    assert command is not None
    assert command.operation == "remember"
    assert command.normalized_key == "sales_region"
    assert command.value == "华东区"


def test_detect_correction_command():
    command = detect_memory_command("我不负责华东了，现在负责华南")
    assert command is not None
    assert command.operation == "correct"
    assert command.normalized_key == "sales_region"
    assert command.value == "华南"


def test_detect_forget_exact_key_command():
    command = detect_memory_command("忘记我的区域信息")
    assert command is not None
    assert command.operation == "forget"
    assert command.normalized_key == "sales_region"
    assert command.confirm_broad is False


def test_broad_forget_requires_confirmation_phrase():
    command = detect_memory_command("忘记关于我的所有信息")
    assert command is not None
    assert command.operation == "forget"
    assert command.normalized_key is None
    assert command.confirm_broad is False

    confirmed = detect_memory_command("确认忘记全部")
    assert confirmed is not None
    assert confirmed.operation == "forget"
    assert confirmed.normalized_key is None
    assert confirmed.confirm_broad is True


def test_ordinary_chat_is_not_memory_command():
    assert detect_memory_command("帮我查一下福多多产品") is None


from sales_agent.services.memory.commands import apply_memory_command


class FakeRepo:
    def __init__(self):
        self.calls = []

    async def activate_explicit(self, scope, candidate, conversation_id, message_id, now=None):
        self.calls.append(("activate", scope.user_id, candidate.normalized_key, candidate.content["value"]))
        return type("Result", (), {"status": "success", "reason_code": "explicit_confirmed"})()

    async def correct_memory(self, scope, normalized_key, new_candidate, conversation_id, message_id, now=None):
        self.calls.append(("correct", scope.user_id, normalized_key, new_candidate.content["value"]))
        return type("Result", (), {"status": "success", "reason_code": "superseded_existing"})()

    async def forget_memory(self, scope, normalized_key, confirm_broad):
        self.calls.append(("forget", scope.user_id, normalized_key, confirm_broad))
        return type("Result", (), {"status": "success", "reason_code": "user_requested"})()


@pytest.mark.asyncio
async def test_apply_memory_command_calls_scoped_repo():
    repo = FakeRepo()
    scope = MemoryScope(tenant_id="t1", agent_id="a1", user_id="u1")
    command = detect_memory_command("记住我负责华东区")

    result = await apply_memory_command(
        repo=repo,
        scope=scope,
        command=command,
        conversation_id="conv1",
        message_id="msg1",
    )

    assert result.status == "success"
    assert repo.calls == [("activate", "u1", "sales_region", "华东区")]
