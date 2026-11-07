"""模型连接配置的运行时存储。

在网页端填写任意 OpenAI 兼容接口的 Base URL / API Key / 模型名，
保存后立即生效，无需重启服务。环境变量（.env）只作为未配置时的默认值。

配置单独存放在项目根目录的 model_config.json 中，与业务数据库分离：
清空/重建演示数据不会连带丢失模型配置。
"""

import json
import os
import threading

from app.config import BASE_DIR, settings

CONFIG_PATH = BASE_DIR / "model_config.json"
_lock = threading.Lock()


def _read_file() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_file(data: dict) -> None:
    tmp_path = str(CONFIG_PATH) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, CONFIG_PATH)  # 原子替换，避免写入中断导致配置损坏


def get_model_config() -> dict:
    """读取当前生效的模型配置；api_key 为明文，仅供后端内部使用。"""
    stored = _read_file()
    runtime_key = str(stored.get("api_key") or "").strip()
    env_key = settings.llm_api_key

    if runtime_key:
        source = "runtime"
    elif env_key:
        source = "env"
    else:
        source = "none"

    return {
        "base_url": str(stored.get("base_url") or settings.llm_base_url),
        "api_key": runtime_key or env_key,
        "model": str(stored.get("model") or settings.llm_model),
        "source": source,
    }


def public_view(config: dict | None = None) -> dict:
    """对外展示用视图：API Key 打码，避免前端拿到完整密钥。"""
    config = config or get_model_config()
    key = config["api_key"]
    if len(key) > 8:
        masked = f"{key[:4]}****{key[-4:]}"
    else:
        masked = "****" if key else ""
    return {
        "base_url": config["base_url"],
        "model": config["model"],
        "api_key_set": bool(key),
        "api_key_masked": masked,
        "source": config["source"],
    }


def save_model_config(
    base_url: str = "",
    api_key: str = "",
    model: str = "",
) -> dict:
    """保存配置。空字段表示保留原值，只更新用户实际填写的项。"""
    with _lock:
        data = _read_file()
        if base_url.strip():
            data["base_url"] = base_url.strip().rstrip("/")
        if model.strip():
            data["model"] = model.strip()
        if api_key.strip():
            data["api_key"] = api_key.strip()
        _write_file(data)
    return public_view()


def clear_api_key() -> dict:
    """清除已保存的 API Key（回到环境变量或未配置状态）。"""
    with _lock:
        data = _read_file()
        data.pop("api_key", None)
        _write_file(data)
    return public_view()
