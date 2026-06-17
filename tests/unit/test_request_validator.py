"""Request Validator 单元测试。"""

import pytest

from sales_agent.services.request_validator import validate_chat_request
from sales_agent.core.exceptions import (
    ValidationError,
    MessageEmptyError,
    MessageTooLongError,
)


class TestRequestValidator:
    def test_missing_tenant_id(self):
        with pytest.raises(ValidationError):
            validate_chat_request(None, "user_1", "hello")

    def test_missing_user_id(self):
        with pytest.raises(ValidationError):
            validate_chat_request("taishan", None, "hello")

    def test_empty_message(self):
        with pytest.raises(MessageEmptyError):
            validate_chat_request("taishan", "user_1", "")

    def test_whitespace_message(self):
        with pytest.raises(MessageEmptyError):
            validate_chat_request("taishan", "user_1", "   ")

    def test_valid_request(self):
        # Should not raise
        validate_chat_request("taishan", "user_1", "客户说太贵了怎么办？")

    def test_too_long_message(self):
        long_msg = "x" * 7000
        with pytest.raises(MessageTooLongError):
            validate_chat_request("taishan", "user_1", long_msg)
