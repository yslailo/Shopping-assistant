"""大模型访问层：统一封装 OpenAI 兼容接口。

连接参数（Base URL / API Key / 模型名）由 config_store 提供，可在网页端
"模型设置"中随时修改并即时生效，环境变量只作为默认值。任何提供
/chat/completions 的服务（DeepSeek、通义千问、智谱、vLLM、Ollama、
各类中转站）都可以直接接入。

openai 包采用惰性导入：未安装或未配置 Key 时，其余模块（购物车、
订单、选品规则回退）仍可正常运行。
"""

import json
import re
import time
from typing import Any

from app.llm import config_store


class LLMClient:
    """按运行时配置惰性创建连接；配置变化时自动重建客户端。"""

    def __init__(self) -> None:
        self._client: Any = None
        self._fingerprint: tuple[str, str] | None = None

    @property
    def available(self) -> bool:
        return bool(config_store.get_model_config()["api_key"])

    def _build_client(self, base_url: str, api_key: str, timeout: float = 60.0) -> Any:
        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise RuntimeError("未安装 openai 包，请先执行 pip install -r requirements.txt") from exc
        return OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=1)

    def _get_client(self, config: dict) -> Any:
        fingerprint = (config["base_url"], config["api_key"])
        if self._client is None or fingerprint != self._fingerprint:
            self._client = self._build_client(config["base_url"], config["api_key"])
            self._fingerprint = fingerprint
        return self._client

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.2,
    ) -> Any:
        """执行一轮对话，返回首条 assistant 消息对象。"""
        config = config_store.get_model_config()
        if not config["api_key"]:
            raise RuntimeError("未配置模型 API Key，请在网页左侧「模型设置」中填写后重试")

        client = self._get_client(config)
        kwargs: dict[str, Any] = {
            "model": config["model"],
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message

    def test_connection(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
    ) -> dict:
        """用给定或已保存的配置发一个最小请求，验证接口是否可用。

        Base URL 未带 /v1 且首次请求 404 时，会自动补 /v1 再试一次，
        兼容不同服务商的路径习惯。
        """
        config = config_store.get_model_config()
        base_url = (base_url or config["base_url"]).strip().rstrip("/")
        api_key = (api_key or config["api_key"]).strip()
        model = (model or config["model"]).strip()

        if not api_key:
            return {"ok": False, "message": "未填写 API Key"}
        if not model:
            return {"ok": False, "message": "未填写模型名"}

        candidates = [base_url]
        if not base_url.endswith("/v1"):
            candidates.append(f"{base_url}/v1")

        last_error = ""
        for candidate in candidates:
            try:
                client = self._build_client(candidate, api_key, timeout=20.0)
            except RuntimeError as exc:
                return {"ok": False, "message": str(exc)}
            started = time.perf_counter()
            try:
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "ping"}],
                )
            except Exception as exc:  # noqa: BLE001 网络与鉴权错误统一向上报告
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            latency = int((time.perf_counter() - started) * 1000)
            return {
                "ok": True,
                "message": f"连接成功，模型 {model} 响应正常",
                "latency_ms": latency,
                "base_url": candidate,
                "model": model,
            }

        return {
            "ok": False,
            "message": f"连接失败：{last_error}",
            "base_url": base_url,
        }


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
