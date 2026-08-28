# 将产品推荐作为证据服务的只读投影

HealthFlow 只校验、保存并展示 Genesis Evidence 返回的已发布产品推荐，不复制产品目录、审核状态机或发布逻辑。这样产品治理仍由证据服务单点负责，HealthFlow 只承担报告上传、指标确认和患者侧呈现；代价是推荐可用性依赖 Evidence API 契约。
