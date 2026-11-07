"""评测指标：全部为纯函数，输入是推荐结果与任务，输出是标量。

指标定义（K 默认为 5，与系统的 Top-5 推荐对齐）：

- Hit@K   推荐列表里是否出现目标商品，是推荐系统的基本可用性指标
- MRR@K   目标商品首次出现位置的倒数均值，衡量"排得够不够前"
- NDCG@K  目标商品按位置折损的命中质量，比 MRR 更平滑
- 约束满足率  推荐结果中有多少比例不违反任务的硬约束（预算/品类/排除品牌），
              这是本系统"硬约束过滤"最直接的收益验证
"""

from dataclasses import dataclass, field


def hit_at_k(ranked_ids: list[str], target_id: str, k: int = 5) -> float:
    """目标商品是否落在前 K 位。"""
    return 1.0 if target_id in ranked_ids[:k] else 0.0


def reciprocal_rank(ranked_ids: list[str], target_id: str, k: int = 5) -> float:
    """目标商品位置的倒数；未命中记为 0。"""
    for index, item_id in enumerate(ranked_ids[:k], start=1):
        if item_id == target_id:
            return 1.0 / index
    return 0.0


def ndcg_at_k(ranked_ids: list[str], target_id: str, k: int = 5) -> float:
    """单目标 NDCG：理想排序下目标商品在第 1 位，折损值为 1。"""
    import math

    for index, item_id in enumerate(ranked_ids[:k], start=1):
        if item_id == target_id:
            return 1.0 / math.log2(index + 1) / (1.0 / math.log2(2))
    return 0.0


def constraint_satisfaction(items: list[dict], task) -> float:
    """推荐结果中未违反硬约束的比例（预算 / 品类 / 排除品牌）。

    items 需要包含 price、category、brand 字段，asin 用于比对排除项。
    """
    if not items:
        return 0.0
    ok = 0
    for item in items:
        price = float(item.get("price") or 0)
        if task.price_max is not None and price > task.price_max:
            continue
        if task.price_min is not None and price < task.price_min:
            continue
        if task.category and task.category not in (item.get("category") or ""):
            continue
        if item.get("brand") and item.get("brand") in (task.exclude_brands or []):
            continue
        ok += 1
    return ok / len(items)


@dataclass
class EvalReport:
    """一次评测的汇总结果。"""

    method: str
    total: int = 0
    hit5: float = 0.0
    mrr5: float = 0.0
    ndcg5: float = 0.0
    satisfaction: float = 0.0
    avg_latency_ms: float = 0.0
    details: list[dict] = field(default_factory=list)

    def as_row(self) -> dict:
        return {
            "method": self.method,
            "total": self.total,
            "Hit@5": round(self.hit5, 4),
            "MRR@5": round(self.mrr5, 4),
            "NDCG@5": round(self.ndcg5, 4),
            "约束满足率": round(self.satisfaction, 4),
            "平均耗时ms": round(self.avg_latency_ms, 1),
        }


def evaluate(tasks, method_name: str, run_one) -> EvalReport:
    """对每个任务跑一次 run_one(task)，汇总指标。

    run_one(task) 需返回 (ranked_asins, items, latency_ms)。
    """
    report = EvalReport(method=method_name)
    if not tasks:
        return report

    hit = mrr = ndcg = sat = latency = 0.0
    for task in tasks:
        ranked_ids, items, cost_ms = run_one(task)
        hit += hit_at_k(ranked_ids, task.target_asin)
        mrr += reciprocal_rank(ranked_ids, task.target_asin)
        ndcg += ndcg_at_k(ranked_ids, task.target_asin)
        sat += constraint_satisfaction(items, task)
        latency += cost_ms
        report.details.append(
            {
                "task_id": task.task_id,
                "instruction": task.instruction[:40],
                "target": task.target_asin,
                "top1": ranked_ids[0] if ranked_ids else "",
                "hit": bool(hit_at_k(ranked_ids, task.target_asin)),
                "rank": (
                    ranked_ids.index(task.target_asin) + 1
                    if task.target_asin in ranked_ids
                    else 0
                ),
            }
        )

    count = len(tasks)
    report.total = count
    report.hit5 = hit / count
    report.mrr5 = mrr / count
    report.ndcg5 = ndcg / count
    report.satisfaction = sat / count
    report.avg_latency_ms = latency / count
    return report
