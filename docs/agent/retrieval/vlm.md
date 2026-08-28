# VLM 理解 Agent（src/agent/retrieval/vlm.py）

> 对应代码：`src/agent/retrieval/vlm.py`。查询侧子 Agent 之一，**只调 `src/retrieval` 读门面 + `src/agent/tools` 的 VLMUnderstandTool（见 [../tools/ingest_tool.md](../tools/ingest_tool.md)），严禁 `import src.store.*`**。

## 定位

查询侧的图形理解：检索命中的题目若带图，调用 VLM 生成图形描述，供答案生成使用。**有图才调用**——条件触发，不是每个查询都走。

## 触发条件

`retrieved_docs` 中存在 `has_image=True` 的文档时触发；否则跳过此成员。

## 执行逻辑

1. 遍历 `retrieved_docs`，解析 `doc_id` 两段式（如 `"q_42"`）
2. **Chroma metadata 不存 image_file_ids**（SQLite 权威，见 [vector/vector_store.md](../../store/vector/vector_store.md)「Metadata 格式与过滤语义」）——`entity == "q"` 时解析出 `questions.id`，回查 SQLite 拿图片
3. `kn_*` 是讲解 document，无图片，跳过
4. 对每张图调 `VLMUnderstandTool`（见 [../tools/ingest_tool.md](../tools/ingest_tool.md)），题目文本作为上下文
5. 描述写入 `GaokaoState.vlm_descriptions`

## 关键决策

- **描述入库，查询不重复调用**：VLM 在摄入时调用一次，描述存库；查询时直接复用存储的描述，不再花 token 二次识别（2026-08 决策）
- 查询侧 VLM 只处理"检索命中的题图"，摄入侧 VLM 处理"上传照片的题目内容"——同一工具，两种挂载
