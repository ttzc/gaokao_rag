# src/agent/ingestion/__init__.py
# 摄入侧子 Agent 包：把学生资料（文档 / 图片 / 单题）整理为「题目段 + 讲解段」再写库。
# 分工含文档识别 / 结构识别 / 知识整理 / 入库决策；当前实现结构识别子 Agent
# （structure_recognition）与入库决策子 Agent（storage_decision，纯写库执行者）。
