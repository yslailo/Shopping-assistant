"""FastAPI 后端入口：对话、购物车、订单与提醒接口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.agent import loop
from app.cart import service as cart_service
from app.config import settings
from app.db import database, seed
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


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "llm_configured": llm.available,
        "db_path": settings.db_path,
    }


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict:
    if not llm.available:
        return {
            "reply": "系统尚未配置大模型 API Key，请复制 .env.example 为 .env 并填写 LLM_API_KEY 后重启服务。",
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
