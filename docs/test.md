# 测试规范（Test Guide）

> Gaokao RAG 的单元测试约定：框架 **pytest**，命名扁平化 `test_<模块>.py`，`src/` 与 `tests/` 一一对应。

## 为什么用 pytest

| 维度 | unittest | **pytest** |
| ------ | ---------- | ----------- |
| 安装 | 内置零依赖 | `pip install pytest`（轻量） |
| 断言 | `self.assertEqual(...)` | `assert x == y`（原生语法） |
| 参数化 | 繁琐 | `@pytest.mark.parametrize` 一行 |
| fixture | setUp/tearDown | `@pytest.fixture` 更灵活 |
| 错误信息 | 一般 | 友好（diff 对比） |
| 兼容性 | — | **完全兼容 unittest 风格** |

pytest 兼容 unittest 风格，用 pytest 无损失只有增益。

## 目录结构

```tree
gaokao_rag/
├── src/
│   ├── config.py          # 被测模块
│   ├── api/llm.py         # 子模块
│   └── store/db.py
├── tests/
│   ├── conftest.py        # 共享 fixture（测试库、mock API Key）
│   ├── test_config.py     # 测 src/config.py
│   ├── test_api_llm.py    # 测 src/api/llm.py（下划线展平路径）
│   ├── test_store_db.py   # 测 src/store/db.py
│   └── ...
└── pyproject.toml
```

**命名规则**：

- 测试文件：`test_<模块名>.py`，**加 `test_` 前缀**（避免与被测模块同名造成 import 冲突）
- 子模块路径用下划线展平：`src/store/db.py` → `tests/test_store_db.py`
- pytest 自动发现 `tests/` 下所有 `test_*.py`

## pyproject.toml 配置

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v"
```

安装：`uv sync --extra dev`

## 测试分层

| 层级 | 内容 | 是否连外部服务 |
| ------ | ------ | -------------- |
| **纯单元测试** | 配置解析、工具函数、纯逻辑 | ❌ 不连 |
| **模块测试** | 存储层（SQLite 内存库）、摄入管线（mock VLM/LLM） | ❌ mock |
| **集成测试** | 真实 API 调用（DeepSeek/Qwen） | ✅ 连，标 `@pytest.mark.integration` |

**默认只跑单元 + 模块测试**（快、稳定、CI 友好）；集成测试显式标记，本地手动跑。

## 测试规范细则

### 1. conftest.py 共享 fixture

```python
# tests/conftest.py
import os
import pytest

@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    """所有测试默认用假 API Key，避免误调真实服务。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setenv("QQ_APP_ID", "test-id")
    monkeypatch.setenv("QQ_APP_SECRET", "test-secret")

@pytest.fixture
def tmp_store(tmp_path):
    """临时存储目录 fixture（每个测试独立）。"""
    from src.config import StoreConfig
    return StoreConfig(
        data_dir=str(tmp_path),
    )
```

**关键原则**：`autouse` fixture 让每个测试都跑在**隔离环境**（假 Key + 临时目录），测试之间零污染。

### 2. 测试示例：test_config.py

```python
# tests/test_config.py
"""src/config.py 的单元测试。"""
from src.config import LLMConfig, VLMConfig, AppConfig, _expand


class TestExpand:
    """${VAR} 占位符替换。"""

    def test_expand_existing_env(self, monkeypatch):
        monkeypatch.setenv("FOO", "bar")
        assert _expand("${FOO}") == "bar"

    def test_expand_missing_env_keeps_placeholder(self):
        assert _expand("${NOT_SET_VAR}") == "${NOT_SET_VAR}"

    def test_expand_nested_in_string(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        assert _expand("${HOST}:5432") == "localhost:5432"


class TestLLMConfig:
    """LLM 配置默认值。"""

    def test_default_model_is_v4_flash(self):
        """默认模型必须是 deepseek-v4-flash（不是老名 deepseek-chat）。"""
        assert LLMConfig().model == "deepseek-v4-flash"

    def test_default_base_url_no_v1(self):
        """DeepSeek base_url 不带 /v1（官方地址就是根路径）。"""
        assert LLMConfig().base_url == "https://api.deepseek.com"


class TestAppConfig:
    """顶层配置聚合。"""

    def test_load_from_toml(self, tmp_path, monkeypatch):
        import tomllib
        from pathlib import Path
        cfg = tmp_path / "config.toml"
        cfg.write_text(
            '[llm]\nmodel = "deepseek-v4-flash"\n'
            'api_key = "${DEEPSEEK_API_KEY}"\n',
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        # 重新加载（实际项目里 config 是模块级单例，测试用 _load 的副本逻辑）
        raw = tomllib.load(cfg.open("rb"))
        from src.config import _expand_dict
        raw = _expand_dict(raw)
        app = AppConfig(**raw)
        assert app.llm.model == "deepseek-v4-flash"
        assert app.llm.api_key == "sk-test"  # 来自 conftest 的假 Key
```

### 3. Mock 外部服务（模块测试）

```python
# tests/test_api_llm.py
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_llm_call_mocked():
    """LLM 调用用 mock，不真连 DeepSeek。"""
    with patch("src.api.llm.some_client.chat") as mock_chat:
        mock_chat.return_value = "mocked answer"
        # ... 调用被测函数
```

**原则**：所有外部服务（LLM/VLM/嵌入 API）在单元/模块测试中一律 mock；真实调用只出现在显式标记的 integration 测试里。

### 4. 集成测试（显式标记）

```python
# tests/test_integration_real_api.py
import pytest

pytestmark = pytest.mark.integration

def test_real_deepseek_call():
    """真实调用 DeepSeek（需要 .env 真实 Key，本地手动跑）。"""
    ...
```

运行方式：

```bash
pytest                                   # 只跑单元+模块（默认）
pytest -m integration                    # 只跑集成
pytest -m "not integration"              # 排除集成
```

## 运行命令

```bash
uv sync --extra dev              # 安装 pytest
pytest                           # 跑全部单元测试
pytest tests/test_config.py      # 跑单个文件
pytest -k "config"               # 按名称过滤
pytest --cov=src --cov-report=term-missing   # 覆盖率
```

## 覆盖率目标

| 模块 | 目标 |
| ------ | ------ |
| config.py（配置解析） | ≥ 90% |
| store/（存储层） | ≥ 80% |
| ingestion/（摄取管线） | ≥ 70%（LLM 调用 mock 后） |
| agent/（TeamAgent 编排） | ≥ 60%（集成测试补充） |

> 覆盖率是参考不是红线——核心逻辑（配置/存储/解析）优先，Agent 编排以行为验证为主。

## 与开发流程的配合

- **每实现一个模块 → 同步写 test_<模块>.py**（TDD 可选，但"实现即测"必须）
- **提交前**：`pytest` 全绿 + `pytest --cov` 检查新增模块覆盖
- **重构后**：跑全量确认无回归
- **CI（未来）**：GitHub Actions 跑 `pytest -m "not integration"`（不暴露真实 Key）
