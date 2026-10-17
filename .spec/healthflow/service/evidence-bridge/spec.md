---
title: evidence-mirror
desc: 已确认行到 genesis-evidence 的 HTTP 桥
hue: 80
code:
  - app/service/evidence_bridge.py
---
# evidence-bridge（已确认行 → genesis-evidence）

把**已确认**的 Health-Flow 行按 Evidence API 契约送到 genesis-evidence。

## 不变量

- 只有 `confirmed` 的行出域；未确认行不得进入匹配。
- 桥只做形状转换与传输，**不做匹配、不改判定**——匹配是 genesis-evidence 侧的职责。
- 上游不可达时如实报告为不可用，不静默丢弃、不伪造结果。
