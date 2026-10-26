"""对话上下文按会话隔离的回归测试。

历史 bug：会话历史按 user_id 存放，新开浏览器窗口仍能看到之前
窗口的问题。修复后按 session_id 隔离，这里固化两条约束：
1. 不同 session_id 的历史互相不可见；
2. 未传 session_id 时回退到用户级键，兼容脚本调用。
"""

import os

import pytest

from app.agent import loop
from app.config import settings
from app.db import database, seed

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testdata")


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content
        self.tool_calls = None


class _FakeLLM:
    """记录每轮送给模型的消息列表，返回固定文本，不产生工具调用。"""

    def __init__(self):
        self.prompts: list[list[dict]] = []

    def chat(self, messages, tools=None):
        self.prompts.append(list(messages))
        return _FakeMessage("好的")


@pytest.fixture()
def db(request, monkeypatch):
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{request.node.name}.db")
    if os.path.exists(path):
        os.remove(path)
    monkeypatch.setattr(settings, "db_path", path)
    database.init_db(path)
    seed.seed_if_empty(path)
    return path


@pytest.fixture()
def fake_llm(monkeypatch):
    fake = _FakeLLM()
    monkeypatch.setattr(loop, "llm", fake)
    loop._sessions.clear()
    return fake


def test_history_isolated_between_sessions(db, fake_llm):
    loop.run_turn(1, "推荐降噪耳机", session_id="win-a")
    loop.run_turn(1, "推荐电饭煲", session_id="win-b")

    assert loop._sessions["win-a"] == [
        {"role": "user", "content": "推荐降噪耳机"},
        {"role": "assistant", "content": "好的"},
    ]
    # 最后一个窗口的完整消息（含系统提示词）里不应出现另一个窗口的问题
    all_contents = [m.get("content", "") for m in fake_llm.prompts[-1]]
    assert all("推荐降噪耳机" not in c for c in all_contents)


def test_missing_session_id_falls_back_to_user(db, fake_llm):
    loop.run_turn(1, "脚本调用", session_id="")
    assert "user:1" in loop._sessions
    assert loop._sessions["user:1"][0]["content"] == "脚本调用"
