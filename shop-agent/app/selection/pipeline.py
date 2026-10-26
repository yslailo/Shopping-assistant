"""Top-K 选品流水线：结构化召回 -> 软约束打分 -> 可解释排序。

设计要点：
1. 召回阶段只用硬约束过滤（SQL），预算超标、品类不符的商品在物理层面
   出局，杜绝模型"幻觉推荐"；
2. 打分阶段由大模型对候选商品逐条评估软约束满足度（0 / 0.5 / 1），
   按组加权求和；LLM 不可用时回退关键词匹配打分；
3. 输出附带命中/未命中约束与推荐理由，保证结果可解释。
"""

import json
from dataclasses import dataclass, field

from app.db.database import get_conn
from app.llm.client import extract_json, llm
from app.selection.parser import HardConstraints, SoftConstraints, parse_intent

TOP_K = 5
RECALL_LIMIT = 40
SCORE_LIMIT = 15  # 控制打分阶段的输入长度与调用成本

# 约束组权重：功能 0.35 / 场景 0.25 / 规格 0.25 / 偏好 0.15 的简化版
GROUP_WEIGHTS = {"features": 0.4, "scenario": 0.3, "specs": 0.3}


@dataclass
class RecommendItem:
    product: dict
    score: float
    matched: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class RecommendResult:
    mode: str  # ok / no_result / clarification
    items: list[RecommendItem] = field(default_factory=list)
    question: str = ""
    summary: str = ""


# ---------------------------------------------------------------------------
# 阶段一：结构化召回（确定性）
# ---------------------------------------------------------------------------

def recall(hard: HardConstraints, keyword: str = "", limit: int = RECALL_LIMIT) -> list[dict]:
    """按硬约束做 SQL 过滤；无品类时退化为关键词匹配商品名与描述。"""
    sql = (
        "SELECT id, name, category, brand, price, specs, features, rating, stock, description "
        "FROM products WHERE stock > 0"
    )
    params: list = []

    if hard.category:
        sql += " AND category = ?"
        params.append(hard.category)
    if hard.brand_in:
        sql += " AND brand IN (%s)" % ",".join("?" * len(hard.brand_in))
        params.extend(hard.brand_in)
    if hard.price_min is not None:
        sql += " AND price >= ?"
        params.append(hard.price_min)
    if hard.price_max is not None:
        sql += " AND price <= ?"
        params.append(hard.price_max)
    if not hard.category and keyword:
        sql += " AND (name LIKE ? OR description LIKE ? OR features LIKE ?)"
        like = f"%{keyword}%"
        params.extend([like, like, like])

    sql += " ORDER BY rating DESC LIMIT ?"
    params.append(limit)

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# 阶段二：软约束打分
# ---------------------------------------------------------------------------

def _flatten(soft: SoftConstraints) -> list[tuple[str, str, str]]:
    """展开为 (标签, 组名, 约束文本) 列表，标签用于和模型输出对齐。"""
    constraints: list[tuple[str, str, str]] = []
    for feature in soft.features:
        constraints.append((f"C{len(constraints) + 1}", "features", feature))
    if soft.scenario:
        constraints.append((f"C{len(constraints) + 1}", "scenario", soft.scenario))
    for spec in soft.specs:
        constraints.append((f"C{len(constraints) + 1}", "specs", spec))
    return constraints


def _score_prompt(constraints: list[tuple[str, str, str]], candidates: list[dict]) -> list[dict]:
    constraint_lines = "\n".join(f"{label}[{group}] {text}" for label, group, text in constraints)
    candidate_lines = "\n".join(
        f"P{item['id']} | {item['name']} | ¥{item['price']:.2f} | 特征: {item['features']} "
        f"| 规格: {item['specs']} | {item['description']}"
        for item in candidates
    )
    system = (
        "你是商品打分器。根据候选商品满足每条约束的程度打分："
        "1=完全满足，0.5=部分满足，0=不满足。只输出 JSON 数组，"
        '格式：[{"id": 商品id, "scores": {"C1": 1, "C2": 0.5}, "reason": "不超过20字的推荐理由"}]，'
        "每个候选都必须出现一次，scores 必须覆盖所有约束标签。"
    )
    message = llm.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": f"约束列表：\n{constraint_lines}\n\n候选商品：\n{candidate_lines}"},
        ],
        temperature=0.0,
    )
    result = extract_json(message.content)
    if not isinstance(result, list):
        raise ValueError("打分输出不是数组")
    return result


def _heuristic_scores(constraints: list[tuple[str, str, str]], candidates: list[dict]) -> list[dict]:
    """关键词匹配打分：约束文本出现在特征/规格/描述中记 1，部分字重叠记 0.5。"""
    result = []
    for item in candidates:
        haystack = " ".join(
            [item["features"], item["specs"], item["description"], item["name"]]
        )
        scores: dict[str, float] = {}
        for label, _group, text in constraints:
            if text in haystack:
                scores[label] = 1.0
            elif any(ch in haystack for ch in text if not ch.isspace()):
                scores[label] = 0.5
            else:
                scores[label] = 0.0
        result.append({"id": item["id"], "scores": scores, "reason": ""})
    return result


def _weighted_total(
    scores: dict[str, float], constraints: list[tuple[str, str, str]]
) -> float:
    """组内取均值，组间按权重加权（缺省组自动归一化）。"""
    group_scores: dict[str, list[float]] = {}
    label_to_group = {label: group for label, group, _ in constraints}
    for label, score in scores.items():
        group = label_to_group.get(label)
        if group:
            group_scores.setdefault(group, []).append(float(score))

    total_weight = 0.0
    weighted_sum = 0.0
    for group, values in group_scores.items():
        weight = GROUP_WEIGHTS.get(group, 0.2)
        weighted_sum += weight * (sum(values) / len(values))
        total_weight += weight
    return weighted_sum / total_weight if total_weight else 0.0


def _normalize_id(value) -> int | None:
    """把模型返回的商品标识统一成整数 ID，兼容 "P15" / "15" / 15 等写法。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().upper().lstrip("P")
    try:
        return int(text)
    except ValueError:
        return None


def _build_items(
    constraints: list[tuple[str, str, str]],
    candidates: list[dict],
    score_result: list[dict],
) -> list[RecommendItem]:
    score_map: dict[int, dict] = {}
    for entry in score_result:
        if isinstance(entry, dict) and "id" in entry:
            product_id = _normalize_id(entry["id"])
            if product_id is not None:
                score_map[product_id] = entry
    text_of = {label: text for label, _g, text in constraints}

    items: list[RecommendItem] = []
    for product in candidates:
        entry = score_map.get(product["id"], {})
        scores = entry.get("scores", {}) or {}
        matched = [text_of[l] for l, s in scores.items() if float(s) >= 0.5 and l in text_of]
        missed = [text_of[l] for l, s in scores.items() if float(s) < 0.5 and l in text_of]
        reason = entry.get("reason") or (
            f"命中 {len(matched)} 项偏好" if matched else "综合口碑排序"
        )
        items.append(
            RecommendItem(
                product=product,
                score=round(_weighted_total(scores, constraints), 4),
                matched=matched,
                missed=missed,
                reason=reason,
            )
        )
    items.sort(key=lambda x: (x.score, x.product["rating"]), reverse=True)
    return items


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def recommend(
    query: str,
    filters: HardConstraints | None = None,
    top_k: int = TOP_K,
) -> RecommendResult:
    """执行完整的选品流水线。filters 中的显式约束优先于解析结果。"""
    intent = parse_intent(query)
    hard = intent.hard
    if filters:
        hard = hard.model_dump()
        for key, value in filters.model_dump(exclude_none=True).items():
            hard[key] = value
        hard = HardConstraints.model_validate(hard)

    if intent.intent == "chitchat":
        return RecommendResult(mode="clarification", question="这看起来不是购物需求，请问你想买点什么？")

    has_signal = bool(hard.category or hard.brand_in or hard.price_max or hard.price_min or intent.soft.features)
    if intent.need_clarification and not has_signal:
        return RecommendResult(
            mode="clarification",
            question="想先了解两点：你的预算大概是多少？主要使用场景或必须满足的功能是什么？",
        )

    candidates = recall(hard, keyword=query[:20])
    if not candidates:
        return RecommendResult(
            mode="no_result",
            summary="当前商品库中没有符合全部硬约束的商品，可以放宽预算或品牌限制后再试。",
        )

    constraints = _flatten(intent.soft)
    candidates = candidates[:SCORE_LIMIT]

    if constraints:
        if llm.available:
            try:
                score_result = _score_prompt(constraints, candidates)
            except (RuntimeError, ValueError):
                score_result = _heuristic_scores(constraints, candidates)
        else:
            score_result = _heuristic_scores(constraints, candidates)
        items = _build_items(constraints, candidates, score_result)
    else:
        # 没有软约束时按口碑（评分）排序，结果同样可解释
        items = [
            RecommendItem(product=p, score=p["rating"] / 5.0, matched=[], missed=[], reason="按用户口碑排序")
            for p in sorted(candidates, key=lambda x: x["rating"], reverse=True)
        ]

    items = items[:top_k]
    constraint_desc = "、".join(t for _l, _g, t in constraints) or "无软性偏好"
    return RecommendResult(
        mode="ok",
        items=items,
        summary=f"召回 {len(candidates)} 款候选，按软约束（{constraint_desc}）加权打分后取前 {len(items)} 款。",
    )
