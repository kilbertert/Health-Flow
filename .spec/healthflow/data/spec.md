---
title: data
desc: 持久化与外部服务客户端
hue: 40
code:
  - app/data/models.py
related:
  - app/data/mysql_client.py
  - app/config.py
  - app/data/milvus_client.py
  - app/data/neo4j_client.py
  - app/data/__init__.py
  - app/model/llm.py
  - app/model/embedding.py
  - app/model/__init__.py
  - app/migrate_evidence_payloads.py
  - app/__init__.py

---
# data

持久化与外部服务客户端。开发默认 SQLite，生产按 `ARCHITECTURE.md` 第 7 节接 MySQL/PostgreSQL
与 Milvus/Neo4j。

## 不变量

- 所有外部服务通过**环境变量**配置。
- 阶段边界上线前创建的报告标记 `legacy_unclaimed` 并封存：数据保留给运维侧执行保留或删除策略，
  但患者 API **不允许认领或读取**。这是所有权边界的既成事实，不是待修的缺陷。
- `.env.example` 只含占位值；**禁止提交**真实 Key、数据库密码与患者文件。
