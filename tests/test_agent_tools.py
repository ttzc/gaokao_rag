# tests/test_agent_tools.py
"""agent 工具层（src/agent/tools/）测试：导出面 + FunctionTool 元数据 + 调用转发 + 分层铁律。

被测主体是模块级工具实例（子 Agent 挂载的交付物）：写侧 ``ingest_question_tool``
（FunctionTool）+ 读侧 ``knowledge_search_tool``（框架 LangchainKnowledgeSearchTool），
非测试内自行包装的副本。全部 mock 写门面（src.ingestion），不触真实存储 / 网络 / 计费 API。

工具函数经 `from src.ingestion.question import ingest_question as _ingest_question`
绑定到工具模块，monkeypatch 必须打在 ``src.agent.tools.ingest_tool._ingest_question``
（from-import 在 import 时把函数对象绑进本模块全局，只 patch 源模块不会重绑——
同 tests/conftest.py 嵌入层 patch 三处的教训）。

`_run_async_impl` 直调（非公开 run_async）：仿官方 tests/tools/test_function_tool.py，
run_async 的 filter 链是框架自身职责，本文件只测工具层语义。
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.knowledge.langchain_knowledge import SearchType
from trpc_agent_sdk.server.knowledge.tools import LangchainKnowledgeSearchTool
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools.utils import get_mandatory_args

from src.agent.tools import ingest_tool, retrieve_tool
from src.agent.tools.ingest_tool import ingest_question_tool


def _fake_tool_context() -> MagicMock:
    """构造 _run_async_impl 可用的最小 tool_context（工具函数不感知 context）。"""
    return MagicMock(spec=InvocationContext)


class _FacadeRecorder:
    """记录门面入参的替身：返回固定 {question_id, doc_id}。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"question_id": 7, "doc_id": "q_7"}


# ═══════════════════════════════════════════════════════════════════════════════
# 导出面：模块级 FunctionTool 实例 + 挂载清单（storage_decision 直接 tools=INGEST_TOOLS）
# ═══════════════════════════════════════════════════════════════════════════════


class TestToolExports:
    """tools 层唯一交付物是包装完成的 FunctionTool 实例（组合列表归 agent 层）。"""

    def test_instance_is_function_tool(self) -> None:
        assert isinstance(ingest_question_tool, FunctionTool)
        assert ingest_question_tool.name == "ingest_question"
        assert ingest_question_tool.func is ingest_tool.ingest_question

    def test_public_names(self) -> None:
        """只导出 tool 实例；包装函数与未来工具不进公共接口面。"""
        assert ingest_tool.__all__ == ["ingest_question_tool"]


class TestRetrieveToolExports:
    """读侧交付物：框架检索工具实例（挂到搜索信息子 Agent）。

    仅断言导出面与常量配置，不执行检索。knowledge_search_tool 为 PEP 562
    惰性导出——顶层 import 模块不实体化（CI 无 .env 也能 collect），实例必须在
    测试函数内经 retrieve_tool.knowledge_search_tool 取（此时 conftest autouse
    已把 get_embedding_model 换成 fake，实体化安全），严禁提到模块顶层。
    """

    def test_instance_is_knowledge_search_tool(self) -> None:
        tool = retrieve_tool.knowledge_search_tool
        assert isinstance(tool, LangchainKnowledgeSearchTool)
        assert tool.name == "knowledge_search"

    def test_lazy_export_is_singleton(self) -> None:
        """__getattr__ 每次经 _build_tool 返回同一缓存实例。"""
        assert retrieve_tool.knowledge_search_tool is retrieve_tool.knowledge_search_tool

    def test_public_names(self) -> None:
        """只导出 tool 实例；get_knowledge 绑定与内部机制不进公共接口面。"""
        assert retrieve_tool.__all__ == ["knowledge_search_tool"]

    def test_search_config(self) -> None:
        """MVP 基线：top-10 纯相似度，不配过滤（Agentic 版留待升级）。"""
        tool = retrieve_tool.knowledge_search_tool
        assert retrieve_tool.TOP_K == 10
        assert retrieve_tool.SEARCH_TYPE is SearchType.SIMILARITY
        assert tool.top_k == retrieve_tool.TOP_K
        assert tool.search_type is retrieve_tool.SEARCH_TYPE
        assert tool.knowledge_filter is None


# ═══════════════════════════════════════════════════════════════════════════════
# FunctionTool 元数据（name / description / schema）
# ═══════════════════════════════════════════════════════════════════════════════


class TestFunctionToolMetadata:
    """导出实例 ingest_question_tool 自动生成的声明符合约定。"""

    def test_tool_name(self) -> None:
        tool = ingest_question_tool
        assert tool.name == "ingest_question"

    def test_description_from_docstring(self) -> None:
        """description 取包装函数 docstring，LLM 靠它理解工具语义。"""
        tool = ingest_question_tool
        assert tool.description == tool.func.__doc__
        assert "三层存储" in tool.description
        assert "exam" in tool.description  # source_type 取值说明对 LLM 可见

    def test_declaration_schema(self) -> None:
        """schema 覆盖全部 12 个子集参数；question_text 为必填。

        默认 api_variant 下 genai Schema 不写 required 字段，必填性由
        get_mandatory_args（签名推导，FunctionTool 运行时校验同源）+ 无 default
        两个角度断言。
        """
        tool = ingest_question_tool
        decl = tool._get_declaration()
        assert decl is not None
        assert decl.name == "ingest_question"

        props = decl.parameters.properties
        assert set(props.keys()) == {
            "question_text", "answer_text", "analysis_text", "topic_names",
            "raw_file_path", "question_type", "source_type", "subject",
            "exam_year", "exam_month", "question_number", "exam_regions",
        }
        # question_text：必填（无 default）且类型为 STRING
        qt = props["question_text"].model_dump(exclude_none=True)
        assert qt == {"type": qt["type"]}  # 无 default/nullable → 必填
        assert get_mandatory_args(ingest_question_tool.func) == ["question_text"]

    def test_complex_facade_params_not_exposed(self) -> None:
        """门面剩余复杂参数（image_file_ids/vlm_descriptions）不进工具 schema——
        薄封装存在的意义就是收紧 LLM 参数面。exam_regions 已于 2026-08-28 打通
        来源链路时放开（扁平 str 列表，LLM 可稳定产出形状）。"""
        tool = ingest_question_tool
        props = tool._get_declaration().parameters.properties
        assert "image_file_ids" not in props
        assert "vlm_descriptions" not in props

    @pytest.mark.parametrize("param", ["topic_names", "exam_regions"])
    def test_list_params_schema_is_nullable_string_array(self, param: str) -> None:
        """list 参数在 typing.Optional 写法下能生成合法 schema（ARRAY + items STRING）。

        回归保护：`list[str] | None`（PEP 604 UnionType）会让 schema 生成器直接抛
        ValueError，故可空参数必须用 typing.Optional[...] 写法。
        """
        tool = ingest_question_tool
        tn = tool._get_declaration().parameters.properties[param]
        assert tn.type.value == "ARRAY"
        assert tn.nullable is True
        assert tn.items.type.value == "STRING"


# ═══════════════════════════════════════════════════════════════════════════════
# 调用转发（mock 门面）
# ═══════════════════════════════════════════════════════════════════════════════


class TestCallForwarding:
    """工具经 FunctionTool 执行时，参数透传门面、返回值原样给出。"""

    @pytest.mark.asyncio
    async def test_forwards_all_args_to_facade(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _FacadeRecorder()
        monkeypatch.setattr(ingest_tool, "_ingest_question", recorder)
        tool = ingest_question_tool

        result = await tool._run_async_impl(
            tool_context=_fake_tool_context(),
            args={
                "question_text": "已知函数 f(x) = x² - 2x，求最小值。",
                "answer_text": "-1",
                "analysis_text": "配方 f(x) = (x-1)² - 1。",
                "topic_names": ["二次函数", "配方法"],
                "raw_file_path": "data/files/raw/pdfs/exam.pdf",
                "question_type": "解答题",
                "source_type": "homework",
                "subject": "数学",
                "exam_year": 2026,
                "exam_month": 6,
                "question_number": "第15题",
                "exam_regions": ["深圳", "广东", "全国一卷"],
            },
        )

        assert result == {"question_id": 7, "doc_id": "q_7"}
        assert len(recorder.calls) == 1
        assert recorder.calls[0] == {
            "question_text": "已知函数 f(x) = x² - 2x，求最小值。",
            "answer_text": "-1",
            "analysis_text": "配方 f(x) = (x-1)² - 1。",
            "subject": "数学",
            "source_type": "homework",
            "question_type": "解答题",
            "raw_file_path": "data/files/raw/pdfs/exam.pdf",
            "exam_year": 2026,
            "exam_month": 6,
            "question_number": "第15题",
            "exam_regions": ["深圳", "广东", "全国一卷"],
            "topic_names": ["二次函数", "配方法"],
        }

    @pytest.mark.asyncio
    async def test_defaults_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLM 只传必填项时，默认值（source_type=exam / subject=数学 / 空串）照常透传。"""
        recorder = _FacadeRecorder()
        monkeypatch.setattr(ingest_tool, "_ingest_question", recorder)
        tool = ingest_question_tool

        result = await tool._run_async_impl(
            tool_context=_fake_tool_context(),
            args={"question_text": "一道题"},
        )

        assert result == {"question_id": 7, "doc_id": "q_7"}
        fwd = recorder.calls[0]
        assert fwd["question_text"] == "一道题"
        assert fwd["source_type"] == "exam"
        assert fwd["subject"] == "数学"
        assert fwd["answer_text"] == ""
        assert fwd["analysis_text"] == ""
        assert fwd["topic_names"] is None
        assert fwd["raw_file_path"] is None
        assert fwd["exam_regions"] is None
        # 门面是 keyword-only：转发不得出现位置参数（recorder 只收 kwargs，能跑通即证明）

    @pytest.mark.asyncio
    async def test_facade_exception_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """门面异常原样抛出（工具不吞异常），由上层转成 error 告知 Agent。"""

        def boom(**kwargs) -> None:
            raise RuntimeError("chroma down")

        monkeypatch.setattr(ingest_tool, "_ingest_question", boom)
        tool = ingest_question_tool

        with pytest.raises(RuntimeError, match="chroma down"):
            await tool._run_async_impl(
                tool_context=_fake_tool_context(),
                args={"question_text": "题干"},
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 必填参数校验（FunctionTool 内建，缺失返回 error 不触门面）
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingMandatoryArg:

    @pytest.mark.asyncio
    async def test_missing_question_text_returns_error_not_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """缺 question_text 时 FunctionTool 返回 error 提示 LLM 重试，门面零调用。"""
        recorder = _FacadeRecorder()
        monkeypatch.setattr(ingest_tool, "_ingest_question", recorder)
        tool = ingest_question_tool

        result = await tool._run_async_impl(
            tool_context=_fake_tool_context(),
            args={"answer_text": "有答案没题干"},
        )

        assert isinstance(result, dict)
        assert "error" in result
        assert "question_text" in result["error"]
        assert recorder.calls == []


# ═══════════════════════════════════════════════════════════════════════════════
# 分层铁律：agent/tools 不得 import src.store
# ═══════════════════════════════════════════════════════════════════════════════


class TestLayeringRule:
    """工具层只准走 src/ingestion（写）/ src/retrieval（读）门面（docs/agent/tools/README.md）。

    用 AST 解析真实 import 而非文本 grep：文件注释/文档里合法出现"严禁 import
    src.store"字样（说明铁律本身），grep 会误报。
    """

    @staticmethod
    def _imported_modules(path: Path) -> set[str]:
        """收集文件中全部 import / from-import 的模块名（含子模块全名）。"""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        return mods

    @pytest.mark.parametrize(
        "filename", ["__init__.py", "ingest_tool.py", "retrieve_tool.py"]
    )
    def test_no_store_import(self, filename: str) -> None:
        mods = self._imported_modules(Path(ingest_tool.__file__).parent / filename)
        violations = [m for m in mods if m == "src.store" or m.startswith("src.store.")]
        assert violations == []
