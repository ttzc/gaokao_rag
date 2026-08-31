# src/agent/retrieval/__init__.py
# 查询侧子 Agent 包：读数据产生回答，分工含搜索信息 / VLM 理解 / 聚合数据 / 输出整理；
# 当前实现搜索信息子 Agent（search，挂 knowledge_search 语义检索 +
# get_question_detail 详情查询两个工具）。
#
# 分层铁律（docs/agent/README.md）：本包只允许 import src/retrieval 读门面
# （经 src/agent/tools 工具层注入），严禁 import src.store.*。
