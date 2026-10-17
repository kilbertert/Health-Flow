---
title: schema
desc: 出域契约——对外形状的最底层，因此不能依赖 service
hue: 210
code:
  - app/schema/evidence.py
related:
  - app/schema/report.py
  - app/schema/chat.py
  - app/schema/train.py
  - app/schema/auth.py
  - app/schema/__init__.py
  - app/api/kg.py
  - app/api/deps.py
  - app/api/__init__.py
  - app/service/__init__.py

---
# schema

对外契约的形状。它在**最底层**，这个位置不是审美选择，是硬约束。

## raw source

证据契约要直接消费 [[admission]] 的词表，而词表不 import 任何项目模块——
原因正相反：一旦词表依赖了 service 层，导入就成了环。实测：`schema.evidence` 在模块级
import `service.admission` 会拉起 `service.__init__` → `vision_encoder` → `schema.report`
→ **半初始化的 `schema.evidence`**，报 `ImportError`。

## 不变量

- 本层**不得** import service 层。要读词表，读那个只 import 标准库的词表模块。
- 形状变化是跨文件契约变化，需要一份决策记录。
