"""大模型访问层：统一封装 OpenAI 兼容接口。

DeepSeek、通义千问、智谱等厂商均提供 OpenAI 风格的 Chat Completions
接口，切换厂商只需要修改环境变量，业务代码不感知。

openai 包采用惰性导入：未安装或未配置 Key 时，其余模块（购物车、
订单、选品规则回退）仍可正常运行。
"""

import json
import re
from typing import Any

from app.config import settings


class LLMClient:
    """惰性创建连接的聊天客户端。未配置 API Key 时 available 为 False。"""

    def __init__(self) -> None:
        self._client: Any = None

    @property
    def available(self) -> bool:
        return bool(settings.llm_api_key)

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.2,
    ) -> Any:
        """执行一轮对话，返回首条 assistant 消息对象。"""
        if self._client is None:
            if not self.available:
                raise RuntimeError("未配置 LLM_API_KEY，请在 .env 中填写后重试")
            try:
                from openai import OpenAI
            except ModuleNotFoundError as exc:
                raise RuntimeError("未安装 openai 包，请先执行 pip install -r requirements.txt") from exc
            self._client = OpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
            )
        kwargs: dict[str, Any] = {
            "model": settings.llm_model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        response = self._client.chat.completions.create(**kwargs)
        return response.choices[0].message


llm = LLMClient()


def extract_json(text: str) -> Any:
    """从模型输出中提取 JSON 对象或数组，容忍 markdown 代码块包裹。"""
    text = re.sub(r"```(?:json)?|```", "", text).strip()
    for pattern in (r"\{.*\}", r"\[.*\]", r"\{.*", r"\[.*"):
        matched = re.search(pattern, text, re.DOTALL)
        if matched:
            try:
                return json.loads(matched.group(0))
            except json.JSONDecodeError:
                continue
    raise ValueError(f"输出中未找到合法 JSON: {text[:200]}")
