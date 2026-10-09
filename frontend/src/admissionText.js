// 「解读准入」的文案（GLOSSARY.md 的「解读准入」）。
//
// 服务端给出唯一结论，这里只负责把它翻成患者能懂的一句话。**没有推导**：不认识的原因
// 一律落到一句兜底，而不是猜一个更具体的意思 —— 猜错会把人引向错误的动作（去修正一个
// 本来就正常的数值）。
//
// 在此之前这里有两处推导，本模块取代它们：
//
//   1. `valueUnusable`（在 Upload.jsx 里）拿 `inferred_abnormal_flag === null` 当
//      「这个值用不了」的代理，把四种不同的事实压成同一句「数值无法识别为单个数字」：
//      值还没解析出来、值不是一个数、**参考范围缺失（值本身完全正常）**、以及服务端
//      根本没给字段的旧响应。第三种最误导 —— 它引导患者去修正一个没有毛病的数字。
//   2. 页面底部一句 `{N} 个指标未进入匹配（正常、缺参考范围、原文证据或数值不足）`
//      把七个原因压成四个词，并把 `within_reference_range`（在参考区间内，即「正常」）
//      也说成「未进入匹配」—— 与同一张卡片上方刚说过的「均在参考区间内」直接矛盾。

/** 原因 → 患者可见的一句话。键与 `app/service/admission_vocabulary.py` 的词表一致。 */
export const ADMISSION_TEXT = Object.freeze({
  // 尚未核对、患者已排除：两种显式的准入结论。
  pending: '尚未核对，暂不参与解读',
  excluded: '已排除，不参与解读',
  // 值这一类 —— 三句不同的话，对应三个不同的动作。
  missing_value: '数值还没解析出来',
  invalid_value: '数值不是一个数（如多值或带符号），需要修正',
  // 参考范围这一类：**值本身没问题**，所以不说「数值无法识别」。
  missing_reference_range: '缺少参考范围，无法判断是否异常',
  // 证据不完备。
  missing_unit: '缺少单位，未进入解读',
  missing_source_evidence: '缺少原文证据，未进入解读',
  missing_source_page: '缺少原文页码，未进入解读',
  // 判定过、在区间内 —— 这一条是「正常」，**不是**「没进解读」。
  within_reference_range: '在参考区间内',
  // 找不到对应的知识卡。
  unknown_metric_code: '没有可对应的标准指标编码',
  no_published_knowledge_card: '暂无已审核的关联知识卡',
});

/** 这些原因属于「正常」，不属于「没能解读」—— 与摘要口径必须一致。 */
export const NORMAL_REASONS = Object.freeze(['within_reference_range']);

/** 这些原因属于「患者自己的决定」，不是缺陷。 */
export const PATIENT_DECISION_REASONS = Object.freeze(['excluded', 'pending']);

/** 服务端没给结论（`null` / `undefined`）：进入解读，或这份报告还没评估。 */
export function admissionText(reason) {
  if (reason === null || reason === undefined || reason === '') return null;
  return ADMISSION_TEXT[reason] ?? '未进入解读（原因未识别）';
}

/** 这一行的结论是不是「正常」。（不是「没能解读」，也不是缺陷。） */
export function isNormalReason(reason) {
  return NORMAL_REASONS.includes(reason);
}

/**
 * 这行有没有一个**值得一提**的准入结论。
 *
 * 「在参考区间内」不算 —— 它是正常，不该在指标旁边挂一个提示。患者在指标列表里看到
 * 的应当是「没能进入解读」的那些，以及「还没核对 / 已排除」这两种需要他自己知道的状态。
 */
export function notableAdmission(reason) {
  if (reason === null || reason === undefined || reason === '') return false;
  return !isNormalReason(reason);
}

/**
 * 把一组指标行的准入结论汇成**按原因分组**的说明。
 *
 * 返回 `[{ reason, text, count }]`，按 `order` 里给定的顺序排列（服务端词表的顺序），
 * 未知原因排在最后。空数组表示没有值得一提的结论 —— 那时页面不该出现那句话。
 */
export function admissionGroups(metrics) {
  const counts = new Map();
  for (const metric of metrics || []) {
    const reason = metric?.admission_reason;
    if (!notableAdmission(reason)) continue;
    counts.set(reason, (counts.get(reason) || 0) + 1);
  }
  const order = Object.keys(ADMISSION_TEXT);
  return [...counts.entries()]
    .sort(([a], [b]) => {
      const indexA = order.indexOf(a);
      const indexB = order.indexOf(b);
      return (indexA < 0 ? order.length : indexA) - (indexB < 0 ? order.length : indexB);
    })
    .map(([reason, count]) => ({ reason, text: ADMISSION_TEXT[reason] ?? '未进入解读（原因未识别）', count }));
}
