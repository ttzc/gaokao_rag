# tests/test_agent_tools.py
"""agent 工具层（src/agent/tools/）测试：导出面 + FunctionTool 元数据 + 调用转发 + 分层铁律。

被测主体是模块级工具实例（子 Agent 挂载的交付物）：写侧 ``ingest_question_tool``
+ ``update_question_tool`` + ``delete_question_tool``（FunctionTool，后两件挂题目
维护子 Agent）+ 错题本写侧三件 ``ingest_error_tool`` / ``update_error_tool`` /
``delete_error_tool``（2026-09-21，挂错题管理子 Agent，规划中）
+ 读侧 ``knowledge_search_tool``（框架 LangchainKnowledgeSearchTool）
+ 读侧 ``get_question_detail_tool``（业务查询 FunctionTool）
+ 错题本读侧两件 ``get_error_stats_tool`` / ``get_error_details_tool``
（包装 src.retrieval.error 门面，dataclass 经 asdict 转 dict），
非测试内自行包装的副本。全部 mock 门面（src.ingestion / src.retrieval），
不触真实存储 / 网络 / 计费 API。

工具函数经 `from src.ingestion.question import ingest_question as _ingest_question`
（update / delete 及错题三件 `_ingest_error` / `_update_error` / `_delete_error`、
读侧 `_get_error_stats` / `_get_error_details` 同款）绑定到工具模块，monkeypatch
必须打在 ``src.agent.tools.ingest_tool._ingest_question`` 等**工具模块全局**
（from-import 在 import 时把函数对象绑进本模块全局，
只 patch 源模块不会重绑——同 tests/conftest.py 嵌入层 patch 三处的教训）。

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
from src.agent.tools.ingest_tool import (
    delete_error_tool,
    delete_question_tool,
    ingest_error_tool,
    ingest_question_tool,
    update_error_tool,
    update_question_tool,
)
from src.retrieval.error import ErrorDetail, ErrorStats
from src.retrieval.question import QuestionDetail


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

    def test_maintain_instances_are_function_tools(self) -> None:
        """改 / 删两件（题目维护子 Agent 挂载）同为模块级 FunctionTool 实例。"""
        assert isinstance(update_question_tool, FunctionTool)
        assert update_question_tool.name == "update_question"
        assert update_question_tool.func is ingest_tool.update_question
        assert isinstance(delete_question_tool, FunctionTool)
        assert delete_question_tool.name == "delete_question"
        assert delete_question_tool.func is ingest_tool.delete_question

    def test_public_names(self) -> None:
        """只导出 tool 实例；包装函数与未来工具不进公共接口面。"""
        assert ingest_tool.__all__ == [
            "ingest_question_tool", "update_question_tool", "delete_question_tool",
            "ingest_error_tool", "update_error_tool", "delete_error_tool",
        ]


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
        assert retrieve_tool.__all__ == [
            "knowledge_search_tool", "get_question_detail_tool",
            "get_error_stats_tool", "get_error_details_tool",
        ]

    def test_question_detail_instance(self) -> None:
        """get_question_detail_tool 是模块级 FunctionTool（构造零副作用，非惰性导出），
        LLM 可见工具名取函数 __name__。"""
        tool = retrieve_tool.get_question_detail_tool
        assert isinstance(tool, FunctionTool)
        assert tool.name == "get_question_detail"
        assert tool.func is retrieve_tool.get_question_detail

    def test_question_detail_declaration(self) -> None:
        """schema 只暴露 question_id（INTEGER、必填）；description 取包装函数 docstring。"""
        tool = retrieve_tool.get_question_detail_tool
        decl = tool._get_declaration()
        props = decl.parameters.properties
        assert set(props.keys()) == {"question_id"}
        assert props["question_id"].type.value == "INTEGER"
        assert get_mandatory_args(tool.func) == ["question_id"]
        assert "完整详情" in tool.description
        assert "doc_id" in tool.description  # question_id 来源说明（q_42 → 42）对 LLM 可见

    def test_search_config(self) -> None:
        """MVP 基线：top-10 纯相似度全量召回，不配过滤（Agentic 版留待升级）。

        SEARCH_TYPE 用 SIMILARITY_SCORE_THRESHOLD（2026-08-29 排查）：框架
        SIMILARITY 分支走 asearch 不带 score，SearchDocument.score 恒 0.0；
        该分支召回集合/排序与 SIMILARITY 相同，仅 score 有值。
        MIN_SCORE=-1.0 防 langchain l2 relevance 负分被工具层静默过滤。
        """
        tool = retrieve_tool.knowledge_search_tool
        assert retrieve_tool.TOP_K == 10
        assert retrieve_tool.SEARCH_TYPE is SearchType.SIMILARITY_SCORE_THRESHOLD
        assert retrieve_tool.MIN_SCORE == -1.0
        assert tool.top_k == retrieve_tool.TOP_K
        assert tool.search_type is retrieve_tool.SEARCH_TYPE
        assert tool.min_score == retrieve_tool.MIN_SCORE
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


class TestMaintainToolMetadata:
    """改 / 删两件工具（题目维护子 Agent 挂载）的声明符合约定。"""

    def test_update_declaration_schema(self) -> None:
        """update_question：question_id + 9 个可变字段全 Optional；必填仅 question_id。

        image_file_ids 不进工具 schema——图片摄入管线未落地、图形改动本版不支持，
        薄封装收紧 LLM 参数面（同 ingest_question 不暴露 vlm_descriptions 的思路）。
        """
        tool = update_question_tool
        decl = tool._get_declaration()
        assert decl is not None
        assert decl.name == "update_question"
        props = decl.parameters.properties
        assert set(props.keys()) == {
            "question_id", "content_text", "answer_text", "analysis_text",
            "question_number", "question_type", "exam_regions", "exam_year",
            "exam_month", "topic_names",
        }
        assert "image_file_ids" not in props
        qid = props["question_id"].model_dump(exclude_none=True)
        assert qid == {"type": qid["type"]}  # 无 default/nullable → 必填
        assert get_mandatory_args(tool.func) == ["question_id"]

    def test_update_description_semantics(self) -> None:
        """description（docstring）把三态语义 / 不可变字段 / 图形降级写清，LLM 可见。"""
        desc = update_question_tool.description
        assert desc == update_question_tool.func.__doc__
        for marker in ("部分更新", "全量替换", "不可变字段", "清空",
                       "图形", "暂不支持", "updated_fields"):
            assert marker in desc

    @pytest.mark.parametrize("param", ["topic_names", "exam_regions"])
    def test_update_list_params_nullable(self, param: str) -> None:
        """可空 list 参数同样走 typing.Optional 写法，schema 生成不炸（回归保护）。"""
        prop = update_question_tool._get_declaration().parameters.properties[param]
        assert prop.type.value == "ARRAY"
        assert prop.nullable is True
        assert prop.items.type.value == "STRING"

    def test_delete_declaration_schema(self) -> None:
        """delete_question：schema 只有 question_id（INTEGER、必填）。"""
        tool = delete_question_tool
        decl = tool._get_declaration()
        assert decl.name == "delete_question"
        props = decl.parameters.properties
        assert set(props.keys()) == {"question_id"}
        assert props["question_id"].type.value == "INTEGER"
        assert get_mandatory_args(tool.func) == ["question_id"]

    def test_delete_description_confirmation_gate(self) -> None:
        """不可逆 + 确认前置（user_confirmed）必须写进 docstring——防 LLM 擅自删。"""
        desc = delete_question_tool.description
        assert desc == delete_question_tool.func.__doc__
        for marker in ("不可逆", "仅当用户已明确确认删除", "user_confirmed",
                       "级联", "幂等", "cascade"):
            assert marker in desc


class TestErrorWriteToolExports:
    """错题本写侧三件：模块级 FunctionTool 实例 + 工具名逐字一致。

    ``_get_declaration()`` 调用本身就是校验——可空参数误写成 `X | None`
    （PEP 604 UnionType）会在此抛 ValueError，schema 生成器只认 typing.Optional。
    """

    def test_instances_are_function_tools(self) -> None:
        assert isinstance(ingest_error_tool, FunctionTool)
        assert ingest_error_tool.name == "ingest_error"
        assert ingest_error_tool.func is ingest_tool.ingest_error
        assert isinstance(update_error_tool, FunctionTool)
        assert update_error_tool.name == "update_error"
        assert update_error_tool.func is ingest_tool.update_error
        assert isinstance(delete_error_tool, FunctionTool)
        assert delete_error_tool.name == "delete_error"
        assert delete_error_tool.func is ingest_tool.delete_error

    def test_declarations_build(self) -> None:
        """三件 schema 全部生成不抛（抓 Optional 写法回归 + 声明名逐字）。"""
        for tool in (ingest_error_tool, update_error_tool, delete_error_tool):
            decl = tool._get_declaration()
            assert decl.name == tool.name


class TestErrorWriteToolMetadata:
    """错题本写侧三件的参数 schema 与 docstring 语义关键词（LLM 选工具/传参依据）。"""

    def test_ingest_declaration_schema(self) -> None:
        """ingest_error：question_id 必填；error_summary 为 Optional[dict] → OBJECT+nullable。"""
        props = ingest_error_tool._get_declaration().parameters.properties
        assert set(props.keys()) == {"question_id", "user_reflection", "error_summary"}
        assert props["question_id"].type.value == "INTEGER"
        assert get_mandatory_args(ingest_error_tool.func) == ["question_id"]
        es = props["error_summary"]
        assert es.type.value == "OBJECT"
        assert es.nullable is True
        assert props["user_reflection"].type.value == "STRING"
        assert props["user_reflection"].nullable is True

    def test_ingest_description_semantics(self) -> None:
        """幂等 / 允许空错因 / 复位+覆盖 / 空值不覆盖 / 四键键名必须全部 LLM 可见。"""
        desc = ingest_error_tool.description
        assert desc == ingest_error_tool.func.__doc__
        for marker in ("幂等", "不必先查", "空错因", "复位", "覆盖", "不会被清空",
                       "error_type", "knowledge_gap", "fix_suggestion", "created"):
            assert marker in desc

    def test_update_declaration_schema(self) -> None:
        """update_error：question_id + 3 个可选字段；resolved BOOLEAN+nullable（三态）。"""
        props = update_error_tool._get_declaration().parameters.properties
        assert set(props.keys()) == {
            "question_id", "user_reflection", "error_summary", "resolved",
        }
        assert get_mandatory_args(update_error_tool.func) == ["question_id"]
        r = props["resolved"]
        assert r.type.value == "BOOLEAN"
        assert r.nullable is True

    def test_update_description_semantics(self) -> None:
        """补录/修正/掌握三用途 + 部分更新 + 与 ingest_error 的区别写进 docstring。"""
        desc = update_error_tool.description
        assert desc == update_error_tool.func.__doc__
        for marker in ("补录", "掌握", "部分更新", "不自动碰", "ingest_error",
                       "updated_fields"):
            assert marker in desc

    def test_delete_declaration_schema(self) -> None:
        """delete_error：schema 只有 question_id（INTEGER、必填）。"""
        props = delete_error_tool._get_declaration().parameters.properties
        assert set(props.keys()) == {"question_id"}
        assert props["question_id"].type.value == "INTEGER"
        assert get_mandatory_args(delete_error_tool.func) == ["question_id"]

    def test_delete_description_gates(self) -> None:
        """移出≠删题 + 不可逆 + 确认前置（回显确认）+ 幂等，全部 LLM 可见。"""
        desc = delete_error_tool.description
        assert desc == delete_error_tool.func.__doc__
        for marker in ("移出错题本", "≠ 删题目", "delete_question", "不可逆",
                       "回显确认", "幂等", "deleted"):
            assert marker in desc


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
# update / delete 调用转发（mock 门面）
# ═══════════════════════════════════════════════════════════════════════════════


class _MaintainRecorder:
    """记录改 / 删门面入参的替身：返回构造时给定的固定结果。"""

    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return self.result


class TestUpdateCallForwarding:
    """update_question 工具经 FunctionTool 执行时，参数透传门面、返回值原样给出。"""

    @pytest.mark.asyncio
    async def test_forwards_all_args_to_facade(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _MaintainRecorder(
            {"question_id": 7, "doc_id": "q_7", "updated_fields": ["answer_text"]}
        )
        monkeypatch.setattr(ingest_tool, "_update_question", recorder)

        result = await update_question_tool._run_async_impl(
            tool_context=_fake_tool_context(),
            args={
                "question_id": 7,
                "content_text": "已知函数 f(x) = x² - 2x，求最小值。",
                "answer_text": "B",
                "analysis_text": "配方 f(x) = (x-1)² - 1。",
                "question_number": "第15题",
                "question_type": "单选题",
                "exam_regions": ["南昌", "江西"],
                "exam_year": 2026,
                "exam_month": 3,
                "topic_names": ["二次函数", "配方法"],
            },
        )

        assert result == {"question_id": 7, "doc_id": "q_7",
                          "updated_fields": ["answer_text"]}
        assert len(recorder.calls) == 1
        assert recorder.calls[0] == {
            "question_id": 7,
            "content_text": "已知函数 f(x) = x² - 2x，求最小值。",
            "answer_text": "B",
            "analysis_text": "配方 f(x) = (x-1)² - 1。",
            "question_number": "第15题",
            "question_type": "单选题",
            "exam_regions": ["南昌", "江西"],
            "exam_year": 2026,
            "exam_month": 3,
            "topic_names": ["二次函数", "配方法"],
        }

    @pytest.mark.asyncio
    async def test_defaults_forwarded_as_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLM 只传 question_id（部分更新）时，其余字段全部以 None 透传 = 不修改。"""
        recorder = _MaintainRecorder(
            {"question_id": 7, "doc_id": "q_7", "updated_fields": []}
        )
        monkeypatch.setattr(ingest_tool, "_update_question", recorder)

        result = await update_question_tool._run_async_impl(
            tool_context=_fake_tool_context(), args={"question_id": 7},
        )

        assert result["updated_fields"] == []
        fwd = recorder.calls[0]
        assert fwd["question_id"] == 7
        for field in ("content_text", "answer_text", "analysis_text",
                      "question_number", "question_type", "exam_regions",
                      "exam_year", "exam_month", "topic_names"):
            assert fwd[field] is None, field
        # 门面是 keyword-only：转发不得出现位置参数（recorder 只收 kwargs，能跑通即证明）

    @pytest.mark.asyncio
    async def test_facade_exception_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """question_id 不存在时门面 ValueError 原样抛出，由框架转 error 告知 Agent。"""

        def boom(**kwargs) -> None:
            raise ValueError("question_id=999 不存在，无法修改")

        monkeypatch.setattr(ingest_tool, "_update_question", boom)

        with pytest.raises(ValueError, match="不存在"):
            await update_question_tool._run_async_impl(
                tool_context=_fake_tool_context(), args={"question_id": 999},
            )


class TestDeleteCallForwarding:
    """delete_question 工具：question_id 透传、cascade 返回体原样给出、幂等不抛。"""

    @pytest.mark.asyncio
    async def test_forwards_id_and_returns_cascade(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _MaintainRecorder({
            "question_id": 7, "doc_id": "q_7", "deleted": True,
            "blocked_by": None,
            "cascade": {"question_topics": 2, "vector": True},
        })
        monkeypatch.setattr(ingest_tool, "_delete_question", recorder)

        result = await delete_question_tool._run_async_impl(
            tool_context=_fake_tool_context(), args={"question_id": 7},
        )

        assert result["deleted"] is True
        assert result["cascade"]["question_topics"] == 2
        assert recorder.calls == [{"question_id": 7}]

    @pytest.mark.asyncio
    async def test_idempotent_result_passthrough(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """门面幂等（不存在 → deleted=False 不抛）——工具原样透传，不加工不重试。"""
        recorder = _MaintainRecorder({
            "question_id": 999, "doc_id": "q_999", "deleted": False,
            "blocked_by": None,
            "cascade": {"question_topics": 0, "vector": False},
        })
        monkeypatch.setattr(ingest_tool, "_delete_question", recorder)

        result = await delete_question_tool._run_async_impl(
            tool_context=_fake_tool_context(), args={"question_id": 999},
        )

        assert result["deleted"] is False
        assert result["cascade"]["vector"] is False

    @pytest.mark.asyncio
    async def test_facade_exception_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """存储层异常原样抛出（工具不吞异常），由框架转 error 告知 Agent。"""

        def boom(**kwargs) -> None:
            raise RuntimeError("chroma down")

        monkeypatch.setattr(ingest_tool, "_delete_question", boom)

        with pytest.raises(RuntimeError, match="chroma down"):
            await delete_question_tool._run_async_impl(
                tool_context=_fake_tool_context(), args={"question_id": 7},
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 错题本写侧调用转发（mock src.ingestion.error 门面）
# ═══════════════════════════════════════════════════════════════════════════════


class TestIngestErrorCallForwarding:
    """ingest_error 工具：参数 kwargs 透传门面、{error_id, created} 原样返回、异常不吞。"""

    @pytest.mark.asyncio
    async def test_forwards_all_args_with_dict_intact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        summary = {
            "error_type": "知识盲区", "cause": "记混 e=c/a 与 b²=a²-c²",
            "knowledge_gap": "椭圆离心率定义", "fix_suggestion": "复习焦点三角形模型",
        }
        recorder = _MaintainRecorder({"error_id": 3, "created": True})
        monkeypatch.setattr(ingest_tool, "_ingest_error", recorder)

        result = await ingest_error_tool._run_async_impl(
            tool_context=_fake_tool_context(),
            args={
                "question_id": 42,
                "user_reflection": "把 b/a 当成离心率了",
                "error_summary": summary,
            },
        )

        assert result == {"error_id": 3, "created": True}
        assert len(recorder.calls) == 1
        fwd = recorder.calls[0]
        assert fwd["question_id"] == 42
        assert fwd["user_reflection"] == "把 b/a 当成离心率了"
        # error_summary 四键 dict 原样透传：不字符串化、不丢键、不加键
        assert fwd["error_summary"] == summary
        assert set(fwd.keys()) == {"question_id", "user_reflection", "error_summary"}

    @pytest.mark.asyncio
    async def test_empty_reflection_forwards_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """允许空错因：只传 question_id 时两个错因参数以 None 透传（先建行、错因后补）。"""
        recorder = _MaintainRecorder({"error_id": 4, "created": True})
        monkeypatch.setattr(ingest_tool, "_ingest_error", recorder)

        result = await ingest_error_tool._run_async_impl(
            tool_context=_fake_tool_context(), args={"question_id": 42},
        )

        assert "error_id" in result and "created" in result
        assert recorder.calls[0] == {
            "question_id": 42, "user_reflection": None, "error_summary": None,
        }

    @pytest.mark.asyncio
    async def test_facade_exception_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """先题后错：门面 ValueError 原样抛出，由框架转 error 告知 Agent。"""

        def boom(**kwargs) -> None:
            raise ValueError("question_id=999 不存在，请先入库题目（先题后错）")

        monkeypatch.setattr(ingest_tool, "_ingest_error", boom)

        with pytest.raises(ValueError, match="先题后错"):
            await ingest_error_tool._run_async_impl(
                tool_context=_fake_tool_context(), args={"question_id": 999},
            )


class TestUpdateErrorCallForwarding:
    """update_error 工具：部分更新原样透传——resolved 的 None（不动）与 False（取消掌握）必须区分。"""

    @pytest.mark.asyncio
    async def test_resolved_false_not_collapsed_to_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _MaintainRecorder({"error_id": 3, "updated_fields": ["resolved"]})
        monkeypatch.setattr(ingest_tool, "_update_error", recorder)

        result = await update_error_tool._run_async_impl(
            tool_context=_fake_tool_context(),
            args={"question_id": 42, "resolved": False},
        )

        assert result == {"error_id": 3, "updated_fields": ["resolved"]}
        fwd = recorder.calls[0]
        assert fwd["resolved"] is False  # 显式 False 不被 or/默认值逻辑折叠成 None
        assert fwd["user_reflection"] is None
        assert fwd["error_summary"] is None

    @pytest.mark.asyncio
    async def test_summary_and_resolved_true_passthrough(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        summary = {"cause": "符号看漏了"}
        recorder = _MaintainRecorder(
            {"error_id": 3, "updated_fields": ["error_summary", "resolved"]}
        )
        monkeypatch.setattr(ingest_tool, "_update_error", recorder)

        result = await update_error_tool._run_async_impl(
            tool_context=_fake_tool_context(),
            args={"question_id": 42, "error_summary": summary, "resolved": True},
        )

        assert result["updated_fields"] == ["error_summary", "resolved"]
        fwd = recorder.calls[0]
        assert fwd["error_summary"] == summary
        assert fwd["resolved"] is True
        assert fwd["user_reflection"] is None  # 未传字段保持 None = 不动

    @pytest.mark.asyncio
    async def test_all_defaults_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """只传 question_id → 三个可选字段全部 None 透传；空 updated_fields 原样返回。"""
        recorder = _MaintainRecorder({"error_id": 3, "updated_fields": []})
        monkeypatch.setattr(ingest_tool, "_update_error", recorder)

        result = await update_error_tool._run_async_impl(
            tool_context=_fake_tool_context(), args={"question_id": 42},
        )

        assert result["updated_fields"] == []  # 无变更是合法结果，不是错误
        assert recorder.calls[0] == {
            "question_id": 42, "user_reflection": None,
            "error_summary": None, "resolved": None,
        }

    @pytest.mark.asyncio
    async def test_facade_exception_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """该题无错题记录 → 门面 ValueError 原样抛出（提示改用 ingest_error，归上层判断）。"""

        def boom(**kwargs) -> None:
            raise ValueError("question_id=999 无错题记录，无法更新")

        monkeypatch.setattr(ingest_tool, "_update_error", boom)

        with pytest.raises(ValueError, match="无错题记录"):
            await update_error_tool._run_async_impl(
                tool_context=_fake_tool_context(), args={"question_id": 999},
            )


class TestDeleteErrorCallForwarding:
    """delete_error 工具：question_id 透传、幂等 deleted=False 原样给出、缺参不触门面。"""

    @pytest.mark.asyncio
    async def test_forwards_id_and_returns_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _MaintainRecorder({"question_id": 7, "deleted": True})
        monkeypatch.setattr(ingest_tool, "_delete_error", recorder)

        result = await delete_error_tool._run_async_impl(
            tool_context=_fake_tool_context(), args={"question_id": 7},
        )

        assert result == {"question_id": 7, "deleted": True}
        assert recorder.calls == [{"question_id": 7}]

    @pytest.mark.asyncio
    async def test_idempotent_false_passthrough(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无错题记录 → deleted=False 不抛异常，工具原样透传（不加工不重试）。"""
        recorder = _MaintainRecorder({"question_id": 999, "deleted": False})
        monkeypatch.setattr(ingest_tool, "_delete_error", recorder)

        result = await delete_error_tool._run_async_impl(
            tool_context=_fake_tool_context(), args={"question_id": 999},
        )

        assert result["deleted"] is False

    @pytest.mark.asyncio
    async def test_missing_question_id_returns_error_not_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """缺 question_id → FunctionTool error 提示补参，门面零调用（不可逆操作防误删）。"""
        recorder = _MaintainRecorder({"question_id": 0, "deleted": True})
        monkeypatch.setattr(ingest_tool, "_delete_error", recorder)

        result = await delete_error_tool._run_async_impl(
            tool_context=_fake_tool_context(), args={},
        )

        assert isinstance(result, dict)
        assert "error" in result
        assert recorder.calls == []


# ═══════════════════════════════════════════════════════════════════════════════
# get_question_detail 调用转发（mock 读门面）
# ═══════════════════════════════════════════════════════════════════════════════


def _fake_detail(question_id: int = 7) -> QuestionDetail:
    """构造一个完整的 QuestionDetail 替身（不触库）。"""
    return QuestionDetail(
        question_id=question_id,
        doc_id=f"q_{question_id}",
        subject="数学",
        source_type="exam",
        question_type="解答题",
        content_text="已知函数 f(x) = x² - 2x，求最小值。",
        file_id=1,
        question_number="第15题",
        exam_regions=["南昌", "江西"],
        exam_year=2026,
        exam_month=3,
        answer_text="-1",
        analysis_text="配方 f(x) = (x-1)² - 1。",
        topic_names=["二次函数"],
        image_file_ids=[3],
    )


class TestQuestionDetailCall:
    """工具执行时 question_id 透传门面、QuestionDetail 经 asdict 转 dict、异常不吞。"""

    @pytest.mark.asyncio
    async def test_forwards_id_and_returns_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[int] = []

        def fake_facade(question_id: int) -> QuestionDetail:
            seen.append(question_id)
            return _fake_detail(question_id)

        monkeypatch.setattr(retrieve_tool, "_get_question_detail", fake_facade)
        tool = retrieve_tool.get_question_detail_tool

        result = await tool._run_async_impl(
            tool_context=_fake_tool_context(), args={"question_id": 7},
        )

        assert seen == [7]
        assert isinstance(result, dict)  # dataclass 不直接给 LLM，asdict 转平铺 dict
        assert result["question_id"] == 7
        assert result["doc_id"] == "q_7"
        assert result["answer_text"] == "-1"
        assert result["topic_names"] == ["二次函数"]
        assert result["image_file_ids"] == [3]

    @pytest.mark.asyncio
    async def test_facade_exception_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """question_id 不存在时门面 ValueError 原样抛出，由框架转成 error 告知 Agent。"""

        def boom(question_id: int) -> None:
            raise ValueError(f"question_id={question_id} 不存在，无法取详情")

        monkeypatch.setattr(retrieve_tool, "_get_question_detail", boom)
        tool = retrieve_tool.get_question_detail_tool

        with pytest.raises(ValueError, match="不存在"):
            await tool._run_async_impl(
                tool_context=_fake_tool_context(), args={"question_id": 999},
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 错题本读侧两件（get_error_stats / get_error_details）：导出 + schema + asdict 转发
# ═══════════════════════════════════════════════════════════════════════════════


def _fake_stats() -> ErrorStats:
    """构造一个完整的 ErrorStats 替身（不触库）。"""
    return ErrorStats(total=10, resolved=4, resolve_rate=0.4, pending_count=2)


def _fake_error_detail(question_id: int = 42) -> ErrorDetail:
    """构造一条已填错因的 ErrorDetail 替身（不触库）。"""
    return ErrorDetail(
        error_id=3,
        question_id=question_id,
        user_reflection="把 b/a 当成离心率了",
        error_summary={
            "error_type": "知识盲区", "cause": "记混公式",
            "knowledge_gap": "椭圆离心率定义", "fix_suggestion": "复习焦点三角形模型",
        },
        pending=False,
        resolved=False,
        first_seen="2026-09-01 20:00:00",
        last_seen="2026-09-10 21:30:00",
    )


class TestErrorReadToolExports:
    """错题本读侧两件：模块级 FunctionTool 实例 + 工具名逐字 + schema 生成不抛。"""

    def test_instances_and_names(self) -> None:
        stats = retrieve_tool.get_error_stats_tool
        details = retrieve_tool.get_error_details_tool
        assert isinstance(stats, FunctionTool)
        assert stats.name == "get_error_stats"
        assert stats.func is retrieve_tool.get_error_stats
        assert isinstance(details, FunctionTool)
        assert details.name == "get_error_details"
        assert details.func is retrieve_tool.get_error_details

    def test_declarations_build(self) -> None:
        """构造 + _get_declaration() 不抛（抓 Optional 写法回归）。"""
        assert retrieve_tool.get_error_stats_tool._get_declaration().name == "get_error_stats"
        assert retrieve_tool.get_error_details_tool._get_declaration().name == "get_error_details"

    def test_declaration_schemas(self) -> None:
        """stats 零参数（实测 parameters 整体为 None）；details 只暴露 question_id 必填。"""
        stats_decl = retrieve_tool.get_error_stats_tool._get_declaration()
        assert not (stats_decl.parameters.properties if stats_decl.parameters else None)
        props = retrieve_tool.get_error_details_tool._get_declaration().parameters.properties
        assert set(props.keys()) == {"question_id"}
        assert props["question_id"].type.value == "INTEGER"
        assert get_mandatory_args(retrieve_tool.get_error_details) == ["question_id"]

    def test_description_semantics(self) -> None:
        """统计四字段 / 明细可空与空列表语义写进 docstring，LLM 可见。"""
        stats_desc = retrieve_tool.get_error_stats_tool.description
        for marker in ("不是语义检索", "knowledge_search", "掌握率", "错因待补",
                       "total", "resolve_rate", "pending_count"):
            assert marker in stats_desc
        details_desc = retrieve_tool.get_error_details_tool.description
        for marker in ("为什么错", "一题一行", "error_summary", "user_reflection",
                       "pending", "resolved", "空列表"):
            assert marker in details_desc


class TestErrorStatsCall:
    """ErrorStats 经 asdict 转平铺 dict（dataclass 不直接给 LLM）。"""

    @pytest.mark.asyncio
    async def test_returns_dict_not_dataclass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_facade() -> ErrorStats:
            return _fake_stats()

        monkeypatch.setattr(retrieve_tool, "_get_error_stats", fake_facade)

        result = await retrieve_tool.get_error_stats_tool._run_async_impl(
            tool_context=_fake_tool_context(), args={},
        )

        assert not isinstance(result, ErrorStats)
        assert isinstance(result, dict)
        assert result == {
            "total": 10, "resolved": 4, "resolve_rate": 0.4, "pending_count": 2,
        }

    @pytest.mark.asyncio
    async def test_empty_book_all_zero_passthrough(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """空错题本全 0 是合法返回，原样透传（不抛不加工）。"""
        monkeypatch.setattr(
            retrieve_tool, "_get_error_stats",
            lambda: ErrorStats(total=0, resolved=0, resolve_rate=0.0, pending_count=0),
        )

        result = await retrieve_tool.get_error_stats_tool._run_async_impl(
            tool_context=_fake_tool_context(), args={},
        )

        assert result == {
            "total": 0, "resolved": 0, "resolve_rate": 0.0, "pending_count": 0,
        }


class TestErrorDetailsCall:
    """question_id 透传门面，list[ErrorDetail] 逐条 asdict 转 list[dict]。"""

    @pytest.mark.asyncio
    async def test_forwards_id_and_returns_list_of_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[int] = []

        def fake_facade(question_id: int) -> list[ErrorDetail]:
            seen.append(question_id)
            return [_fake_error_detail(question_id)]

        monkeypatch.setattr(retrieve_tool, "_get_error_details", fake_facade)

        result = await retrieve_tool.get_error_details_tool._run_async_impl(
            tool_context=_fake_tool_context(), args={"question_id": 42},
        )

        assert seen == [42]
        assert isinstance(result, list) and len(result) == 1
        row = result[0]
        assert isinstance(row, dict) and not isinstance(row, ErrorDetail)
        assert row["error_id"] == 3
        assert row["question_id"] == 42
        assert row["pending"] is False
        assert row["resolved"] is False
        assert row["error_summary"]["knowledge_gap"] == "椭圆离心率定义"
        assert row["user_reflection"] == "把 b/a 当成离心率了"
        assert row["first_seen"] == "2026-09-01 20:00:00"
        assert row["last_seen"] == "2026-09-10 21:30:00"

    @pytest.mark.asyncio
    async def test_empty_result_is_empty_list_not_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无记录（含题不存在）→ 工具函数透传 []——不是错误、不抛。

        空值折叠是框架行为非工具语义：`_run_async_impl` 内
        `res = await self.func(**args) or {}`（_function_tool.py）会把 falsy 的
        [] 折成 {} 再给 LLM——故工具函数本体直接断言 []；FunctionTool 路径
        一并断言 {} 钉住现状（框架若修正折叠，此断言会红，届时改回 [] 即可）。
        """
        monkeypatch.setattr(retrieve_tool, "_get_error_details", lambda question_id: [])

        assert await retrieve_tool.get_error_details(999) == []

        folded = await retrieve_tool.get_error_details_tool._run_async_impl(
            tool_context=_fake_tool_context(), args={"question_id": 999},
        )
        assert folded == {}


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

    @pytest.mark.asyncio
    async def test_missing_question_id_returns_error_not_call_update(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """update 工具缺 question_id → error 提示补参，门面零调用。"""
        recorder = _MaintainRecorder({"question_id": 0, "doc_id": "", "updated_fields": []})
        monkeypatch.setattr(ingest_tool, "_update_question", recorder)

        result = await update_question_tool._run_async_impl(
            tool_context=_fake_tool_context(), args={"answer_text": "B"},
        )

        assert isinstance(result, dict)
        assert "error" in result
        assert "question_id" in result["error"]
        assert recorder.calls == []

    @pytest.mark.asyncio
    async def test_missing_question_id_returns_error_not_call_delete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """delete 工具缺 question_id → error 提示补参，门面零调用（防误删）。"""
        recorder = _MaintainRecorder({"deleted": True})
        monkeypatch.setattr(ingest_tool, "_delete_question", recorder)

        result = await delete_question_tool._run_async_impl(
            tool_context=_fake_tool_context(), args={},
        )

        assert isinstance(result, dict)
        assert "error" in result
        assert "question_id" in result["error"]
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
