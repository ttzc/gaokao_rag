# ingest_exam_paper — 存储一份试卷

将原始试卷 PDF 写入文件层 + DB 注册表，供后续切分摄入使用。

```python
def ingest_exam_paper(
    content: bytes,               # PDF 原始字节
    title: str,                   # 试卷标题（如 "2026 南昌一模"）
    source_type: str = "exam",    # 固定 "exam"
) -> dict:
```

**内部自动完成**：

1. **文件层**：`save_raw` → sha256 哈希命名落盘
2. **DB 层**：`insert_file` → 注册 files 表（`title` + `sha256` + `file_path`）

**返回**：`{"file_id": int, "file_path": str}`

## 整卷作答摄入

试卷切分入库后，学生可能做完整张卷子并报告作答情况。不识别手写成绩单（同错题原则），改为用户口述 + LLM 解析：

```
用户口述："选择错2个填空错1个，导数大题没写出来，总分68"
     ↓
LLM 解析 → 逐题对错 + 总分 + 整卷分析
     ↓
写入 exam_attempts 表
```

`question_results` 用 `question_id` 关联已入库的题目，周报可据此聚合"哪些题型失分最多"。
