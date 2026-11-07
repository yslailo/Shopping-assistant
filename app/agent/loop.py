"""Function Calling 主循环。

单轮对话流程：组装带实时上下文的系统提示词 -> 调用模型 ->
模型请求工具则执行并把结果回填 -> 直到模型给出最终文本。
会话历史只保留 user/assistant 文本，工具消息不落历史，控制长度。

上下文隔离：对话历史按前端会话（session_id）存放，购物车/订单等
业务状态按 user_id 存放。同一账号开多个浏览器窗口时，各窗口的
对话互不干扰（语义与真实电商一致：会话独立，账号数据共享）。
"""

from app.agent import tools
from app.agent.prompts import build_system_prompt
from app.cart import service as cart_service
from app.llm.client import llm

MAX_STEPS = 6          # 单轮最多工具往返次数，防止死循环
HISTORY_LIMIT = 20     # 保留的最近历史条数
MAX_SESSIONS = 100     # 内存中最多保留的会话数，超出后丢弃最早的

# 会话键 -> 历史消息。演示环境用进程内存；多机部署时替换为 Redis 等外部存储
_sessions: dict[str, list[dict]] = {}


def _session_key(user_id: int, session_id: str) -> str:
    """会话隔离键：优先前端 session_id，缺省回退到用户级（脚本/测试场景）。"""
    return session_id.strip() if session_id and session_id.strip() else f"user:{user_id}"


def run_turn_stream(user_id: int, user_message: str, session_id: str = ""):
    """流式处理一轮用户输入，产出事件字典。

    事件格式：
    - {"delta": "文本增量"}          正文 token，可直接推给前端渲染
    - {"done": {...}}               结束时的业务快照（回复/购物车/推荐/提醒）

    工具调用轮次不产生 delta 之外的额外事件：模型决定调用工具前若已输出
    文本，那些文本已经流给前端，最终回复会把它们一并计入历史。
    """
    cart_service.refresh_order_status()  # 每轮先推进订单状态，保证上下文准确

    session_key = _session_key(user_id, session_id)
    if session_key not in _sessions and len(_sessions) >= MAX_SESSIONS:
        _sessions.pop(next(iter(_sessions)))  # 淘汰最早创建的会话，防止内存无限增长
    history = _sessions.setdefault(session_key, [])
    history.append({"role": "user", "content": user_message})

    messages: list[dict] = [{"role": "system", "content": build_system_prompt(user_id)}]
    messages.extend(history[-HISTORY_LIMIT:])

    reply_parts: list[str] = []
    for _step in range(MAX_STEPS):
        round_content: list[str] = []
        round_tool_calls = None
        for event in llm.stream_chat(messages, tools=tools.TOOL_SCHEMAS):
            if "content" in event:
                round_content.append(event["content"])
                yield {"delta": event["content"]}
            elif "tool_calls" in event:
                round_tool_calls = event["tool_calls"]

        text = "".join(round_content)
        reply_parts.append(text)
        if not round_tool_calls:
            break  # 没有工具调用说明是最终回复

        messages.append({"role": "assistant", "content": text, "tool_calls": round_tool_calls})
        for call in round_tool_calls:
            result = tools.execute_tool(user_id, call["function"]["name"], call["function"]["arguments"], session_key)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})

    reply = "".join(reply_parts).strip()
    if not reply:
        reply = "抱歉，这一轮没有处理成功，请换个说法再试一次。"

    history.append({"role": "assistant", "content": reply})

    yield {
        "done": {
            "reply": reply,
            "cart": cart_service.get_cart(user_id),
            "recommendations": tools.get_last_recommendations(session_key),
            "reminders": cart_service.list_reminders(user_id, unread_only=True),
        }
    }


def run_turn(user_id: int, user_message: str, session_id: str = "") -> dict:
    """非流式封装：跑完整个回合，只返回最终业务快照。"""
    final: dict = {}
    for event in run_turn_stream(user_id, user_message, session_id):
        if "done" in event:
            final = event["done"]
    return final
