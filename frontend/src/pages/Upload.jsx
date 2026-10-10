import {
  admissionBeforeAssessment,
  admissionGroups,
  admissionText,
  dualValues,
  notableAdmission,
  valueNotParsed,
  valueReasonOrNull,
} from '../admissionText';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { reportStatusColor, reportStatusLabel } from '../reportStatus.js';
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Form,
  Image,
  Input,
  List,
  message,
  Modal,
  Radio,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from 'antd';
import { CheckCircleOutlined, EyeOutlined, InboxOutlined, PictureOutlined, ReloadOutlined } from '@ant-design/icons';
import Recommendations from '../components/Recommendations.jsx';
import {
  assessReport,
  confirmReport,
  fetchReportPage,
  getMetricCatalog,
  getReport,
  uploadReport,
} from '../api.js';
import {
  classifyPaste,
  dataImageCandidates,
  extensionFromName,
  normalizePasteMime,
  pastedFileName,
  unsupportedPasteMessage,
} from '../pasteImage.js';
import {
  acceptAttribute,
  describeBytes,
  loadUploadPolicy,
  normalizeUploadPolicy,
} from '../uploadPolicy.js';

const REPORT_TYPES = ['体检', '门诊', '住院', '其他'];
const DECISIONS = [
  { label: '待核对', value: 'pending', disabled: true },
  { label: '确认', value: 'confirmed' },
  { label: '修正', value: 'corrected' },
  { label: '排除', value: 'excluded' },
];
const PASTE_LONG_PRESS_MS = 600;
const PASTE_LONG_PRESS_MOVE_TOLERANCE = 10;
const PASTE_EDITABLE_PLACEHOLDER = '\u200B';

export { extensionFromName, normalizePasteMime };

function pasteImageFromBase64(mime, base64) {
  try {
    const bytes = Uint8Array.from(atob(base64.replace(/\s/g, '')), (char) => char.charCodeAt(0));
    return new File([bytes], '', { type: normalizePasteMime(mime) });
  } catch {
    return null;
  }
}

/** 收集剪贴板里的候选（不判受理）—— 分类只有一个执行点（`classifyPaste`）。 */
function pasteCandidates(event) {
  const clipboardData = event?.clipboardData || event?.nativeEvent?.clipboardData;
  const candidates = [];
  let sawPastePayload = false;

  const items = clipboardData?.items ? Array.from(clipboardData.items) : [];
  if (items.length > 0) {
    sawPastePayload = true;
    for (const item of items) {
      if (item.kind !== 'file') continue;
      const mime = normalizePasteMime(item.type);
      const file = item.getAsFile?.();
      if (file) {
        candidates.push({ file, mime: mime || file.type || '', name: file.name || '' });
      } else if (mime.startsWith('image/')) {
        // 拿不到 File 的剪贴项：仍然登记它，让分类回答「受不受理」。
        candidates.push({ file: null, mime, name: '' });
      }
    }
  } else {
    const files = clipboardData?.files ? Array.from(clipboardData.files) : [];
    sawPastePayload = files.length > 0;
    for (const file of files) {
      candidates.push({ file, mime: file.type || '', name: file.name || '' });
    }
  }

  const html = typeof clipboardData?.getData === 'function'
    ? clipboardData.getData('text/html') || ''
    : '';
  if (html) {
    sawPastePayload = true;
    for (const embedded of dataImageCandidates(html)) {
      const file = pasteImageFromBase64(embedded.mime, embedded.base64);
      if (file) candidates.push({ file, mime: embedded.mime, name: '' });
    }
  }
  const plainText = typeof clipboardData?.getData === 'function' && !html
    ? clipboardData.getData('text/plain') || ''
    : '';
  if (plainText) {
    sawPastePayload = true;
  }

  return { candidates, sawPastePayload };
}

/**
 * 分类剪贴内容。受理判据只有一处（`pasteImage.js` 的 `classifyPaste`），
 * 所以「入口说暂不支持」与「分类认得它」不可能再给出相反结论。
 */
function readPastedImages(event, acceptedExtensions) {
  const { candidates, sawPastePayload } = pasteCandidates(event);

  const images = [];
  let unsupported = null;
  let unreadable = false;
  for (const candidate of candidates) {
    // **每个候选都过分类**，包括拿不到 `File` 的那些 —— 否则 `unsupported` 会是
    // 原始候选（没有 `extension`），提示退回笼统的「暂不支持」，这条路径上的
    // 「说清是什么格式」就丢了（评审在 #174 指出）。
    const classified = classifyPaste(candidate, acceptedExtensions);
    if (classified.supported && candidate.file) {
      images.push({ file: candidate.file, ...classified });
    } else if (!classified.supported) {
      unsupported = unsupported || classified;
    } else {
      // 类型受理、内容却取不到：不谎报「格式不支持」，按「没拿到内容」处理。
      unreadable = true;
    }
  }

  if (images.length > 0) return { images, status: 'ok' };
  if (unsupported) return { status: 'unsupported', unsupported };
  if (sawPastePayload || unreadable) return { status: 'empty' };
  return { status: 'unavailable' };
}
// 异常标记：H=偏高(红) L=偏低(橙) N=正常(绿)
export function abnormalTag(flag) {
  if (!flag) return <Tag>—</Tag>;
  const f = String(flag).toUpperCase();
  if (f === 'H' || f === 'HIGH' || f === '高') return <Tag color="red">H 偏高</Tag>;
  if (f === 'L' || f === 'LOW' || f === '低') return <Tag color="orange">L 偏低</Tag>;
  if (f === 'A' || f === '*') return <Tag color="red">异常</Tag>;
  if (f === 'N' || f === 'NORMAL' || f === '正常') return <Tag color="green">N 正常</Tag>;
  if (flag === '待核对') return <Tag color="gold">待核对</Tag>;
  // 患者排除的指标：不是异常，也不是正常，如实说「已排除」。
  if (flag === 'excluded') return <Tag>已排除</Tag>;
  return <Tag>{String(flag)}</Tag>;
}

export function isAbnormal(flag) {
  return ['H', 'HIGH', '高', 'L', 'LOW', '低', 'A', '*'].includes(String(flag || '').toUpperCase());
}

function needsReview(metric) {
  // 「这条指标值不值得患者看一眼」= 它显示的标记是偏高/偏低/待核对。
  // 直接复用 displayFlag，避免「显示的标记」与「计入异常候选」再次分叉。
  const flag = displayFlag(metric);
  return flag === 'H' || flag === 'L' || flag === '待核对';
}

// 一条指标在界面上应显示的异常标记。
//
// 服务端算好的 `inferred_abnormal_flag` 优先 —— 它是「异常判定」的唯一口径，
// 历史摘要的「N 项偏高/偏低」也用它，所以两处永远同口径。
//
// 「待核对 / 已排除」这两个状态**由服务端的准入结论给出**（`admission_reason`），
// 不再从原始 `confirmation_status` 推导：那是同一个问题在两处的第二个答案，而
// 服务端已经有一个了。字段出现之前的响应（`undefined`）才退回原始标记做纯展示映射。
//
// `not_decidable` 的那两种情形刻意**不**回落：
//   - 患者排除的指标，患者已经表态不要它参与解读，服务端也不会为它计数；
//     这里再按原始标记显示一个红色「H」就自相矛盾了。
//   - 值还没判定的指标，患者要在确认页上看到「待核对」，而不是一个凭解析
//     不到的值推出来的假异常。
export function displayFlag(metric) {
  const admission = metric?.admission_reason;
  if (admission === 'excluded') return 'excluded';
  // 「可判、但患者还没核对」（`awaiting_confirmation`）在这里**没有分支**，是刻意的：
  // 那一行判得出 H/L/N，所以下面那一步（读 `inferred_abnormal_flag`）正是它的答案。
  // 这一条此前不存在 —— 那时「未核对」与「判不了」共用一个 `pending`，于是显示分支
  // 在读到判定之前就先返回「待核对」，患者看到的是一整页待核对（#203）。
  const inferred = metric?.inferred_abnormal_flag;
  if (inferred !== undefined && inferred !== null) return inferred;
  const raw = metric?.abnormal_flag;
  if (inferred === null) {
    // 评估之前：患者已经做过的表态（排除 / 尚未核对）也要如实显示，不该等到评估
    // 之后才出现。这条**同义**映射由 `admissionText.js` 单点持有 —— 见那里的说明。
    const preAssessment = admissionBeforeAssessment(metric);
    if (preAssessment) return preAssessment;
    // 判不出来，但模型宣称异常 —— 交患者核对（这正是 #129 要保住的场景）。
    if (isAbnormal(raw)) return '待核对';
    // 其余判不出来的行（如模型标 N、或没有参考范围）如实显示原始标记：
    // 把它们一律标成「待核对」会让每一份含无范围指标的报告满屏待核对。
    return raw;
  }
  return isAbnormal(raw) ? '待核对' : raw;
}

/**
 * 服务端**已经就这一行**给出的决策，或 `null`（还没有）。
 *
 * 只取真正的决定（确认 / 修正 / 排除）—— `pending` 是「还没决定」，把它当成一个可提交的
 * 表态会让界面替患者说「我还没决定」，而那句话在请求里没有意义。服务端的请求词表也不收
 * 它（见 `confirmation_vocabulary.REQUEST_DECISIONS`）。
 */
function serverDecision(metric) {
  const status = metric?.confirmation_status;
  return status === 'confirmed' || status === 'corrected' || status === 'excluded' ? status : null;
}

/**
 * 这一行在界面上**初始选中**哪一项 —— **不是**患者的表态。
 *
 * 名字里的 `suggested` 是全部要点：它的结果是让患者少点几下，而不是替患者作决定。
 * 它只在一种情况下进提交载荷（见 `observationsFor`）：**这一行确实显示给患者看过**
 * （`needsReview`），而他没改 —— 那就是他的答案。**隐藏**的正常行拿不到这个初选，
 * 它们如实说「我没动过这一行」。那条分界线才是「把初选当答案」与「客户端替患者表态」
 * 的区别所在。
 *
 * 那两种情况今天对**每一种行形态**给出同一个答案（判成 H/L 或待核对的行 `needsReview`
 * 必然为真，正常行又必然拿到「排除」而 `needsReview` 为假），所以这个条件在行为上是**冗余**
 * 的。留着它是因为它写的是那条规则本身 —— 判据一旦新增一条返回 `pending` 的分支，载荷就
 * 会跟着去替一个页面上没有的行表态，而那时唯一的信号是患者发现多出几个他答不上来的问题。
 *
 * 它回答的两个问题，判据都来自服务端：
 *   - 服务端说这一行没能进入解读（准入结论，或 #129 那条「值解析不出一个数」）→ 建议
 *     「待核对」，逼一次显式选择。**不能**建议「确认」：后端会把它连行一起丢掉，而界面
 *     却让它一路走到「已生成健康提示」（报告 44 的三个异常项就是这么消失的）。
 *   - 判不出来但模型宣称异常 → 也建议「待核对」（这正是 #129 要保住的场景）。
 *   - 其余（正常、模型没标异常）→ 建议「排除」，因为这些行患者不需要看。
 *
 * 最后一条曾经是患者可见缺陷的来源：它在**没显示给患者看**的那些行上被当成患者的表态
 * 提交，于是「患者没看过的正常行」被记成「患者已排除」。现在两者分开 —— 建议只对患者
 * 看到过的行生效，其余的行服务端听到的是一句诚实的「还没动」。
 */
export function suggestedDecision(metric) {
  const flag = displayFlag(metric);
  // 两个值都在的行（#204）：患者要做的是**选一个**，不是「确认 / 排除」——所以在
  // 这里如实说「还没决定」，页面把两个候选列出来给他点（见 `dualValueChoice`）。
  // 没有它的话，一行 `3.39 / 3.63`（判成 H、证据齐）会拿到初选「确认」，而患者对
  // 「用哪个数」根本没表过态。
  if (dualValueChoice(metric)) return 'pending';
  // 「可判、待核对」不算一条值得一提的结论：值、单位、参考范围、原文证据都齐，判得出
  // H/L/N —— 缺的只是患者那一次核对，而确认页正是他在核对（#203）。把它算进去会让每一份
  // 刚评估完的报告满页「待核对」，而患者要处理的其实只有下面两类。
  if (notableAdmission(metric?.admission_reason) || valueNotParsed(metric)) return 'pending';
  if ((flag === 'H' || flag === 'L') && metric?.evidence_text && metric?.page_number) return 'confirmed';
  if (flag === 'H' || flag === 'L') return 'pending';
  return flag === '待核对' ? 'pending' : 'excluded';
}

/**
 * 这一行页面上出现了**两个来源不同的数值**时，那两个候选；否则 `null`。
 *
 * 抽取如实转录了页面上出现的东西（化验单常是两行：实验室印的一行、患者手写的一行），
 * 而下游要求恰好一个数 —— 于是整行被丢掉，页面却问患者「这条的数值是多少」，而那两个
 * 数**本来就是他写的**（#204）。
 *
 * **不预设哪个是「当前值」**：谁是手写、谁是印刷需要模型去猜，而猜错的方向不对称
 * （把患者手写值当成实验室值，或反过来）。能确定的事实只有「页面上有两个来源不同的
 * 数值」——所以这里把它们如实列出来，让患者做他本来就在做的那个选择。
 */
export function dualValueChoice(metric) {
  if (valueReasonOrNull(metric) !== 'two_values') return null;
  const values = dualValues(metric);
  return values.length === 2 ? values : null;
}

function useNarrowViewport() {
  const [isNarrow, setIsNarrow] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(max-width: 700px)').matches,
  );
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const media = window.matchMedia('(max-width: 700px)');
    const handleChange = (event) => setIsNarrow(event.matches);
    setIsNarrow(media.matches);
    media.addEventListener('change', handleChange);
    return () => media.removeEventListener('change', handleChange);
  }, []);
  return isNarrow;
}

/**
 * 一行出现两个来源不同的数值时，让患者**选一个**（#204）。
 *
 * 这是本票的患者侧落点：不问「数值是多少」（那两个数本来就是他写的），而是把页面上出现
 * 的两个数并列出来 —— 他选的那个成为修正值。选项文案不猜来源（不说「印刷/手写」）：
 * 谁是手写需要模型去猜，而猜错的方向不对称；能确定的事实只有「页面上有两个数」。
 *
 * 只在**恰好两个**数值时出现。三个以上的形态（抽取把整段文字混进来）没有「选一个」这个
 * 问题，落 `invalid_value` 那条路 —— 那时患者确实需要重新给一个值。
 */
function DualValueChoice({ metric, draft, disabled, onUpdateDraft }) {
  const candidates = dualValueChoice(metric);
  if (!candidates) return null;
  return (
    <div className="metric-card-field">
      <Typography.Text type="secondary">这一项有两个值，用哪个</Typography.Text>
      <Radio.Group
        aria-label={`${metric.metric_name}取值`}
        value={draft?.decision === 'corrected' ? String(draft?.value ?? '') : undefined}
        disabled={disabled}
        onChange={(event) => {
          const chosen = event.target.value;
          // 选一个 = 一次修正：值就是选中的那个，其余字段沿用这一行已有的。
          onUpdateDraft(metric.id, 'decision', 'corrected');
          onUpdateDraft(metric.id, 'value', chosen);
          onUpdateDraft(metric.id, 'unit', draft?.unit || metric.effective_unit || metric.unit || '');
          onUpdateDraft(
            metric.id,
            'reference_range',
            draft?.reference_range || metric.effective_reference_range || metric.reference_range || '',
          );
          onUpdateDraft(
            metric.id,
            'evidence_text',
            draft?.evidence_text || metric.effective_evidence_text || metric.evidence_text || '',
          );
        }}
      >
        {candidates.map((candidate) => (
          <Radio.Button key={candidate} value={candidate} aria-label={`${metric.metric_name}用 ${candidate}`}>
            {candidate}
          </Radio.Button>
        ))}
      </Radio.Group>
    </div>
  );
}

function MetricCard({ metric, draft, metricCatalog, disabled, onUpdateDraft, onOpenSource }) {
  const [expanded, setExpanded] = useState(false);
  const decision = draft?.decision || suggestedDecision(metric);
  return (
    <Card size="small" className="metric-card">
      <div className="metric-card-header">
        <button
          type="button"
          className="metric-card-toggle"
          aria-label={`${metric.metric_name}指标卡片`}
          aria-expanded={expanded}
          onClick={() => setExpanded((open) => !open)}
        >
          <span className="metric-card-name">{metric.metric_name}</span>
          <span className="metric-card-value">
            {metric.effective_value}
            {metric.effective_unit ? ` ${metric.effective_unit}` : ''}
          </span>
          {abnormalTag(displayFlag(metric))}
          {/* 服务端说这一行没进解读时，如实说**它的原因**，而不是等它被后端悄悄
              丢掉、或拿「数值无法识别」一句盖住四种不同的事实。 */}
          {chipReason(metric) && (
            <Tag color="orange">{admissionText(chipReason(metric))}</Tag>
          )}
        </button>
        {/* 确认页与报告单一致：定位缺失时不再是沉默的「没有按钮」——
            按钮照常在，弹窗如实说明「无原文定位」（#163 复审）。 */}
        <Tooltip title="查看原文定位">
          <Button
            type="text"
            icon={<EyeOutlined />}
            aria-label={`查看${metric.metric_name}原文`}
            onClick={() => onOpenSource(metric)}
          />
        </Tooltip>
      </div>
      {expanded ? (
        <div className="metric-card-details">
          <DualValueChoice metric={metric} draft={draft} disabled={disabled} onUpdateDraft={onUpdateDraft} />
          <div className="metric-card-field">
            <Typography.Text type="secondary">标准指标</Typography.Text>
            <Select
              aria-label={`${metric.metric_name}标准指标编码`}
              value={draft?.metric_code || undefined}
              options={metricCatalog}
              showSearch
              optionFilterProp="label"
              allowClear
              disabled={disabled}
              onChange={(value) => onUpdateDraft(metric.id, 'metric_code', value || '')}
              placeholder="选择标准指标"
            />
          </div>
          <div className="metric-card-field">
            <Typography.Text type="secondary">处理</Typography.Text>
            <Select
              aria-label={`${metric.metric_name}处理方式`}
              value={decision}
              options={DECISIONS}
              disabled={disabled}
              onChange={(value) => onUpdateDraft(metric.id, 'decision', value)}
            />
          </div>
          <div className="metric-card-field">
            <Typography.Text type="secondary">修正值</Typography.Text>
            <Input
              aria-label={`${metric.metric_name}修正值`}
              disabled={disabled || decision !== 'corrected'}
              value={draft?.value || ''}
              onChange={(event) => onUpdateDraft(metric.id, 'value', event.target.value)}
              placeholder="数值"
            />
          </div>
          <div className="metric-card-field">
            <Typography.Text type="secondary">修正单位</Typography.Text>
            <Input
              aria-label={`${metric.metric_name}修正单位`}
              disabled={disabled || decision !== 'corrected'}
              value={draft?.unit || ''}
              onChange={(event) => onUpdateDraft(metric.id, 'unit', event.target.value)}
              placeholder="单位"
            />
          </div>
          <div className="metric-card-field">
            <Typography.Text type="secondary">修正范围</Typography.Text>
            <Input
              aria-label={`${metric.metric_name}修正参考范围`}
              disabled={disabled || decision !== 'corrected'}
              value={draft?.reference_range || ''}
              onChange={(event) => onUpdateDraft(metric.id, 'reference_range', event.target.value)}
              placeholder="如 3.9-6.1"
            />
          </div>
          <div className="metric-card-field">
            <Typography.Text type="secondary">修正原文证据</Typography.Text>
            <Input
              aria-label={`${metric.metric_name}修正原文证据`}
              disabled={disabled || decision !== 'corrected'}
              value={draft?.evidence_text || ''}
              onChange={(event) => onUpdateDraft(metric.id, 'evidence_text', event.target.value)}
              placeholder="必须包含修正值和参考范围"
            />
          </div>
        </div>
      ) : null}
    </Card>
  );
}

export function SourceEvidence({ reportId, reportToken, metric, file }) {
  const [sourceUrl, setSourceUrl] = useState('');
  const [sourceError, setSourceError] = useState('');
  // 位置只有一个适配器：两种词汇（指标行用 `page_number`、来源观测用
  // `source_page`）→ 一个对象。这里**不兜底到第 1 页** —— 定位缺失时如实说
  // 「无原文定位」，而不是把患者送到错误的页（词条的「不猜测」）。
  const page = metric?.page_number ?? metric?.source_page ?? null;
  const fileIndex = metric?.source_file_index;
  useEffect(() => {
    if (!metric) return undefined;
    let active = true;
    let objectUrl = '';
    setSourceUrl('');
    setSourceError('');
    if (page === null || fileIndex === undefined) {
      // 定位为空 —— 不请求，也不猜一页。
      return undefined;
    }
    fetchReportPage(reportId, fileIndex, page, reportToken)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setSourceUrl(objectUrl);
      })
      .catch((err) => {
        if (active) setSourceError(err.message);
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [reportId, reportToken, fileIndex, page, metric]);
  if (!metric) return null;
  if (page === null || fileIndex === undefined) {
    return (
      <div>
        <Alert
          type="info"
          showIcon
          title="无原文定位"
          description="这条指标没有可用的原文位置（文件编号或页码缺失），无法定位到报告页面。"
        />
        <Typography.Paragraph copyable style={{ marginTop: 12 }}>
          {metric.evidence_text || '未提取到原文片段'}
        </Typography.Paragraph>
      </div>
    );
  }
  const box = metric.bbox_normalized;
  const hasBox = Array.isArray(box) && box.length === 4;
  const highlight = hasBox ? {
    left: `${box[0] / 10}%`,
    top: `${box[1] / 10}%`,
    width: `${Math.max(0, box[2] - box[0]) / 10}%`,
    height: `${Math.max(0, box[3] - box[1]) / 10}%`,
  } : null;
  return (
    <div>
      <Typography.Paragraph>
        <Typography.Text strong>{file?.original_filename || `文件 #${metric.source_file_index}`}</Typography.Text>
        {` · 第 ${page} 页`}
      </Typography.Paragraph>
      {sourceError && <Alert type="error" showIcon title={sourceError} />}
      <div style={{ position: 'relative', width: '100%', maxWidth: 900, margin: '0 auto' }}>
        {sourceUrl && <img src={sourceUrl} alt={`报告原文第 ${page} 页`} style={{ width: '100%', display: 'block' }} />}
        {highlight && (
          <div
            aria-label="指标原文位置"
            style={{ position: 'absolute', border: '3px solid #cf1322', background: 'rgba(255, 77, 79, 0.16)', pointerEvents: 'none', ...highlight }}
          />
        )}
      </div>
      {!hasBox && (
        <Typography.Paragraph type="secondary">
          这条指标没有位置坐标，只能定位到本页，页面上没有需要高亮的区域。
        </Typography.Paragraph>
      )}
      <Typography.Paragraph copyable style={{ marginTop: 12 }}>
        {metric.evidence_text || '未提取到原文片段'}
      </Typography.Paragraph>
    </div>
  );
}

function evidenceStatus(status) {
  return <Tag color={reportStatusColor(status)}>{reportStatusLabel(status)}</Tag>;
}

function evidenceAlertType(hasFindings, hasUnmatched) {
  if (hasFindings) return 'success';
  if (hasUnmatched) return 'warning';
  return 'info';
}


/** 指标旁那一枚橙色标签要说的话，或 `null`（这一行没有什么要说的）。
 *
 * 两个来源，都是服务端的名字：已有准入结论时用它；还没评估时（确认页）用
 * `valueReasonOrNull` —— 那时唯一确定的事实就是「值解析不出一个数」，而它同样是
 * `admission.parse_reference_range` / `value_reason` 那条路上的一个名字。
 *
 * 「在参考区间内」不在此列：它是正常。
 */
export function chipReason(metric) {
  const reason = metric?.admission_reason;
  if (reason && notableAdmission(reason)) return reason;
  return valueReasonOrNull(metric);
}

/** 报告级台账 → 患者能读懂的分行说明。
 *
 * 台账是服务端算好的四类行数，所以这些话与卡片上方的摘要**同源**：摘要说「均在参考
 * 区间内」，这里就不会同时说「N 项未进入匹配」（那是两侧口径分叉的旧症状）。
 * 只列真正需要患者知道的两类（未进入解读、尚未核对/已排除）；`included` 不说。
 */
export function admissionLines(ledger) {
  if (!ledger) return [];
  const lines = [];
  // `skipped` 这一桶在服务端**已经排除了**「在参考区间内」（那一桶是 `normal`），
  // 所以这句话不会把正常指标算进去 —— 它与卡片上方的摘要同源。
  if (ledger.skipped > 0 || ledger.unmatched > 0) {
    lines.push({
      key: 'not-read',
      text: `有 ${ledger.skipped + ledger.unmatched} 项指标未进入解读，具体原因见各指标旁。`,
    });
  }
  if (ledger.not_evaluated > 0) {
    lines.push({
      key: 'not-evaluated',
      text: `另有 ${ledger.not_evaluated} 项尚未核对或已排除，未参与解读。`,
    });
  }
  return lines;
}

function EvidenceSummaryCard({ result, admissionLedger = null, onOpenSource }) {
  if (!result) return null;
  // 服务端从 #159 起只给**一个**投影（PatientNotices）：`findings` 就是患者可见
  // 集合，`summary` / `title` / `unmatched_count` 都在顶层。前端不再 join 两个
  // 数组、不再走回退链、不再用已废弃的 v2 字段现场合成证据项。
  const findings = Array.isArray(result.findings) ? result.findings : [];
  const unmatched = Array.isArray(result.unmatched) ? result.unmatched : [];
  const skipped = Array.isArray(result.skipped) ? result.skipped : [];
  const urgencyLabels = { routine: '常规', soon: '近期', urgent: '紧急', emergency: '危急' };
  const urgencyColors = { routine: 'blue', soon: 'orange', urgent: 'red', emergency: 'magenta' };
  const strengthLabels = { high: '高', moderate: '中等', low: '低', very_low: '极低', mixed: '各指标分别评级' };
  const summary = result.summary;
  return (
    <Card
      className="evidence-result-card"
      title={result.title}
      style={{ marginTop: 16 }}
    >
      {summary && (
        <Alert
          type={evidenceAlertType(findings.length > 0, unmatched.length > 0)}
          showIcon
          title={summary}
          style={{ marginBottom: 16 }}
        />
      )}
      {findings.length > 0 && (
        <List
          dataSource={findings}
          renderItem={(finding) => {
            // 服务端已给出证据项；不再现场合成，也不再自己数异常指标个数。
            //
            // 例外：证据服务返回的**旧版单卡**（v2）没有来源观测，投影里保留的是
            // v2 字段（`card_id` / `patient_visible_body` / `sources`）—— 那是
            // 患者原本就能看到的已审核内容，不能因为形状收敛就消失。这里按 v2
            // 卡片渲染，不做任何"合成 v3 形状"的推导。
            const evidenceItems = Array.isArray(finding.evidence_items) ? finding.evidence_items : [];
            const legacySources = Array.isArray(finding.sources) ? finding.sources : [];
            const observationCount = new Set(evidenceItems.flatMap((item) => item.source_observation_ids || [])).size;
            return (
              <List.Item>
                <div style={{ width: '100%' }}>
                  <Space wrap>
                    <Typography.Text strong>可能相关健康问题：{finding.condition_name || finding.condition_code}</Typography.Text>
                    <Tag color="gold">{observationCount || evidenceItems.length} 个异常指标</Tag>
                    <Tag>证据：{strengthLabels[finding.evidence_strength] || finding.evidence_strength || '—'}</Tag>
                    {finding.urgency && (
                      <Tag color={urgencyColors[finding.urgency] || 'blue'}>
                        紧急程度：{urgencyLabels[finding.urgency] || finding.urgency}
                      </Tag>
                    )}
                    <Tag>{finding.department || '建议就诊科室未记录'}</Tag>
                  </Space>
                  {finding.needs_recheck && (
                    <Typography.Paragraph type="secondary" style={{ margin: '8px 0' }}>
                      建议复查{finding.recheck_direction ? `：${finding.recheck_direction}` : ''}
                    </Typography.Paragraph>
                  )}
                  {evidenceItems.length === 0 && legacySources.length > 0 && (
                    <List
                      size="small"
                      header={<Typography.Text strong>审核证据（旧版知识卡）</Typography.Text>}
                      dataSource={legacySources}
                      renderItem={(source) => (
                        <List.Item>
                          <Typography.Text>
                            {source.paper_title}
                            {source.doi ? ` · DOI ${source.doi}` : ''}
                          </Typography.Text>
                        </List.Item>
                      )}
                    />
                  )}
                  {evidenceItems.length > 0 && (
                    <List
                      size="small"
                      header={<Typography.Text strong>异常指标与审核证据</Typography.Text>}
                      dataSource={evidenceItems}
                      renderItem={(item) => {
                        const card = item.card || {};
                        const sourceObservations = Array.isArray(item.source_observations) ? item.source_observations : [];
                        const sources = Array.isArray(card.sources) ? card.sources : [];
                        return (
                          <List.Item>
                            <div style={{ width: '100%' }}>
                              <Space wrap>
                                <Typography.Text strong>{item.metric_label || item.metric_code}</Typography.Text>
                                <Tag>证据强度：{strengthLabels[item.evidence_strength || card.grade] || item.evidence_strength || card.grade || '—'}</Tag>
                                <Tag color="green">知识卡 {card.id ? `${card.id} · ` : ''}v{card.version || '—'}</Tag>
                              </Space>
                              {sourceObservations.map((source) => (
                                <div key={source.observation_id} style={{ marginTop: 8 }}>
                                  <Space align="start">
                                    <Tooltip title="查看报告原文定位">
                                      <Button
                                        type="text"
                                        icon={<EyeOutlined />}
                                        aria-label={`查看${item.metric_label || item.metric_code || '指标'}报告原文`}
                                        onClick={() => onOpenSource?.(source)}
                                      />
                                    </Tooltip>
                                    <Typography.Text>
                                      {source.value} {source.unit}
                                      {source.reference_high !== null && source.reference_high !== undefined ? `（参考上限 ${source.reference_high}）` : ''}
                                      {source.reference_low !== null && source.reference_low !== undefined ? `（参考下限 ${source.reference_low}）` : ''}
                                      {` · 文件 #${source.source_file_index} · 第 ${source.source_page} 页`}
                                      <br />
                                      <Typography.Text type="secondary">{source.evidence_text || '未记录原文'}</Typography.Text>
                                    </Typography.Text>
                                  </Space>
                                </div>
                              ))}
                              {card.patient_visible_body && (
                                <Typography.Paragraph style={{ margin: '10px 0 4px' }}>
                                  {card.patient_visible_body}
                                </Typography.Paragraph>
                              )}
                              {sources.length > 0 && (
                                <List
                                  size="small"
                                  header="论文与 Claim 来源"
                                  dataSource={sources}
                                  renderItem={(source) => (
                                    <List.Item>
                                      <Typography.Text>
                                        {source.paper_title || source.paper_id || '未命名论文'}
                                        {source.doi && (
                                          <>（<Typography.Link href={`https://doi.org/${encodeURIComponent(source.doi)}`} target="_blank" rel="noreferrer">{source.doi}</Typography.Link>）</>
                                        )}
                                        {' · '}{source.claim_id || '未命名 Claim'}
                                        {source.locator ? ` · ${source.locator}` : ''}
                                      </Typography.Text>
                                    </List.Item>
                                  )}
                                />
                              )}
                            </div>
                          </List.Item>
                        );
                      }}
                    />
                  )}
                </div>
              </List.Item>
            );
          }}
        />
      )}
      {unmatched.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Alert
            type="warning"
            showIcon
            title={`有 ${unmatched.length} 条指标与健康问题关联暂未匹配到已发布知识卡`}
            description="这些关联保留了原始报告证据，但不会由模型补写结论。"
          />
          <List
            size="small"
            dataSource={unmatched}
            renderItem={(item) => {
              const source = item.source_observation;
              return (
                <List.Item
                  actions={source ? [
                    <Tooltip key="source" title="查看报告原文定位">
                      <Button
                        type="text"
                        icon={<EyeOutlined />}
                        aria-label={`查看${item.metric_label || '异常指标'}原文`}
                        // 三种入口喂**同一个形状**：观测形状本身（`source_page`）
                        // 就够 —— `SourceEvidence` 的适配器认识两种词汇，
                        // 这里不需要再手工改写一遍。
                        onClick={() => onOpenSource?.({
                          ...source,
                          metric_name: item.metric_label,
                        })}
                      />
                    </Tooltip>,
                  ] : undefined}
                >
                  <Typography.Text>
                    {item.metric_label || '未命名指标'}：{source?.value ?? '—'} {source?.unit || ''}
                    {source?.reference_high !== null && source?.reference_high !== undefined
                      ? `（参考上限 ${source.reference_high}）`
                      : ''}
                    {source?.reference_low !== null && source?.reference_low !== undefined
                      ? `（参考下限 ${source.reference_low}）`
                      : ''}
                    {Array.isArray(item.condition_names) && item.condition_names.length
                      ? ` · 可能相关：${item.condition_names.join('、')}`
                      : ''}
                    {' · 暂无已审核内容'}
                  </Typography.Text>
                </List.Item>
              );
            }}
          />
        </div>
      )}
      {result.disclaimer && (
        <Typography.Paragraph type="secondary" style={{ margin: '12px 0 0' }}>
          {result.disclaimer}
        </Typography.Paragraph>
      )}
      {/* 逐条按**原因**说，不再把七个原因压成四个词、也不再把它与「正常」混在一起。
          这里读的是报告级台账（服务端算好的四类行数），不是 `skipped` 数组的长度：
          「尚未核对」「患者已排除」两类不在 `skipped` 里，而它们同样需要一句说明。 */}
      {admissionLines(admissionLedger).length > 0 && (
        <div style={{ margin: '12px 0 0' }}>
          {admissionLines(admissionLedger).map((line) => (
            <Typography.Paragraph key={line.key} type="secondary" style={{ margin: 0 }}>
              {line.text}
            </Typography.Paragraph>
          ))}
        </div>
      )}
    </Card>
  );
}

function initialDrafts(metrics) {
  return Object.fromEntries((metrics || []).map((metric) => [
    metric.id,
    {
      // **不预填 `decision`**：它记录的是「患者动过这一行」，而预填会让每个草稿看起来
      // 都像患者选过。界面要显示的初始值由 `suggestedDecision` 现算（见卡片与表格的
      // `value`），提交载荷只认这里真正存在的值。
      metric_code: metric.metric_code || '',
      // 生效值：pending 指标就是模型值（临时生效值），已确认/已修正的指标是
      // 患者上次核对过的值 —— 重入时输入框预填它，不用从零重输二十项。
      value: metric.effective_value || '',
      unit: metric.effective_unit || '',
      reference_range: metric.effective_reference_range || '',
      evidence_text: metric.effective_evidence_text || '',
    },
  ]));
}

// 主体一致性的两个派生问题。它们与服务端 `app/service/report_subject.py` 的
// `can_enter_reading` / `is_stop_declaration` 一一对应 —— 前端只做映射，不重新
// 发明语义（此前这两处在两个组件里各推导一遍）。
function needsSubjectDeclaration(result) {
  return result?.status === 'pending_confirmation' && result?.subject_consistency !== 'same';
}

function isSubjectStop(declared) {
  return Boolean(declared) && declared !== 'same';
}

// 「停止」的两种成因说法不同 —— `uncertain` 是「你没能确认」，不是「他们不是同一
// 个人」。把两者说成一回事是替患者下结论（评审在 #154 指出）。
function subjectStopMessage(declared) {
  return declared === 'different'
    ? '这批文件不属于同一主体，不能合并解读；请返回上传页分开上传。'
    : '无法确认这批文件属于同一主体，不能合并解读；请返回上传页分开上传。';
}

export function TechnicalDetails({ result, subjectConsistency, onSubjectConsistencyChange }) {
  const trace = result.extraction_trace;
  const subjectNeedsConfirmation = needsSubjectDeclaration(result);
  return (
    <Descriptions column={{ xs: 1, sm: 2, md: 2 }} size="small" bordered>
      <Descriptions.Item label="文件主体一致性">
        {subjectNeedsConfirmation ? (
          <Select
            aria-label="确认文件属于同一主体"
            value={subjectConsistency || undefined}
            placeholder="请选择"
            options={[
              { label: '同一主体，继续', value: 'same' },
              { label: '不同主体，停止', value: 'different' },
              { label: '无法确认，停止', value: 'uncertain' },
            ]}
            onChange={(value) => {
              onSubjectConsistencyChange(value);
              if (isSubjectStop(value)) message.warning(subjectStopMessage(value));
            }}
            style={{ width: '100%', maxWidth: 220 }}
          />
        ) : (result.subject_consistency || '—')}
      </Descriptions.Item>
      <Descriptions.Item label="抽取模型">
        {trace?.extraction_model || '—'}
      </Descriptions.Item>
      <Descriptions.Item label="抽取运行 ID">
        {trace?.extraction_run_id || '—'}
      </Descriptions.Item>
      <Descriptions.Item label="Prompt 版本">
        {trace?.extraction_prompt_version || '—'}
      </Descriptions.Item>
    </Descriptions>
  );
}

// 报告一旦评估完成（assessed），推荐就应当出现——它与报告详情页共用同一组渲染。
// 上传完停在本页时，此前只挂了摘要，推荐只在报告详情挂载，于是「看不看得到商品」
// 取决于用户走的是哪个入口，而不是取决于数据。这里让 assessed 的结果同时渲染
// EvidenceSummaryCard 与 Recommendations，与报告详情保持一致。
//
// reportId 与 assessed 由调用方传入：本组件的 result 是 evidence_result 本身，
// 它既没有 status 也没有 id，不能从里面推断「报告是否已评估」。
export function EvidenceResult({ result, admissionLedger = null, onOpenSource, reportId = null, assessed = false }) {
  if (!result) return null;
  return (
    <>
      <EvidenceSummaryCard result={result} admissionLedger={admissionLedger} onOpenSource={onOpenSource} />
      {assessed && reportId ? <Recommendations reportId={reportId} /> : null}
    </>
  );
}

export default function UploadPage({ account, initialReportId = null, onReportSaved }) {
  const [form] = Form.useForm();
  const uploadZoneRef = useRef(null);
  const pasteEditableRef = useRef(null);
  const pasteSequenceRef = useRef(0);
  const longPressTimerRef = useRef(null);
  const longPressStartRef = useRef(null);
  const longPressTriggeredRef = useRef(false);
  const [fileList, setFileList] = useState([]);
  const [pasteGuideVisible, setPasteGuideVisible] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [subjectConsistency, setSubjectConsistency] = useState('');
  const [error, setError] = useState('');
  const [showAllMetrics, setShowAllMetrics] = useState(false);
  const [sourceMetric, setSourceMetric] = useState(null);
  const [reportToken, setReportToken] = useState('');
  const [metricCatalog, setMetricCatalog] = useState([]);
  const [metricCatalogError, setMetricCatalogError] = useState('');
  const [technicalDetailsOpen, setTechnicalDetailsOpen] = useState(false);
  // 受理规则由服务端下发（`app/service/upload_policy.py`）。起点是内建默认值 ——
  // 一次请求失败不该挡住上传；拿到之后一律以服务端那份为准。
  const [uploadPolicy, setUploadPolicy] = useState(() => normalizeUploadPolicy(null));
  const isNarrow = useNarrowViewport();

  useEffect(() => {
    let active = true;
    loadUploadPolicy().then((policy) => {
      if (active) setUploadPolicy(policy);
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    getMetricCatalog()
      .then((catalog) => {
        if (active) setMetricCatalog(catalog.map(({ code, label }) => ({ value: code, label: `${label} · ${code}` })));
      })
      .catch((err) => {
        if (active) setMetricCatalogError(err.message);
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!initialReportId) return;
    let active = true;
    setError('');
    getReport(initialReportId)
      .then((data) => {
        if (!active) return;
        setResult(data);
        setReportToken('');
        setDrafts(initialDrafts(data.metrics || []));
        setSubjectConsistency(data.subject_consistency === 'same' ? 'same' : '');
      })
      .catch((err) => {
        if (active) setError(err.message);
      });
    return () => { active = false; };
  }, [initialReportId]);

  const subjectGateOpen = needsSubjectDeclaration(result);
  useEffect(() => {
    if (subjectGateOpen) setTechnicalDetailsOpen(true);
  }, [subjectGateOpen]);

  const updateDraft = (id, field, value) => {
    setDrafts((current) => ({ ...current, [id]: { ...current[id], [field]: value } }));
  };

  const openUploadSelector = () => {
    const input = uploadZoneRef.current?.querySelector?.('input[type="file"]');
    if (input) input.click();
  };

  const focusPasteEditable = () => {
    const editable = pasteEditableRef.current;
    if (!editable) return;
    if (!editable.textContent) {
      editable.textContent = PASTE_EDITABLE_PLACEHOLDER;
    }
    try {
      editable.focus({ preventScroll: true });
    } catch {
      editable.focus();
    }
    const selection = window.getSelection();
    if (selection) {
      const range = document.createRange();
      range.selectNodeContents(editable);
      range.collapse(false);
      selection.removeAllRanges();
      selection.addRange(range);
    }
    setPasteGuideVisible(true);
  };

  const clearLongPressTimer = () => {
    if (longPressTimerRef.current) {
      window.clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }
  };

  const beginLongPress = (event) => {
    if (typeof event.button === 'number' && event.button !== 0) return;
    longPressTriggeredRef.current = false;
    longPressStartRef.current = { x: event.clientX, y: event.clientY };
    clearLongPressTimer();
    longPressTimerRef.current = window.setTimeout(() => {
      longPressTriggeredRef.current = true;
      longPressStartRef.current = null;
      focusPasteEditable();
    }, PASTE_LONG_PRESS_MS);
  };

  const cancelLongPress = () => {
    clearLongPressTimer();
    longPressStartRef.current = null;
  };

  const moveLongPress = (event) => {
    if (!longPressStartRef.current) return;
    const { x, y } = longPressStartRef.current;
    const moved = Math.hypot(event.clientX - x, event.clientY - y);
    if (moved > PASTE_LONG_PRESS_MOVE_TOLERANCE) {
      cancelLongPress();
    }
  };

  const guardLongPressClick = (event) => {
    if (!longPressTriggeredRef.current) return;
    longPressTriggeredRef.current = false;
    event.preventDefault();
    event.stopPropagation();
  };

  const handleContextMenu = (event) => {
    clearLongPressTimer();
    longPressTriggeredRef.current = true;
    focusPasteEditable();
  };

  const tooManyFilesMessage = `最多上传 ${uploadPolicy.max_files} 个文件`;

  const appendPastedImages = (pastedImages) => {
    const remaining = Math.max(0, uploadPolicy.max_files - fileList.length);
    if (remaining === 0) {
      message.warning(tooManyFilesMessage);
      return;
    }
    const nextFiles = pastedImages.slice(0, remaining).map((image) => {
      pasteSequenceRef.current += 1;
      const name = pastedFileName(image, { sequence: pasteSequenceRef.current });
      const renamed = new File([image.file], name, { type: image.mime || image.file.type });
      return {
        uid: `paste-${Date.now()}-${pasteSequenceRef.current}`,
        name,
        type: renamed.type,
        size: renamed.size,
        status: 'done',
        originFileObj: renamed,
      };
    });
    if (pastedImages.length > remaining) {
      message.warning(tooManyFilesMessage);
    }
    setFileList((current) => [...current, ...nextFiles]);
  };

  const handlePaste = (event) => {
    const target = event.target;
    const isPasteEditable = target === pasteEditableRef.current;
    if (
      target instanceof HTMLElement
      && !isPasteEditable
      && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
    ) {
      return;
    }
    if (isPasteEditable) {
      event.preventDefault();
      if (pasteEditableRef.current) {
        pasteEditableRef.current.textContent = PASTE_EDITABLE_PLACEHOLDER;
      }
    }
    const pasted = readPastedImages(event, uploadPolicy.accepted_extensions);
    if (pasted.status === 'unsupported') {
      message.warning(unsupportedPasteMessage(pasted.unsupported, uploadPolicy.accepted_extensions));
      return;
    }
    if (pasted.status === 'unavailable') {
      message.warning('当前环境不支持粘贴，请选择文件');
      openUploadSelector();
      return;
    }
    if (pasted.status === 'empty' || pasted.images.length === 0) {
      message.info('剪贴板中没有可粘贴的图片');
      return;
    }
    appendPastedImages(pasted.images);
  };

  const handleUpload = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    if (fileList.length === 0) {
      message.warning('请先选择要上传的报告文件');
      return;
    }
    setUploading(true);
    setError('');
    setResult(null);
    try {
      const formData = new FormData();
      fileList.forEach((item) => formData.append('files', item.originFileObj || item));
      if (values.report_type) formData.append('report_type', values.report_type);
      if (values.department && values.department.trim()) formData.append('department', values.department.trim());
      let data = await uploadReport(formData);
      const accessToken = data.access_token || '';
      setReportToken(accessToken);
      setResult(data);
      onReportSaved?.();
      message.info('文件已上传，正在后台解析');
      // 轮询读服务端已知的抽取任务状态,而不是盲等。
      //
      // 此前是 `while status === 'processing'` × 2 秒 × 300 次（最长十分钟），
      // 超时后只说「报告仍在后台解析」—— 而 API 早就返回了 `extraction_job`，
      // 里面就有队列生命周期。任务失败时立刻停并说明原因，任务重试时把次数
      // 告诉患者，别让他们对着一个不动的时间轴干等。
      //
      // 660 次（22 分钟）是**轮询的上限，不是完成的保证**：
      // `REPORT_JOB_STALE_SECONDS` 衡量的是运行中任务的不活跃时长，排队延迟与
      // 多轮重试都能超过它。取这个量级是为了覆盖一次典型的回收周期，而不是
      // 断言报告一定会在窗口内跑完。
      //
      // 超时后可恢复：状态由服务端单点拥有，患者刷新或从历史列表重进会读到真实
      // 状态，不会永远卡在「解析中」。后台续轮询（页面级轮询直到完成）属于交互
      // 设计改动，不在本次状态收敛的范围。
      const POLL_INTERVAL_MS = 2000;
      const MAX_ATTEMPTS = 660;
      for (let attempt = 0; data.status === 'processing' && attempt < MAX_ATTEMPTS; attempt += 1) {
        const job = data.extraction_job;
        if (job?.status === 'failed') {
          throw new Error(data.processing_error || '报告智能解读失败，请重试');
        }
        await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS));
        data = await getReport(data.id, accessToken);
        setResult(data);
      }
      if (data.status === 'failed') throw new Error(data.processing_error || '报告智能解读失败，请重试');
      if (data.status === 'processing') {
        const attempts = data.extraction_job?.attempt_count;
        message.warning(
          attempts
            ? `报告仍在后台解析（第 ${attempts} 次尝试），请保持当前页面并稍后重试`
            : '报告仍在后台解析，请保持当前页面并稍后重试',
        );
        return;
      }
      setDrafts(initialDrafts(data.metrics));
      setSubjectConsistency(data.subject_consistency === 'same' ? 'same' : '');
      message.success(`已解析 ${fileList.length} 个文件，请批量确认指标`);
    } catch (err) {
      setError(err.message);
      message.error(err.message);
    } finally {
      setUploading(false);
    }
  };

  const handleConfirm = async () => {
    if (!result) return;
    if (Array.isArray(result.processing_warnings) && result.processing_warnings.length > 0) {
      message.warning('仍有文件未完成解析，不能生成健康提示');
      return;
    }
    if (isSubjectStop(subjectConsistency)) {
      // 「停止」不是一次失败的提交，而是一个明确的结论：这批文件不该合并解读。
      message.warning(subjectStopMessage(subjectConsistency));
      return;
    }
    if (result.subject_consistency !== 'same' && subjectConsistency !== 'same') {
      message.warning('请先确认所有文件属于同一主体');
      return;
    }
    // 闸门问的是「**显示给他看过的**行里，还有没有他没处理的」。
    //
    // 取值规则与载荷**同一条**（含 `serverDecision`）：患者上次已经排除过的行（重入确认）
    // 不该被要求再表态一次 —— #203 的实测现场里，报告 54 的 19 行就是这么被拦下的。
    //
    // `needsReview` 这个条件今天是**冗余**的（`suggestedDecision` 只在判成 H/L 或待核对时
    // 返回 `pending`，而那些行 `needsReview` 必然为真），但它把「闸门只问显示过的行」这句
    // 话写进了代码：以后有人给 `suggestedDecision` 加一条返回 `pending` 的判据时，闸门不会
    // 跟着去拦一个页面上根本没有的行。
    const decisionOf = (metric) => drafts[metric.id]?.decision
      || serverDecision(metric)
      || (needsReview(metric) ? suggestedDecision(metric) : 'pending');
    const unresolved = (result.metrics || []).filter(
      (metric) => needsReview(metric) && decisionOf(metric) === 'pending',
    );
    if (unresolved.length > 0) {
      // 两个值的行有它自己的一句：闸门拦下它是因为「用哪个数还没选」，而那句泛泛的
      // 「需要确认、修正或排除」说不清他要做什么（#204）。
      const dual = unresolved.filter((metric) => dualValueChoice(metric)).length;
      if (dual > 0) {
        message.warning(`还有 ${dual} 项页面上有两个值，请选择用哪一个`);
        return;
      }
      message.warning(`还有 ${unresolved.length} 个异常候选项需要确认、修正或排除`);
      return;
    }
    // 显式选了「确认」但值仍是多值/带符号的行：接受它就等于接受一次**静默丢弃**。
    // 后端要求恰好一个数，这种值它会连行一起丢掉（reason=invalid_value），
    // 而用户会以为已经确认过了。挡在这里，明确要求修正或排除。
    // 这一处**不能**只看准入结论：走到这里的是确认动作，而那时报告还没评估
    // （准入结论全是 `null`）。#129 要保住的正是这个时机 —— 患者把一个多值行显式选了
    // 「确认」，而后端会连行丢掉。所以这里用同一条不依赖评估的判据。
    const unusable = (result.metrics || []).filter(
      (metric) => decisionOf(metric) === 'confirmed' && valueNotParsed(metric),
    );
    if (unusable.length > 0) {
      // 按**原因**分组说，不再把四种事实压成一句「数值无法识别为单个数字」——
      // 其中「缺少参考范围」的值本身完全正常，让人去修正它是把人引向错误的动作。
      const detail = admissionGroups(unusable)
        .map((group) => `${group.count} 项${group.text}`)
        .join('；');
      message.warning(`有 ${unusable.length} 项未进入解读（${detail}）；请「修正」或「排除」。`);
      return;
    }
    setConfirming(true);
    setError('');
    // ── 提交载荷：**只看患者看到过的行** ────────────────────────────────────
    //
    // 三条来源，优先级从高到低：
    //   - 患者**动过**它（`draft.decision` 已设）→ 用他的选择；
    //   - 服务端**已经给过**决策（`confirmation_status` 已落定，重入确认时）→ 沿用那个；
    //   - 以上都没有，但这一行**界面上显示给他看过**（`needsReview`：红色 H/L、待核对）→
    //     用界面上那个初选。他没改，就是他的答案 —— 那一条**确实被呈现过**。
    //
    // 最后一条与「客户端替患者猜一个默认」的差别全在 `needsReview` 上：**隐藏**的正常行
    // 拿不到那个初选，它们如实说「我没动过这一行」。默认值曾经冒充患者表态的那次事故
    // （#195）正是把**没显示过的**行也算成了他的表态 —— 报告 54/55 的 LDL-C / Non-HDL /
    // Total Chol 就是这么从解读里消失的。
    //
    // `needsReview` 今天是**冗余**的：`suggestedDecision` 只在判成 H/L 或待核对时返回
    // `pending`，而那些行 `needsReview` 必然为真，所以两条路对今天每一种行形态给出同一
    // 个答案。留着它是因为它写的正是那条规则（只有显示过的行才用初选）—— 以后有人给
    // `suggestedDecision` 加一条返回 `pending` 的判据时，载荷不会跟着去替一个页面上没有的
    // 行表态。这一条由 `tests/test_frontend_suggested_decision.py` 的守卫钉住。
    //
    // 兜底是 `pending`（「这条我还没动」），由服务端解：已落定过的沿用上次的决定，没落定
    // 过的仍是未决（见 `confirmation_decision.request_decision`）。
    const observations = (result.metrics || []).map((metric) => {
      const draft = drafts[metric.id] || {};
      const selectedCode = draft.metric_code || metric.metric_code;
      const decided = draft.decision
        || serverDecision(metric)
        || (needsReview(metric) ? suggestedDecision(metric) : 'pending');
      const item = {
        metric_id: metric.id,
        decision: decided,
        metric_code: selectedCode || undefined,
      };
      if (decided === 'corrected') {
        item.value = draft.value;
        item.unit = draft.unit;
        item.reference_range = draft.reference_range || undefined;
        item.evidence_text = draft.evidence_text || undefined;
      }
      return item;
    });
    try {
      const data = await confirmReport(result.id, reportToken, { observations, subject_consistency: subjectConsistency || 'same' });
      setResult(data);
      message.success(data.status === 'assessed' ? '已生成健康风险提示' : '指标已确认，可重试生成健康提示');
    } catch (err) {
      const saved = await getReport(result.id, reportToken).catch(() => null);
      if (saved) setResult(saved);
      setError(err.message);
      message.warning(saved?.status === 'confirmed' ? '确认已保存，但证据服务暂不可用，请重试' : err.message);
    } finally {
      setConfirming(false);
    }
  };

  const handleAssess = async () => {
    if (!result) return;
    setConfirming(true);
    setError('');
    try {
      const data = await assessReport(result.id, reportToken);
      setResult(data);
      message.success('已重新生成健康风险提示');
    } catch (err) {
      setError(err.message);
      message.error(err.message);
    } finally {
      setConfirming(false);
    }
  };

  const metricColumns = useMemo(() => [
    { title: '文件', dataIndex: 'source_file_index', width: 60, render: (v) => `#${v}` },
    { title: '指标', dataIndex: 'metric_name', width: 140 },
    // 与移动端卡片、报告单显示同一份值：确认过/修正过的指标显示生效值，
    // 否则显示模型值（那就是它的临时生效值）。
    {
      title: '结果', key: 'effective_value', width: 90,
      render: (_, record) => record.effective_value || record.metric_value || '—',
    },
    {
      title: '单位', key: 'effective_unit', width: 80,
      render: (_, record) => record.effective_unit || record.unit || '—',
    },
    {
      title: '参考范围', key: 'effective_reference_range', width: 110,
      render: (_, record) => record.effective_reference_range || record.reference_range || '—',
    },
    {
      title: '异常', key: 'abnormal_flag', width: 90,
      render: (_, record) => (
        <Space size={4} wrap>
          {abnormalTag(displayFlag(record))}
          {/* 表形态下同样要标出来——这是桌面端默认视图 */}
          {chipReason(record) && (
            <Tag color="orange">{admissionText(chipReason(record))}</Tag>
          )}
        </Space>
      ),
    },
    {
      title: '证据原文', key: 'effective_evidence_text', width: 220, ellipsis: true,
      render: (_, record) => record.effective_evidence_text || record.evidence_text || '—',
    },
    {
      // 桌面形态同样把「定位缺失」与「没做定位」分开：按钮照常在，弹窗如实说明。
      title: '原文', key: 'source', width: 62,
      render: (_, record) => (
        <Tooltip title="查看原文定位">
          <Button type="text" icon={<EyeOutlined />} aria-label={`查看${record.metric_name}原文`} onClick={() => setSourceMetric(record)} />
        </Tooltip>
      ),
    },
    {
      title: '标准指标', key: 'metric_code', width: 210,
      render: (_, record) => (
        <Select
          aria-label={`${record.metric_name}标准指标编码`}
          value={drafts[record.id]?.metric_code || undefined}
          options={metricCatalog}
          showSearch
          optionFilterProp="label"
          allowClear
          disabled={result.status !== 'pending_confirmation'}
          onChange={(value) => updateDraft(record.id, 'metric_code', value || '')}
          placeholder="选择标准指标"
          style={{ width: 195 }}
        />
      ),
    },
    {
      title: '处理', key: 'decision', width: 100, fixed: 'right',
      render: (_, record) => (
        <Select
          aria-label={`${record.metric_name}处理方式`}
          value={drafts[record.id]?.decision || suggestedDecision(record)}
          options={DECISIONS}
          disabled={result.status !== 'pending_confirmation'}
          onChange={(value) => updateDraft(record.id, 'decision', value)}
          style={{ width: 88 }}
        />
      ),
    },
    {
      // 两个来源不同的数值（#204）：桌面端与卡片端同一件事 —— 患者**选一个**，
      // 而不是重输一遍数字。只在页面上恰好两个数时出现。
      title: '两个值', key: 'dual_value', width: 130,
      render: (_, record) => {
        const candidates = dualValueChoice(record);
        if (!candidates) return null;
        return (
          <Radio.Group
            aria-label={`${record.metric_name}取值`}
            value={drafts[record.id]?.decision === 'corrected' ? String(drafts[record.id]?.value ?? '') : undefined}
            disabled={result.status !== 'pending_confirmation'}
            onChange={(event) => {
              const chosen = event.target.value;
              updateDraft(record.id, 'decision', 'corrected');
              updateDraft(record.id, 'value', chosen);
              updateDraft(record.id, 'unit', drafts[record.id]?.unit || record.effective_unit || record.unit || '');
              updateDraft(
                record.id,
                'reference_range',
                drafts[record.id]?.reference_range || record.effective_reference_range || record.reference_range || '',
              );
              updateDraft(
                record.id,
                'evidence_text',
                drafts[record.id]?.evidence_text || record.effective_evidence_text || record.evidence_text || '',
              );
            }}
          >
            {candidates.map((candidate) => (
              <Radio.Button key={candidate} value={candidate} aria-label={`${record.metric_name}用 ${candidate}`}>
                {candidate}
              </Radio.Button>
            ))}
          </Radio.Group>
        );
      },
    },
    {
      title: '修正值', key: 'corrected_value', width: 105,
      render: (_, record) => (
        <Input
          aria-label={`${record.metric_name}修正值`}
          disabled={result.status !== 'pending_confirmation' || drafts[record.id]?.decision !== 'corrected'}
          value={drafts[record.id]?.value || ''}
          onChange={(event) => updateDraft(record.id, 'value', event.target.value)}
          placeholder="数值"
        />
      ),
    },
    {
      title: '修正单位', key: 'corrected_unit', width: 95,
      render: (_, record) => (
        <Input
          aria-label={`${record.metric_name}修正单位`}
          disabled={result.status !== 'pending_confirmation' || drafts[record.id]?.decision !== 'corrected'}
          value={drafts[record.id]?.unit || ''}
          onChange={(event) => updateDraft(record.id, 'unit', event.target.value)}
          placeholder="单位"
        />
      ),
    },
    {
      title: '修正范围', key: 'corrected_reference', width: 120,
      render: (_, record) => (
        <Input
          aria-label={`${record.metric_name}修正参考范围`}
          disabled={result.status !== 'pending_confirmation' || drafts[record.id]?.decision !== 'corrected'}
          value={drafts[record.id]?.reference_range || ''}
          onChange={(event) => updateDraft(record.id, 'reference_range', event.target.value)}
          placeholder="如 3.9-6.1"
        />
      ),
    },
    {
      title: '修正原文证据', key: 'corrected_evidence', width: 240,
      render: (_, record) => (
        <Input
          aria-label={`${record.metric_name}修正原文证据`}
          disabled={result.status !== 'pending_confirmation' || drafts[record.id]?.decision !== 'corrected'}
          value={drafts[record.id]?.evidence_text || ''}
          onChange={(event) => updateDraft(record.id, 'evidence_text', event.target.value)}
          placeholder="必须包含修正值和参考范围"
        />
      ),
    },
  ], [drafts, metricCatalog, result?.status]);

  const visibleMetrics = useMemo(() => {
    const metrics = result?.metrics || [];
    return showAllMetrics
      ? metrics
      : metrics.filter(needsReview);
  }, [result?.metrics, showAllMetrics]);
  const abnormalCount = (result?.metrics || []).filter(needsReview).length;

  return (
    <div className="page-stack">
      <Card title="体检报告解读与健康风险提示" extra={<Typography.Text type="secondary">支持多文件，按选择顺序保留来源</Typography.Text>}>
        {metricCatalogError && (
          <Alert
            type="warning"
            showIcon
            title="标准指标目录暂不可用"
            description={
              '未加载正式目录前不会使用过期的本地指标列表。你仍然可以确认指标：'
              + '编码以服务端为准 —— 它那边目录可用就按正式目录匹配，不可用则留空保存、'
              + '等目录恢复后重新匹配。'
            }
            style={{ marginBottom: 16 }}
          />
        )}
        <Form form={form} layout="inline" style={{ rowGap: 16 }}>
          <Form.Item name="report_type" label="报告类型" initialValue="体检">
            <Select style={{ width: 120 }} options={REPORT_TYPES.map((t) => ({ label: t, value: t }))} />
          </Form.Item>
          <Form.Item name="department" label="科室">
            <Input placeholder="可选" style={{ width: 150 }} />
          </Form.Item>
        </Form>

        <div
          ref={uploadZoneRef}
          className="report-paste-zone"
          onPaste={handlePaste}
          onPointerDown={beginLongPress}
          onPointerMove={moveLongPress}
          onPointerUp={cancelLongPress}
          onPointerCancel={cancelLongPress}
          onPointerLeave={cancelLongPress}
          onContextMenu={handleContextMenu}
          onClickCapture={guardLongPressClick}
        >
          <Upload.Dragger
            style={{ marginTop: 16 }}
            accept={acceptAttribute(uploadPolicy)}
            multiple
            maxCount={uploadPolicy.max_files}
            fileList={fileList}
            beforeUpload={() => false}
            onChange={({ fileList: next }) => setFileList(next)}
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">点击或拖拽多张报告文件到此区域</p>
            <p className="ant-upload-hint">
              文件顺序会保留；最多 {uploadPolicy.max_files} 个文件，每个不超过 {describeBytes(uploadPolicy.max_file_bytes)}
            </p>
          </Upload.Dragger>
          <div className="report-paste-actions">
            <Button icon={<PictureOutlined />} onClick={focusPasteEditable}>
              粘贴图片
            </Button>
            {pasteGuideVisible && (
              <Typography.Text className="report-paste-guide" aria-live="polite">
                长按屏幕 → 粘贴
              </Typography.Text>
            )}
          </div>
          <div
            ref={pasteEditableRef}
            className="report-paste-editable"
            contentEditable
            suppressContentEditableWarning
            tabIndex={-1}
            aria-label="图片粘贴区域"
          >
            {PASTE_EDITABLE_PLACEHOLDER}
          </div>
        </div>

        <Button
          type="primary"
          icon={<ReloadOutlined />}
          loading={uploading}
          onClick={handleUpload}
          style={{ marginTop: 16 }}
        >
          上传并解析
        </Button>
      </Card>

      {error && <Alert type="error" showIcon title={error} />}

      {result && (
        <Card title={<Space>解析结果 · 报告 #{result.id} {evidenceStatus(result.status)}</Space>}>
          <Descriptions column={{ xs: 1, sm: 2, md: 4 }} size="small" bordered style={{ marginBottom: 16 }}>
            <Descriptions.Item label="账户">{result.owned_by_account ? '当前账户' : result.patient_id}</Descriptions.Item>
            <Descriptions.Item label="报告类型">{result.report_type}</Descriptions.Item>
            <Descriptions.Item label="科室">{result.department || '—'}</Descriptions.Item>
          </Descriptions>
          <Collapse
            className="technical-details"
            activeKey={technicalDetailsOpen ? ['technical-details'] : []}
            onChange={(keys) => setTechnicalDetailsOpen(keys.includes('technical-details'))}
            items={[{
              key: 'technical-details',
              label: '技术详情',
              children: (
                <TechnicalDetails
                  result={result}
                  subjectConsistency={subjectConsistency}
                  onSubjectConsistencyChange={setSubjectConsistency}
                />
              ),
            }]}
          />
          {result.status === 'pending_confirmation' && (
            <Alert
              type="info"
              showIcon
              title={`识别到 ${result.metrics?.length || 0} 项指标，其中 ${abnormalCount} 项需要确认；当前优先显示异常候选项和未映射项。`}
              style={{ marginBottom: 16 }}
            />
          )}
          {result.status === 'processing' && (
            <Alert type="info" showIcon title="报告正在后台解析，完成后将自动显示指标。" style={{ marginBottom: 16 }} />
          )}
          {Array.isArray(result.processing_warnings) && result.processing_warnings.length > 0 && (
            <Alert
              type="error"
              showIcon
              title="报告解析不完整，已阻止生成健康提示"
              description={result.processing_warnings.join('；')}
              style={{ marginBottom: 16 }}
            />
          )}
          {isNarrow ? (
            <div className="metric-card-list" aria-label="指标确认卡片列表">
              {visibleMetrics.length === 0 ? (
                <Typography.Text type="secondary">
                  {result.status === 'processing' ? '正在解析' : '未解析出指标'}
                </Typography.Text>
              ) : visibleMetrics.map((metric) => (
                <MetricCard
                  key={metric.id}
                  metric={metric}
                  draft={drafts[metric.id]}
                  metricCatalog={metricCatalog}
                  disabled={result.status !== 'pending_confirmation'}
                  onUpdateDraft={updateDraft}
                  onOpenSource={setSourceMetric}
                />
              ))}
            </div>
          ) : (
            <Table
              rowKey="id"
              columns={metricColumns}
              dataSource={visibleMetrics}
              pagination={{ pageSize: 20, showTotal: (total) => `共 ${total} 项` }}
              size="small"
              scroll={{ x: 1500 }}
              locale={{ emptyText: result.status === 'processing' ? '正在解析' : '未解析出指标' }}
            />
          )}
          <Space style={{ marginTop: 16 }} wrap>
            <Switch checked={showAllMetrics} onChange={setShowAllMetrics} />
            <Typography.Text type="secondary">显示全部指标</Typography.Text>
            {result.status === 'pending_confirmation' && (
              <Button
                type="primary"
                icon={<CheckCircleOutlined />}
                loading={confirming}
                disabled={(result.processing_warnings || []).length > 0}
                onClick={handleConfirm}
              >
                确认并生成健康提示
              </Button>
            )}
            {result.status === 'confirmed' && (
              <Button type="primary" loading={confirming} onClick={handleAssess}>
                重试生成健康提示
              </Button>
            )}
          </Space>
          <EvidenceResult
            result={result.evidence_result}
            admissionLedger={result.admission}
            onOpenSource={setSourceMetric}
            reportId={result.id}
            assessed={result.status === 'assessed'}
          />
        </Card>
      )}
      <Modal
        title="报告原文定位"
        open={Boolean(sourceMetric)}
        footer={null}
        width={960}
        wrapClassName="source-modal-wrap"
        className="source-modal"
        onCancel={() => setSourceMetric(null)}
        destroyOnHidden
      >
        <SourceEvidence
          reportId={result?.id}
          reportToken={reportToken}
          metric={sourceMetric}
          file={(result?.files || []).find((item) => item.file_index === sourceMetric?.source_file_index)}
        />
      </Modal>
    </div>
  );
}
