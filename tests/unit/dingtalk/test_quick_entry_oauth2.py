"""快捷入口 OAuth2 回调辅助函数单测（PC 浏览器场景）。"""

from sales_agent.integrations.dingtalk.quick_entry import (
    _oauth_result_page,
    _parse_oauth_state,
)
from fastapi.responses import HTMLResponse


class TestParseOauthState:
    def test_normal(self):
        assert _parse_oauth_state("pre_visit_prepare:taishan") == ("pre_visit_prepare", "taishan")

    def test_sales_block(self):
        assert _parse_oauth_state("sales_block_breakthrough:taishan") == (
            "sales_block_breakthrough", "taishan",
        )

    def test_missing_tenant(self):
        # 只有 action、无冒号 → tenant 为空，端点会判定失败
        action, tenant = _parse_oauth_state("pre_visit_prepare")
        assert action == "pre_visit_prepare" and tenant == ""

    def test_empty(self):
        assert _parse_oauth_state("") == ("", "")


class TestOAuthResultPage:
    def test_returns_html(self):
        resp = _oauth_result_page(True, "已发送")
        assert isinstance(resp, HTMLResponse)
        assert "已发送" in resp.body.decode("utf-8")

    def test_escapes_message(self):
        # 异常文本里的 HTML 不应原样渲染（防注入）
        resp = _oauth_result_page(False, "<script>x</script>")
        body = resp.body.decode("utf-8")
        assert "<script>" not in body
        assert "&lt;script&gt;" in body

    def test_ok_vs_err_color(self):
        ok_body = _oauth_result_page(True, "x").body.decode()
        err_body = _oauth_result_page(False, "x").body.decode()
        assert "#52c41a" in ok_body      # 成功绿
        assert "#ff4d4f" in err_body     # 失败红
