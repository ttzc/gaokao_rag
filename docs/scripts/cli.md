# 题目只读 CLI（scripts/cli.py）

> 对应代码：`scripts/cli.py`。**包装 [retrieval/question](../retrieval/question.md) 读门面的两个纯数据库读取函数**——`browse_questions`（结构化浏览）与 `get_question_detail`（完整详情）。只读、无 LLM、无向量检索。

## 定位

| 入口 | 用途 | 是否经 LLM |
|------|------|:---:|
| `scripts/cli.py` | 开发调试 / 外部 Agent：结构化浏览 + 题目详情（SQLite 直读） | ❌ |
| `scripts/chat.py` | 开发调试：命令行模拟 QQ 与 Team Leader 对话 | ✅ |
| QQ（trpc-claw） | 正式学生入口 | ✅ |

CLI 是门面函数的薄封装：**只做参数解析 + 输出格式化，不重复任何组合逻辑**（过滤、join、校验全在门面层，见 [retrieval/question.md](../retrieval/question.md)）。上层严禁绕过门面直连 store——本脚本同样只 import `src.retrieval.question`。

`search_questions`（语义召回）**暂不包装**：涉及嵌入模型与 .env API Key，待门面落地后再加子命令。

## 前置条件

1. **依赖已安装**（`uv sync`）
2. **`data/gaokao.db` 有数据**：首次使用前需先经摄入管线入库题目（`scripts/ingest.py` 落地前可用 `src.ingestion.question.ingest_question` 或 pytest 夹具灌数）
3. **不需要 API Key**：纯 SQLite 读取，`.env` 缺 `*_API_KEY` 也能跑（config.toml 只有 `${VAR}` 解析兜底）

## 运行

```bash
uv run python scripts/cli.py <browse|detail> [选项]
```

### browse — 结构化浏览（无语义）

所有选项可选，**相互 AND**；不给任何选项 = 列出全部题目（按 id 升序）。

| 选项 | 对应 filter 键 | 说明 |
|------|--------------|------|
| `--subject` | `subject` | 学科，如 `数学` |
| `--source-type` | `source_type` | `exam` / `homework` / `special_topic` / `reference` / `error_book`（error_book 预留） |
| `--year` | `exam_year` | 年份，如 `2026` |
| `--month` | `exam_month` | 月份 1-12 |
| `--type` | `question_type` | 题型，如 `解答题` |
| `--region` | `exam_region` | 考区**单值**，对层级列表做包含匹配（`南昌` 命中 `["南昌","江西","全国一卷"]`） |
| `--topic` | `topic_name` | 知识点规范名（经 `question_topics` 反查题目） |
| `--file-id` | `file_id` | 来源试卷/作业的 `files.id`（列出整卷题目） |
| `--limit` | `limit` | 返回条数上限 |
| `--json` | — | 输出 `QuestionHit` 列表的 JSON |

```bash
# 文档示例场景："列出 2026 南昌一模所有解答题"
uv run python scripts/cli.py browse --year 2026 --region 南昌 --type 解答题

# 某知识点相关题目，最多 5 条，机器可读
uv run python scripts/cli.py browse --topic 椭圆 --limit 5 --json
```

人类可读输出（每题两行：元信息 + 题干摘要）：

```
共 1 题：
  1. id=1  q_1  2026-03  第21题  解答题  [南昌/江西/全国一卷]  🖼
     已知椭圆 C 的离心率为 1/2，…（摘要，超长截断）
```

### detail — 题目完整详情

```bash
uv run python scripts/cli.py detail 42          # 人类可读（溯源头 + 题干/答案/解析分块）
uv run python scripts/cli.py detail 42 --json   # QuestionDetail JSON
```

```
id=42  q_42  数学/exam  解答题  2026-03  第21题
考区: 南昌 / 江西 / 全国一卷
来源: file_id=1
知识点: 椭圆、离心率
图片: file_id=10、file_id=11
── 题干 ──────────────────────────────
（全文，不截断）
── 答案 ──────────────────────────────
……
── 解析 ──────────────────────────────
……
```

## 输出与退出码

| 情形 | 输出 | 退出码 |
|------|------|:---:|
| 正常（含 0 条结果） | stdout：人类可读 / `--json` 纯 JSON | 0 |
| `detail` id 不存在 / 门面 `ValueError` | stderr：`[错误] …`，**stdout 保持干净**（JSON 管道安全） | 1 |
| argparse 参数错误（缺子命令、类型不符） | argparse 标准用法提示 | 2 |

`--json` 输出为 `dataclasses.asdict` 序列化（`ensure_ascii=False`），字段契约见 [retrieval/question.md](../retrieval/question.md) 的 `QuestionHit` / `QuestionDetail`。

## 技术实现要点

```python
filters = {"exam_year": args.year, ..., "limit": args.limit}   # 丢弃 None
hits = browse_questions({k: v for k, v in mapping.items() if v is not None})
```

- **CLI 薄壳原则**：不写 SQL、不 import `src.store`，所有读取组合在门面层
- **UTF-8 兜底**：stdout / stderr 均 `reconfigure(encoding="utf-8")`，Git Bash / Windows GBK 终端中文不乱码
- **退出码约定**：0/1/2 三级，方便脚本与外部 Agent 判断成败

## MVP 边界

- 无分页（`--limit` 截断）、无排序选项（恒按 id 升序）
- `--json` 输出单行紧凑 JSON，不提供 pretty-print 开关
- 错题统计、复习报告等业务查询随对应门面函数落地再增子命令

## 测试约定

`scripts/` 下一律不进 pytest（与 chat.py 同约定）：CLI 只是薄壳（参数解析 + 输出格式化），验证靠手动调用；组合逻辑（过滤 / join / 校验）已由门面单测 `tests/test_retrieval_question.py` 覆盖。

## 相关

- 门面函数与返回对象定义：[docs/retrieval/question.md](../retrieval/question.md)
- 对话式入口（经 Leader / LLM）：[docs/scripts/chat.md](chat.md)
