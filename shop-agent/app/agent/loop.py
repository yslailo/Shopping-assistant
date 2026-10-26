"""Function Calling 主循环。

单轮对话流程：组装带实时上下文的系统提示词 -> 调用模型 ->
模型请求工具则执行并把结果回填 -> 直到模型给出最终文本。
会话历史只保留 user/assistant 文本，工具消息不落历史，控制长度。
"""

from app.agent import tools
from app.agent.prompts import build_system_prompt
from app.cart import service as cart_service
from app.llm.client import llm

MAX_STEPS = 6          # 单轮最多工具往返次数，防止死循环
HISTORY_LIMIT = 20     # 保留的最近历史条数

# 演示环境按单用户使用内存会话；多用户部署时替换为 Redis 等外部存储
_sessions: dict[int, list[dict]] = {}


def _serialize_tool_calls(message) -> list[dict]:
    """把 SDK 返回的 tool_calls 转为可回传给接口的字典。"""
    return [
        {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.function.name,
                "arguments": call.function.arguments,
            },
        }
        for call in (message.tool_calls or [])
    ]


def run_turn(user_id: int, user_message: str) -> dict:
    """处理一轮用户输入，返回回复与最新业务状态。"""
    cart_service.refresh_order_status()  # 每轮先推进订单状态，保证上下文准确

    history = _sessions.setdefault(user_id, [])
    history.append({"role": "user", "content": user_message})

    messages: list[dict] = [{"role": "system", "content": build_system_prompt(user_id)}]
    messages.extend(history[-HISTORY_LIMIT:])

    reply = ""
    for _step in range(MAX_STEPS):
        message = llm.chat(messages, tools=tools.TOOL_SCHEMAS)

        if not message.tool_calls:
            reply = (message.content or "").strip()
            break

        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": _serialize_tool_calls(message),
            }
        )
        for call in message.tool_calls:
            result = tools.execute_tool(user_id, call.function.name, call.function.arguments)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": result}
            )

    if not reply:
        reply = "抱歉，这一轮没有处理成功，请换个说法再试一次。"

    history.append({"role": "assistant", "content": reply})

    return {
        "reply": reply,
        "cart": cart_service.get_cart(user_id),
        "recommendations": tools.get_last_recommendations(user_id),
        "reminders": cart_service.list_reminders(user_id, unread_only=True),
    }
