---
title: HealthFlow
desc: 报告解读与健康风险提示——原文可追溯，不独立诊断
hue: 45
related:
  - README.md
  - ARCHITECTURE.md
  - GLOSSARY.md
  - acceptance.feature
  - qa-plan.md
---
# HealthFlow

医疗辅助系统：把一份体检报告变成患者能读懂、能追溯原文、且不越界成诊断的健康提示。

## raw source

**本系统不承担独立诊断、处方或具体剂量建议。** 这条不是免责声明，是设计约束：
它决定了输出面能说什么（[[safety-guard]]）、一条结论需要什么证据才能出域。

**当前运行时只保留**报告上传、解析、用户确认、原文追溯和已发布知识卡匹配。
Chat、知识图谱、指标趋势与训练模块**保留在仓库中作为冻结代码，不属于当前 API**——
它们是历史与将来，不是现状。读这一层时不要把它们当作在跑的东西。

## expanded spec

分四层，与 `ARCHITECTURE.md` 的分节一致：

- **解析**（[[vision-encoder]]）：文件 → 带坐标与原文的指标记录。
- **判定**（[[admission]]、[[report-material]]、[[interpretation-failure]]）：
  一行指标能不能进解读、材料是什么类型、一次失败是什么成因。
- **出域**（[[schema]]、[[safety-guard]]）：对外契约的形状，以及不许说出口的东西。
- **边界**（[[report-api]]、[[mall-goods]]、[[tickets]]）：HTTP 面与两条外部信任边界。

## 不变量

- 没有坐标的指标**返回 `null`，不猜**；没有原文片段的结论不得出域。
- 阶段边界上线前创建的报告标记 `legacy_unclaimed` 并封存：数据保留给运维侧执行保留策略，
  但**患者 API 不允许认领或读取**。这是所有权边界的既成事实，不是缺陷。
- 一条规则只能有一处定义——本仓库的多个模块 docstring 记着同一条规则曾被实现四到六次
  并各自漂移的代价。
