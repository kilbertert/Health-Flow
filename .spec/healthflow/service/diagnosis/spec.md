---
title: diagnosis
desc: 报告与指标的判定模块族——每条判定一个家
hue: 140
related:
  - app/service/metric_rows.py
  - app/service/metric_effective_value.py
  - app/service/origin_location.py
  - app/service/report_subject.py
  - app/service/admission_projection.py
---
# diagnosis

报告与指标的各类判定。每个成员都是同一个形态的实例：**一个问题此前由两到四套平行实现回答，
现在收成一处**。

| 模块 | 概念（GLOSSARY） | 此前几份实现 |
| --- | --- | --- |
| `metric_rows` | 同一观测 | 两套判定键，对同一内容给出**不同行数** |
| `metric_effective_value` | 指标生效值 | 一段无名局部规则（四条回退链），决定哪些值跨证据边界 |
| `origin_location` | 原文定位 | **四层平行实现**，坐标合法性一层就有四种实现、三种裁决 |
| `report_subject` | 报告主体一致性 | 三套通道，互不感知 |
| `admission_projection` | 解读准入（出域一半） | — |

## 不变量

- **退化框 `[100,100,100,100]` 的裁决必须有读写两层**：创建边界**从严**（拒绝——
  一个退化框指不到任何东西），读取边界**从宽**（历史数据要能读出来）。
  这是兼容性要求，**不是残留的双规则**。
- 生效值决定哪些值跨过证据边界，因此必须**被命名并透出到响应契约**，
  不能留作无名的 `or` 链。
- 去重键必须包含**文件编号**：页内去重与报告级去重给出不同行数，正是因为前者没有它。
