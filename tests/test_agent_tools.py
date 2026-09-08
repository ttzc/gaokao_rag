# tests/test_agent_tools.py
"""agent 工具层（src/agent/tools/）测试：导出面 + FunctionTool 元数据 + 调用转发 + 分层铁律。

被测主体是模块级工具实例（子 Agent 挂载的交付物）：写侧 ``ingest_question_tool``
+ ``update_question_tool`` + ``delete_question_tool``（FunctionTool，后两件挂题目
维护子 Agent）+ 读侧 ``knowledge_search_tool``（框架 LangchainKnowledgeSearchTool）
+ 读侧 ``get_question_detail_tool``（业务查询 FunctionTool），
非测试内自行包装的副本。全部 mock 门面（src.ingestion / src.retrieval），
不触真实存储 / 网络 / 计费 API。

工具函数经 `from src.ingestion.question import ingest_question as _ingest_question`
（update / delete 同款）绑定到工具模块，monkeypatch 必须打在
``src.agent.tools.ingest_tool._ingest_question`` / ``._update_question`` /
``._delete_question``（from-import 在 import 时把函数对象绑进本模块全局，
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
    delete_question_tool,
    ingest_question_tool,
    update_question_tool,
)
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
            "cascade": {"question_topics": 2, "errors": 0,
                        "exam_attempts": 0, "vector": True},
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
            "cascade": {"question_topics": 0, "errors": 0,
                        "exam_attempts": 0, "vector": False},
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
