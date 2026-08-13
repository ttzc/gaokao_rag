# tests/conftest.py
"""pytest 共享 fixture。"""

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有测试默认用假 API Key，避免误调真实服务。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setenv("MINERU_API_KEY", "sk-test")
    monkeypatch.setenv("QQ_APP_ID", "test-id")
    monkeypatch.setenv("QQ_APP_SECRET", "test-secret")
