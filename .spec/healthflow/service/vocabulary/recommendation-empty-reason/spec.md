---
title: recommendation-empty-reason
desc: 取货为空的原因——封闭值集，前端不得自造第四个值
hue: 120
code:
  - app/service/recommendation_empty_reason.py
---
# recommendation-empty-reason

一次检测页取货（推荐列表）或加购（深链）**为什么没有给出东西**，此前有四份答案，
三份在服务端、一份**只在浏览器里**：

- 服务端契约只认三种：`no_published_card`、`no_label_data`、`mall_unavailable`。
  ADR-0005 第 3 点点名要求「为空时必须说明是哪一种空」。
- 客户端自造了第四种 `session…`。

后果：值取决于「服务端那句话里有没有 3 位数字」；404 被说成「商城不可用」；
会话失效被叫去「重新登录」一个不存在的页面。

## raw source

模型是封闭的，而**语言是穷尽的**：写不出一个能被校验的取值，就写一个谁都校验不到的字符串。
所以这条边界的解不是"让前端也遵守"，是**让契约承载这个事实**——三处（契约、商城子集、
加购契约）都从本模块派生。

## 取货为空里的四个取值

`no_published_card`（没有可据以取货的健康风险）、`no_label_data`（商城调用成功但没货）、
`mall_unavailable`（不可达/超时/业务错误/凭据未配置），以及 #271 新增的
`product_recommendation_consent_required`（患者没有对当前版本的「商品推荐」同意）。

**后两者都不调用商城**，但它们是两件事：前者说「没有可据以推荐的东西」，后者说「患者没
允许我们问商城」。患者侧的动作也不同 —— 前者只能说暂无推荐，后者要指向「开启推荐」。
把它并进 `MALL_GOODS_REASONS_ORDERED` 会让 `fetch_goods` 看起来能返回一个它永远不会返回
的值，而闸门恰恰在它之前。

## 不变量

- 值域封闭；前端不得自造取值。
- 每种空必须有**具名**原因，不得合并成一句「没有推荐」。
- 只能由端点判定的取值（`no_published_card`、`product_recommendation_consent_required`）
  **不进商城子集**：那是「哪一层能产生它」的事实，不是文案选择。
