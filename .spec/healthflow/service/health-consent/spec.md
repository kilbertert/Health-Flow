---
title: health-consent
desc: 健康数据同意——只追加的记录流，有效状态只判一次
hue: 100
code:
  - app/service/consent.py
related:
  - app/service/consent_vocabulary.py
  - app/schema/consent.py
  - app/api/auth.py
---
# health-consent

患者对其健康数据被如何使用的表态。两项彼此独立（报告解读必选、商品推荐可选），记录**只追加**，
当前状态取每项最新一条。见 `docs/adr/0027-health-consent-is-an-append-only-stream.md`（数据形状与判据归属），概念背景见同目录 ADR 0026。

## 不变量

- **只追加。** 代码里没有 UPDATE 与 DELETE 的路径：撤回是一条 `revoked` 行，不是删掉上一条。
  改写历史记录就无法举证「他在什么时候、对哪一版文本、从哪个通道表过什么态」——与
  [[audit-events]] 同一取向。
- **最新一条决定一切，包括它写在旧版本上。** 不是「最近一条有效版本的记录」——后者会让
  「他在新文本下撤回过」被一条更早的旧版同意盖过去。
- **有效 = 最新一条是 `granted` 且 `text_version` 等于当前要求的版本。** 判据只有一处
  （`ConsentState.valid`）：状态接口渲染它，上传闸门读它，推荐闸门（将来）也读它。
  两处各比一次就是「界面说已同意、服务端拒绝」的形状。
- **文本版本由服务端写定**，请求里没有版本字段；两项各有一个配置项，改一项不影响另一项。
- **来源由会话通道判定**（Bearer → `mall_h5`，Cookie → `legacy_web`，夹具 → `seed`），
  不由客户端声明。
- 没有有效的报告解读同意时，上传在**读取任何文件之前**返回 403，原因
  `report_interpretation_consent_required` 经 `X-Report-Access-Reason` 出域
  （[[recommendation-empty-reason]] 的「报告访问」那一组）。
- 没有有效的商品推荐同意时，推荐在**调用商城之前**返回空的商品列表与原因
  `product_recommendation_consent_required`（[[recommendation-empty-reason]] 的
  「取货为空」那一组），且**一次商城请求都不发**。它是 200 而不是错误：页面照常渲染，
  患者看到的是「开启推荐」而不是一次失败。
