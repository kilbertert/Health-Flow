Feature: 体检报告健康风险提示
  HealthFlow 展示 Genesis Evidence 对已确认健康风险返回的已发布证据。
  商品不在该契约内——商品权威已移到商城（genesis-evidence ADR 0006）。

  Rule: 只有已发布知识卡支撑的证据对患者可见

    Scenario: 已确认异常项产生健康风险提示
      Given 一份体检报告已解析出带原文证据的异常指标
      And 患者确认该异常指标
      And 对应健康风险存在已发布知识卡
      When HealthFlow 请求 Genesis Evidence 生成健康风险提示
      Then 患者看到风险名称、证据正文、原文回链与建议复查方向
      And 页面说明该内容不构成诊断或治疗建议

    Scenario: 没有已发布知识卡时不构造结论
      Given 一个已确认异常指标没有已发布知识卡
      When HealthFlow 展示该健康风险提示
      Then 该指标进入 `unmatched`，原因为 `no_published_knowledge_card`
      And 页面不构造风险结论，也不以草稿内容补充

    Scenario: 响应中不出现任何商品字段
      Given Genesis Evidence 返回已确认风险的证据响应
      When HealthFlow 校验并持久化该响应
      Then 响应不含 `recommendations`、`recommendation_message` 或 `product_status`
      And 报告状态仍可进入 `assessed`

  Rule: AFK 拉取请求自动化使用可信控制面

    Scenario: 持久化 runner 使用当前 main 作为审核基线
      Given runner 的本地 main 已落后于 origin main
      When 仓库所有者创建的同仓库 PR 触发 agent:review
      Then 可信 controller 将本地 main 重置到当前 origin main
      And 审核差异以该当前基线计算

    Scenario: 候选代码不能获得交付凭据
      Given 仓库所有者创建的同仓库 PR 触发 AFK 变更工作流
      When 工作流执行候选分支
      Then 宿主依赖和编排只从 main controller 加载
      And 候选命令只在带只读 token 的 Docker 沙箱执行
      And 干净 delivery checkout 导入并推送已验证的 Git bundle

    Scenario: 不可信 PR 或缺失交付凭据时停止
      Given PR 来自 fork、作者不是仓库所有者，或 AGENT_PAT 不可用
      When PR 被添加 AFK 变更标签
      Then 工作流不报告成功交付
      And 凭据失败时记录 agent:blocked
