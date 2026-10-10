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

/**
 * 评估**之前**的准入状态，或 `null`（没有能同义读出的）。
 *
 * **这个名字可以退休**，但今天还不行：确认页面对的报告还没评估，`admission_reason`
 * 全是 `null`，所以「这一行患者还没核对过」只能从这里读。等服务端在未评估时也给逐行
 * 结论（那是另一张票：#203 只改评估之后的判定），这一处就可以删掉。
 *
 * 服务端的准入结论要等评估之后才有（那时才写进 `admission_reason`），但有两个状态是
 * 患者**自己**的表态，不该等到评估之后才显示：
 *
 * - `excluded` —— 患者明确排除；
 * - `pending` —— 还没核对。
 *
 * 这里读的是 `confirmation_status` 的两个值，而它们与该状态**同义**（同一个概念、
 * 同一个来源：患者自己的表态，记录在唯一的那个列上），不是第二处判定。这正是为什么
 * 它必须**只在这里**：`app/service/admission.py` 对同一行的结论就是这两个词，两个方向
 * 翻的是同一份词表。
 *
 * 与 `valueReasonOrNull` 的分工：那一条回答「值用不用得了」（与确认状态无关）；这一条
 * 回答「患者对这条表过什么态」。
 */
export function admissionBeforeAssessment(metric) {
  if (metric?.confirmation_status === 'excluded') return 'excluded';
  if (metric?.confirmation_status === 'pending') return '待核对';
  return null;
}

/** 原因 → 患者可见的一句话。键与 `app/service/admission_vocabulary.py` 的词表一致。 */
export const ADMISSION_TEXT = Object.freeze({
  // 可判、但患者还没核对（#203）：**不是**「未能解读」，所以不说那句话。这一行的值、
  // 单位、参考范围、原文证据都齐，判得出 H/L/N —— 它只是还没参与解读。患者在确认页上
  // 本来就要逐条核对，那里不需要再挂一枚「未进入解读」的橙色标签。
  awaiting_confirmation: '尚未核对，暂不参与解读',
  // 患者已排除：一个表态，也是一个结论。
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

/** 判定过、且**患者不必为它做任何事**的两条结论 —— 逐行不挂提示、也不进待处理集合。
 *
 * 两条各自的理由不同，所以它们在这一层是分开的（服务端台账里也分列 `normal` 与
 * `not_evaluated`）：
 *   - `within_reference_range` —— 结论已给：正常。
 *   - `awaiting_confirmation` —— 结论已给：判得出（值/单位/参考范围/原文证据都齐），
 *     缺的只是患者那一次核对；而「核对」正是确认页在做的事，报告单上不必逐行再说一遍。
 *
 * 与后端 `admission_vocabulary.NO_ACTION_REASONS` 同一份口径，由 `test_admission` 的守卫
 * 钉住两者相等 —— 分叉会让同一行在两处得到不同的说法。 */
export const NO_ACTION_REASONS = Object.freeze(['within_reference_range', 'awaiting_confirmation']);

/**
 * 值这一类原因的**子集**：它们说「这个值还没被解析成一个数」。
 *
 * 这是 #129 的守卫所依赖的那两条：多值/带符号的值会被后端整行丢掉，而界面若默认
 * 「确认」，患者会以为它参与了解读（报告 44 的三个异常项就是这么消失的）。
 *
 * 「参考范围缺失」**不在**此列：它的值完全正常，让人去修正数字是误导 —— 这一条的区别
 * 正是本次改动要修的东西。
 */
export const VALUE_UNPARSED_REASONS = Object.freeze(['missing_value', 'invalid_value']);

/**
 * 这一行为什么「值用不了」，或 `null`（值没问题 / 无从判断）。
 *
 * **两个时机各有来源，都不是新规则**：
 *
 * 1. 服务端已经给出准入结论时（报告 `assessed`），直接读它。
 * 2. 还没有结论时（确认页面对的就是这个状态 —— 那时每一行的 `admission_reason` 都是
 *    `null`），用服务端给出的**异常判定**反推同一件事：判定为空**且**模型宣称它异常，
 *    这条值就解析不出一个数。空值与多值在这里分开说，因为患者要做的事不同。
 *
 * 为什么确认页不能只信准入结论：准入结论要等评估之后才有，而确认页必须**在提交之前**
 * 就说明白（#129 的核心）。所以这一段判定留在前端，但用的是**同一个问题的服务端名字**
 * —— 不是第二套解析规则（旧代码在这里复刻过一遍数值与范围解析，那才是要消灭的东西）。
 */
export function valueReasonOrNull(metric) {
  const reason = metric?.admission_reason;
  if (reason) return VALUE_UNPARSED_REASONS.includes(reason) ? reason : null;
  if (reason !== null && reason !== undefined) return null;
  if (metric?.inferred_abnormal_flag === undefined) return null; // 旧响应：无从判断
  if (metric.inferred_abnormal_flag !== null) return null;
  if (!isAbnormalLike(metric?.abnormal_flag)) return null;
  return String(metric?.metric_value ?? '').trim() === '' ? 'missing_value' : 'invalid_value';
}

/** 这一行值用不了吗（见 `valueReasonOrNull`）。 */
export function valueNotParsed(metric) {
  return valueReasonOrNull(metric) !== null;
}

/** 与 `abnormalTag` 同源的原始标记判定（`Upload.jsx` 的 `isAbnormal` 只认大写，
 * 这里对全角/小写一视同仁 —— 抽取模型两种写法都产出过）。 */
function isAbnormalLike(flag) {
  return ['H', 'HIGH', '高', 'L', 'LOW', '低', 'A', '*'].includes(String(flag || '').toUpperCase());
}

/** 服务端没给结论（`null` / `undefined`）：进入解读，或这份报告还没评估。 */
export function admissionText(reason) {
  if (reason === null || reason === undefined || reason === '') return null;
  return ADMISSION_TEXT[reason] ?? '未进入解读（原因未识别）';
}

/** 词表里的每一个原因都必须有一句话 —— 缺一个就会在界面上显示「原因未识别」。 */
export function missingTexts(reasons) {
  return (reasons || []).filter((reason) => !(reason in ADMISSION_TEXT));
}

/** 这一行的结论是不是「不需要患者做任何事」（正常，或可判、待核对）。 */
export function isNoActionReason(reason) {
  return NO_ACTION_REASONS.includes(reason);
}

/**
 * 这行有没有一个**值得一提**的准入结论。
 *
 * 「在参考区间内」与「可判、待核对」都不算 —— 前者结论已给（正常），后者缺的只是患者
 * 那一次核对（确认页正在做）。患者在指标列表里看到的应当是「没能进入解读」的那些，以及
 * 「已排除」这种需要他自己知道的状态。
 */
export function notableAdmission(reason) {
  if (reason === null || reason === undefined || reason === '') return false;
  return !isNoActionReason(reason);
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
