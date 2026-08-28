# question — 题目检索门面

把「语义召回 + 结构化过滤 + SQLite 补全」组合成对学生友好的题目查询接口。本模块只读，不触碰任何 store 写方法。

## search_questions — 混合召回 + 过滤

```python
def search_questions(
    query: str,                                 # 自然语言检索词（如"椭圆离心率最值"）
    k: int = 8,                                 # 召回题目数
    where: KnowledgeFilterExpr | dict | None = None,  # 结构化过滤（年份/考区/题型/知识点）
) -> list[QuestionHit]:
```

**内部流程**：

1. `get_knowledge().search(query, k, filter_expr)` 返回框架 `SearchResult`（题目 + 讲解同 Collection，见 [knowledge.md](knowledge.md)）
2. 遍历 `result.documents`，仅保留 `doc.document.metadata["doc_type"] == "question"` 的命中
3. 用 `doc.document.metadata["doc_id"]` → `questions.id` 回查 `src.store.db.questions` 补全结构化字段（题号、题型、考区、年份、图）
4. 封装为 `QuestionHit`（业务语义对象，非裸 Row）

**返回**：`QuestionHit` 列表，每个含 `doc_id / question_id / content_text(摘要) / question_type / exam_regions / exam_year / question_number / has_image / score`。

## get_question_detail — 题目完整详情

```python
def get_question_detail(question_id: int) -> QuestionDetail:
```

**内部流程**：

1. `questions` 主表取题目内容（题干 + 答案 + 解析）
2. `question_topics` join `topics` 取关联知识点名列表
3. `image_file_ids` → `files` 取图片 `file_id` 列表（供上层拼 VLM 描述路径）

**返回**：`QuestionDetail`（题目 + 关联知识点 + 图片 file_id 列表），供输出整理 Agent 拼答案与溯源引用。

## browse_questions — 结构化浏览（无语义）

```python
def browse_questions(filters: dict) -> list[QuestionHit]:
```

**内部流程**：不走向量检索，直接 `src.store.db.questions` 按 `filters`（年份 / 考区 / 题型 / 知识点名）过滤，返回 `QuestionHit` 列表。用于"列出 2026 南昌一模所有解答题"这类明确筛选。

## 与 Agent 的协作

查询侧搜索信息 Agent（[agent/retrieval/search.md](../agent/retrieval/search.md)）调用上述函数；输出整理 Agent（[agent/retrieval/output.md](../agent/retrieval/output.md)）消费 `QuestionDetail` 拼溯源引用（哪份试卷第几题）。
