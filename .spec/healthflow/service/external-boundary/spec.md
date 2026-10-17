---
title: external-boundary
desc: 商城标签映射与加购深链——被有意复制的表，靠契约测试拦漂移
hue: 20
code:
  - app/service/condition_tags.py
related:
  - app/service/deep_link.py
---
# external-boundary

两条通往商城的语义边界。

## raw source

`condition_tags` 是**一份被有意复制过来的表**：真值在 genesis-evidence 的
`docs/condition-to-mall-tag.md`（交付给商城侧的词汇表），本仓只消费它的结果。
**复制会漂移**——所以这里有一个契约测试逐条比对两仓取值，任一侧改了名字而另一侧没跟，
测试就红。**漂移由测试拦住，不靠人记得。**

`deep_link` 只做两件事：构造 URL 与签名。它**不**调用商城任何写接口、不持有购物车状态。

## 不变量

- 复制表的漂移由**契约测试**检测，不由纪律保证。
- 深链载荷里**没有任何秘密**（`detection_id` 会回显给患者、会被转发、会进日志），
  所以**安全性完全来自签名，不来自不可猜测**。
