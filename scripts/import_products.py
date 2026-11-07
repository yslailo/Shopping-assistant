"""把公开中文商品数据集导入 products 表。

数据来源：ShopSimulator 的 fine_items_eval_train_all.json.gz（约 2.3 万件真实商品，
字段含标题、三级类目、店铺、属性标签、变体价格、商品图）。导入时会做清洗：

- 价格取各变体最低有效价，剔除 0 < price <= 20000 之外的异常值；
- 类目取二级类目（如「枕头」），完整三级路径另存 category_path 供模糊匹配；
- 品牌位用店铺名填充（公开数据无独立品牌字段）；
- 评分与库存原始数据没有，用商品标识做确定性映射生成，保证重复导入结果一致；
- 以 asin 为去重键，重复执行只更新不重复插入。

用法：
    python scripts/import_products.py                     # 默认数据源
    python scripts/import_products.py --limit 5000        # 只导入前 5000 件
    python scripts/import_products.py --db mydata.db      # 指定数据库
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.database import get_conn, init_db  # noqa: E402

DEFAULT_SOURCE = os.getenv(
    "PRODUCT_SOURCE",
    r"F:\Shopping-demo\ShopSimulator\shop_env\data\fine_items_eval_train_all.json.gz",
)

MAX_PRICE = 20000.0
MIN_PRICE = 0.1


def _stable_hash(text: str) -> int:
    """把字符串映射成稳定的整数，用于生成可复现的评分/库存。"""
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)


def _pick_price(item: dict) -> float | None:
    prices = [p for p in (item.get("pricing") or []) if isinstance(p, (int, float)) and p > MIN_PRICE]
    options = item.get("customization_options") or {}
    for values in options.values():
        for variant in values or []:
            price = variant.get("price")
            if isinstance(price, (int, float)) and price > MIN_PRICE:
                prices.append(float(price))
    prices = [p for p in prices if p <= MAX_PRICE]
    return min(prices) if prices else None


def _pick_specs(item: dict) -> dict:
    specs: dict[str, str] = {}
    for group, values in (item.get("customization_options") or {}).items():
        for variant in values or []:
            name = (variant.get("value") or "").strip()
            if name:
                specs[group] = name[:40]
                break
    return specs


def _split_categories(path: str) -> tuple[str, str]:
    """返回 (用于过滤的类目, 完整路径)。三级类目取二级更稳，缺失则退化。"""
    parts = [p for p in (path or "").split("›") if p.strip()]
    if not parts:
        return "其他", ""
    category = parts[1] if len(parts) >= 2 else parts[0]
    return category, "›".join(parts)


def _to_row(item: dict) -> dict | None:
    title = (item.get("title") or "").strip()
    asin = str(item.get("asin") or "").strip()
    if not title or not asin:
        return None
    price = _pick_price(item)
    if price is None:
        return None

    category, path = _split_categories(item.get("category", ""))
    seed = _stable_hash(asin)
    features = ",".join(a.strip() for a in (item.get("attribute") or []) if a.strip())
    images = item.get("images") or []
    description = (item.get("full_description") or title).strip()

    return {
        "name": title[:120],
        "category": category[:30],
        "brand": (item.get("shop_name") or "未知店铺").strip()[:40],
        "price": round(price, 2),
        "specs": json.dumps(_pick_specs(item), ensure_ascii=False),
        "features": features[:200],
        "rating": round(3.8 + (seed % 12) / 10, 1),  # 3.8 ~ 4.9，确定性生成
        "stock": 5 + seed % 200,
        "description": description[:300],
        "category_path": path[:120],
        "shop": (item.get("shop_name") or "").strip()[:60],
        "image": images[0] if images else "",
        "asin": asin,
    }


def import_products(source: str, db_path: str | None = None, limit: int | None = None) -> dict:
    """执行导入，返回统计信息。"""
    init_db(db_path)
    with gzip.open(source, "rt", encoding="utf-8") as handle:
        items = json.load(handle)
    if limit:
        items = items[:limit]

    rows = [row for row in (_to_row(item) for item in items) if row]
    inserted = updated = 0

    with get_conn(db_path) as conn:
        for row in rows:
            existing = conn.execute(
                "SELECT id FROM products WHERE asin = ?", (row["asin"],)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE products SET name=?, category=?, brand=?, price=?, specs=?, features=?, "
                    "rating=?, stock=?, description=?, category_path=?, shop=?, image=? WHERE id=?",
                    (
                        row["name"], row["category"], row["brand"], row["price"], row["specs"],
                        row["features"], row["rating"], row["stock"], row["description"],
                        row["category_path"], row["shop"], row["image"], existing["id"],
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO products (name, category, brand, price, specs, features, rating, "
                    "stock, description, category_path, shop, image, asin) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["name"], row["category"], row["brand"], row["price"], row["specs"],
                        row["features"], row["rating"], row["stock"], row["description"],
                        row["category_path"], row["shop"], row["image"], row["asin"],
                    ),
                )
                inserted += 1

    with get_conn(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        categories = conn.execute(
            "SELECT category, COUNT(*) FROM products GROUP BY category ORDER BY 2 DESC"
        ).fetchall()

    return {
        "source_items": len(items),
        "valid": len(rows),
        "inserted": inserted,
        "updated": updated,
        "total": total,
        "categories": [(row[0], row[1]) for row in categories],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="导入商品数据集到 SQLite")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="gzip JSON 数据集路径")
    parser.add_argument("--db", default=None, help="目标数据库文件（默认项目所在库）")
    parser.add_argument("--limit", type=int, default=None, help="只导入前 N 条，便于快速试跑")
    args = parser.parse_args()

    if not Path(args.source).exists():
        print(f"[ERROR] 数据文件不存在：{args.source}")
        return 1

    stats = import_products(args.source, args.db, args.limit)
    print(f"源数据 {stats['source_items']} 条，有效 {stats['valid']} 条")
    print(f"新增 {stats['inserted']} 条，更新 {stats['updated']} 条，库内共 {stats['total']} 条")
    print("类目分布（前 15）：")
    for name, count in stats["categories"][:15]:
        print(f"  {name}: {count}")
    print("IMPORT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
