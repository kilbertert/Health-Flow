---
title: agent
desc: Chat 线的路由、专科 Agent 与 Self-Correction——冻结代码
hue: 260
related:
  - app/agent/dynamic_router.py
  - app/agent/recursive_feedback.py
  - app/agent/graph/medical_graph.py
  - app/agent/consistency_manager.py
  - app/agent/specialist_agents.py
  - app/agent/__init__.py
  - app/agent/graph/__init__.py

---
# agent

Chat 线的分诊路由、专科 Agent、GraphRAG 与自纠正。**这一整层是冻结代码**：
保留在仓库中，但不属于当前 API（见根节点 body 的 raw source）。

保留它而不是删除，是因为它是下一阶段的地基；把它当现状读则会误判系统在跑什么。

## 不变量（对它生效时）

- 路由**不直接做疾病诊断**；置信度过低、出现高风险词或跨科室不明确时设
  `human_review_required`。显式关键词计分先于 LLM 意图分布——常见问题因此是**确定性**路由的。
- GraphRAG 的 Milvus 结果标 `V-*`、Neo4j 结果标 `G-*`，加权 RRF 合并，
  来源编号与图谱路径保留进 Prompt。数据库不可用时**返回空证据**让上层继续，
  **不伪造检索结果**。
- Self-Correction 是**有界递归**：超最大轮数则保守提示并停止；
  **语义相似度指标不能替代医学事实校验**。
