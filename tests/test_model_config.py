"""模型配置存储单元测试。

覆盖：运行时保存与读取、Key 打码、空字段保留原值、清除 Key、
配置文件与业务数据库分离、缺 Key 时测试接口快速失败（不发网络请求）。
"""

import os

import pytest

from app.config import settings
from app.llm import config_store
from app.llm.client import LLMClient


@pytest.fixture()
def config_file(tmp_path, monkeypatch):
    """把配置文件重定向到临时路径，避免污染真实配置。"""
    path = tmp_path / "model_config.json"
    monkeypatch.setattr(config_store, "CONFIG_PATH", path)
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_base_url", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "llm_model", "deepseek-chat")
    return path


def test_defaults_come_from_env(config_file):
    config = config_store.get_model_config()
    assert config["base_url"] == "https://api.deepseek.com"
    assert config["model"] == "deepseek-chat"
    assert config["source"] == "none"


def test_save_and_read_runtime_config(config_file):
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


def test_config_persisted_to_file_not_database(config_file):
    config_store.save_model_config(api_key="sk-persist-0001", base_url="https://x.example/v1")
    assert config_file.exists()
    content = config_file.read_text(encoding="utf-8")
    assert "sk-persist-0001" in content


def test_public_view_masks_key(config_file):
    config_store.save_model_config(api_key="sk-test-1234567890")
    view = config_store.public_view()
    assert "sk-test-1234567890" not in str(view)
    assert view["api_key_masked"].startswith("sk-t")
    assert view["api_key_set"] is True


def test_empty_fields_keep_existing_values(config_file):
    config_store.save_model_config(base_url="https://a.example/v1", api_key="sk-aaaaaaaaaa", model="m1")
    config_store.save_model_config(base_url="", api_key="", model="")
    config = config_store.get_model_config()
    assert config["base_url"] == "https://a.example/v1"
    assert config["model"] == "m1"
    assert config["api_key"] == "sk-aaaaaaaaaa"


def test_clear_api_key(config_file):
    config_store.save_model_config(api_key="sk-bbbbbbbbbb")
    view = config_store.clear_api_key()
    assert view["api_key_set"] is False
    assert config_store.get_model_config()["api_key"] == ""


def test_trailing_slash_removed(config_file):
    config_store.save_model_config(base_url="https://c.example/v1/")
    assert config_store.get_model_config()["base_url"] == "https://c.example/v1"


def test_test_connection_without_key_fails_fast(config_file):
    result = LLMClient().test_connection(base_url="https://nowhere.example/v1", api_key="", model="m")
    assert result["ok"] is False
    assert "API Key" in result["message"]
