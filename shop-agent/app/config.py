"""全局配置：统一从环境变量读取，集中管理默认值。"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    """应用配置。LLM 部分兼容所有 OpenAI 风格接口，切换厂商只需改环境变量。"""

    def __init__(self) -> None:
        self.llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
        self.llm_api_key: str = os.getenv("LLM_API_KEY", "")
        self.llm_model: str = os.getenv("LLM_MODEL", "deepseek-chat")

        self.db_path: str = os.getenv("SHOP_AGENT_DB", str(BASE_DIR / "shop_agent.db"))
        self.payment_timeout_minutes: int = int(os.getenv("PAYMENT_TIMEOUT_MINUTES", "30"))
        self.remind_before_minutes: int = int(os.getenv("REMIND_BEFORE_MINUTES", "10"))


settings = Settings()
