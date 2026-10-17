---
title: vocabulary
desc: 一个概念一个家——只有声明、不 import 任何项目模块
hue: 120
related:
  - app/service/confirmation_vocabulary.py
  - app/service/admission_vocabulary.py
---
# vocabulary

本仓库反复出现的**唯一**缺陷形态，用 `docs/analysis-2026-10-17-vocabulary-ownership.md`
的一句话概括：

> **一个领域事实的含义，由「恰好写了它的那段代码」携带，而不是由一份所有生产者与消费者
> 共享的声明携带。**

三份同步失败的后果逐条对应、且不是巧合：N 个生产者 → 漂移；契约不承载该事实 → 前端复算或
发明一个谁都校验不到的字符串；词表住在散文里 → 拼错漏值无人发现；守卫是事后手写、
一次一个概念 → **覆盖面等于「有人想起来写了一份」**。

量出来的普遍性：改前对 `app/` 全部 `Literal[...]` 做 AST 扫描，34 处里**只有 8 处是派生的**，
值域完全相同的重复声明 **8 组**。

## raw source

收敛形状已经验证过，也是这一层的定义：

- **一个概念一个家**：词表模块**只放声明、不放函数**，且**不 import 任何项目模块**。
- **判定在词表之上**单开一个模块写字。

词表不能依赖 service 层的理由不是洁癖，是硬约束：**出域契约**（[[schema]]）要直接消费它，
而契约在最底层——一旦词表依赖了 service 层，导入就成了环（实测：`schema.evidence` 在模块级
import `service.admission` 会拉起 `service.__init__` → `vision_encoder` → `schema.report`
→ 半初始化的 `schema.evidence`，报 `ImportError`）。

## 不变量

- 词表模块只 import 标准库；放声明不放函数。
- 契约的值域**派生**自词表（`Literal[*X]`），不再手写第二份。
- 一条规则只有一处定义——改值域只改这里，契约与守卫一起跟着变。
