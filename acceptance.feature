Feature: 体检报告健康管理建议
  HealthFlow 展示 Genesis Evidence 对已确认健康风险返回的已发布产品推荐。

  Rule: 只有可发布的健康管理建议对患者可见

    Scenario: 已确认异常项产生产品推荐
      Given 一份体检报告已解析出带原文证据的异常指标
      And 患者确认该异常指标
      And 对应健康风险存在已发布且无高风险宣称的产品推荐
      When HealthFlow 请求 Genesis Evidence 生成健康风险提示
      Then 患者看到产品名、营养素、理由、安全提醒、免责声明和证据回链
      And 每条推荐直接使用契约返回的真实产品图
      And 页面说明该内容不构成医疗或用药指令

    Scenario: 健康风险没有可用产品推荐
      Given 一个已确认健康风险没有已发布的安全产品推荐
      When HealthFlow 展示该健康风险提示
      Then 患者看到“暂无推荐”
      And 页面不显示未发布、已下架或带高风险宣称的产品

  Rule: 跨系统契约完整保留推荐结果

    Scenario: Evidence API 返回可用推荐
      Given Genesis Evidence 返回 product_status available 和 recommendations
      When HealthFlow 校验并持久化证据响应
      Then 响应保持 recommendations 的全部患者可见字段
      And 报告状态变为 assessed
