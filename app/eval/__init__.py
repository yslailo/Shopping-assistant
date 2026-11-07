"""评测模块：把"推荐得好不好"变成可量化、可复现的指标。

设计要点：
1. 评测任务集来自公开数据集的人工标注需求（instruction + 目标商品 asin），
   每条任务都指定了唯一的目标商品，因此可以用 Hit@K / MRR 这类标准指标；
2. 评测只走 pipeline 的确定性入口，不经过大模型对话，保证不同方法之间
   唯一变化的因素就是我们想消融的那个开关；
3. 指标全部由本模块计算，输入只有"任务"与"推荐结果"，便于在论文里复核。
"""

from app.eval.tasks import EvalTask, load_tasks, save_tasks, get_tasks
from app.eval.metrics import (
    EvalReport,
    evaluate,
    hit_at_k,
    reciprocal_rank,
    ndcg_at_k,
    constraint_satisfaction,
)

__all__ = [
    "EvalTask",
    "load_tasks",
    "save_tasks",
    "get_tasks",
    "EvalReport",
    "evaluate",
    "hit_at_k",
    "reciprocal_rank",
    "ndcg_at_k",
    "constraint_satisfaction",
]
