---
title: service
desc: 判定与编排层——每条规则的家
hue: 160
code:
  - app/service/report_worker.py
related:
  - app/service/data_augment.py
  - app/service/llm_expander.py
  - app/service/medical_rag.py
  - app/service/safety_dpo.py
  - app/service/vlm_tuner.py
  - app/service/metric_rows.py
  - app/service/metric_effective_value.py
  - app/service/origin_location.py
  - app/service/admission_projection.py
  - app/service/report_subject.py
  - app/service/report_status.py
  - app/service/report_ownership.py
  - app/service/upload_policy.py
  - app/service/condition_tags.py
  - app/service/deep_link.py
  - app/service/sessions.py
  - app/service/ticket_sessions.py

---
# service

规则与编排。这一层的 docstring 是本仓库最有价值的一手材料——
它们记着**同一条规则曾有几份实现、副本怎么漂移、代价是什么**。

## raw source

本仓库反复出现的缺陷形态只有一个：**用词表回答问题，而词表有四个副本。**
`admission_vocabulary`、`report_material`、`interpretation_failure` 的模块文档
各自记录了一次同样的事故——四处或四套平行实现，互不引用，随后各自分叉。

这条形态决定了这一层的结构：**词表与判定分离**，词表不 import 任何项目模块
（否则出域契约导入它会成环，实测会报半初始化 `ImportError`），判定在词表之上写字。

## 不变量

- 词表模块**只放声明、不放函数**，且只 import 标准库。
- 判定优先取**结构化事实**（HTTP 状态、异常类），拿不到才看文本，且输出仍是**具名成因**
  而不是布尔。

## 冻结代码

`data_augment`、`llm_expander`、`medical_rag`、`safety_dpo`、`vlm_tuner` 保留在仓库中，
但**不属于当前 API**。它们不是"待办"，是已移出运行时承诺的代码——改动它们不产生对外行为。
