"""调用统计：记录每次模型调用的耗时与 token 用量。

论文里要回答"这个方案跑一次要多少钱、慢不慢"，就必须有实测数据。
这里把每次调用记录成一行，接口层再做聚合，避免把统计逻辑散落在各处。

存储用内存 + 落盘 JSON 的轻量方案：演示环境重启后数据不丢，
也不引入额外依赖；并发写入用锁保护。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

STATS_PATH = Path(__file__).resolve().parent.parent.parent / "llm_usage.json"
MAX_RECORDS = 500  # 只保留最近若干条，防止文件无限增长

_lock = threading.Lock()
_records: list[dict] = []
_loaded = False


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if STATS_PATH.exists():
            data = json.loads(STATS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                _records.extend(data[-MAX_RECORDS:])
    except (OSError, json.JSONDecodeError):
        pass  # 统计文件损坏不能影响主流程


def record(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    purpose: str = "chat",
    ok: bool = True,
) -> None:
    """记录一次模型调用。"""
    with _lock:
        _ensure_loaded()
        _records.append(
            {
                "ts": round(time.time(), 3),
                "model": model,
                "purpose": purpose,
                "prompt_tokens": int(prompt_tokens or 0),
                "completion_tokens": int(completion_tokens or 0),
                "latency_ms": round(float(latency_ms), 1),
                "ok": bool(ok),
            }
        )
        if len(_records) > MAX_RECORDS:
            del _records[: len(_records) - MAX_RECORDS]
        try:
            STATS_PATH.write_text(
                json.dumps(_records, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # 落盘失败只影响持久化，不影响当次统计


def summary() -> dict:
    """聚合统计：总调用次数、成功次数、token 总量与平均延迟。"""
    with _lock:
        _ensure_loaded()
        records = list(_records)

    if not records:
        return {
            "calls": 0,
            "success": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "avg_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "by_purpose": {},
        }

    latencies = sorted(r["latency_ms"] for r in records)
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
    by_purpose: dict[str, dict] = {}
    for record_item in records:
        bucket = by_purpose.setdefault(
            record_item["purpose"], {"calls": 0, "tokens": 0, "latency_ms": 0.0}
        )
        bucket["calls"] += 1
        bucket["tokens"] += record_item["prompt_tokens"] + record_item["completion_tokens"]
        bucket["latency_ms"] += record_item["latency_ms"]

    for bucket in by_purpose.values():
        bucket["avg_latency_ms"] = round(bucket["latency_ms"] / bucket["calls"], 1)
        del bucket["latency_ms"]

    return {
        "calls": len(records),
        "success": sum(1 for r in records if r["ok"]),
        "prompt_tokens": sum(r["prompt_tokens"] for r in records),
        "completion_tokens": sum(r["completion_tokens"] for r in records),
        "total_tokens": sum(r["prompt_tokens"] + r["completion_tokens"] for r in records),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1),
        "p95_latency_ms": round(p95, 1),
        "by_purpose": by_purpose,
    }


def reset() -> None:
    """清空统计（演示前重置用）。"""
    global _loaded
    with _lock:
        _records.clear()
        _loaded = True
        try:
            STATS_PATH.unlink(missing_ok=True)
        except OSError:
            pass
