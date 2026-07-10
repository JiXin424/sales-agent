"""销售动作仓储状态机单元测试骨架。

Task 1 仅要求本文件存在并可导入；Task 3 会填充真实的状态机断言。
"""

from sales_agent.models.sales_action import (
    SalesActionCard,
    SalesActionDelivery,
    SalesActionEvent,
    SalesActionReminder,
)


def test_models_importable() -> None:
    """冒烟测试：四个模型均可正常导入并注册到 metadata。"""
    from sales_agent.core.database import Base

    for model in (SalesActionCard, SalesActionReminder, SalesActionDelivery, SalesActionEvent):
        assert model.__tablename__ in Base.metadata.tables
