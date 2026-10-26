"""离线冒烟检查：不依赖大模型，验证核心链路可跑通。

覆盖：建库种子 -> 规则意图解析 -> 硬约束召回 -> 工具层推荐/加购/
结算 -> 超时失效与提醒生成。全部通过时输出 SMOKE_OK。

运行：python scripts/smoke_offline.py
"""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows GBK 控制台兼容

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent import tools  # noqa: E402
from app.cart import service as cart_service  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import database, seed  # noqa: E402
from app.db.database import get_conn  # noqa: E402
from app.llm import config_store  # noqa: E402
from app.selection.parser import parse_intent  # noqa: E402

CHECKS: list[tuple[bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    CHECKS.append((condition, name))
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")


def main() -> int:
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "testdata")
    os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, "smoke.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    settings.db_path = db_path

    # 1. 建库与种子数据
    database.init_db(db_path)
    total = seed.seed_if_empty(db_path)
    check("建库与种子数据", total == 48, f"共 {total} 款商品")

    # 2. 规则意图解析（无 LLM 环境自动回退）
    intent = parse_intent("500以内的蓝牙耳机")
    check("意图解析-品类", intent.hard.category == "蓝牙耳机", f"category={intent.hard.category}")
    check("意图解析-预算", intent.hard.price_max == 500, f"price_max={intent.hard.price_max}")

    # 3. 工具层推荐（硬约束召回生效：不得出现超预算商品）
    reply = tools.execute_tool(1, "search_products", '{"query": "500以内的蓝牙耳机"}')
    check("推荐返回Top列表", "【Top-" in reply, f"回复 {len(reply)} 字符")
    with get_conn() as conn:
        over_budget = conn.execute(
            "SELECT COUNT(*) FROM products WHERE category = '蓝牙耳机' AND price <= 500"
        ).fetchone()[0]
    check("硬约束召回有候选", over_budget > 0, f"500 以内蓝牙耳机 {over_budget} 款")
    check("推荐无超预算商品", "1899" not in reply and "1299" not in reply)

    # 4. 加购 -> 结算 -> 订单
    add_result = tools.execute_tool(1, "add_to_cart", '{"selection_index": 1}')
    check("序号指代加购", "已加入购物车" in add_result, add_result[:40])
    checkout_result = tools.execute_tool(1, "checkout", "{}")
    check("结算生成待支付订单", "已生成" in checkout_result, checkout_result[:40])
    orders = cart_service.list_orders(1, status="pending_payment")
    check("订单金额确定性计算", len(orders) == 1 and orders[0]["total_price"] > 0)

    # 5. 超时失效与提醒
    with get_conn() as conn:
        conn.execute("UPDATE orders SET deadline = '2000-01-01 00:00:00' WHERE id = ?", (orders[0]["id"],))
    refresh = cart_service.refresh_order_status()
    check("超时订单自动失效", refresh["expired"] == 1)
    reminders = cart_service.list_reminders(1)
    check("生成失效提醒", any(r["type"] == "order_expired" for r in reminders))

    # 6. 运行时模型配置（网页端填写后即时生效，不依赖 .env）
    saved = config_store.save_model_config(
        base_url="https://example.com/v1", api_key="sk-smoke-1234567890", model="smoke-model"
    )
    config = config_store.get_model_config()
    check("保存运行时模型配置", config["base_url"] == "https://example.com/v1" and config["source"] == "runtime")
    check("配置接口Key打码", saved["api_key_set"] and "sk-smoke-1234567890" not in str(saved))
    check("运行时配置优先于环境变量", config["model"] == "smoke-model")

    failed = [name for ok, name in CHECKS if not ok]
    if failed:
        print(f"SMOKE_FAILED: {failed}")
        return 1
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
