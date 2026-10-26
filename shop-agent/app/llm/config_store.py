"""模型连接配置的运行时存储。

在网页端填写任意 OpenAI 兼容接口的 Base URL / API Key / 模型名，
保存在 SQLite 的 app_settings 表中，保存后立即生效，无需重启服务。
环境变量（.env）只作为未配置时的默认值，不再是唯一入口。
"""

from app.config import settings
from app.db.database import get_conn

KEY_BASE_URL = "llm.base_url"
KEY_API_KEY = "llm.api_key"
KEY_MODEL = "llm.model"


def get_model_config() -> dict:
    """读取当前生效的模型配置；api_key 为明文，仅供后端内部使用。"""
    stored: dict[str, str] = {}
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT key, value FROM app_settings WHERE key IN (?, ?, ?)",
                (KEY_BASE_URL, KEY_API_KEY, KEY_MODEL),
            ).fetchall()
        stored = {row["key"]: row["value"] for row in rows}
    except Exception:
        stored = {}  # 库表尚未初始化时退回环境变量默认值

    runtime_key = stored.get(KEY_API_KEY, "")
    env_key = settings.llm_api_key
    if runtime_key:
        source = "runtime"
    elif env_key:
        source = "env"
    else:
        source = "none"

    return {
        "base_url": stored.get(KEY_BASE_URL) or settings.llm_base_url,
        "api_key": runtime_key or env_key,
        "model": stored.get(KEY_MODEL) or settings.llm_model,
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
    updates: dict[str, str] = {}
    if base_url.strip():
        updates[KEY_BASE_URL] = base_url.strip().rstrip("/")
    if model.strip():
        updates[KEY_MODEL] = model.strip()
    if api_key.strip():
        updates[KEY_API_KEY] = api_key.strip()

    if updates:
        with get_conn() as conn:
            conn.executemany(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = datetime('now', 'localtime')",
                list(updates.items()),
            )
    return public_view()


def clear_api_key() -> dict:
    """清除已保存的 API Key（回到环境变量或未配置状态）。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (KEY_API_KEY,))
    return public_view()
