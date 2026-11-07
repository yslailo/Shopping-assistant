"""SQLite 连接管理与库表结构。

所有业务表集中定义在 SCHEMA 中，服务启动时执行一次 init_db 即可。
"""

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id            INTEGER PRIMARY KEY,
    name          TEXT    NOT NULL,
    category      TEXT    NOT NULL,
    brand         TEXT    NOT NULL,
    price         REAL    NOT NULL,
    specs         TEXT    NOT NULL DEFAULT '{}',
    features      TEXT    NOT NULL DEFAULT '',
    rating        REAL    NOT NULL DEFAULT 0,
    stock         INTEGER NOT NULL DEFAULT 0,
    description   TEXT    NOT NULL DEFAULT '',
    category_path TEXT    NOT NULL DEFAULT '',
    shop          TEXT    NOT NULL DEFAULT '',
    image         TEXT    NOT NULL DEFAULT '',
    asin          TEXT    NOT NULL DEFAULT ''
);

-- 选品召回的硬约束过滤（品类+价格区间）与品牌查询走复合索引；
-- 排序列单独建索引，供无品类条件时 ORDER BY rating 使用。
-- CREATE INDEX IF NOT EXISTS 幂等，老库启动时自动补建。
CREATE INDEX IF NOT EXISTS idx_products_category_price ON products (category, price);
CREATE INDEX IF NOT EXISTS idx_products_brand ON products (brand);
CREATE INDEX IF NOT EXISTS idx_products_rating ON products (rating);

CREATE TABLE IF NOT EXISTS cart_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    product_id  INTEGER NOT NULL,
    quantity    INTEGER NOT NULL DEFAULT 1,
    unit_price  REAL    NOT NULL,
    added_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (user_id, product_id),
    FOREIGN KEY (product_id) REFERENCES products (id)
);

CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    status      TEXT    NOT NULL CHECK (status IN ('pending_payment', 'paid', 'expired')),
    total_price REAL    NOT NULL,
    deadline    TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    paid_at     TEXT
);

CREATE TABLE IF NOT EXISTS order_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     INTEGER NOT NULL,
    product_id   INTEGER NOT NULL,
    product_name TEXT    NOT NULL,
    unit_price   REAL    NOT NULL,
    quantity     INTEGER NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    order_id   INTEGER,
    type       TEXT    NOT NULL,
    message    TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    is_read    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status, deadline);

-- 评测任务集：人工标注需求 + 目标商品 asin，用于 Hit@K / MRR 等指标计算
CREATE TABLE IF NOT EXISTS eval_tasks (
    task_id        TEXT PRIMARY KEY,
    instruction    TEXT NOT NULL,
    target_asin    TEXT NOT NULL,
    category       TEXT NOT NULL DEFAULT '',
    price_min      REAL,
    price_max      REAL,
    attributes     TEXT NOT NULL DEFAULT '[]',
    exclude_brands TEXT NOT NULL DEFAULT '[]'
);
"""

# 老库迁移：products 表后加的列按需补上（SQLite 的 ADD COLUMN 不幂等判断，用 PRAGMA 探测）
_PRODUCT_MIGRATIONS = [
    ("category_path", "TEXT NOT NULL DEFAULT ''"),
    ("shop", "TEXT NOT NULL DEFAULT ''"),
    ("image", "TEXT NOT NULL DEFAULT ''"),
    ("asin", "TEXT NOT NULL DEFAULT ''"),
]


@contextmanager
def get_conn(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    """获取一个自动提交/回滚的数据库连接。"""
    conn = sqlite3.connect(db_path or settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """补齐 products 表后续新增的列，兼容旧版本数据库文件。"""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
    for column, ddl in _PRODUCT_MIGRATIONS:
        if column not in existing:
            conn.execute(f"ALTER TABLE products ADD COLUMN {column} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_asin ON products (asin)")


def init_db(db_path: str | None = None) -> None:
    """建表（幂等），并补齐旧库缺失的列。"""
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
