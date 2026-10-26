"""Streamlit 前端：聊天 + 商品卡片 + 右侧信息面板。

布局说明：不使用 Streamlit 原生侧栏（窄窗口会被自动折叠导致内容不可见），
购物车 / 订单 / 提醒 / 模型设置全部放在主区右列，任何窗口宽度下都可见。

运行：python -m streamlit run ui/streamlit_app.py
"""

import html
import os
import uuid

import requests
import streamlit as st

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000")
USER_ID = 1

st.set_page_config(page_title="小购 · 购物助手", layout="wide")

# 每个浏览器窗口/每次刷新都是一条独立会话，对话上下文在服务端按此隔离；
# 刷新页面后页面与上下文同时清空，两者保持一致。
if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex

# ---------------------------------------------------------------------------
# 样式
# ---------------------------------------------------------------------------

CSS = """
<style>
:root {
  --ink: #18181b;
  --muted: #71717a;
  --line: #e8e8ea;
  --bg: #fbfbfa;
  --accent: #0f766e;
  --accent-soft: #ecfdf5;
}
html, body, [class*="st-"] {
  font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  color: var(--ink);
}
/* Streamlit 的 Material Symbols 图标字体在本机不可用，密码框的眼睛按钮会露出
   原始图标名 "visibility"。隐藏图标文本，改用内联 SVG 绘制小眼睛图标，
   不依赖任何字体文件；背景图按输入框类型切换开眼/闭眼两态。 */
[data-testid="stIconMaterial"] { font-size: 0 !important; }
[data-testid="stTextInputRootElement"]:has(input[type="password"]) [data-testid="stIconMaterial"]::before,
[data-testid="stTextInputRootElement"]:has(input[type="text"]) [data-testid="stIconMaterial"]::before {
  content: ""; display: inline-block; width: 17px; height: 17px; vertical-align: -4px;
  background: center / contain no-repeat; transition: filter .15s ease; }
[data-testid="stTextInputRootElement"] [data-testid="stIconMaterial"]:hover::before { filter: brightness(.55); }
[data-testid="stTextInputRootElement"]:has(input[type="password"]) [data-testid="stIconMaterial"]::before {
  background-image: url("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%239ca3af' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'><path d='M2.6 12S6.1 5.6 12 5.6 21.4 12 21.4 12 17.9 18.4 12 18.4 2.6 12 2.6 12z'/><circle cx='12' cy='12' r='2.9'/></svg>"); }
[data-testid="stTextInputRootElement"]:has(input[type="text"]) [data-testid="stIconMaterial"]::before {
  background-image: url("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2318181b' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'><path d='M2.6 12S6.1 5.6 12 5.6 21.4 12 21.4 12 17.9 18.4 12 18.4 2.6 12 2.6 12z'/><circle cx='12' cy='12' r='2.9'/><path d='M4.2 3.8 19.8 19.4'/></svg>"); }
.stApp { background: var(--bg); }
.block-container { padding-top: 4.2rem; padding-bottom: 5rem; max-width: 1240px; }
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"] { visibility: hidden; }
/* Streamlit 顶栏：默认是不透明的固定白条，会盖住页面顶部内容，置为透明并放行点击 */
header[data-testid="stHeader"] { background: transparent !important;
  box-shadow: none !important; pointer-events: none; }
[data-testid="stAppDeployButton"], [data-testid="stAppDeployButtonContainer"] { display: none !important; }

.brand { display: flex; align-items: center; gap: 10px; }
.brand svg { color: var(--ink); }
.brand-name { font-size: 21px; font-weight: 600; letter-spacing: -0.02em; }
.brand-sub { color: var(--muted); font-size: 13px; margin: 2px 0 20px 34px; }

.section { display: flex; align-items: center; gap: 8px; font-size: 13px;
  font-weight: 600; margin: 16px 0 8px; letter-spacing: 0.01em; }
.section:first-child { margin-top: 2px; }
.section svg { color: var(--muted); }
.rule { height: 1px; background: var(--line); margin: 14px 0; border: 0; }

.msg-user { background: #fff; border: 1px solid var(--line); border-radius: 14px;
  padding: 12px 16px; margin: 8px 0 8px auto; max-width: 82%;
  font-size: 14.5px; line-height: 1.6; white-space: pre-wrap; }
.msg-bot { font-size: 14.5px; line-height: 1.75; color: #27272a; margin: 10px 0 14px;
  max-width: 92%; white-space: pre-wrap; }

/* 商品条目：左图右文，竖向排列，整行点击进入详情 */
.card-media { height: 84px; border-radius: 12px; background: #f6f6f5;
  display: flex; align-items: center; justify-content: center; margin-bottom: 12px; color: #52525b; }
.card-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; min-height: 20px; }
.pcard { display: flex; gap: 14px; align-items: flex-start; background: #fff;
  border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px;
  margin-bottom: 12px; }
.pcard-thumb { width: 60px; height: 60px; flex: none; border-radius: 10px; background: #f6f6f5;
  display: flex; align-items: center; justify-content: center; color: #52525b; }
.pcard-body { flex: 1; min-width: 0; }
.pcard-title { font-size: 14px; font-weight: 550; line-height: 1.45; color: var(--ink); }
.pcard-meta { color: var(--muted); font-size: 12px; margin-top: 3px; }
.pcard-reason { font-size: 11.5px; color: var(--muted); line-height: 1.55; margin-top: 5px;
  display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden; }
.pcard-side { text-align: right; flex: none; }
.pcard-price { font-size: 15px; font-weight: 650; letter-spacing: -0.01em; }
/* 透明点击热区：只作用于商品条目的 keyed 容器（st-key-pcard-*），
   不能匹配外层会话列，否则会误伤其它按钮和间距导致条目重叠 */
[class*="st-key-pcard"] { position: relative; cursor: pointer; }
[class*="st-key-pcard"] .stVerticalBlock { gap: 0; }
[class*="st-key-pcard"]:hover .pcard { border-color: #d4d4d8;
  box-shadow: 0 6px 20px rgba(24,24,27,.06); }
[class*="st-key-pcard"] div[data-testid="stElementContainer"]:has(div.stButton) {
  position: absolute; inset: 0; width: auto; height: auto; margin: 0; z-index: 3; }
[class*="st-key-pcard"] div.stButton { width: 100%; height: 100%; margin: 0; }
[class*="st-key-pcard"] div.stButton > button { width: 100%; height: 100%;
  min-height: 0; padding: 0; border: 0; background: transparent; opacity: 0; cursor: pointer; }

.tag { font-size: 11px; color: #52525b; background: #f4f4f5;
  border-radius: 6px; padding: 2px 7px; white-space: nowrap; }
.tag.accent { color: var(--accent); background: var(--accent-soft); }

.row { display: flex; justify-content: space-between; gap: 10px; font-size: 13px;
  padding: 5px 0; color: #3f3f46; }
.row .k { color: var(--muted); }
.total { display: flex; justify-content: space-between; font-size: 14px;
  font-weight: 600; padding-top: 10px; margin-top: 6px; border-top: 1px solid var(--line); }
.status { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: #3f3f46; }
.dot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
.dot.warn { background: #d97706; } .dot.ok { background: #059669; } .dot.bad { background: #dc2626; }
.hint { color: var(--muted); font-size: 12.5px; line-height: 1.7; }
.note { background: #fff; border: 1px solid var(--line); border-left: 2px solid var(--accent);
  border-radius: 10px; padding: 10px 14px; font-size: 13px; color: #3f3f46; margin: 6px 0; }

/* 右侧信息面板外观：作用于 st.container(border=True) 的包裹层 */
div[data-testid="stVerticalBlockBorderWrapper"] > div {
  border: 1px solid var(--line) !important; border-radius: 18px !important; background: #fff; }

div.stButton > button { border-radius: 10px; border: 1px solid var(--line);
  background: #fff; color: #3f3f46; font-size: 12.5px; padding: 5px 10px; width: 100%;
  box-shadow: none; transition: all .16s ease; }
div.stButton > button:hover { border-color: var(--ink); color: var(--ink); background: #fff; }

div[data-testid="stChatInput"] textarea { font-size: 14px; }
div[data-testid="stChatInput"] { border-radius: 14px; }
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# SVG 图标（线性风格，统一 24 视窗）
# ---------------------------------------------------------------------------

def icon(name: str, size: int = 18, width: float = 1.6) -> str:
    paths = {
        "bag": '<path d="M6 8h12l-1 12H7L6 8z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/>',
        "cart": '<path d="M3 4h2l2.3 11h10.2l2-8H6"/><circle cx="9.5" cy="18.5" r="1.3"/><circle cx="17" cy="18.5" r="1.3"/>',
        "box": '<path d="M20 8.5 12 4 4 8.5 12 13l8-4.5z"/><path d="M4 8.5V16l8 4 8-4V8.5"/><path d="M12 13v7"/>',
        "bell": '<path d="M18 9a6 6 0 1 0-12 0c0 4.5-1.8 5.5-1.8 5.5h15.6S18 13.5 18 9z"/><path d="M10.5 19a1.8 1.8 0 0 0 3 0"/>',
        "search": '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/>',
        "headphone": '<path d="M4 14v-2a8 8 0 0 1 16 0v2"/><rect x="3" y="14" width="4" height="6" rx="2"/><rect x="17" y="14" width="4" height="6" rx="2"/>',
        "phone": '<rect x="7" y="3" width="10" height="18" rx="2.6"/><path d="M10.8 18h2.4"/>',
        "laptop": '<rect x="4.5" y="5" width="15" height="10.5" rx="2"/><path d="M2.5 19h19"/>',
        "bottle": '<path d="M9.5 7V4.5h5V7"/><rect x="7" y="7" width="10" height="13" rx="2.6"/><path d="M10 11h4"/>',
        "cooker": '<rect x="4" y="9.5" width="16" height="10.5" rx="3"/><path d="M8.5 9.5V8a3.5 3.5 0 0 1 7 0v1.5"/>',
        "dryer": '<circle cx="10" cy="9.5" r="4"/><path d="m13 10.5 6-3v6l-6-1"/><path d="M9 13.5v6"/>',
    }
    body = paths.get(name, paths["box"])
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


CATEGORY_ICONS = {
    "蓝牙耳机": "headphone",
    "智能手机": "phone",
    "笔记本电脑": "laptop",
    "洗发水": "bottle",
    "电饭煲": "cooker",
    "吹风机": "dryer",
}


def category_icon(category: str, size: int = 34) -> str:
    return icon(CATEGORY_ICONS.get(category, "box"), size=size, width=1.3)


# ---------------------------------------------------------------------------
# 后端接口
# ---------------------------------------------------------------------------

def api_get(path: str) -> dict:
    response = requests.get(f"{API_BASE}{path}", timeout=60)
    response.raise_for_status()
    return response.json()


def api_post(path: str, payload: dict | None = None) -> dict:
    response = requests.post(f"{API_BASE}{path}", json=payload or {}, timeout=120)
    response.raise_for_status()
    return response.json()


def api_delete(path: str) -> None:
    requests.delete(f"{API_BASE}{path}", timeout=30).raise_for_status()


def refresh_state() -> None:
    """拉取购物车、订单与提醒。"""
    try:
        st.session_state.cart = api_get(f"/api/cart?user_id={USER_ID}")
        st.session_state.orders = api_get(f"/api/orders?user_id={USER_ID}")["orders"]
        st.session_state.reminders = api_get(f"/api/reminders?user_id={USER_ID}")["reminders"]
    except requests.RequestException:
        st.session_state.setdefault("cart", {"items": [], "total": 0})
        st.session_state.setdefault("orders", [])
        st.session_state.setdefault("reminders", [])


def money(value: float) -> str:
    return f"¥{value:,.2f}"


# ---------------------------------------------------------------------------
# 商品卡片
# ---------------------------------------------------------------------------

def render_cards(recommendations: list[dict]) -> None:
    """把推荐结果渲染成竖向商品条目，点击图片/标题任意处进入详情弹窗。"""
    if not recommendations:
        return
    for rank, item in enumerate(recommendations, 1):
        # 带 key 的容器：容器本身获得 st-key-pcard-* 类，点击热区样式只作用于它内部
        with st.container(key=f"pcard-{rank}-{item['product_id']}"):
            matched = item.get("matched") or []
            tags = "".join(
                f'<span class="tag accent">{html.escape(t)}</span>' for t in matched[:3]
            ) or f'<span class="tag">{html.escape(item["category"])}</span>'
            st.markdown(
                f"""
                <div class="pcard">
                  <div class="pcard-thumb">{category_icon(item["category"], size=30)}</div>
                  <div class="pcard-body">
                    <div class="pcard-title">{html.escape(item["name"])}</div>
                    <div class="pcard-meta">第 {rank} 款 · {html.escape(item["brand"])} · {item["rating"]:.1f} 分</div>
                    <div class="card-tags">{tags}</div>
                    <div class="pcard-reason">{html.escape(item.get("reason") or "")}</div>
                  </div>
                  <div class="pcard-side"><div class="pcard-price">{money(item["price"])}</div></div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            # 透明热区：按钮经 CSS 隐藏并铺满整个条目，点击商品行任意位置即打开详情
            if st.button("打开商品详情", key=f"open-{rank}-{item['product_id']}"):
                st.session_state.detail_id = item["product_id"]
                st.rerun()


@st.dialog("商品详情", width="large")
def show_product_dialog(product_id: int) -> None:
    try:
        product = api_get(f"/api/products/{product_id}")
    except requests.RequestException as exc:
        st.error(f"加载商品失败：{exc}")
        return

    left, right = st.columns([1, 1.6], gap="large")
    with left:
        st.markdown(
            f'<div class="card-media" style="height:200px;border-radius:14px;">'
            f"{category_icon(product['category'], size=72)}</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="hint" style="margin-top:10px;">{html.escape(product["description"])}</div>',
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            f'<div style="font-size:19px;font-weight:600;line-height:1.4;">'
            f'{html.escape(product["name"])}</div>'
            f'<div class="hint" style="margin-top:6px;">'
            f'{html.escape(product["brand"])} · {html.escape(product["category"])}'
            f' &nbsp;|&nbsp; 评分 {product["rating"]:.1f} &nbsp;|&nbsp; 库存 {product["stock"]}</div>'
            f'<div style="font-size:24px;font-weight:650;margin:14px 0 6px;letter-spacing:-0.02em;">'
            f'{money(product["price"])}</div>',
            unsafe_allow_html=True,
        )
        features = "".join(
            f'<span class="tag accent">{html.escape(f)}</span>' for f in product["features"]
        )
        st.markdown(f'<div class="card-tags">{features}</div>', unsafe_allow_html=True)

        if product["specs"]:
            st.markdown('<hr class="rule">', unsafe_allow_html=True)
            rows = "".join(
                f'<div class="row"><span class="k">{html.escape(str(k))}</span>'
                f"<span>{html.escape(str(v))}</span></div>"
                for k, v in product["specs"].items()
            )
            st.markdown(rows, unsafe_allow_html=True)

        st.markdown('<hr class="rule">', unsafe_allow_html=True)
        if st.button("加入购物车", key=f"add-{product_id}", use_container_width=True):
            result = api_post("/api/cart/add", {"product_id": product_id, "user_id": USER_ID})
            st.session_state.detail_id = None
            st.session_state.flash = result["message"]
            st.rerun()


# ---------------------------------------------------------------------------
# 模型设置（低调入口：面板底部小齿轮）
# ---------------------------------------------------------------------------

@st.dialog("模型接口设置")
def show_settings_dialog() -> None:
    try:
        config = api_get("/api/model-config")
    except requests.RequestException as exc:
        st.error(f"读取配置失败：{exc}")
        return

    st.markdown(
        '<div class="hint">填写任意 OpenAI 兼容接口的地址、模型名与密钥，保存后立即生效。'
        "密钥仅保存在本机文件。</div>",
        unsafe_allow_html=True,
    )
    base_url = st.text_input("Base URL", value=config["base_url"], placeholder="https://api.deepseek.com/v1")
    model = st.text_input("模型名", value=config["model"], placeholder="deepseek-chat")
    api_key = st.text_input(
        "API Key",
        type="password",
        placeholder=f"已保存 {config['api_key_masked']}" if config["api_key_set"] else "sk-...",
    )
    source_label = {"runtime": "页面设置", "env": "环境变量", "none": "无"}[config["source"]]
    st.markdown(
        f'<div class="hint">当前状态：{"已配置" if config["api_key_set"] else "未配置"}'
        f"（来源：{source_label}）</div>",
        unsafe_allow_html=True,
    )

    col_save, col_test = st.columns(2)
    if col_save.button("保存", use_container_width=True):
        api_post("/api/model-config", {"base_url": base_url, "model": model, "api_key": api_key})
        st.session_state.flash = "模型设置已保存"
        st.rerun()
    if col_test.button("测试连接", use_container_width=True):
        result = api_post("/api/model-config/test", {"base_url": base_url, "model": model, "api_key": api_key})
        if result.get("ok"):
            st.success(f"{result['message']}（{result.get('latency_ms', 0)} ms）")
            if result.get("base_url") and result["base_url"] != base_url.rstrip("/"):
                st.info(f"建议把 Base URL 填为 {result['base_url']}")
        else:
            st.error(result.get("message", "连接失败"))

    if config["api_key_set"]:
        if st.button("清除已保存的密钥", use_container_width=True):
            api_delete("/api/model-config/api-key")
            st.session_state.flash = "已清除密钥"
            st.rerun()


# ---------------------------------------------------------------------------
# 会话发送
# ---------------------------------------------------------------------------

def send_message(text: str) -> None:
    st.session_state.messages.append({"role": "user", "content": text})
    try:
        with st.spinner("正在挑选…"):
            data = api_post(
                "/api/chat",
                {"user_id": USER_ID, "message": text, "session_id": st.session_state.session_id},
            )
        reply = data.get("reply", "")
        recommendations = data.get("recommendations") or []
    except requests.RequestException as exc:
        reply, recommendations = f"后端服务连接失败：{exc}", []
    st.session_state.messages.append(
        {"role": "assistant", "content": reply, "recommendations": recommendations}
    )
    st.rerun()


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

refresh_state()
try:
    model_config = api_get("/api/model-config")
except requests.RequestException:
    model_config = {"api_key_set": False, "source": "none"}

if "messages" not in st.session_state:
    st.session_state.messages = []

st.markdown(
    '<div class="brand">'
    f"{icon('bag', 22)}"
    '<span class="brand-name">小购</span>'
    "</div>"
    '<div class="brand-sub">告诉我你的需求，我来替你挑商品、管购物车、盯支付时间。</div>',
    unsafe_allow_html=True,
)

chat_column, panel_column = st.columns([2.35, 1.0], gap="large")

# ---- 左列：会话 ----
with chat_column:
    if flash := st.session_state.pop("flash", None):
        st.markdown(f'<div class="note">{html.escape(flash)}</div>', unsafe_allow_html=True)

    if not st.session_state.messages:
        st.markdown(
            f'<div class="note">{icon("search", 15)} 不知道从哪开始？点下面任意一句试试：</div>',
            unsafe_allow_html=True,
        )
        samples = [
            "预算 500 以内，想要一副降噪蓝牙耳机",
            "给爸爸买个 300 元左右的电饭煲",
            "想要一台轻薄笔记本，预算 6000 上下",
        ]
        for sample in samples:
            if st.button(sample, key=f"sample-{sample}"):
                send_message(sample)

    for message in st.session_state.messages:
        if message["role"] == "user":
            st.markdown(f'<div class="msg-user">{html.escape(message["content"])}</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="msg-bot">{html.escape(message["content"])}</div>', unsafe_allow_html=True)
            render_cards(message.get("recommendations") or [])

# ---- 右列：购物车 / 订单 / 提醒 / 设置 ----
with panel_column:
    panel = st.container(border=True)
    with panel:
        st.markdown(
            f'<div class="section">{icon("cart", 16)}购物车</div>', unsafe_allow_html=True
        )
        cart = st.session_state.get("cart", {"items": [], "total": 0})
        if cart["items"]:
            for item in cart["items"]:
                st.markdown(
                    f'<div class="row"><span>{html.escape(item["name"])} × {item["quantity"]}</span>'
                    f"<span>{money(item['subtotal'])}</span></div>",
                    unsafe_allow_html=True,
                )
            st.markdown(
                f'<div class="total"><span>合计</span><span>{money(cart["total"])}</span></div>',
                unsafe_allow_html=True,
            )
            if st.button("结算", use_container_width=True):
                reply = api_post(
                    "/api/chat",
                    {
                        "user_id": USER_ID,
                        "message": "帮我结算购物车",
                        "session_id": st.session_state.session_id,
                    },
                ).get("reply", "")
                st.session_state.messages.append({"role": "user", "content": "帮我结算购物车"})
                st.session_state.messages.append({"role": "assistant", "content": reply})
                st.rerun()
        else:
            st.markdown('<div class="hint">购物车是空的</div>', unsafe_allow_html=True)

        st.markdown(
            f'<div class="section">{icon("box", 16)}订单</div>', unsafe_allow_html=True
        )
        orders = st.session_state.get("orders", [])
        if not orders:
            st.markdown('<div class="hint">暂无订单</div>', unsafe_allow_html=True)
        status_meta = {
            "pending_payment": ("warn", "待支付"),
            "paid": ("ok", "已支付"),
            "expired": ("bad", "已超时"),
        }
        for order in orders:
            css_class, label = status_meta[order["status"]]
            st.markdown(
                f'<div class="row"><span>#{order["id"]} &nbsp;{money(order["total_price"])}</span>'
                f'<span class="status"><i class="dot {css_class}"></i>{label}</span></div>',
                unsafe_allow_html=True,
            )
            if order["status"] == "pending_payment":
                if st.button(f"支付 #{order['id']}", key=f"pay-{order['id']}", use_container_width=True):
                    api_post("/api/orders/pay", {"user_id": USER_ID, "order_id": order["id"]})
                    st.session_state.flash = f"订单 #{order['id']} 支付成功"
                    st.rerun()

        st.markdown(
            f'<div class="section">{icon("bell", 16)}提醒</div>', unsafe_allow_html=True
        )
        reminders = st.session_state.get("reminders", [])
        if reminders:
            for reminder in reminders:
                st.markdown(f'<div class="note">{html.escape(reminder["message"])}</div>', unsafe_allow_html=True)
            if st.button("全部标为已读", use_container_width=True):
                api_post("/api/reminders/read", {"user_id": USER_ID})
                st.rerun()
        else:
            st.markdown('<div class="hint">暂无新提醒</div>', unsafe_allow_html=True)

        st.markdown(
            f'<div class="hint" style="display:flex;align-items:center;gap:6px;margin-top:12px;">'
            f'<i class="dot {"ok" if model_config["api_key_set"] else "bad"}"></i>'
            f'模型接口{"已就绪" if model_config["api_key_set"] else "未配置"}</div>',
            unsafe_allow_html=True,
        )
        if st.button("设置", key="open-settings", use_container_width=True):
            st.session_state.show_settings = True

# ---- 弹窗与输入 ----
if st.session_state.pop("show_settings", False):
    show_settings_dialog()
if st.session_state.get("detail_id"):
    show_product_dialog(st.session_state["detail_id"])

if user_input := st.chat_input("说说你想要什么…"):
    send_message(user_input)
