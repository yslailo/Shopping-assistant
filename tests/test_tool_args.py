"""工具参数校验的单元测试。

模型给的工具参数是不可信输入，这里覆盖缺字段、类型错、超范围、非法 JSON
以及"连续失败后降级"这几类真实会出现的坏情况。
"""

import pytest

from app.agent import tools


@pytest.fixture(autouse=True)
def _reset_failures():
    tools._tool_failures.clear()
    tools._last_recommendations.clear()
    yield
    tools._tool_failures.clear()
    tools._last_recommendations.clear()


def test_bad_json_returns_error_not_raise():
    result = tools.execute_tool(1, "search_products", "{not json", "s1")
    assert "JSON" in result


def test_missing_required_query():
    result = tools.execute_tool(1, "search_products", '{"category": "枕头"}', "s1")
    assert "校验未通过" in result and "query" in result


def test_price_min_greater_than_max_rejected():
    result = tools.execute_tool(
        1, "search_products", '{"query": "枕头", "price_min": 800, "price_max": 100}', "s1"
    )
    assert "price_min" in result


def test_negative_price_rejected():
    result = tools.execute_tool(
        1, "search_products", '{"query": "枕头", "price_max": -5}', "s1"
    )
    assert "校验未通过" in result


def test_quantity_out_of_range_rejected():
    result = tools.execute_tool(1, "add_to_cart", '{"product_id": 1, "quantity": 0}', "s1")
    assert "校验未通过" in result


def test_unknown_tool_reported():
    result = tools.execute_tool(1, "delete_database", "{}", "s1")
    assert "未知工具" in result


def test_escalates_to_degraded_hint_after_repeated_failures():
    bad = '{"product_id": "abc"}'  # 类型错误，稳定触发校验失败
    first = tools.execute_tool(1, "add_to_cart", bad, "s1")
    second = tools.execute_tool(1, "add_to_cart", bad, "s1")
    third = tools.execute_tool(1, "add_to_cart", bad, "s1")
    assert "重试一次" in first
    assert "重试一次" in second
    assert "停止重试" in third  # 超过上限后不再诱导模型重试


def test_business_rejection_is_not_counted_as_tool_failure():
    """业务层拒绝（如推荐序号越界）不是参数错误，不该累计失败次数。"""
    tools.execute_tool(1, "add_to_cart", '{"selection_index": 3}', "s1")
    assert tools._tool_failures.get("s1", 0) == 0


def test_success_resets_failure_counter():
    tools.execute_tool(1, "add_to_cart", '{"product_id": "abc"}', "s1")
    tools.execute_tool(1, "view_cart", "{}", "s1")
    assert tools._tool_failures.get("s1", 0) == 0


def test_empty_arguments_accepted_for_view_cart():
    result = tools.execute_tool(1, "view_cart", "", "s1")
    assert "购物车" in result
