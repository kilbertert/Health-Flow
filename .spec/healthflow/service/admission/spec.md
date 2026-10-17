---
title: admission
desc: 解读准入的判定与词表——一条指标为什么没进解读
hue: 160
code:
  - app/service/admission.py
related:
  - app/service/admission_vocabulary.py
---
# admission

一条**已解析出的**结构化指标，能不能进入健康风险提示生成？不能的话，**唯一**的原因是什么？

## raw source

词表的家**只有这一个**，价值全在"只有一处"上。此前这个问题由**四套互不同步的词汇**回答，
各自在自己的文件里声明自己认识的名字。

词表模块刻意不 import 任何项目模块：它必须能被**出域契约**（[[schema]]）导入，
而契约在最底层——一旦词表依赖了 service 层，导入就成了环（实测：
`schema.evidence` 在模块级 import `service.admission` 会拉起 `service.__init__` →
`vision_encoder` → `schema.report` → 半初始化的 `schema.evidence`，报 `ImportError`）。

## 不变量

- **尚未核对不是准入结论**：一行齐全时判定照常走完，落 `awaiting_confirmation`，
  不提前收口。那个词以前叫 `pending`，与患者侧的 `pending` 同名，
  于是"患者没看过的正常行"与"判不了的行"在数据上不可分辨。
- `excluded` 是一个**表态、也是结论**：只有它退出异常口径。
- 缺单位 / 缺原文证据 / 缺页码会让一行**没进解读**，却不妨碍它的异常判定显示 H。
  这个分叉是"报告单说 H、解读里没有它"的**唯一**来源，如实保留，不替它遮掩。
- 准入结论与异常判定在**值/参考范围**这一类原因上必须同名；在证据完备性这一类上本就无关。
