---
title: archive
desc: 冻结与已移出运行时承诺的模块
hue: 300
related:
  - app/api/train.py
  - app/service/vlm_tuner.py
  - app/service/safety_dpo.py
  - app/service/medical_rag.py
  - app/service/data_augment.py
  - app/service/llm_expander.py
---
# archive

保留在仓库中、但**不属于当前 API** 的模块：训练（`vlm_tuner`、`safety_dpo`）、
知识图谱与检索（`medical_rag`）、数据增强（`data_augment`、`llm_expander`）。

## 为什么单独成节点

"冻结"是一个**状态**，不是一个位置。这些模块散落在 `api/`、`service/`、`agent/` 里，
读代码的人无法从目录判断它是否在跑。把状态写成节点，读的人一眼知道改它不产生对外行为。

## 不变量

- 冻结不等于废弃：改动不产生对外行为，但**被测试与迁移脚本引用**，删除需评估。
- 训练数据、模型权重与真实医疗数据**不随仓库发布**。
- 训练任务应通过独立 CLI 或受控异步队列执行，**不能在 Web 进程中长时间阻塞**。
