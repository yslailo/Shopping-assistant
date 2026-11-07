"""购物车与订单状态机。

状态流转：加购 -> checkout 生成 [待支付] -> pay 变 [已支付]；
超过截止时间未支付则由 refresh_order_status 变 [已超时]。

所有金额计算、状态迁移都由本模块的确定性代码完成，大模型只负责
组织话术，不允许参与算术或改状态。
"""

from datetime import datetime, timedelta

from app.config import settings
from app.db.database import get_conn

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# 购物车
# ---------------------------------------------------------------------------

def get_cart(user_id: int) -> dict:
    """返回购物车明细与合计金额。合计由代码计算，不经过模型。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT c.product_id, p.name, p.brand, c.unit_price, c.quantity "
            "FROM cart_items c JOIN products p ON p.id = c.product_id "
            "WHERE c.user_id = ? ORDER BY c.added_at",
            (user_id,),
        ).fetchall()
    items = [
        {
            "product_id": row["product_id"],
            "name": row["name"],
            "brand": row["brand"],
            "unit_price": row["unit_price"],
            "quantity": row["quantity"],
            "subtotal": round(row["unit_price"] * row["quantity"], 2),
        }
        for row in rows
    ]
    total = round(sum(item["subtotal"] for item in items), 2)
    return {"items": items, "total": total}


def add_to_cart(user_id: int, product_id: int, quantity: int = 1) -> str:
    """加入购物车。同一商品重复加购时累加数量，价格以当前商品表为准。"""
    if quantity < 1:
        return "加购失败：数量必须大于 0"
    with get_conn() as conn:
        product = conn.execute(
            "SELECT name, price, stock FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if product is None:
            return f"加购失败：不存在 ID 为 {product_id} 的商品"
        if product["stock"] < 1:
            return f"加购失败：{product['name']} 已无库存"

        existing = conn.execute(
            "SELECT quantity FROM cart_items WHERE user_id = ? AND product_id = ?",
            (user_id, product_id),
        ).fetchone()
        new_quantity = (existing["quantity"] if existing else 0) + quantity
        if new_quantity > product["stock"]:
            return f"加购失败：{product['name']} 库存仅剩 {product['stock']} 件"

        conn.execute(
            "INSERT INTO cart_items (user_id, product_id, quantity, unit_price) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, product_id) DO UPDATE SET quantity = excluded.quantity",
            (user_id, product_id, new_quantity, product["price"]),
        )
    cart = get_cart(user_id)
    return f"已加入购物车：{product['name']} x{quantity}，当前共 {len(cart['items'])} 种商品，合计 ¥{cart['total']:.2f}"


def remove_from_cart(user_id: int, product_id: int) -> str:
    """从购物车移除指定商品。"""
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM cart_items WHERE user_id = ? AND product_id = ?",
            (user_id, product_id),
        )
    if cursor.rowcount == 0:
        return f"移除失败：购物车中没有 ID 为 {product_id} 的商品"
    return f"已从购物车移除 ID 为 {product_id} 的商品"


# ---------------------------------------------------------------------------
# 订单状态机
# ---------------------------------------------------------------------------

def checkout(user_id: int) -> str:
    """结算购物车，生成待支付订单并清空购物车。"""
    cart = get_cart(user_id)
    if not cart["items"]:
        return "结算失败：购物车是空的，请先加购商品"

    deadline = datetime.now() + timedelta(minutes=settings.payment_timeout_minutes)
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO orders (user_id, status, total_price, deadline) VALUES (?, 'pending_payment', ?, ?)",
            (user_id, cart["total"], deadline.strftime(TIME_FORMAT)),
        )
        order_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO order_items (order_id, product_id, product_name, unit_price, quantity) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (order_id, item["product_id"], item["name"], item["unit_price"], item["quantity"])
                for item in cart["items"]
            ],
        )
        conn.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
    return (
        f"订单 #{order_id} 已生成，合计 ¥{cart['total']:.2f}，"
        f"请在 {deadline.strftime('%H:%M')} 前完成支付，超时订单将自动失效。"
    )


def pay_order(user_id: int, order_id: int) -> str:
    """模拟支付：仅待支付订单可支付。"""
    with get_conn() as conn:
        order = conn.execute(
            "SELECT id, status FROM orders WHERE id = ? AND user_id = ?",
            (order_id, user_id),
        ).fetchone()
        if order is None:
            return f"支付失败：找不到订单 #{order_id}"
        if order["status"] != "pending_payment":
            return f"支付失败：订单 #{order_id} 当前状态为 {order['status']}，无法支付"
        conn.execute(
            "UPDATE orders SET status = 'paid', paid_at = ? WHERE id = ?",
            (datetime.now().strftime(TIME_FORMAT), order_id),
        )
    return f"订单 #{order_id} 支付成功，商品将尽快发出，感谢购买！"


def list_orders(user_id: int, status: str | None = None) -> list[dict]:
    """查询用户订单列表，可按状态过滤。"""
    sql = "SELECT id, status, total_price, deadline, created_at, paid_at FROM orders WHERE user_id = ?"
    params: list = [user_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT 20"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def refresh_order_status() -> dict:
    """扫描订单并推进状态：超时未支付 -> 已超时；临近截止 -> 生成支付提醒。

    由定时调度器周期调用，也在每次对话前惰性调用一次。
    """
    now = datetime.now()
    remind_threshold = now + timedelta(minutes=settings.remind_before_minutes)
    expired_count = 0
    reminder_count = 0

    with get_conn() as conn:
        # 1) 超时订单置为 expired，并写入提醒
        overdue = conn.execute(
            "SELECT id, user_id, total_price FROM orders "
            "WHERE status = 'pending_payment' AND deadline < ?",
            (now.strftime(TIME_FORMAT),),
        ).fetchall()
        for order in overdue:
            conn.execute(
                "UPDATE orders SET status = 'expired' WHERE id = ?", (order["id"],)
            )
            conn.execute(
                "INSERT INTO reminders (user_id, order_id, type, message) VALUES (?, ?, 'order_expired', ?)",
                (order["user_id"], order["id"], f"订单 #{order['id']} 已超时失效（金额 ¥{order['total_price']:.2f}），如仍需要请重新下单。"),
            )
            expired_count += 1

        # 2) 临近截止且尚未提醒过的订单，写入支付提醒（每单只提醒一次）
        due = conn.execute(
            "SELECT o.id, o.user_id, o.total_price, o.deadline FROM orders o "
            "WHERE o.status = 'pending_payment' AND o.deadline <= ? "
            "AND NOT EXISTS (SELECT 1 FROM reminders r WHERE r.order_id = o.id AND r.type = 'payment_due')",
            (remind_threshold.strftime(TIME_FORMAT),),
        ).fetchall()
        for order in due:
            deadline_hm = datetime.strptime(order["deadline"], TIME_FORMAT).strftime("%H:%M")
            conn.execute(
                "INSERT INTO reminders (user_id, order_id, type, message) VALUES (?, ?, 'payment_due', ?)",
                (order["user_id"], order["id"], f"订单 #{order['id']} 将于 {deadline_hm} 截止支付（金额 ¥{order['total_price']:.2f}），请及时处理。"),
            )
            reminder_count += 1

    return {"expired": expired_count, "reminded": reminder_count}


# ---------------------------------------------------------------------------
# 提醒
# ---------------------------------------------------------------------------

def list_reminders(user_id: int, unread_only: bool = False) -> list[dict]:
    sql = "SELECT id, order_id, type, message, created_at, is_read FROM reminders WHERE user_id = ?"
    if unread_only:
        sql += " AND is_read = 0"
    sql += " ORDER BY id DESC LIMIT 20"
    with get_conn() as conn:
        rows = conn.execute(sql, (user_id,)).fetchall()
    return [dict(row) for row in rows]


def mark_reminders_read(user_id: int) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            "UPDATE reminders SET is_read = 1 WHERE user_id = ? AND is_read = 0", (user_id,)
        )
    return cursor.rowcount
