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
│   ├── config.py                   # 被测模块
│   ├── api/llm.py                  # 子模块
│   ├── store/db/                   # SQLite 数据层包（files/questions/topics/question_topics）
│   ├── store/vector/               # Chroma 向量层包（vector_store/knowledge）
│   ├── store/file_store.py         # 文件存储
│   └── ingestion/question.py       # 原子化单题摄入
├── tests/
│   ├── conftest.py                       # 隔离唯一入口：_reset_state autouse + FakeEmbeddings + fixtures
│   ├── test_config.py                    # 测 src/config.py
│   ├── test_api_llm.py                   # 测 src/api/llm.py
│   ├── test_api_embedding.py             # 测 src/api/embedding.py
│   ├── test_api_vlm.py                   # 测 src/api/vlm.py
│   ├── test_store_db_files.py            # 测 src/store/db/files.py
│   ├── test_store_db_questions.py        # 测 src/store/db/questions.py
│   ├── test_store_db_topics.py           # 测 src/store/db/topics.py
│   ├── test_store_db_question_topics.py  # 测 src/store/db/question_topics.py
│   ├── test_store_file.py                # 测 src/store/file_store.py
│   ├── test_vector_store.py              # 测 src/store/vector/vector_store.py
│   ├── test_knowledge.py                 # 测 src/store/vector/knowledge.py
│   └── test_ingestion_question.py        # 测 src/ingestion/question.py
└── pyproject.toml
```

**命名规则**：

- 测试文件：`test_<模块名>.py`，**加 `test_` 前缀**（避免与被测模块同名造成 import 冲突）
- 子模块路径用下划线展平：`src/store/db/files.py` → `tests/test_store_db_files.py`
- pytest 自动发现 `tests/` 下所有 `test_*.py`

## pyproject.toml 配置

```toml
[dependency-groups]
dev = [
    "pytest>=9.1.1",
    "pytest-asyncio>=1.4.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v -m 'not integration'"
markers = [
    "integration: 真实 API 调用（DeepSeek / Qwen / DashScope 等），会产生计费；全量 pytest 默认排除，仅 `pytest -m integration` 显式运行。",
]
```

安装：`uv sync --extra dev`

## 测试分层

| 层级 | 内容 | 是否连外部服务 |
| ------ | ------ | -------------- |
| **纯单元测试** | 配置解析、工具函数、纯逻辑 | ❌ 不连 |
| **模块测试** | 存储层（直接使用 config 路径库 + 每测试前清空）、摄入管线（mock VLM/LLM） | ❌ mock |
| **集成测试** | 真实 API 调用（DeepSeek/Qwen/DashScope），会产生计费 | ✅ 连，标 `@pytest.mark.integration` |

**默认 `pytest` 已强制排除 integration**（`addopts = "-v -m 'not integration'"`）；集成测试仅 `pytest -m integration` 显式运行（本地手动、需真实 Key）。注意：`tests/test_api_*.py` 是 mock 单测（仅验证 wrapper 构造/配置/分批逻辑），**不**标 integration，随默认跑、不计费。

## 测试规范细则

### 1. conftest.py —— 测试隔离唯一入口

`tests/conftest.py` 是**唯一的隔离入口**：autouse `_reset_state` 在每个测试【前】清空全部业务数据 + 重置单例 + patch 假嵌入，保证每个测试从确定状态开始、测试间无顺序依赖。测试文件内不再做手动单例重置 / tmp chroma / 清表。

```python
# tests/conftest.py（隔离核心，节选）
from __future__ import annotations

import os
import shutil
import sqlite3
import time

import chromadb
import pytest
from langchain_core.embeddings import Embeddings

from src.config import config
from src.store.db import get_shared_conn
from src.store.file_store import FileStore
from src.store.vector.vector_store import VectorStore


class FakeEmbeddings(Embeddings):
    """测试用假嵌入模型，维度取自 config.embedding.dimension，不真调 DashScope API。"""

    def __init__(self, size: int | None = None) -> None:
        self._dim = size if size is not None else config.embedding.dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self._dim for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * self._dim


# 清空顺序 = 外键依赖逆序（子表先删；foreign_keys=ON 下先删父表会报错）
_BUSINESS_TABLES = (
    "errors", "question_topics", "questions", "exam_attempts",
    "topics", "review_plans", "periodic_reports", "files",
)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个测试【前】清空全部业务数据 + 重置单例 + patch 假嵌入。"""
    # 1. SQLite：清空全部业务表（缺失表静默跳过），不关闭共享连接（保持 WAL + foreign_keys）
    conn = get_shared_conn()
    for table in _BUSINESS_TABLES:
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass  # 表尚未创建
    conn.commit()

    # 2. Chroma：先关闭全部残留 client（GC 不释放 chroma 的 sqlite 文件锁，必须显式
    #    close()），再整体清空目录重建 —— 规避 chroma 1.5.9 删同名 collection 的
    #    segment 元数据残留 bug（count=0 但新 doc 合并旧字段）
    _close_tracked_chroma_clients()
    _wipe_chroma_dir()

    # 3. 重置全部单例 + schema 初始化标志（files/questions/topics/question_topics/
    #    vector_store/knowledge/file_store 的 _instance 与 _schema_initialized）
    # 4. 清空 FileStore 5 个子目录下的文件（保留目录本身）
    # 5. patch 假嵌入：无参 get_vector_store() 构造都用 FakeEmbeddings，不真调 API
    monkeypatch.setattr(
        "src.store.vector.vector_store.get_embedding_model",
        lambda: FakeEmbeddings(config.embedding.dimension),
    )
```

另有公共 fixtures：`files_db` / `questions_db` / `topics_db` / `question_topics_db`（各表单例）、`file_store`（config 真实目录）、`fake_embeddings`（session 级）、`vector_store`（config 目录 + FakeEmbeddings，并写入 `_instance` 供 `get_vector_store()` 复用）。测试文件可直接 `from conftest import FakeEmbeddings`（pytest prepend 导入模式）。

chroma 清理的两个关键助手：session 级 autouse `_track_vector_store_instances` 给 `VectorStore.__init__` 挂登记钩子（所有构造实例入册，含中途 raise 的部分实例）；`_close_tracked_chroma_clients()` 对入册实例统一 `client.close()` —— 实测 chroma 的 sqlite 文件锁在 `del + gc.collect` 后仍不释放，直接构造的本地实例不显式 close 则 rmtree 必失败。`_wipe_chroma_dir()` 用 rmtree 整体清空（3 次重试，全失败才回退 delete_collection）。

### 2. 测试数据策略

**不做"测试数据隔离"**——测试直接使用 config 真实路径（`data/gaokao.db`、`data/chroma_db`、`data/files`）：

- **开发阶段 `data/` 不会有重要数据**（终极决策）：开发期数据随时可重新导入重建，测试清掉 `data/` 下现有数据是预期行为，**一律放心清理重置，不做保护、不再提示**
- **生产环境不跑测试**：所以真实路径不会污染生产
- `_reset_state` 在【测试前】执行（autouse setup-only，无 yield），每个测试从确定状态开始，隔离与运行顺序完全清晰、顺序无关
- **唯一例外：绝对路径功能测试**（`TestAbsoluteDataDir`）保留 `tmp_path`——它需要"项目外绝对目录"作输入，`tmp_path` 是**测试输入沙箱**而非数据隔离，不写 `data/` 不冲突

常见问题：`get_shared_conn()` 的 `_shared` 单例在首次创建时绑定 `config.store.sqlite_path`，因此**不能用 monkeypatch `data_dir` 换库做隔离**（会绑定失效）——隔离统一由清空 + 重置单例承担。

### 3. 测试示例：test_config.py

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
        from src.config import _load
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test")
        cfg = tmp_path / "config.toml"
        cfg.write_text(
            '[llm]\nmodel = "deepseek-v4-flash"\n'
            'api_key = "${DEEPSEEK_API_KEY}"\n',
            encoding="utf-8",
        )
        app = _load(cfg)  # ${VAR} 由环境变量展开 + Pydantic 校验
        assert app.llm.model == "deepseek-v4-flash"
        assert app.llm.api_key == "sk-deepseek-test"
```

### 4. Mock 外部服务（模块测试）

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

### 5. 集成测试（显式标记，会计费）

真实 API 调用的端到端测试必须标 `@pytest.mark.integration`，且默认 `pytest` 已自动排除（见 pyproject `addopts`）。约定：**Agent 层真实端到端测试统一放 `tests/test_agent_integration.py`**，文件级 `pytestmark = pytest.mark.integration`；纯行为/配置测试（mock LLM/VLM/嵌入）留在 `tests/test_agent_*.py`，随默认跑、不计费。

```python
# tests/test_agent_integration.py
import pytest

pytestmark = pytest.mark.integration   # 整个文件 = 真实 API，会计费

def test_real_ingest_roundtrip():
    """真实调用 DeepSeek/Qwen/DashScope（需要 .env 真实 Key，仅本地手动跑）。"""
    ...
```

conftest 说明：autouse `_reset_state` 对标记 `integration` 的测试**跳过 FakeEmbeddings patch**，让嵌入层真正打到云端 API；数据清空与单例重置仍照常执行，保证每次从干净状态开始。

运行方式：

```bash
pytest                          # 全量但排除 integration（默认，不计费）
pytest -m integration           # 只跑集成（会计费，需真实 Key）
pytest -m "not integration"     # 显式排除集成（等于默认）
pytest -m ""                    # 真·全部（含 integration，会计费）
```

## 运行命令

```bash
uv sync --extra dev              # 安装 pytest
pytest                           # 跑全部单元测试（自动排除 integration）
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
- **CI（未来）**：GitHub Actions 跑默认 `pytest`（已 `addopts` 排除 integration，不暴露真实 Key）；计费集成测试不进 CI。
