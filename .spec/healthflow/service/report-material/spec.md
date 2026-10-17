---
title: report-material
desc: 报告原始材料的类型判定——内容嗅探优先于后缀
hue: 160
code:
  - app/service/report_material.py
---
# report-material

「这份原始材料是什么类型、该走哪条抽取路径」此前由**四处平行实现**回答，互不引用：
上传闸门一份后缀白名单、`report.py` 一张后缀 → MIME 表、`vision_encoder.parse` 一套按文件名的
后缀路由加**第二张** MIME 表（未知后缀默认落到 `image/png`），以及把渲染页一律写成字面量
`image/png` 的那处。

## raw source

本模块是**唯一**权威。它只做判定，不落盘、不解析内容：落盘留在 [[report-api]]，
解析留在 [[vision-encoder]]，两者都调用这里。

## 不变量

- **判定优先级：内容嗅探（magic bytes）优先于后缀。**
- 嗅不出类型时回落到后缀——「嗅不出」不等于「不是」，一个截断的 PDF 仍然是 PDF；
  这时以患者提交的后缀为准，**不猜**一个别的类型出来。
- 后缀与内容不一致时：判定结果按内容，且 `mismatch` 为真，上传闸门据此**拒绝**。
