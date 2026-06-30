"""Tests for fast_commands and validation nodes."""
import pytest
from sales_agent.graph.nodes.fast_commands import HELP_TEXT, RESET_TEXT, fast_command_node
from sales_agent.graph.nodes.validation import validate_node
from sales_agent.graph.edges.path_conditions import is_fast_command
from sales_agent.graph.state import ChatGraphState


def test_is_fast_command_help_chinese():
    state: ChatGraphState = {"message": "帮助", "tenant_id": "t1", "user_id": "u1", "conversation_id": "c1", "channel": "local"}
    assert is_fast_command(state) == "fast"


def test_is_fast_command_help_english():
    state: ChatGraphState = {"message": "help", "tenant_id": "t1", "user_id": "u1", "conversation_id": "c1", "channel": "local"}
    assert is_fast_command(state) == "fast"


def test_is_fast_command_question_mark():
    state: ChatGraphState = {"message": "？", "tenant_id": "t1", "user_id": "u1", "conversation_id": "c1", "channel": "local"}
    assert is_fast_command(state) == "fast"


def test_is_fast_command_reset():
    state: ChatGraphState = {"message": "新话题", "tenant_id": "t1", "user_id": "u1", "conversation_id": "c1", "channel": "local"}
    assert is_fast_command(state) == "fast"


def test_is_fast_command_normal_message():
    state: ChatGraphState = {"message": "客户说太贵怎么回", "tenant_id": "t1", "user_id": "u1", "conversation_id": "c1", "channel": "local"}
    assert is_fast_command(state) == "normal"


def test_fast_command_node_help():
    state: ChatGraphState = {"message": "帮助", "tenant_id": "t1", "user_id": "u1", "conversation_id": "c1", "channel": "local"}
    result = fast_command_node(state)
    assert result["path"] == "fast"
    assert result["path_reason"] == "help_command"
    assert "answer_dict" in result
    assert result["answer_dict"]["summary"] == HELP_TEXT


def test_fast_command_node_reset():
    state: ChatGraphState = {"message": "新话题", "tenant_id": "t1", "user_id": "u1", "conversation_id": "c1", "channel": "local"}
    result = fast_command_node(state)
    assert result["path"] == "fast"
    assert result["path_reason"] == "reset_command"
    assert result["answer_dict"]["summary"] == RESET_TEXT
    assert result["conversation_id"] != "c1"  # new conversation_id generated


def test_validate_node_passes_valid_input():
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1", "message": "hello",
        "conversation_id": "c1", "channel": "local",
    }
    result = validate_node(state)
    assert result.get("error") is None


def test_validate_node_fails_missing_tenant():
    state: ChatGraphState = {
        "tenant_id": "", "user_id": "u1", "message": "hello",
        "conversation_id": "c1", "channel": "local",
    }
    result = validate_node(state)
    assert result["error"] is not None


def test_validate_node_fails_empty_message():
    state: ChatGraphState = {
        "tenant_id": "t1", "user_id": "u1", "message": "",
        "conversation_id": "c1", "channel": "local",
    }
    result = validate_node(state)
    assert result["error"] is not None
