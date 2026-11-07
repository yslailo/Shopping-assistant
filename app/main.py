"""FastAPI 后端入口：对话、购物车、订单与提醒接口。"""

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent import loop
from app.cart import service as cart_service
from app.config import settings
from app.db import database, seed
from app.db.database import get_conn
from app.llm import config_store
from app.llm.client import llm
from app.reminder import scheduler

DEFAULT_USER_ID = 1


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.init_db()
    product_count = seed.seed_if_empty()
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="购物助手智能体", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    user_id: int = DEFAULT_USER_ID
    session_id: str = ""  # 前端浏览器会话号，用于隔离对话上下文；留空回退到用户级


class PayRequest(BaseModel):
    order_id: int
    user_id: int = DEFAULT_USER_ID


class ModelConfigRequest(BaseModel):
    """模型连接配置。字段留空表示保留已保存的值。"""

    base_url: str = ""
    model: str = ""
    api_key: str = ""


class CartChangeRequest(BaseModel):
    product_id: int
    quantity: int = 1
    user_id: int = DEFAULT_USER_ID


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "llm_configured": llm.available,
        "db_path": settings.db_path,
    }


@app.get("/api/model-config")
def get_model_config() -> dict:
    """返回当前模型配置（API Key 打码）。"""
    return config_store.public_view()


@app.get("/api/model-config/reveal")
def reveal_model_config() -> dict:
    """供设置弹窗回显完整密钥，便于用户核对与编辑。

    本服务仅监听 127.0.0.1、单用户本机使用，密钥本就保存在本机文件；
    列表与状态展示接口（/api/model-config）仍然只返回打码视图。
    """
    return {"api_key": config_store.get_model_config()["api_key"]}


@app.post("/api/model-config")
def save_model_config(request: ModelConfigRequest) -> dict:
    """保存模型配置，立即生效，无需重启。"""
    return config_store.save_model_config(
        base_url=request.base_url,
        api_key=request.api_key,
        model=request.model,
    )


@app.delete("/api/model-config/api-key")
def delete_model_api_key() -> dict:
    """清除已保存的 API Key。"""
    return config_store.clear_api_key()


@app.post("/api/model-config/test")
def test_model_config(request: ModelConfigRequest) -> dict:
    """测试模型接口连通性。request 为空时使用已保存的配置。"""
    return llm.test_connection(
        base_url=request.base_url,
        api_key=request.api_key,
        model=request.model,
    )


@app.post("/api/chat")
def chat(request: ChatRequest):
    """对话接口：以 SSE 流式返回模型正文增量与最终业务快照。"""

    def events():
        if not llm.available:
            note = "系统尚未配置大模型接口，请在右侧「设置」中填写 Base URL、模型名和 API Key 后再试。"
            yield f"data: {json.dumps({'delta': note})}\n\n"
            yield f"data: {json.dumps({'done': {'reply': note}})}\n\n"
            return
        try:
            for event in loop.run_turn_stream(request.user_id, request.message, request.session_id):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:  # noqa: BLE001 流内异常无法再改状态码，以 done 事件回传错误
            note = f"模型调用失败：{type(exc).__name__}: {exc}"
            yield f"data: {json.dumps({'done': {'reply': note}})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/api/cart")
def get_cart(user_id: int = DEFAULT_USER_ID) -> dict:
    return cart_service.get_cart(user_id)


@app.get("/api/products/{product_id}")
def get_product(product_id: int) -> dict:
    """商品详情：供前端卡片弹窗展示完整信息。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, category, brand, price, specs, features, rating, stock, description "
            "FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    product = dict(row)
    product["features"] = [f.strip() for f in product["features"].split(",") if f.strip()]
    try:
        product["specs"] = json.loads(product["specs"] or "{}")
    except json.JSONDecodeError:
        product["specs"] = {}
    return product


@app.post("/api/cart/add")
def add_cart(request: CartChangeRequest) -> dict:
    """直接加购（前端按钮使用，不经过大模型）。"""
    message = cart_service.add_to_cart(request.user_id, request.product_id, request.quantity)
    return {"message": message, "cart": cart_service.get_cart(request.user_id)}


@app.post("/api/cart/remove")
def remove_cart(request: CartChangeRequest) -> dict:
    """直接从购物车移除。"""
    message = cart_service.remove_from_cart(request.user_id, request.product_id)
    return {"message": message, "cart": cart_service.get_cart(request.user_id)}


@app.get("/api/orders")
def get_orders(user_id: int = DEFAULT_USER_ID) -> dict:
    cart_service.refresh_order_status()
    return {"orders": cart_service.list_orders(user_id)}


@app.post("/api/orders/pay")
def pay(request: PayRequest) -> dict:
    message = cart_service.pay_order(request.user_id, request.order_id)
    return {"message": message, "orders": cart_service.list_orders(request.user_id)}


@app.get("/api/reminders")
def get_reminders(user_id: int = DEFAULT_USER_ID, unread_only: bool = True) -> dict:
    cart_service.refresh_order_status()
    return {"reminders": cart_service.list_reminders(user_id, unread_only=unread_only)}


@app.post("/api/reminders/read")
def read_reminders(user_id: int = DEFAULT_USER_ID) -> dict:
    return {"marked": cart_service.mark_reminders_read(user_id)}
