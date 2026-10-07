// 报告状态的展示（GLOSSARY.md 的「报告状态」）。
//
// 状态机由服务端单点拥有（app/service/report_status.py）。前端只做**映射**：
// 一个状态 → 一句患者看得懂的话 + 一个颜色。此前这份映射有三处副本：
// App.jsx 与 ReportDetail.jsx 的 statusLabel 逐字相同，Upload.jsx 的
// evidenceStatus 已经漂移（同一状态 confirmed 在前两处是「已确认」，在上传页是
// 「已确认，待生成提示」）；颜色也是两套（历史列表二色 vs 详情五色），患者今天
// 就能看到分歧 —— 一份 failed 的报告在历史列表显示金色，点开详情才是红色。
//
// 措辞的取舍：取上传页那一份。它出现在患者刚做完确认的上下文里，「已确认，待生成
// 提示」比「已确认」多说了下一步；而历史列表与详情页用的是同一份状态，多出来的
// 那几个字在那里同样成立（确认完还没生成提示，本来就是待生成提示）。反过来取
// 「已确认」会让上传页丢掉那句提示 —— 两害相权取其重。

/** 状态 → 患者看到的说法。 */
const LABELS = {
  processing: '解析中',
  pending_confirmation: '待确认',
  confirmed: '已确认，待生成提示',
  assessed: '已生成健康提示',
  failed: '解析失败',
};

/** 状态 → 标签颜色。一套映射，历史列表与详情页共用。 */
const COLORS = {
  processing: 'processing',
  pending_confirmation: 'gold',
  confirmed: 'blue',
  assessed: 'green',
  failed: 'error',
};

export function reportStatusLabel(status) {
  return LABELS[status] || status || '未知状态';
}

export function reportStatusColor(status) {
  return COLORS[status] || 'default';
}

/** 解析是否已经结束（轮询据此停止）。 */
export function isReportSettled(status) {
  return status !== 'processing';
}
