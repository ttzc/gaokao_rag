# ingest_image — 存储一张图

将用户上传或 PDF 提取的图像写入文件层 + DB 注册表。

```python
def ingest_image(
    content: bytes,               # 图像原始字节
    subdir: str = "uploaded",     # uploaded（用户拍照）/ extracted（PDF 提取）
    kind: str = "image",          # 固定 "image"
) -> dict:
```

**内部自动完成**：

1. **文件层**：`save_raw` → sha256 哈希命名落盘
2. **DB 层**：`insert_file` → 注册 files 表（`kind='image'`）

**返回**：`{"file_id": int, "file_path": str}`

## 图像来源

| 来源 | subdir | 说明 |
|------|--------|------|
| 用户拍照/QQ 上传 | `uploaded` | 即时摄入场景 |
| PDF 提取插图 | `extracted` | 批量摄取时 PyMuPDF 抽取 |

图像与题目通过 `image_file_ids` 关联（`ingest_question` 参数），VLM 描述存储在 `vlm_descriptions` 列表中，不单独建表。
