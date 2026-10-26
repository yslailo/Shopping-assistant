"""购物车与订单状态机单元测试。

状态机是系统中最需要确定性的部分，重点覆盖：
金额计算、重复加购、结算快照、支付状态校验、超时失效与提醒生成。

每个用例在项目内 tests/testdata/ 下使用独立的 SQLite 文件，
避免依赖系统临时目录。
"""

import os

import pytest

from app.cart import service as cart_service
from app.config import settings
from app.db import database, seed
from app.db.database import get_conn

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testdata")


@pytest.fixture()
def db(request, monkeypatch):
    """每个用例使用独立的临时数据库文件。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{request.node.name}.db")
    if os.path.exists(path):
        os.remove(path)
    monkeypatch.setattr(settings, "db_path", path)
    database.init_db(path)
    seed.seed_if_empty(path)
    return path


def _first_product_id() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT id FROM products ORDER BY id LIMIT 1").fetchone()[0]


def test_add_to_cart_and_total(db):
    product_id = _first_product_id()
    cart_service.add_to_cart(1, product_id, 2)

    cart = cart_service.get_cart(1)
    assert len(cart["items"]) == 1
    assert cart["items"][0]["quantity"] == 2
    with get_conn() as conn:
        price = conn.execute("SELECT price FROM products WHERE id = ?", (product_id,)).fetchone()[0]
    assert cart["total"] == round(price * 2, 2)


def test_add_same_product_accumulates_quantity(db):
    product_id = _first_product_id()
    cart_service.add_to_cart(1, product_id, 1)
    cart_service.add_to_cart(1, product_id, 1)

    cart = cart_service.get_cart(1)
    assert len(cart["items"]) == 1
    assert cart["items"][0]["quantity"] == 2


def test_remove_from_cart(db):
    product_id = _first_product_id()
    cart_service.add_to_cart(1, product_id)
    assert "已从购物车移除" in cart_service.remove_from_cart(1, product_id)
    assert cart_service.get_cart(1)["items"] == []


def test_invalid_product_fails(db):
    assert "加购失败" in cart_service.add_to_cart(1, 99999)


def test_checkout_creates_pending_order_and_clears_cart(db):
    product_id = _first_product_id()
    cart_service.add_to_cart(1, product_id)

    message = cart_service.checkout(1)
    assert "已生成" in message
    assert cart_service.get_cart(1)["items"] == []

    orders = cart_service.list_orders(1, status="pending_payment")
    assert len(orders) == 1
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0]
    assert count == 1


def test_pay_order_changes_status(db):
    product_id = _first_product_id()
    cart_service.add_to_cart(1, product_id)
    cart_service.checkout(1)
    order_id = cart_service.list_orders(1)[0]["id"]

    assert "支付成功" in cart_service.pay_order(1, order_id)
    assert cart_service.list_orders(1)[0]["status"] == "paid"
    # 已支付订单不能重复支付
    assert "支付失败" in cart_service.pay_order(1, order_id)


def test_expired_order_generates_reminder(db):
    product_id = _first_product_id()
    cart_service.add_to_cart(1, product_id)
    cart_service.checkout(1)
    order_id = cart_service.list_orders(1)[0]["id"]

    # 把截止时间改到过去，模拟超时
    with get_conn() as conn:
        conn.execute(
            "UPDATE orders SET deadline = '2000-01-01 00:00:00' WHERE id = ?", (order_id,)
        )

    result = cart_service.refresh_order_status()
    assert result["expired"] == 1

    order = cart_service.list_orders(1)[0]
    assert order["status"] == "expired"

    reminders = cart_service.list_reminders(1)
    assert any(r["type"] == "order_expired" for r in reminders)
