"""错题本写门面 src/ingestion/error.py 测试：ingest_error / update_error / delete_error。

覆盖：先查后写幂等（新建/更新双态）、空值规范化与"空值不覆盖"、resolved 复位、
待补行不写向量、error_summary 变化才重嵌（JSON 存 SQLite、中文分节文本进 Chroma）、
metadata 字段取舍（条件写入）、JSON 容错、跨存储删除顺序、幂等删除。

向量层不真调 API：conftest 已 patch FakeEmbeddings + 每测试前清空 Chroma，
用 get_vector_store().get(f"err_{eid}") 验证 document 写入/不写入。

依赖 conftest._reset_state，测试之间无顺序依赖。
"""

from __future__ import annotations

import json

import pytest

from src.ingestion.error import _summary_to_text, delete_error, ingest_error, update_error
from src.ingestion.question import ingest_question
from src.store.db.errors import get_errors_db
from src.store.db.questions import get_questions_db
from src.store.vector import get_vector_store
from src.store.vector.vector_store import VectorStore


# ── 常量与 Fixtures ─────────────────────────────────────────────────

SUMMARY_A = {
    "error_type": "知识盲区",
    "cause": "把离心率 e = c/a 与 b² = a² - c² 两个关系记混，误将 b/a 当作离心率",
    "knowledge_gap": "椭圆离心率定义与 a、b、c 的关系",
    "fix_suggestion": "复习「焦点三角形」模型，配套练习 3 道离心率计算题",
}

SUMMARY_B = {
    "error_type": "审题错误",
    "cause": "把 a>b>0 的条件看漏了",
}


@pytest.fixture()
def question_id() -> int:
    """预入库一道题（带知识点标注，供 metadata topic_tags），返回 question_id。"""
    r = ingest_question(
        question_text="已知椭圆 C: x²/a² + y²/b² = 1 (a>b>0)，求离心率。",
        question_type="解答题",
        subject="数学",
        topic_names=["椭圆", "离心率"],
    )
    return r["question_id"]


@pytest.fixture()
def upsert_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """记录 VectorStore.upsert 被调用的 doc_id 列表（透传原实现，不改变行为）。"""
    calls: list[str] = []
    original = VectorStore.upsert

    def spy(self, doc_id, text, metadata):  # noqa: ANN001
        calls.append(doc_id)
        return original(self, doc_id, text, metadata)

    monkeypatch.setattr(VectorStore, "upsert", spy)
    return calls


# ── _summary_to_text 纯函数 ─────────────────────────────────────────

class TestSummaryToText:

    def test_four_sections(self):
        """四键齐全 → 中文分节、顺序固定、以换行连接。"""
        assert _summary_to_text(SUMMARY_A) == (
            "错因类型：知识盲区\n"
            "错因：把离心率 e = c/a 与 b² = a² - c² 两个关系记混，误将 b/a 当作离心率\n"
            "知识点缺口：椭圆离心率定义与 a、b、c 的关系\n"
            "改进建议：复习「焦点三角形」模型，配套练习 3 道离心率计算题"
        )

    def test_partial_keys_skip_empty(self):
        """部分键为空串/缺失 → 只拼非空段、不留空行。"""
        assert _summary_to_text({"cause": "符号看漏", "fix_suggestion": "慢一点"}) == (
            "错因：符号看漏\n改进建议：慢一点"
        )
        assert _summary_to_text({"error_type": "", "cause": "X", "knowledge_gap": ""}) == "错因：X"
        assert "\n\n" not in _summary_to_text({"error_type": "", "cause": "X"})

    def test_none_and_empty_return_blank(self):
        assert _summary_to_text(None) == ""
        assert _summary_to_text({}) == ""
        assert _summary_to_text(
            {"error_type": "", "cause": "", "knowledge_gap": "", "fix_suggestion": ""}
        ) == ""

    def test_no_json_artifacts(self):
        """输出不含花括号/引号/英文键名——JSON 不进向量库。"""
        out = _summary_to_text(SUMMARY_A)
        assert "{" not in out and "}" not in out and '"' not in out
        assert "error_type" not in out and "cause" not in out


# ── ingest_error：新建路径 ──────────────────────────────────────────

class TestIngestErrorNew:

    def test_creates_row_and_document(self, question_id: int):
        """题目已入库 → created=True，DB 有行、向量有 err_{id} document。"""
        res = ingest_error(
            question_id=question_id,
            user_reflection="我当时把 b/a 当成离心率了",
            error_summary=SUMMARY_A,
        )
        assert res["created"] is True
        assert isinstance(res["error_id"], int) and res["error_id"] > 0

        row = get_errors_db().get_by_question_id(question_id)
        assert row["user_reflection"] == "我当时把 b/a 当成离心率了"
        assert json.loads(row["error_summary"]) == SUMMARY_A

        doc = get_vector_store().get(f"err_{res['error_id']}")
        assert doc is not None
        assert "错因类型：知识盲区" in doc["text"]

    def test_metadata_fields(self, question_id: int):
        """metadata 契约：doc_type/subject/question_id/first_seen + error_type/topic_tags。"""
        res = ingest_error(question_id=question_id, error_summary=SUMMARY_A)
        meta = get_vector_store().get(f"err_{res['error_id']}")["metadata"]
        assert meta["doc_type"] == "error"
        assert meta["subject"] == "数学"
        assert meta["question_id"] == question_id
        assert meta["first_seen"].startswith("202")  # SQLite datetime 原值透传
        assert meta["error_type"] == "知识盲区"
        assert set(meta["topic_tags"]) == {"椭圆", "离心率"}
        assert "resolved" not in meta  # 明确不存

    def test_conditional_metadata_skipped(self):
        """无 error_type、题目无知识点 → 条件字段不写入（Chroma 拒空串/空列表）。"""
        qid = ingest_question(question_text="计算 1+1", question_type="填空题")["question_id"]
        res = ingest_error(question_id=qid, error_summary={"cause": "看漏负号"})
        doc = get_vector_store().get(f"err_{res['error_id']}")
        assert "error_type" not in doc["metadata"]
        assert "topic_tags" not in doc["metadata"]
        assert doc["text"] == "错因：看漏负号"

    def test_pending_row_no_vector(self, question_id: int):
        """不传错因 → 行建成、两列 NULL、count_pending()==1、向量层无 document。"""
        res = ingest_error(question_id=question_id)
        row = get_errors_db().get_by_question_id(question_id)
        assert row["user_reflection"] is None
        assert row["error_summary"] is None
        assert get_errors_db().count_pending() == 1
        assert get_vector_store().get(f"err_{res['error_id']}") is None

    def test_empty_string_normalized_to_null(self, question_id: int):
        """传 "" → 规范化为 NULL（待补判定只认 IS NULL，"" 不得落库）。"""
        ingest_error(question_id=question_id, user_reflection="", error_summary={})
        assert get_errors_db().count_pending() == 1
        assert get_vector_store().count() == 1  # 只有题目 document，无错因 document

    def test_all_empty_summary_dict_normalized(self, question_id: int):
        """四键全空的 truthy dict（{"cause": ""}）→ 规范化 NULL：不落悬空脏行。"""
        ingest_error(question_id=question_id, error_summary={"error_type": "", "cause": ""})
        row = get_errors_db().get_by_question_id(question_id)
        assert row["error_summary"] is None
        assert get_errors_db().count_pending() == 1
        assert get_vector_store().get(f"err_{row['id']}") is None

    def test_nonexistent_question_raises_and_no_row(self):
        """题目不存在 → ValueError（先题后错），且 DB 未写入任何行。"""
        with pytest.raises(ValueError, match="先题后错"):
            ingest_error(question_id=999999, user_reflection="无题有错")
        assert get_errors_db().count() == 0


# ── ingest_error：幂等更新路径 ──────────────────────────────────────

class TestIngestErrorIdempotent:

    def test_second_ingest_updates_not_inserts(self, question_id: int):
        """同题二次 ingest → created=False、不新增行、新错因覆盖旧值、向量更新。"""
        r1 = ingest_error(
            question_id=question_id,
            user_reflection="旧口述",
            error_summary=SUMMARY_A,
        )
        r2 = ingest_error(
            question_id=question_id,
            user_reflection="这次是看漏条件",
            error_summary=SUMMARY_B,
        )
        assert r2["created"] is False
        assert r2["error_id"] == r1["error_id"]  # 同一行
        assert get_errors_db().count() == 1
        assert get_vector_store().count() == 2  # 题目 + 错因各一篇，没多

        row = get_errors_db().get_by_question_id(question_id)
        assert row["user_reflection"] == "这次是看漏条件"
        assert json.loads(row["error_summary"]) == SUMMARY_B

        doc = get_vector_store().get(f"err_{r2['error_id']}")
        assert "看漏" in doc["text"] and "离心率 e = c/a" not in doc["text"]

    def test_second_ingest_same_summary_skips_reembed(self, question_id: int, upsert_spy: list[str]):
        """再错一次但错因没变 → DB 照常更新（复位/刷时间），不白花一次 embedding。"""
        r1 = ingest_error(question_id=question_id, error_summary=SUMMARY_A)
        upsert_spy.clear()  # 只统计第二次 ingest 的向量写入

        r2 = ingest_error(question_id=question_id, error_summary=SUMMARY_A)
        assert r2["created"] is False
        assert upsert_spy == []
        # document 保持原样且可读
        doc = get_vector_store().get(f"err_{r1['error_id']}")
        assert "错因类型：知识盲区" in doc["text"]

    def test_second_ingest_resets_resolved(self, question_id: int):
        """再次错同一题自动复位 resolved（即使本次没带新错因）。"""
        ingest_error(question_id=question_id, error_summary=SUMMARY_A)
        update_error(question_id=question_id, resolved=True)
        assert get_errors_db().get_by_question_id(question_id)["resolved"] == 1

        res = ingest_error(question_id=question_id)  # 只说"又错了"
        assert res["created"] is False
        assert get_errors_db().get_by_question_id(question_id)["resolved"] == 0

    def test_empty_values_do_not_overwrite(self, question_id: int):
        """更新路径传空值 → 原有错因不被清空（"" 规范化为 None = 不动）。"""
        ingest_error(
            question_id=question_id,
            user_reflection="已记好的口述",
            error_summary=SUMMARY_A,
        )
        ingest_error(question_id=question_id, user_reflection="")

        row = get_errors_db().get_by_question_id(question_id)
        assert row["user_reflection"] == "已记好的口述"
        assert json.loads(row["error_summary"]) == SUMMARY_A


# ── update_error：补录 / 修正 / 标记掌握 ────────────────────────────

class TestUpdateError:

    def test_fill_pending_creates_document(self, question_id: int):
        """待补行补录 error_summary → 行更新、向量 document 此时才建。"""
        res = ingest_error(question_id=question_id)  # 待补
        eid = res["error_id"]
        assert get_vector_store().get(f"err_{eid}") is None

        out = update_error(question_id=question_id, error_summary=SUMMARY_A)
        assert out["error_id"] == eid
        assert out["updated_fields"] == ["error_summary"]
        assert json.loads(get_errors_db().get_by_question_id(question_id)["error_summary"]) == SUMMARY_A
        doc = get_vector_store().get(f"err_{eid}")
        assert doc is not None and "错因类型：知识盲区" in doc["text"]

    def test_resolved_only_does_not_reembed(self, question_id: int, upsert_spy: list[str]):
        """只改 resolved → 不重嵌（upsert 零调用）、document 内容不变。"""
        res = ingest_error(question_id=question_id, error_summary=SUMMARY_A)
        upsert_spy.clear()  # 只统计 update_error 阶段的向量写入

        out = update_error(question_id=question_id, resolved=True)
        assert out["updated_fields"] == ["resolved"]
        assert upsert_spy == []
        assert get_errors_db().get_by_question_id(question_id)["resolved"] == 1
        doc = get_vector_store().get(f"err_{res['error_id']}")
        assert "错因类型：知识盲区" in doc["text"]

    def test_summary_change_reembeds(self, question_id: int, upsert_spy: list[str]):
        """error_summary 实际变化 → 重嵌一次、document 文本更新。"""
        res = ingest_error(question_id=question_id, error_summary=SUMMARY_A)
        upsert_spy.clear()

        out = update_error(question_id=question_id, error_summary=SUMMARY_B)
        assert out["updated_fields"] == ["error_summary"]
        assert upsert_spy == [f"err_{res['error_id']}"]
        doc = get_vector_store().get(f"err_{res['error_id']}")
        assert "看漏" in doc["text"] and "知识盲区" not in doc["text"]

    def test_same_summary_skips_reembed(self, question_id: int, upsert_spy: list[str]):
        """传入与现值相同的 summary → 无变更、不重嵌（不空耗 embedding）。"""
        ingest_error(question_id=question_id, error_summary=SUMMARY_A)
        upsert_spy.clear()

        out = update_error(question_id=question_id, error_summary=SUMMARY_A)
        assert out["updated_fields"] == []
        assert upsert_spy == []

    def test_updated_fields_in_signature_order(self, question_id: int):
        """updated_fields 按签名字段序（user_reflection → error_summary → resolved）输出。"""
        ingest_error(question_id=question_id, user_reflection="旧", error_summary=SUMMARY_A)
        out = update_error(
            question_id=question_id,
            resolved=True,
            error_summary=SUMMARY_B,
            user_reflection="新",
        )
        assert out["updated_fields"] == ["user_reflection", "error_summary", "resolved"]

    def test_noop_keeps_last_seen(self, question_id: int):
        """全不传/全同值 → 不写 DB，last_seen 保持原样（不刷）。"""
        ingest_error(question_id=question_id, user_reflection="口述")
        before = get_errors_db().get_by_question_id(question_id)
        out = update_error(question_id=question_id)
        assert out["updated_fields"] == []
        after = get_errors_db().get_by_question_id(question_id)
        assert after["last_seen"] == before["last_seen"]

    def test_empty_string_reflection_is_noop(self, question_id: int):
        """必修回归：update_error 传 "" = 未提供，**不清空**已有口述（与 question 相反）。"""
        ingest_error(question_id=question_id, user_reflection="已记好的口述")
        out = update_error(question_id=question_id, user_reflection="")
        assert out["updated_fields"] == []
        assert get_errors_db().get_by_question_id(question_id)["user_reflection"] == "已记好的口述"

    def test_empty_dict_summary_is_noop(self, question_id: int):
        """update_error 传 {} / 四键全空 dict → 未提供，不清空、不重嵌。"""
        res = ingest_error(question_id=question_id, error_summary=SUMMARY_A)
        out = update_error(question_id=question_id, error_summary={"cause": ""})
        assert out["updated_fields"] == []
        assert json.loads(get_errors_db().get_by_question_id(question_id)["error_summary"]) == SUMMARY_A
        assert get_vector_store().get(f"err_{res['error_id']}")["text"].startswith("错因类型：")

    def test_missing_error_row_raises(self, question_id: int):
        """无错题记录（题目在、错题本没有）→ ValueError。"""
        with pytest.raises(ValueError, match="无错题记录"):
            update_error(question_id=question_id, resolved=True)
        assert get_errors_db().count() == 0

    def test_dirty_json_does_not_crash(self, question_id: int):
        """error_summary 落库被手改成非法 JSON → 比对时按 None 容错，不抛穿。"""
        res = ingest_error(question_id=question_id)
        get_errors_db().update(question_id, error_summary="{坏 JSON")  # 绕过门面直写脏数据
        out = update_error(question_id=question_id, error_summary=SUMMARY_B)
        assert out["updated_fields"] == ["error_summary"]
        assert json.loads(get_errors_db().get_by_question_id(question_id)["error_summary"]) == SUMMARY_B


# ── delete_error ────────────────────────────────────────────────────

class TestDeleteError:

    def test_delete_removes_row_and_document_keeps_question(self, question_id: int):
        """deleted=True + 向量 document 消失 + DB 行消失，questions 主行不动。"""
        res = ingest_error(question_id=question_id, error_summary=SUMMARY_A)
        out = delete_error(question_id)
        assert out == {"question_id": question_id, "deleted": True}
        assert get_errors_db().get_by_question_id(question_id) is None
        assert get_vector_store().get(f"err_{res['error_id']}") is None
        assert get_questions_db().get_by_id(question_id) is not None  # 只移出错题本
        assert get_vector_store().get(f"q_{question_id}") is not None

    def test_delete_twice_is_idempotent(self, question_id: int):
        """再删一次 → deleted=False，不抛异常。"""
        ingest_error(question_id=question_id, error_summary=SUMMARY_A)
        assert delete_error(question_id)["deleted"] is True
        assert delete_error(question_id) == {"question_id": question_id, "deleted": False}

    def test_delete_pending_row_without_document(self, question_id: int):
        """待补行本无 document——删除幂等跳过向量层，不报错。"""
        ingest_error(question_id=question_id)
        assert delete_error(question_id)["deleted"] is True
        assert get_errors_db().count() == 0


# ── error_summary JSON 往返 ─────────────────────────────────────────

class TestJsonRoundtrip:

    def test_dict_roundtrip_with_chinese(self, question_id: int):
        """dict 存入 → SQLite 为 ensure_ascii=False 的 JSON 串 → 读出还原同构 dict。"""
        ingest_error(question_id=question_id, error_summary=SUMMARY_A)
        raw = get_errors_db().get_by_question_id(question_id)["error_summary"]
        assert raw == json.dumps(SUMMARY_A, ensure_ascii=False)
        assert "知识盲区" in raw  # 中文未被转义成 \uXXXX
        assert json.loads(raw) == SUMMARY_A

    def test_document_holds_text_not_json(self, question_id: int):
        """两态各取所需：SQLite 存 JSON，Chroma 存中文分节文本。"""
        res = ingest_error(question_id=question_id, error_summary=SUMMARY_A)
        doc = get_vector_store().get(f"err_{res['error_id']}")
        assert "{" not in doc["text"] and "错因：" in doc["text"]
        raw = get_errors_db().get_by_question_id(question_id)["error_summary"]
        assert raw.startswith("{")
