# 错题本写门面（`src/ingestion/error.py`）

错题本（`errors` 表）的写入门面：记错因 / 改错因 / 移出错题本。**先题后错是硬约束**——所有函数都要求 `question_id` 已存在。

> **调用方**：错题管理 Agent（`src/agent/ingestion/error_maintain.py`）经 [ingest_tool.py](../agent/tools/ingest_tool.md) 的工具调用。
> **本层无 LLM 决策**：`error_summary` 由结构识别 Agent 过 `error-organize` Skill 产出后传入（见 [skills/error-organize.md](../agent/skills/error-organize.md)）。

## ingest_error — 记错因

```python
def ingest_error(
    *,
    question_id: int,
    user_reflection: str = "",
    error_summary: dict | None = None,
) -> dict:
```

**内部流程**：**先查后写**（`errors` 上有 `UNIQUE INDEX idx_errors_question`，一题一行）——该题不在错题本则插入、已存在则更新该行。全项目无 UPSERT 先例（`grep "ON CONFLICT" src/store/db` 零命中），故沿用「先查后写」而非 SQL 的 `ON CONFLICT DO UPDATE`；`error_summary` 以 JSON 字符串落库。

**幂等语义**：调用方（错题管理 Agent）**无需先判断该题是否已在错题本**——直接调本函数即可，重复调用是更新而非报错（与 `topics.create` 冲突抛 `ValueError` 的语义不同：那是"重名是错误"，这里"同一题又错一次/又补一句"是常态）。

**空值不覆盖已有值**（新建 / 更新的双态语义，实现时勿混）：**新建**时空值照写（得到「错因待补」行）；**更新**时传入的空值（`""` / `None`）**不覆盖**原有内容——否则用户只说一句「这题我又错了」，就会把已经记好的错因清空。

**再次错同一题会把 `resolved` 重置为 0**（2026-09-17 定）：走更新路径时，非空的新错因说明还没掌握——"已掌握"标记同步复位，`last_seen` 刷新。此时若用户提供了新错因，按**覆盖**语义写入（多次错因的演化不用系统层追加字段，由 `error-organize` 在 `cause` 的自然语言里描述）。

**允许空错因入库**（2026-09-13 定）：`user_reflection` 与 `error_summary` 均为空时，写入的是一条「**错因待补**」记录——错题本先建行、错因后补，不阻塞摄入流程。

**返回**：`{"error_id": int, "created": bool}`（`created=false` 表示更新了已有记录）

## update_error — 改错因 / 补录 / 标记掌握

```python
def update_error(
    *,
    question_id: int,
    user_reflection: str | None = None,
    error_summary: dict | None = None,
    resolved: bool | None = None,
) -> dict:
```

**部分更新语义**（与 `update_question` 一致）：不传 / `None` = 不修改该字段。

`resolved`（是否已掌握）是 `errors` 行的字段，**标记掌握本质上也是修改**（2026-09-13 用户明确），归本函数——是否再拆出独立的 `resolve_error` 属函数粒度问题，随落地时定。

**返回**：`{"error_id": int, "updated_fields": list[str]}`

## delete_error — 移出错题本

```python
def delete_error(question_id: int) -> dict:
```

删 `errors` 行，**不动 `questions` 主行**——与 `delete_question` 是两件事：一个是「这道题我不想再在错题本里看到」，一个是「这道题从题库删掉」。不可逆 → 调用前须经 Leader 回显确认。

**返回**：`{"question_id": int, "deleted": bool}`

## 错因来源与「待补」状态

| 来源 | 说明 |
|------|------|
| 用户口述（入库时交代） | 结构识别 Agent 过 `error-organize` 整理成四键 JSON 后传入 |
| 用户口述（事后补录） | 同上；经 `update_error` 写入先前留空的行（隔天补录是主路径） |
| 整卷作答 | ⏳ **MVP 内但后置**：`exam_attempts` 中逐题对错后自动生成 error 记录——随**整卷作答功能**落地时一并实现（先错题本、后作答，见 [roadmap](../roadmap.md)） |

**「错因待补」的判定**：`user_reflection IS NULL AND error_summary IS NULL`——不新增状态字段。周报 / 薄弱点分析对这类记录只计数量，不参与错因分析。

**设计原则**：不存手写解题过程（VLM 识别手写 CER 15-20% 不可靠），只存用户口述 + LLM 结构化总结。

**依赖方向**：所有函数都接收「已入库的 `question_id`」——**错题本体系依赖题目摄入体系，而非相反**。题目摄入与错题记录是两个独立原子门面，由 Leader 按顺序委派两个 Agent 串联（先题后错），互不耦合。
