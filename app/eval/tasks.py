"""评测任务集：从公开数据集的人工标注需求构建可复现的评测任务。

每条任务的构成：
- instruction  用户自然语言需求（数据集原始标注）
- target_asin  标准答案商品（标注者针对该商品写的需求）
- price_max / price_min  从需求文本解析出的预算边界，作为硬约束
- category     目标商品的二级类目，作为硬约束（品类必须对得上）

任务写入 eval_tasks 表，评测脚本可以直接从库里读，保证多次评测用的是
同一批任务，结果可比。
"""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass, field

from app.db.database import get_conn


@dataclass
class EvalTask:
    task_id: str
    instruction: str
    target_asin: str
    category: str = ""
    price_min: float | None = None
    price_max: float | None = None
    attributes: list[str] = field(default_factory=list)
    exclude_brands: list[str] = field(default_factory=list)


_PRICE_MAX_PATTERNS = [
    r"(\d+(?:\.\d+)?)\s*(?:元|块)?\s*(?:以下|以内|之内|内)",
    r"不超过\s*(\d+(?:\.\d+)?)",
    r"预算\s*(?:在|为|是)?\s*(\d+(?:\.\d+)?)\s*(?:元|块)?\s*(?:以下|以内|内)",
]
_PRICE_MIN_PATTERNS = [
    r"(\d+(?:\.\d+)?)\s*(?:元|块)?\s*(?:以上|往上)",
]


def parse_budget(text: str) -> tuple[float | None, float | None]:
    """从需求文本里抽预算区间。抽不到就返回 (None, None)，不臆造约束。"""
    price_max = price_min = None
    for pattern in _PRICE_MAX_PATTERNS:
        matched = re.search(pattern, text)
        if matched:
            price_max = float(matched.group(1))
            break
    for pattern in _PRICE_MIN_PATTERNS:
        matched = re.search(pattern, text)
        if matched:
            price_min = float(matched.group(1))
            break
    return price_min, price_max


def load_tasks(source: str, limit: int | None = None) -> list[EvalTask]:
    """从数据集读取标注需求（tag=eval 的条目带 instructions）。"""
    with gzip.open(source, "rt", encoding="utf-8") as handle:
        items = json.load(handle)

    tasks: list[EvalTask] = []
    for item in items:
        if item.get("tag") != "eval":
            continue
        asin = str(item.get("asin") or "").strip()
        parts = [p for p in (item.get("category") or "").split("›") if p.strip()]
        category = parts[1] if len(parts) >= 2 else (parts[0] if parts else "")
        for index, record in enumerate(item.get("instructions") or []):
            instruction = (record.get("instruction") or "").strip()
            if not instruction or not asin:
                continue
            price_min, price_max = parse_budget(instruction)
            tasks.append(
                EvalTask(
                    task_id=f"{asin}-{index}",
                    instruction=instruction,
                    target_asin=asin,
                    category=category,
                    price_min=price_min,
                    price_max=price_max,
                    attributes=[a for a in (record.get("attributes") or []) if a],
                )
            )
        if limit and len(tasks) >= limit:
            break
    return tasks[:limit] if limit else tasks


def save_tasks(tasks: list[EvalTask], db_path: str | None = None) -> int:
    """把任务写入 eval_tasks 表（task_id 唯一，重复导入即更新）。"""
    with get_conn(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS eval_tasks (
                task_id       TEXT PRIMARY KEY,
                instruction   TEXT NOT NULL,
                target_asin   TEXT NOT NULL,
                category      TEXT NOT NULL DEFAULT '',
                price_min     REAL,
                price_max     REAL,
                attributes    TEXT NOT NULL DEFAULT '[]',
                exclude_brands TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        for task in tasks:
            conn.execute(
                "INSERT INTO eval_tasks (task_id, instruction, target_asin, category, price_min, "
                "price_max, attributes, exclude_brands) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET instruction=excluded.instruction, "
                "target_asin=excluded.target_asin, category=excluded.category, "
                "price_min=excluded.price_min, price_max=excluded.price_max, "
                "attributes=excluded.attributes, exclude_brands=excluded.exclude_brands",
                (
                    task.task_id,
                    task.instruction,
                    task.target_asin,
                    task.category,
                    task.price_min,
                    task.price_max,
                    json.dumps(task.attributes, ensure_ascii=False),
                    json.dumps(task.exclude_brands, ensure_ascii=False),
                ),
            )
    return len(tasks)


def get_tasks(limit: int | None = None, db_path: str | None = None) -> list[EvalTask]:
    """从库里读取评测任务；表不存在或为空时返回空列表。"""
    try:
        with get_conn(db_path) as conn:
            sql = "SELECT * FROM eval_tasks ORDER BY task_id"
            if limit:
                sql += f" LIMIT {int(limit)}"
            rows = conn.execute(sql).fetchall()
    except Exception:
        return []
    return [
        EvalTask(
            task_id=row["task_id"],
            instruction=row["instruction"],
            target_asin=row["target_asin"],
            category=row["category"],
            price_min=row["price_min"],
            price_max=row["price_max"],
            attributes=json.loads(row["attributes"] or "[]"),
            exclude_brands=json.loads(row["exclude_brands"] or "[]"),
        )
        for row in rows
    ]
