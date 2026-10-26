"""支付提醒调度器。

后台定时扫描订单状态：超时自动失效、临近截止生成支付提醒。
触发时机完全由确定性代码控制；提醒文案在 service 中生成，
大模型只在对话轮中负责把这些提醒组织进回复。

APScheduler 为可选依赖：未安装时不启动定时器，系统降级为
"每次对话前惰性刷新"（见 agent.loop.run_turn），功能依然成立。
"""

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ModuleNotFoundError:  # 可选依赖缺失时降级运行
    BackgroundScheduler = None  # type: ignore[assignment,misc]

from app.cart import service as cart_service

CHECK_INTERVAL_SECONDS = 30

_scheduler = BackgroundScheduler(timezone="Asia/Shanghai") if BackgroundScheduler else None
_available = _scheduler is not None


def is_available() -> bool:
    return _available


def start() -> None:
    if _scheduler is None:
        print("[reminder] 未安装 APScheduler，后台定时提醒关闭；订单状态将在每次对话前惰性刷新。")
        return
    if not _scheduler.get_job("order_status"):
        _scheduler.add_job(
            cart_service.refresh_order_status,
            "interval",
            seconds=CHECK_INTERVAL_SECONDS,
            id="order_status",
            max_instances=1,
            coalesce=True,
        )
    if not _scheduler.running:
        _scheduler.start()


def shutdown() -> None:
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
