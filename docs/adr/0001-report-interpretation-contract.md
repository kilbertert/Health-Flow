# 报告解读作为证据服务的只读投影

Status: Superseded by 0003

HealthFlow 只校验、保存并展示 Genesis Evidence 返回的已发布证据，不复制知识卡审核状态机或发布逻辑。证据治理由证据服务单点负责，HealthFlow 只承担报告上传、指标确认和患者侧呈现；代价是可用性依赖 Evidence API 契约。
