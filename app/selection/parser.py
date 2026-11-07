"""意图解析：把自然语言购物需求解析为结构化约束。

核心设计：硬约束（违反即淘汰，走 SQL 过滤）与软约束（影响打分排序）
必须分离。优先使用大模型解析，失败时回退到基于词表的正则规则，
保证在无 LLM 环境下流程仍可运行。
"""

import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.db.database import get_conn
from app.llm.client import extract_json, llm

_INTENTS = ("product_search", "cart_op", "payment_op", "chitchat", "unclear")


class HardConstraints(BaseModel):
    """硬约束：品类、品牌、预算。不满足的商品直接不进入候选。"""

    category: str | None = None
    brand_in: list[str] = Field(default_factory=list)
    price_min: float | None = None
    price_max: float | None = None


class SoftConstraints(BaseModel):
    """软约束：功能、场景、规格偏好，用于候选商品打分排序。"""

    features: list[str] = Field(default_factory=list)
    scenario: str | None = None
    specs: list[str] = Field(default_factory=list)


class UserIntent(BaseModel):
    intent: str = "unclear"
    hard: HardConstraints = Field(default_factory=HardConstraints)
    soft: SoftConstraints = Field(default_factory=SoftConstraints)
    need_clarification: bool = False


# ---------------------------------------------------------------------------
# 词表：从商品库动态加载，用于规则回退
# ---------------------------------------------------------------------------

def _load_vocab(db_path: str | None = None) -> dict[str, set[str]]:
    categories: set[str] = set()
    brands: set[str] = set()
    features: set[str] = set()
    try:
        with get_conn(db_path) as conn:
            for row in conn.execute("SELECT DISTINCT category FROM products"):
                categories.add(row["category"])
            for row in conn.execute("SELECT DISTINCT brand FROM products"):
                brands.add(row["brand"])
            for row in conn.execute("SELECT features FROM products"):
                features.update(f.strip() for f in row["features"].split(",") if f.strip())
    except Exception:
        pass  # 库表尚未初始化时按空词表处理，仅影响规则回退的召回质量
    return {"categories": categories, "brands": brands, "features": features}


# 预算表达式的几种常见说法：300以内 / 300以下 / 300左右 / 300以上 / 预算300
_PRICE_RULES: list[tuple[str, str]] = [
    (r"(\d+(?:\.\d+)?)\s*(?:元|块)?\s*(?:以内|以下|之内)", "max"),
    (r"(?:超过|高于)\s*(\d+(?:\.\d+)?)", "max"),
    (r"(\d+(?:\.\d+)?)\s*(?:元|块)?\s*(?:以上|往上)", "min"),
    (r"预算\s*(\d+(?:\.\d+)?)", "around"),
    (r"(\d+(?:\.\d+)?)\s*(?:元|块)?\s*左右", "around"),
]


def _parse_price(text: str) -> tuple[float | None, float | None]:
    for pattern, mode in _PRICE_RULES:
        matched = re.search(pattern, text)
        if not matched:
            continue
        value = float(matched.group(1))
        if mode == "max":
            return None, value
        if mode == "min":
            return value, None
        return round(value * 0.8), round(value * 1.2)  # around 放宽 20%
    return None, None


def _heuristic_parse(text: str) -> UserIntent:
    """规则回退解析：词表匹配品类/品牌/功能，正则匹配预算。"""
    vocab = _load_vocab()
    hard = HardConstraints()
    soft = SoftConstraints()

    matched_category = [c for c in vocab["categories"] if c and c in text]
    if matched_category:
        hard.category = max(matched_category, key=len)

    hard.brand_in = sorted(b for b in vocab["brands"] if b and b in text)
    hard.price_min, hard.price_max = _parse_price(text)

    soft.features = sorted(f for f in vocab["features"] if f and f in text)
    if re.search(r"游戏|出差|旅行|送礼|考研|上课|办公|宿舍", text):
        soft.scenario = text

    has_query_signal = bool(hard.category or hard.brand_in or soft.features or hard.price_max)
    return UserIntent(
        intent="product_search" if has_query_signal else "unclear",
        hard=hard,
        soft=soft,
        need_clarification=not has_query_signal,
    )


# ---------------------------------------------------------------------------
# 大模型解析
# ---------------------------------------------------------------------------

_PARSE_SYSTEM_PROMPT = """你是购物需求解析器，把用户消息解析为结构化 JSON，只输出 JSON 本身。
字段定义：
- intent: product_search(找商品) / cart_op(购物车操作) / payment_op(支付操作) / chitchat(闲聊) / unclear
- hard: 硬约束，违反即淘汰。{"category": 品类或null, "brand_in": [品牌列表], "price_min": 数字或null, "price_max": 数字或null}
- soft: 软约束，影响排序。{"features": [功能关键词], "scenario": 使用场景或null, "specs": [规格偏好]}
- need_clarification: 信息不足以推荐时为 true
规则：
1. 金额单位为人民币元，只写数字；
2. "X左右"的预算放宽 20% 写入 price_min/price_max；
3. 用户消息里的功能、场景、规格偏好放入 soft，不要放进 hard；
4. 未提及的字段保持空值，不要臆造。"""


def parse_intent(text: str) -> UserIntent:
    """解析用户意图。LLM 可用时走模型，异常时回退规则。"""
    if llm.available:
        try:
            message = llm.chat(
                [
                    {"role": "system", "content": _PARSE_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
            )
            data: dict[str, Any] = extract_json(message.content)
            if data.get("intent") not in _INTENTS:
                data["intent"] = "unclear"
            return UserIntent.model_validate(data)
        except (RuntimeError, ValueError, ValidationError, KeyError):
            pass  # 模型输出不合格时回退规则解析
    return _heuristic_parse(text)
