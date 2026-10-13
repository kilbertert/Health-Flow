// 解读失败的展示（GLOSSARY.md 的「解读失败」，PRD #226）。
//
// 服务端持有成因（`app/service/interpretation_failure.py`）并逐报告出域一个类型化的
// 失败块：`{ cause, retryable, action, failed_files }`。前端只做**映射** —— 一个成因
// → 一句患者看得懂的话 + 一句他该做什么。
//
// 这里取代的是四处各写一遍的兜底文案（后端一句、Upload.jsx 两句、ReportDetail.jsx
// 一句、reportStatus.js 一句），它们措辞已经分叉，而**患者动作永远是「重新上传」**
// —— 那句话对多类失败是错的：同一份材料再传一遍既救不了「服务里读不出来」，也救不了
// 「服务当时不可用」，还会留下第二份记录。ADR-0006 结尾那句「请重新上传对这类失败是
// 误导的，但那是另一张票」指的就是这里。
//
// 三句今天说不出来的话（这是本模块存在的理由）：
//   * `material_unreadable`  —— 你给的那份我们没读到 → 请重传；
//   * `content_unrecognized` —— 你给的这份里没有可确认的指标 → 换一份更清晰的重传；
//   * 我们这边出的问题（服务不可用 / 模型不可用 / 凭据被拒）→ **重传没用**。
//
// **不承诺「马上会自动重试」**：`retryable` 说的是「换个时刻再来有意义」，而重试次数
// 可能已经用完了（那时报告终态就是 `failed`）。承诺一件可能不会发生的事，比不说更糟。

/** 成因 → 患者看得懂的一句话。与服务端闸门话术（`GATE_DETAIL_BY_FAILURE`）各写一份：
 *  那一句出现在 HTTP 响应里，这一句在卡片上；两者都由成因派生，都不各自编兜底。 */
const SENTENCES = {
  material_unreadable: '我们没能读取到这份报告的原文。',
  service_unavailable: '我们这边在读取时出了点问题。',
  provider_rejected: '报告解读服务当前不可用，这不是你上传的内容的问题。',
  provider_unavailable: '解读服务当时不可用。',
  content_unrecognized: '这份报告里没有识别出可确认的指标。',
};

/** 成因 → 患者该做什么。与 `PATIENT_ACTION_BY_FAILURE` 同一份口径（服务端也出域
 *  `action`，这里只用它做一次一致性断言，不自己推导）。 */
const ACTIONS = {
  material_unreadable: '请重新上传这一份。',
  service_unavailable: '你不需要做什么，稍后再来看。',
  provider_rejected: '你不需要做什么，稍后再来看。',
  provider_unavailable: '你不需要做什么，稍后再来看。',
  content_unrecognized: '请换一份更清晰的重新上传。',
};

/** 不知道成因时（历史行、或服务端没给块）说的话。它**不是**一个成因的兜底 ——
 *  它明说我们不知道，而不是猜一个出来。 */
const NO_CAUSE = '报告解读未能完成，请稍后再试。';

/** 成因 → 标签颜色。**与状态色分开**：状态色说的是「这份报告在什么阶段」（`failed`
 *  一律 error 红），成因色说的是「这件事有多值得他动手」。
 *
 *  「我们这边出的问题」用 `warning` 而不是 `error`：它是**我们**的故障，红色会让患者
 *  以为是自己那份材料的问题 —— 而这一整票要消灭的正是那个印象。 */
const COLORS = {
  material_unreadable: 'error',
  service_unavailable: 'warning',
  provider_rejected: 'warning',
  provider_unavailable: 'warning',
  content_unrecognized: 'error',
};

/** 失败块 → `{ cause, title, description, color, failedFiles }`。没有块时返回 null
 *  （调用点用自己的空态）。 */
export function interpretationFailureText(block) {
  const cause = block?.cause;
  if (!cause) return null;
  return {
    cause,
    title: SENTENCES[cause] || NO_CAUSE,
    description: ACTIONS[cause] || '请稍后再试。',
    color: COLORS[cause] || 'error',
    // 可重试且重试次数还没用完时，服务端会留在 `processing`；这里拿到的块是**终态**
    // 的，所以不承诺会自动重试。
    failedFiles: (block.failed_files || []).map((item) => item.filename).filter(Boolean),
  };
}

/** 一句话的失败描述（列表、提示条这类只有一行的地方用）。 */
export function interpretationFailureLine(block) {
  const text = interpretationFailureText(block);
  if (!text) return null;
  return `${text.title}${text.description}`;
}

/** 失败块 → 患者侧颜色。没有块时返回 null（调用点用状态色）。 */
export function interpretationFailureColor(block) {
  return interpretationFailureText(block)?.color || null;
}

/** 失败块 → 一个可抛出的错误，**自带颜色**。
 *
 *  只有一句话的 `Error` 传不了颜色，调用点就只好用红色渲染「我们这边出的问题」——
 *  而本模块存在的理由之一就是那句红色是错的。所以把 `{message, color}` 挂在
 *  `err.failure` 上，调用点直接交给告警组件。
 */
export function failureError(report) {
  const text = interpretationFailureText(report?.interpretation_failure);
  const err = new Error(text ? `${text.title}${text.description}` : NO_CAUSE);
  err.failure = { message: err.message, color: text?.color || 'error' };
  return err;
}

/** 没有成因可给时的那句话（历史行）。调用点**不要**各自再写一句兜底 ——
 *  「失败时患者看到什么」只能有一处可改。 */
export const NO_CAUSE_TEXT = NO_CAUSE;

export const FAILURE_SENTENCES = Object.freeze(SENTENCES);
export const FAILURE_ACTIONS = Object.freeze(ACTIONS);
export const FAILURE_COLORS = Object.freeze(COLORS);
