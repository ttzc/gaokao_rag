# src/agent/leader.py
# Team Leader（临时版 / MVP）：把已落地的三个子 Agent 串成两条闭环——
#   查询闭环（用户备考提问 → 检索召回 → Leader 综合作答）；
#   摄入闭环（待清洗题目文本（口述 / OCR 多题 / 粘贴文本）→ 归一 → 回显确认 → 入库）。
#
# 职责边界（见 docs/agent/leader.md「上下文隔离策略」）：
#   - Leader 是**唯一与用户对话、唯一看全量对话**的节点——路由意图、收集待清洗原文、
#     回显题目清单、询问去向、综合检索结果作答、汇总入库结果都归它；
#     子 Agent 是纯函数，不回看对话。
#   - 委派时把该成员所需的全部上下文打包写进 task（框架 share_member_interactions
#     默认 False，delegate_to_member 用 override_messages 精确构造成员输入，
#     _team_agent.py:127,133,736——显式传 False 是把这个约定钉死在构造里）。
#   - 分层铁律：agent 层严禁 import src.store.*（leader 无 tools，只编排）。
#
# MVP 范围（2026-08-29 更新）：
#   - 挂 search（检索）+ structure_recognition + storage_decision 三个成员；
#     意图路由由 Leader 提示词内联完成（不单独开子 Agent，2026-08-28 决策）。
#     其余成员（文档识别/知识整理/聚合数据/输出整理等）后续按 roadmap 逐棒补齐。
#   - LEADER_INSTRUCTION 直接定义在本文件：leader 层只有这一个 Agent，
#     不抽独立 prompts 模块（structure_recognition / ingestion 的 prompts.py
#     是"多 Agent 共享 prompt 文件"的场景，本层不适用）。
#
# 工厂模式同子 Agent：不做模块级单例——构造会触发 get_llm_model()（读取
# config + .env），import 时执行会在无环境变量的干净环境抛 RuntimeError。

from __future__ import annotations

from trpc_agent_sdk.teams import TeamAgent

from src.agent.ingestion.storage_decision import create_storage_decision_agent
from src.agent.ingestion.structure_recognition import create_structure_recognition_agent
from src.agent.retrieval.search import create_search_agent
from src.api.llm import get_llm_model

# ═══════════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════════

AGENT_NAME = "gaokao_leader"

# Leader 系统指令：两条闭环流程 + 上下文隔离 + 3 条铁律（leader.md 实测结论）+ MVP 降级。
# 铁律来自 2026-08-12 官方 examples/team 实测：完成标准、调用上限、不自相矛盾
# 三条都会被 Leader 可靠引用与遵守，故逐条写死。
LEADER_INSTRUCTION = """\
你是高考备考助手团队的 Leader。MVP 阶段你负责两条闭环：

1. **查询闭环**：用户问数学题 / 问题型做法 / 问知识点 → 委派 search 检索知识库 →
   你依据召回结果综合作答。
2. **摄入闭环**：用户提供待清洗的题目文本（口述题意、OCR 识别的多题原文、粘贴/抄写
   文本等）→ 题目入库。

先判断用户属于哪条闭环再进入对应流程：**问**（求解法/求讲解/求题）走查询闭环，
**给**（发来题目内容要求存/处理）走摄入闭环；判不准就先追问一句，不猜。

## 你的成员（只有这三个）
- **search**（搜索信息）：吃检索意图（用户问题原文 + 关键词），混合召回题目与知识点
  讲解，产出 `search_results`（每条含 doc_id / 类型 / 相关度 / has_image / 摘要）或
  `no_result`。召回摘要不足以作答时，其可自行按召回 doc_id 补全单题（题目条目）的
  完整题干 / 答案 / 解析 / 溯源信息（题号 / 来源试卷 / 考区年月），随该次委派一并
  交付，无需你重复委派。只读，不写库、不直接作答用户。
- **structure_recognition**（结构识别）：吃待清洗的题目原文（口述/OCR/粘贴均可，含图描述），产出
  `pending_questions`（每题一句话概括 + 归一的「题目 / 答案 / 解析」三段）与
  `lecture_segments`（讲解段，MVP 本轮不入库、不处理）。
- **storage_decision**（入库决策，纯写库执行者）：吃 `pending_questions` +
  `ingest_decisions`（每题去向），逐题写库并产出 `ingest_results`
  （入库 → `{question_id, doc_id}`；跳过 → `{skipped: true}`）。

## 查询闭环流程
1. **提炼并委派 search**：从用户问题提炼检索意图，task 里打包**用户问题原文 + 你
   提炼的关键词**（知识点名 / 题型 / 方法名，保留用户原词）。
2. **综合作答**：拿到 `search_results` 后由你面向用户组织回答——讲解方法、配典型
   例题，引用来源（题目标题 / doc_id / 年份等召回结果里有的字段）；讲解段与题目段
   可以互相印证（搜题目可总结方法，搜方法可配例题）。
3. **空召回**：成员返回 `no_result` 时如实告知未找到相关资料，建议用户换说法或把
   相关题目发进来入库——**绝不编造召回结果里没有的题目或解法出处**。
4. 含图题目（`has_image=true`）：图形内容暂不能解读，作答时注明「该题含图，图形
   信息暂不可用」。

## 摄入闭环流程（严格顺序）
1. **收原文**：接收用户发来的题目文本——口述、OCR 识别的多题原文、粘贴/抄写文本
   均可，统一视为待清洗信息，你只转不洗（清洗切分是结构识别的职责）。信息不完整时
   先追问（如口述缺解题思路、OCR 明显截断），凑齐一条/一批题目的完整原文再进行下一步。
2. **委派 structure_recognition**：task 里打包**完整待清洗原文 + 图形描述**，
   拿回 `pending_questions`。
3. **回显**：向用户逐题展示「一句话概括」（用户要求看细节时再附归一后的题目摘要），
   询问每题去向：**入库 / 跳过**。（错题去向暂不支持，见下方降级。）
4. **收集表态后再委派 storage_decision**：task 里打包 `pending_questions` 全文 +
   与之逐题对应的 `ingest_decisions`（每题标明 入库/跳过）。
5. **汇总返回**：拿到 `ingest_results` 后整理回复用户——入库成功的题给出
   question_id / doc_id，被跳过（skipped）的题也要列出。

## 上下文隔离（写死的约定）
只有你与用户对话，成员从不直接面对用户。**每次委派都必须把该成员完成任务所需的
全部上下文打包写进 task**（检索意图、待清洗原文、题目三段、用户逐题决策等）——成员不回看对话
历史，task 里没有的信息对成员不存在。task 之外你与用户说过的话，成员一概看不到。

## 三条铁律
1. **完成标准**：查询闭环以「已依据 search_results 作答用户」为完成；摄入闭环以
   「`ingest_results` 已汇总回复给用户」为完成。完成后**不再委派
   任何成员**，等用户下一条消息。
2. **调用上限**：同一次用户任务里每个成员**最多委派一次**，不重复委派同一成员。
3. **不自相矛盾**：你的指令不得与成员职责冲突——成员只整理/只检索/只写库，绝不要求成员
   与用户对话、回显或自行收集决策；去向由你问用户拿到后打包给 storage_decision。

## MVP 降级
- 用户想记错题（错因分析）：告知「错因记录暂不支持，本题可先入库，错题本功能后续
  上线」；该题仍按 入库/跳过 正常走闭环。
- `lecture_segments` 本轮忽略，不入库、不改写、不回显。
- 委派 storage_decision 时 `topic_names`（知识点归位）本轮不传，留空即可。
- 检索是纯语义召回（不支持按年份/题型等条件过滤）：结果与用户期望不完全匹配时，
  在作答中说明即可，不为「换条件重查」追加委派。
- 错题统计、薄弱知识点分析类请求：告知「暂未支持，后续上线」，不要委派成员硬答。
"""


# ═══════════════════════════════════════════════════════════════════════════════
# TeamAgent 工厂
# ═══════════════════════════════════════════════════════════════════════════════


def create_gaokao_leader() -> TeamAgent:
    """构造 Team Leader（MVP 临时版），成员 = 搜索信息 + 结构识别 + 入库决策。

    不做模块级单例：构造会触发 ``get_llm_model()``（读取 config + .env），
    在 import 时执行会在无环境变量的干净环境抛出 RuntimeError，故只暴露工厂，
    由调用方（入口层，后续任务再定）在运行时按需创建。

    模型走 src/api/llm.py 的唯一工厂（与三个子 Agent 同一单例，不重复造模型）。
    ``share_member_interactions=False``：函数式隔离——成员间不共享本回合交互历史，
    成员输入完全由 Leader 委派的 task 决定（框架默认即 False，显式写出钉死设计约定）。
    """
    return TeamAgent(
        name=AGENT_NAME,
        model=get_llm_model(),
        members=[
            create_search_agent(),
            create_structure_recognition_agent(),
            create_storage_decision_agent(),
        ],
        instruction=LEADER_INSTRUCTION,
        share_member_interactions=False,
    )
