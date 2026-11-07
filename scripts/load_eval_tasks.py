"""把数据集中的人工标注需求导入 eval_tasks 表。

用法：
    python scripts/load_eval_tasks.py              # 全量（1459 条左右）
    python scripts/load_eval_tasks.py --limit 200  # 只导前 200 条
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.database import init_db  # noqa: E402
from app.eval.tasks import load_tasks, save_tasks  # noqa: E402

DEFAULT_SOURCE = os.getenv(
    "PRODUCT_SOURCE",
    r"F:\Shopping-demo\ShopSimulator\shop_env\data\fine_items_eval_train_all.json.gz",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="导入评测任务集")
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if not Path(args.source).exists():
        print(f"[ERROR] 数据文件不存在：{args.source}")
        return 1

    init_db()
    tasks = load_tasks(args.source, limit=args.limit)
    count = save_tasks(tasks)
    with_prices = sum(1 for t in tasks if t.price_max is not None)
    print(f"导入任务 {count} 条，其中 {with_prices} 条含明确预算上限")
    print(f"示例：{tasks[0].instruction[:50] if tasks else '无'}")
    print("TASKS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
