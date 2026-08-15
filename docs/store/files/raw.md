# 源文件存储（Layer 1：文件系统）

> 对应 `src/store/file_store.py`。管理两类源文件：**原始 PDF**（试卷/专题/讲义）和**题目图片**（学生 QQ 上传照片 / 从 PDF 提取的图），以及处理后中间产物。文件元数据统一登记在 `files` 表（见 [store/db/files.md](db/files.md)）。

## 功能定位

三层存储的**最底层**——SQLite（元数据）和 Chroma（向量）都是对内容的索引，真正的原始字节存在文件系统。**`files` 表是文件系统与数据库之间的桥**：业务表只存 `file_id`，经 files 表拿磁盘路径和语义标题。

**原则：raw 只写一次、永不删除**——源数据不可再生（学生拍的错题照片删了就没了），SQLite/Chroma 随时可重建，raw 不能丢。

## 目录结构（config `[store]` 段）

**默认（相对路径）**：

```text
data/
├── files/                    # 文件层根目录（raw + processed）
│   ├── raw/                  # raw_dir —— 原始文件（只读源，不可变）
│   │   ├── pdfs/             #   原始 PDF（哈希命名）
│   │   │   └── <sha256>.pdf   #     完整 sha256 + 扩展名
│   │   └── images/           #   学生上传的图片（哈希命名）
│   │       ├── uploaded/     #     QQ 上传、作业拍照等（统一入口）
│   │       │   └── a3f2e1c9.jpg
│   │       └── extracted/    #     从 PDF 提取的插图
│   │           └── b7d4e0f2.png
│   └── processed/            # processed_dir —— 处理后中间产物（可重建，见 processed.md）
│       ├── text/             #   清洗后的文本
│       └── vlm_desc/         #   VLM 图形描述（中间缓存）
├── chroma_db/                # chroma_dir —— Chroma 持久化
└── gaokao.db                 # sqlite_path —— SQLite 索引
```

**数据导入/导出（绝对路径）**：

```toml
# config.toml
[store]
data_dir = "/mnt/external/gaokao_data"    # 绝对路径
```

此时目录结构位于外部存储，不占用项目空间：

```text
/mnt/external/gaokao_data/
├── files/
│   ├── raw/
│   └── processed/
├── chroma_db/
└── gaokao.db
```

切换 `data_dir` 即切换数据仓库，配合数据库备份/恢复实现数据导入导出。

## 命名与去重（核心设计）

### 1. 磁盘 = 哈希命名，原文件名直接丢弃

- **PDF 和图片统一哈希命名**：`{sha256}.{ext}`（完整 64 位，如 `data/files/raw/pdfs/<sha256>.pdf`，相对项目根）
- 原文件名**不保留**——用户上传的文件名大多是噪音（"新建文档.pdf" / "IMG_20260811.jpg"），无保留价值
- 好处：同内容同文件（`files.sha256` UNIQUE 索引天然去重）、同名不覆盖、防恶意文件名、物理层可自由重组

### 2. 语义标题 = title（agent 总结 / 用户自定义）

- `files.title` 存语义标题（"2026 南昌一模数学卷"），**摄入时 agent 从内容总结，用户可自定义**（摄入回显时确认/修改）
- title 挂在**文件**上而非题目上——一份试卷对应 20 道题，改一次标题全局生效
- 可空 = 待生成（用户确认前先落库，检索按原始内容兜底）

### 3. 路径安全（必须防穿越）

`resolve()` 是 file_store 的核心安全函数——**磁盘路径 → 绝对路径时校验必须落在 raw/ 内**：

```python
def resolve(disk_path: str) -> Path:
    p = (raw_dir / disk_path).resolve()
    if not p.is_relative_to(raw_dir.resolve()):
        raise ValueError(f"path escapes raw dir: {disk_path}")   # 防 ../ 穿越
    return p
```

学生上传的文件名、摄入提取的路径都是不可信输入，`resolve()` 是唯一放行口。

## 与 SQLite / Chroma 的关联

| 层 | 桥接 | 说明 |
| ---- | ----- | ---- |
| 文件 ↔ SQLite | `files` 表 | 磁盘哈希路径 → `file_id`，业务表只存 `file_id` |
| files ↔ 题目 | `questions.file_id` / `image_file_ids` | 所属试卷 + 题目图片（files.id 数组 JSON） |
| files ↔ 讲解 | `knowledge_notes.file_id` | 讲解所属的资料/试卷 |
| files ↔ 作答 | `exam_attempts.file_id` | 整卷作答关联的试卷 |
| SQLite ↔ Chroma | `doc_id` | 与文件无关，纯索引桥（见 questions.md） |

## 常见操作（file_store.py）

| 方法 | 用途 |
| ---- | ---- |
| `save_pdf(data)` | 落盘哈希命名 → 返回 file_id（同内容命中已有行，幂等） |
| `save_image(data)` | 同上（kind='image'） |
| `resolve(disk_path)` | 磁盘相对路径 → 绝对路径（**防穿越**） |
| `read_bytes(file_id)` | 读原始字节（VLM 输入、PDF 解析用） |
| `verify(file_id)` | sha256 完整性校验（raw 损坏检测） |

## 注意事项

1. **raw 不删**：`file_store` 不提供 `delete`（源数据不可再生）；确需清理走人工运维
2. **processed 可重建**：中间产物（清洗文本、VLM 描述缓存）丢失可从 raw 重跑管线
3. **图片格式统一**：摄入时把学生照片统一转 jpg（压缩到合理尺寸）再入库，控制存储与 VLM token 成本
4. **大 PDF 分页**：MinerU 兜底时按页处理，提取的插图落 `images/extracted/`
5. **git 忽略**：`data/` 整个目录进 `.gitignore`（大文件不入库）

## 与其他文档的关系

- 注册表：[db/files.md](../db/files.md)（`title` / `file_path` / `sha256` 字段语义）
- 表结构：[db/questions.md](../db/questions.md)（`file_id` / `image_file_ids` / `raw_text`）
- 中间产物：[processed.md](processed.md)（`data/files/processed/`，可重建）
- 配置：[config.toml `[store]` 段](../../../config.toml)（`data_dir`，子目录自动派生）
- 摄入管线：PDF/图片 → 本层落盘 → files 注册 → 下游提取（见 `docs/ingestion.md`）
