"""工具层：Function Calling 的 schema 定义与执行分发。"""

import json

from app.cart import service as cart_service
from app.selection import pipeline
from app.selection.parser import HardConstraints

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "根据用户的购物需求推荐商品，返回 Top-5 列表（含价格、匹配的偏好和推荐理由）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用户的购物需求原话或核心关键词"},
                    "category": {"type": "string", "description": "品类，如：洗发水、蓝牙耳机"},
                    "brand": {"type": "string", "description": "品牌"},
                    "price_max": {"type": "number", "description": "预算上限（元）"},
                    "price_min": {"type": "number", "description": "预算下限（元）"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_cart",
            "description": "把商品加入购物车。用户指定推荐列表中的第N款时传 selection_index；直接给出商品ID时传 product_id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "商品ID"},
                    "selection_index": {"type": "integer", "description": "推荐列表中的序号（1 表示第 1 款）"},
                    "quantity": {"type": "integer", "description": "数量，默认 1"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_from_cart",
            "description": "从购物车移除指定商品。",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "商品ID"},
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_cart",
            "description": "查看当前购物车明细与合计金额。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "checkout",
            "description": "结算购物车，生成待支付订单并给出支付截止时间。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pay_order",
            "description": "支付指定订单（模拟支付）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer", "description": "订单号"},
                },
                "required": ["order_id"],
            },
        },
    },
]

# 每个用户最近一次推荐列表，用于把"第N款"解析成具体商品（确定性解析）
_last_recommendations: dict[int, list[dict]] = {}


def get_last_recommendations(user_id: int) -> list[dict]:
    return _last_recommendations.get(user_id, [])


def _format_recommendation(result: pipeline.RecommendResult, user_id: int) -> str:
    if result.mode == "clarification":
        return f"NEED_CLARIFICATION: {result.question}"
    if result.mode == "no_result":
        return result.summary

    lines: list[str] = [f"【Top-{len(result.items)} 推荐】{result.summary}"]
    for index, item in enumerate(result.items, start=1):
        product = item.product
        matched = "、".join(item.matched) if item.matched else "—"
        missed = "、".join(item.missed) if item.missed else "—"
        lines.append(
            f"{index}. [ID:{product['id']}] {product['name']} ¥{product['price']:.2f} ★{product['rating']}\n"
            f"   匹配偏好: {matched}；未满足: {missed}\n"
            f"   推荐理由: {item.reason}（综合得分 {item.score:.2f}）"
        )
    lines.append('用户说"第N款/那个"时，请用 add_to_cart 的 selection_index 参数加购。')
    lines.append("注意：推荐结果已由界面渲染成商品卡片展示给用户，回复中不要重复罗列商品清单。")

    # 前端会把推荐结果渲染成可点击的商品卡片，这里同步保存结构化数据
    _last_recommendations[user_id] = [
        {
            "product_id": item.product["id"],
            "name": item.product["name"],
            "brand": item.product["brand"],
            "category": item.product["category"],
            "price": item.product["price"],
            "rating": item.product["rating"],
            "features": [f.strip() for f in item.product["features"].split(",") if f.strip()],
            "score": item.score,
            "matched": item.matched,
            "missed": item.missed,
            "reason": item.reason,
        }
        for item in result.items
    ]
    return "\n".join(lines)


def _tool_search_products(user_id: int, arguments: dict) -> str:
    filters = HardConstraints(
        category=arguments.get("category"),
        brand_in=[arguments["brand"]] if arguments.get("brand") else [],
        price_min=arguments.get("price_min"),
        price_max=arguments.get("price_max"),
    )
    result = pipeline.recommend(arguments["query"], filters=filters)
    return _format_recommendation(result, user_id)


def _tool_add_to_cart(user_id: int, arguments: dict) -> str:
    product_id = arguments.get("product_id")
    selection_index = arguments.get("selection_index")

    if product_id is None and selection_index is not None:
        recommendations = _last_recommendations.get(user_id, [])
        if not 1 <= int(selection_index) <= len(recommendations):
            return f"加购失败：推荐列表中没有第 {selection_index} 款，请先让用户确认商品"
        product_id = recommendations[int(selection_index) - 1]["product_id"]

    if product_id is None:
        return "加购失败：缺少 product_id 或 selection_index"
    return cart_service.add_to_cart(user_id, int(product_id), int(arguments.get("quantity") or 1))


def _tool_view_cart(user_id: int) -> str:
    cart = cart_service.get_cart(user_id)
    if not cart["items"]:
        return "购物车是空的。"
    lines = [
        f"- [ID:{item['product_id']}] {item['name']} x{item['quantity']} = ¥{item['subtotal']:.2f}"
        for item in cart["items"]
    ]
    return "当前购物车：\n" + "\n".join(lines) + f"\n合计：¥{cart['total']:.2f}"


def execute_tool(user_id: int, name: str, arguments_json: str) -> str:
    """执行一次工具调用，永远返回字符串结果供模型阅读。"""
    try:
        arguments = json.loads(arguments_json) if arguments_json.strip() else {}
    except json.JSONDecodeError:
        return f"工具调用失败：{name} 的参数不是合法 JSON"

    try:
        if name == "search_products":
            return _tool_search_products(user_id, arguments)
        if name == "add_to_cart":
            return _tool_add_to_cart(user_id, arguments)
        if name == "remove_from_cart":
            return cart_service.remove_from_cart(user_id, int(arguments["product_id"]))
        if name == "view_cart":
            return _tool_view_cart(user_id)
        if name == "checkout":
            return cart_service.checkout(user_id)
        if name == "pay_order":
            return cart_service.pay_order(user_id, int(arguments["order_id"]))
        return f"工具调用失败：未知工具 {name}"
    except (KeyError, TypeError, ValueError) as exc:
        return f"工具调用失败：{name} 参数错误（{exc}）"
