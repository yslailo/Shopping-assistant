"""FastAPI 后端入口：对话、购物车、订单与提醒接口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.agent import loop
from app.cart import service as cart_service
from app.config import settings
from app.db import database, seed
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


class PayRequest(BaseModel):
    order_id: int
    user_id: int = DEFAULT_USER_ID


class ModelConfigRequest(BaseModel):
    """模型连接配置。字段留空表示保留已保存的值。"""

    base_url: str = ""
    model: str = ""
    api_key: str = ""


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
def chat(request: ChatRequest) -> dict:
    if not llm.available:
        return {
            "reply": "系统尚未配置大模型接口，请在左侧「模型设置」中填写 Base URL、模型名和 API Key 后再试。",
        }
    return loop.run_turn(request.user_id, request.message)


@app.get("/api/cart")
def get_cart(user_id: int = DEFAULT_USER_ID) -> dict:
    return cart_service.get_cart(user_id)


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
