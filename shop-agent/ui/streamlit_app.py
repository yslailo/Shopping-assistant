"""Streamlit 演示前端：聊天区 + 购物车/订单/提醒侧栏。

运行：python -m streamlit run ui/streamlit_app.py
"""

import os

import requests
import streamlit as st

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")
USER_ID = 1

st.set_page_config(page_title="小购 · 购物助手", page_icon="🛒", layout="wide")


def api_get(path: str) -> dict:
    response = requests.get(f"{API_BASE}{path}", timeout=60)
    response.raise_for_status()
    return response.json()


def api_post(path: str, payload: dict) -> dict:
    response = requests.post(f"{API_BASE}{path}", json=payload, timeout=120)
    response.raise_for_status()
    return response.json()


def refresh_state() -> None:
    """从后端拉取购物车、订单与提醒，写入会话状态。"""
    try:
        st.session_state.cart = api_get(f"/api/cart?user_id={USER_ID}")
        st.session_state.orders = api_get(f"/api/orders?user_id={USER_ID}")["orders"]
        st.session_state.reminders = api_get(f"/api/reminders?user_id={USER_ID}")["reminders"]
    except requests.RequestException:
        st.session_state.cart = st.session_state.get("cart", {"items": [], "total": 0})
        st.session_state.orders = st.session_state.get("orders", [])
        st.session_state.reminders = st.session_state.get("reminders", [])


# ---------------------------------------------------------------------------
# 侧栏：购物车 / 订单 / 提醒
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("🛒 我的购物车")
    refresh_state()

    cart = st.session_state.get("cart", {"items": [], "total": 0})
    if cart["items"]:
        for item in cart["items"]:
            st.markdown(
                f"{item['name']} × {item['quantity']}　¥{item['subtotal']:.2f}"
            )
        st.markdown(f"**合计：¥{cart['total']:.2f}**")
        if st.button("结算，生成待支付订单", use_container_width=True):
            st.toast(api_post("/api/chat", {"user_id": USER_ID, "message": "帮我结算购物车"}).get("reply", ""))
            st.rerun()
    else:
        st.caption("购物车是空的，和助手聊聊想买什么吧～")

    st.divider()
    st.subheader("📦 我的订单")
    orders = st.session_state.get("orders", [])
    if not orders:
        st.caption("暂无订单")
    for order in orders:
        status_label = {
            "pending_payment": "⏳ 待支付",
            "paid": "✅ 已支付",
            "expired": "❌ 已超时",
        }[order["status"]]
        st.markdown(
            f"订单 #{order['id']}　¥{order['total_price']:.2f}　{status_label}"
        )
        if order["status"] == "pending_payment":
            if st.button(f"支付订单 #{order['id']}", key=f"pay-{order['id']}"):
                api_post("/api/orders/pay", {"user_id": USER_ID, "order_id": order["id"]})
                st.rerun()

    st.divider()
    st.subheader("🔔 提醒")
    reminders = st.session_state.get("reminders", [])
    if reminders:
        for reminder in reminders:
            st.info(reminder["message"])
        if st.button("全部标为已读"):
            api_post("/api/reminders/read", {"user_id": USER_ID})
            st.rerun()
    else:
        st.caption("暂无新提醒")

# ---------------------------------------------------------------------------
# 主区：对话
# ---------------------------------------------------------------------------

st.title("🛒 小购 · 购物助手")
st.caption("告诉我你的需求，我来帮你挑商品、管购物车、盯着支付时间。试试：\"预算 500 以内，想买个降噪耳机\"")

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if user_input := st.chat_input("输入你的购物需求…"):
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("小购思考中…"):
            try:
                data = api_post("/api/chat", {"user_id": USER_ID, "message": user_input})
                reply = data.get("reply", "服务暂时没有返回内容，请稍后再试。")
            except requests.RequestException as exc:
                reply = f"无法连接后端服务（{exc}），请确认已在 8000 端口启动 API。"
        st.markdown(reply)

    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.rerun()
