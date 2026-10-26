"""模型配置存储单元测试。

覆盖：运行时保存与读取、Key 打码、空字段保留原值、清除 Key、
测试接口在缺 Key 时的快速失败（不发网络请求）。
"""

import os

import pytest

from app.config import settings
from app.db import database
from app.llm import config_store
from app.llm.client import LLMClient

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testdata")


@pytest.fixture()
def db(request, monkeypatch):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{request.node.name}.db")
    if os.path.exists(path):
        os.remove(path)
    monkeypatch.setattr(settings, "db_path", path)
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_base_url", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "llm_model", "deepseek-chat")
    database.init_db(path)
    return path


def test_defaults_come_from_env(db):
    config = config_store.get_model_config()
    assert config["base_url"] == "https://api.deepseek.com"
    assert config["model"] == "deepseek-chat"
    assert config["source"] == "none"


def test_save_and_read_runtime_config(db):
    saved = config_store.save_model_config(
        base_url="https://example.com/v1",
        api_key="sk-test-1234567890",
        model="my-model",
    )
    assert saved["api_key_set"] is True
    assert saved["source"] == "runtime"

    config = config_store.get_model_config()
    assert config["base_url"] == "https://example.com/v1"
    assert config["model"] == "my-model"
    assert config["api_key"] == "sk-test-1234567890"


def test_public_view_masks_key(db):
    config_store.save_model_config(api_key="sk-test-1234567890")
    view = config_store.public_view()
    assert "sk-test-1234567890" not in str(view)
    assert view["api_key_masked"].startswith("sk-t")
    assert view["api_key_set"] is True


def test_empty_fields_keep_existing_values(db):
    config_store.save_model_config(base_url="https://a.example/v1", api_key="sk-aaaaaaaaaa", model="m1")
    config_store.save_model_config(base_url="", api_key="", model="")
    config = config_store.get_model_config()
    assert config["base_url"] == "https://a.example/v1"
    assert config["model"] == "m1"
    assert config["api_key"] == "sk-aaaaaaaaaa"


def test_clear_api_key(db):
    config_store.save_model_config(api_key="sk-bbbbbbbbbb")
    view = config_store.clear_api_key()
    assert view["api_key_set"] is False
    assert config_store.get_model_config()["api_key"] == ""


def test_trailing_slash_removed(db):
    config_store.save_model_config(base_url="https://c.example/v1/")
    assert config_store.get_model_config()["base_url"] == "https://c.example/v1"


def test_test_connection_without_key_fails_fast(db):
    result = LLMClient().test_connection(base_url="https://nowhere.example/v1", api_key="", model="m")
    assert result["ok"] is False
    assert "API Key" in result["message"]
