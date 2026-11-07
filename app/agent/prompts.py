"""系统提示词：定义智能体的职责边界与工具使用规则。"""

from app.cart import service as cart_service

_BASE_PROMPT = """你是「小购」，一个中文电商购物助手智能体，通过调用工具替用户完成选品、购物车与订单操作。

当前上下文：
{context}

工作规则：
1. 用户表达购物需求时，调用 search_products，query 参数传用户原话或核心关键词；工具返回的推荐列表是唯一事实来源，严禁编造商品、价格、库存。
2. 用户说"第N款 / 第几个 / 就要那个"时，调用 add_to_cart 并传 selection_index=N（对应推荐列表的序号）。
3. 查看购物车用 view_cart，移除商品用 remove_from_cart，结算用 checkout，支付用 pay_order。
4. 若工具返回以 NEED_CLARIFICATION 开头，请向用户提出最多两个具体问题（优先问预算和关键功能/场景），不要自行猜测。
5. checkout 成功后，明确告知订单号、金额、支付截止时间，并说明超时会自动失效。
6. 若上下文中有未读提醒，请在回复开头自然地告知用户。
7. 推荐结果由界面以商品卡片形式展示给用户，你的回复不要再罗列商品清单，用 1-2 句自然语言说明筛选思路和最优选择即可。
8. 回复使用简体中文；金额保留两位小数；不要输出 markdown 代码块和列表符号。"""


def _build_context(user_id: int) -> str:
    sections: list[str] = []

    cart = cart_service.get_cart(user_id)
    if cart["items"]:
        lines = "\n".join(
            f"- {item['name']} x{item['quantity']} = ¥{item['subtotal']:.2f}"
            for item in cart["items"]
        )
        sections.append(f"当前购物车（合计 ¥{cart['total']:.2f}）：\n{lines}")
    else:
        sections.append("当前购物车：空")

    pending = cart_service.list_orders(user_id, status="pending_payment")
    if pending:
        lines = "\n".join(
            f"- 订单 #{o['id']} ¥{o['total_price']:.2f}，{o['deadline']} 截止" for o in pending
        )
        sections.append(f"待支付订单：\n{lines}")

    unread = cart_service.list_reminders(user_id, unread_only=True)
    if unread:
        lines = "\n".join(f"- {r['message']}" for r in unread)
        sections.append(f"未读提醒：\n{lines}")

    return "\n\n".join(sections)


def build_system_prompt(user_id: int) -> str:
    return _BASE_PROMPT.format(context=_build_context(user_id))
