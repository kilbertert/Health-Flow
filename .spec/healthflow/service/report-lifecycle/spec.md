---
title: report-lifecycle
desc: 报告状态、所有权与上传策略
hue: 60
code:
  - app/service/report_status.py
related:
  - app/service/report_ownership.py
  - app/service/upload_policy.py
---
# report-lifecycle

「这份报告现在处于什么阶段、下一步允许做什么」此前由**四个平行通道**回答：
`MedicalReport.status` 列、`parsed_content` JSON 里的 warnings/file_results、
`ReportExtractionJob.status`、以及审计事件的 action 词汇。写入点散在 API 与 worker
共**八处**，闸门（确认/评估能不能做）**不在 status 列上**，而是从 JSON 里重新推导。

## raw source

状态与审计事件的写入收成**一个入口**（本模块）。所有权是每个患者端点跨过的**第一道**边界
（`report_ownership`）。

上传策略（`upload_policy`）是服务端对患者侧公布的**唯一**一份受理规则：前端要在本地做即时 UX，
但不该自己记一份——`MAX_UPLOAD_FILES = 20` 曾在前端写死三处，运维者调大配置前端**不会**跟着变。

## 不变量

- 闸门判据**在 status 列上**，不从 JSON 重新推导。
- 状态与审计事件只有一个写入入口。
- 上传策略**只下发受理规则本身**（扩展名与三个上限）；服务端路径、目录、解析配置一律不出域。
- 所有权判定只有一处：患者端点的第一道边界。
