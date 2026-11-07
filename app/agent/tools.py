"""工具层：Function Calling 的 schema 定义与执行分发。

模型给出的 tool arguments 是不可信输入：可能缺必填字段、类型不对、数值离谱，
甚至整段不是合法 JSON。这里统一用 Pydantic 校验，校验失败把错误原因作为
工具结果回填给模型，让它自我纠正；连续失败超过上限则降级为固定话术，
避免模型在同一处反复挣扎耗尽轮次。
"""

import json

from pydantic import BaseModel, Field, ValidationError, field_validator

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

# 每个会话最近一次推荐列表，用于把"第N款"解析成具体商品（确定性解析）。
# 与会话历史一样按 session_key 隔离，避免多窗口共用推荐列表导致指代错乱。
_last_recommendations: dict[str, list[dict]] = {}


# ---------------------------------------------------------------------------
# 工具参数模型：把模型的自由 JSON 收束为受控字段
# ---------------------------------------------------------------------------

class SearchArgs(BaseModel):
    """search_products 的参数。query 必填，其余可选。"""

    query: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=30)
    brand: str | None = Field(default=None, max_length=40)
    price_max: float | None = Field(default=None, ge=0, le=10_000_000)
    price_min: float | None = Field(default=None, ge=0, le=10_000_000)

    @field_validator("query", "category", "brand")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("price_min")
    @classmethod
    def _check_range(cls, value: float | None, info) -> float | None:
        other = info.data.get("price_max")
        if value is not None and other is not None and value > other:
            raise ValueError("price_min 不能大于 price_max")
        return value


class AddToCartArgs(BaseModel):
    """add_to_cart 的参数：商品 ID 与推荐序号二选一，数量默认 1。"""

    product_id: int | None = Field(default=None, ge=1)
    selection_index: int | None = Field(default=None, ge=1, le=50)
    quantity: int = Field(default=1, ge=1, le=99)

    @field_validator("quantity", mode="before")
    @classmethod
    def _default_quantity(cls, value):
        return 1 if value in (None, "") else value


class RemoveFromCartArgs(BaseModel):
    product_id: int = Field(ge=1)


class EmptyArgs(BaseModel):
    """view_cart / checkout 无参数。"""


class PayOrderArgs(BaseModel):
    order_id: int = Field(ge=1)


# 工具名 -> 参数模型。校验在 execute_tool 里统一完成
ARG_MODELS: dict[str, type[BaseModel]] = {
    "search_products": SearchArgs,
    "add_to_cart": AddToCartArgs,
    "remove_from_cart": RemoveFromCartArgs,
    "view_cart": EmptyArgs,
    "checkout": EmptyArgs,
    "pay_order": PayOrderArgs,
}

MAX_TOOL_FAILURES = 2  # 同一会话连续失败上限，超过后降级
_tool_failures: dict[str, int] = {}


def get_last_recommendations(session_key: str) -> list[dict]:
    return _last_recommendations.get(session_key, [])


def _format_recommendation(result: pipeline.RecommendResult, session_key: str) -> str:
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
    _last_recommendations[session_key] = [
        {
            "product_id": item.product["id"],
            "name": item.product["name"],
            "brand": item.product["brand"],
            "category": item.product["category"],
            "category_path": item.product.get("category_path", ""),
            "image": item.product.get("image", ""),
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


def _tool_search_products(user_id: int, args: "SearchArgs", session_key: str) -> str:
    filters = HardConstraints(
        category=args.category,
        brand_in=[args.brand] if args.brand else [],
        price_min=args.price_min,
        price_max=args.price_max,
    )
    result = pipeline.recommend(args.query, filters=filters)
    return _format_recommendation(result, session_key)


def _tool_add_to_cart(user_id: int, args: "AddToCartArgs", session_key: str) -> str:
    product_id = args.product_id
    selection_index = args.selection_index

    if product_id is None and selection_index is not None:
        recommendations = _last_recommendations.get(session_key, [])
        if not 1 <= selection_index <= len(recommendations):
            return f"加购失败：推荐列表中没有第 {selection_index} 款，请先让用户确认商品"
        product_id = recommendations[selection_index - 1]["product_id"]

    if product_id is None:
        return "加购失败：缺少 product_id 或 selection_index"
    return cart_service.add_to_cart(user_id, product_id, args.quantity)


def _tool_view_cart(user_id: int) -> str:
    cart = cart_service.get_cart(user_id)
    if not cart["items"]:
        return "购物车是空的。"
    lines = [
        f"- [ID:{item['product_id']}] {item['name']} x{item['quantity']} = ¥{item['subtotal']:.2f}"
        for item in cart["items"]
    ]
    return "当前购物车：\n" + "\n".join(lines) + f"\n合计：¥{cart['total']:.2f}"


def _validate(name: str, arguments: dict) -> tuple[BaseModel | None, str]:
    """校验工具参数。返回 (模型实例, 错误说明)，二者互斥。"""
    model = ARG_MODELS.get(name)
    if model is None:
        return None, f"未知工具 {name}"
    try:
        return model.model_validate(arguments), ""
    except ValidationError as exc:
        problems = "；".join(
            f"{'.'.join(str(p) for p in err['loc']) or '参数'}: {err['msg']}"
            for err in exc.errors()[:3]
        )
        return None, f"参数校验未通过（{problems}）"


def execute_tool(user_id: int, name: str, arguments_json: str, session_key: str = "") -> str:
    """执行一次工具调用，永远返回字符串结果供模型阅读。

    session_key 决定"最近推荐列表"的归属会话；不传时回退到用户级键，
    供脚本与测试直接调用。购物车、订单等仍始终归属 user_id。
    """
    if not session_key:
        session_key = f"user:{user_id}"

    try:
        raw = json.loads(arguments_json) if arguments_json.strip() else {}
    except json.JSONDecodeError:
        return _fail(session_key, name, "参数不是合法 JSON，请重新以 JSON 对象形式给出参数")

    if not isinstance(raw, dict):
        return _fail(session_key, name, "参数必须是 JSON 对象")

    args, error = _validate(name, raw)
    if args is None:
        return _fail(session_key, name, error)

    try:
        result = _dispatch(user_id, name, args, session_key)
    except (KeyError, TypeError, ValueError) as exc:
        return _fail(session_key, name, f"参数错误（{exc}）")
    _tool_failures[session_key] = 0  # 成功一次就重置失败计数
    return result


def _dispatch(user_id: int, name: str, args: BaseModel, session_key: str) -> str:
    if name == "search_products":
        return _tool_search_products(user_id, args, session_key)
    if name == "add_to_cart":
        return _tool_add_to_cart(user_id, args, session_key)
    if name == "remove_from_cart":
        return cart_service.remove_from_cart(user_id, args.product_id)
    if name == "view_cart":
        return _tool_view_cart(user_id)
    if name == "checkout":
        return cart_service.checkout(user_id)
    if name == "pay_order":
        return cart_service.pay_order(user_id, args.order_id)
    return f"工具调用失败：未知工具 {name}"


def _fail(session_key: str, name: str, reason: str) -> str:
    """记录失败次数；超过上限后返回降级话术，阻止模型在同一处死循环。"""
    failures = _tool_failures.get(session_key, 0) + 1
    _tool_failures[session_key] = failures
    if failures > MAX_TOOL_FAILURES:
        return (
            f"工具 {name} 连续 {failures} 次调用失败，请停止重试，直接告诉用户"
            "刚才的操作没成功、需要他换个说法或稍后再试。"
        )
    return f"工具调用失败：{name} {reason}。请修正参数后重试一次。"
