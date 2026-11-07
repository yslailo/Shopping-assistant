"""选了就跑：三档消融基线的离线评测脚本。

对比三种配置，回答"硬约束过滤和软约束打分各自值多少"：

- B0  popularity   不用约束、不打分，直接按口碑取前 5（下界基线）
- B1  heuristic    硬约束召回 + 规则关键词打分（无大模型）
- B2  full         硬约束召回 + 大模型软约束打分（完整方法）

用法：
    python scripts/run_eval.py --limit 50            # 快速看结果
    python scripts/run_eval.py --limit 200 --json out.json
    python scripts/run_eval.py --scoring heuristic   # 只跑某一档
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.database import init_db  # noqa: E402
from app.eval.metrics import evaluate  # noqa: E402
from app.eval.tasks import get_tasks  # noqa: E402
from app.selection.parser import HardConstraints  # noqa: E402
from app.selection.pipeline import recommend  # noqa: E402

METHODS = {
    "popularity": ("B0 口碑基线", "none"),
    "heuristic": ("B1 规则打分", "heuristic"),
    "full": ("B2 完整方法", "llm"),
}


def _run_one(task, scoring: str):
    """跑一条任务，返回 (推荐 asin 列表, 商品明细, 耗时毫秒)。"""
    filters = HardConstraints(
        category=task.category or None,
        price_min=task.price_min,
        price_max=task.price_max,
    )
    started = time.perf_counter()
    result = recommend(task.instruction, filters=filters, scoring=scoring)
    cost_ms = (time.perf_counter() - started) * 1000
    items = [
        {
            "asin": item.product.get("asin", ""),
            "price": item.product.get("price", 0),
            "category": item.product.get("category", ""),
            "brand": item.product.get("brand", ""),
            "name": item.product.get("name", ""),
        }
        for item in result.items
    ]
    return [item["asin"] for item in items], items, cost_ms


def main() -> int:
    parser = argparse.ArgumentParser(description="选品智能体离线评测")
    parser.add_argument("--limit", type=int, default=50, help="参与评测的任务数")
    parser.add_argument(
        "--scoring",
        default="all",
        choices=["all", *METHODS.keys()],
        help="只跑指定档位（默认三档全跑）",
    )
    parser.add_argument("--json", default=None, help="把结果写到指定 JSON 文件")
    args = parser.parse_args()

    init_db()
    tasks = get_tasks(limit=args.limit)
    if not tasks:
        print("[ERROR] 没有评测任务，请先运行 scripts/load_eval_tasks.py")
        return 1

    names = list(METHODS) if args.scoring == "all" else [args.scoring]
    reports = []
    for name in names:
        label, scoring = METHODS[name]
        report = evaluate(tasks, label, lambda task, s=scoring: _run_one(task, s))
        reports.append(report)
        row = report.as_row()
        print(
            f"{row['method']:<12} n={row['total']:<4} Hit@5={row['Hit@5']:.3f} "
            f"MRR@5={row['MRR@5']:.3f} NDCG@5={row['NDCG@5']:.3f} "
            f"约束满足率={row['约束满足率']:.3f} 平均耗时={row['平均耗时ms']:.0f}ms"
        )

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"limit": args.limit, "reports": [r.as_row() for r in reports]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"结果已写入 {args.json}")
    print("EVAL_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
