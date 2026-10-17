---
title: vision-encoder
desc: 坐标感知解析——文件到带坐标与原文的指标记录
hue: 160
code:
  - app/service/vision_encoder.py#VisionEncoderService
---
# vision-encoder

按文件类型选择文本 PDF、扫描 PDF 或图片路径，输出带坐标与原文的指标记录。

## 不变量

- 每条指标带 `page_number`、`bbox`、`bbox_normalized`、`evidence_text`、`source_id`。
  `source_id` 是回答引用与评测回溯的锚——没有它，一条结论无法回到原文。
- **无法定位时返回 `null`，不猜坐标。** 坐标前缀由 `vlm_tuner.coordinate_prefix` 生成，
  形式 `[PAGE=1][BBOX=...]`。
- 类型判定不在这里：走 [[report-material]]（内容嗅探优先于后缀）。
- 真正的 OCR 检测器与训练集由部署方按授权数据源接入，**不随仓库发布**。
