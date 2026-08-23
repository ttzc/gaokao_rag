# ingest_question — 存储一道题

将一道题及其关联数据完整写入三层存储 + 知识点归位，是 ingestion 层最核心的工具。

```python
def ingest_question(
    raw_file_path: str,           # 源文件路径（files 表）
    question_text: str,           # 题干
    answer_text: str = "",        # 答案（可空）
    analysis_text: str = "",      # 解析（可空）
    subject: str = "math",        # 学科
    source_type: str = "exam",    # exam / homework / special_topic / reference
    question_type: str = "",      # 选择题/填空题/解答题
    difficulty: int = 0,          # 难度（可选）
    image_file_ids: list[int] = None,  # 关联图像 ID 列表
    topic_names: list[str] = None,     # 知识点名字列表（Agent 提取）
    vlm_descriptions: list[str] = None, # VLM 图形描述列表
    user_decision: str = "a",     # a=入库 / b=错题 / c=跳过
    error_reflection: str = "",   # 错题口述错因（user_decision=="b" 时提供）
) -> dict:
```

**内部自动完成（四层封装）**：

1. **文件层**：`save_processed` → 落盘处理后文本
2. **DB 层**：`insert_question` → `insert_question_topics`
3. **向量层**：`upsert_question_doc` → Chroma 向量化
4. **知识点层**：`search_topic` / `create_topic` → 知识点归位（复用 `store/knowledge.py`）

如果 `user_decision == "b"`：额外 `insert_error` → 写入错题。

**返回**：`{"question_id": int, "doc_id": str, "error_id": int | None}`

## 与 Agent 的协作

摄入侧 Agent 通过 FunctionTool 调用：

```python
class IngestQuestionTool(FunctionTool):
    name = "ingest_question"
    description = "将一道题写入三层存储（SQLite + Chroma）"
    
    async def execute(self, raw_file_path, question_text, answer_text="", 
                     analysis_text="", topic_names=None, user_decision="a", ...):
        return ingestion.question.ingest_question(
            raw_file_path=raw_file_path,
            question_text=question_text,
            ...
        )
```

Agent 只需要提供结构化数据，调用单个函数即可完成入库，不需要知道 `insert_question` → `insert_question_topics` → `upsert_question_doc` 三个步骤。
