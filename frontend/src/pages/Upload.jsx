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

const REPORT_TYPES = ['体检', '门诊', '住院', '其他'];
const DECISIONS = [
  { label: '待核对', value: 'pending', disabled: true },
  { label: '确认', value: 'confirmed' },
  { label: '修正', value: 'corrected' },
  { label: '排除', value: 'excluded' },
];
const PASTE_IMAGE_EXTENSIONS = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/jpg': 'jpg',
  'image/gif': 'gif',
  'image/bmp': 'bmp',
};

const UNSUPPORTED_PASTE_IMAGE_TYPES = new Set([
  'image/webp',
  'image/heic',
  'image/heif',
]);
const DATA_IMAGE_RE = /data:image\/([a-z0-9.+-]+);base64,([a-z0-9+/=]+)/gi;
const PASTE_LONG_PRESS_MS = 600;
const PASTE_LONG_PRESS_MOVE_TOLERANCE = 10;
const PASTE_EDITABLE_PLACEHOLDER = '\u200B';

function normalizePasteMime(value) {
  return String(value || '').split(';', 1)[0].trim().toLowerCase();
}

function pasteExtensionForMime(mime) {
  const normalized = normalizePasteMime(mime);
  if (Object.prototype.hasOwnProperty.call(PASTE_IMAGE_EXTENSIONS, normalized)) {
    return PASTE_IMAGE_EXTENSIONS[normalized];
  }
  return 'png';
}

function pasteFileMimeForExtension(extension) {
  if (extension === 'jpg') return 'image/jpeg';
  return `image/${extension}`;
}

function pasteMimeFromFileName(name) {
  const extension = String(name || '').split('.').pop().toLowerCase();
  if (['png', 'jpg', 'jpeg', 'gif', 'bmp'].includes(extension)) {
    const normalized = extension === 'jpeg' ? 'jpg' : extension;
    return { mime: pasteFileMimeForExtension(normalized), extension: normalized };
  }
  if (['webp', 'heic', 'heif'].includes(extension)) {
    return { mime: `image/${extension}`, extension };
  }
  return null;
}

function pasteImageFromBase64(mime, base64) {
  try {
    const bytes = Uint8Array.from(atob(base64.replace(/\s/g, '')), (char) => char.charCodeAt(0));
    return new File([bytes], '', { type: normalizePasteMime(mime) });
  } catch {
    return null;
  }
}

function readPastedImages(event) {
  const clipboardData = event?.clipboardData || event?.nativeEvent?.clipboardData;
  const images = [];
  let sawUnsupportedImage = false;
  let sawPastePayload = false;

  const items = clipboardData?.items ? Array.from(clipboardData.items) : [];
  if (items.length > 0) {
    sawPastePayload = items.length > 0;
    for (const item of items) {
      if (item.kind !== 'file') continue;
      const type = normalizePasteMime(item.type);
      if (type.startsWith('image/')) {
        if (UNSUPPORTED_PASTE_IMAGE_TYPES.has(type)) {
          sawUnsupportedImage = true;
          continue;
        }
        const file = item.getAsFile?.();
        if (file) {
          images.push({
            file,
            mime: type || file.type || `image/${pasteExtensionForMime(type)}`,
            extension: pasteExtensionForMime(type),
          });
        } else {
          sawUnsupportedImage = sawUnsupportedImage || UNSUPPORTED_PASTE_IMAGE_TYPES.has(type);
        }
      }
    }
  } else {
    const files = clipboardData?.files ? Array.from(clipboardData.files) : [];
    sawPastePayload = files.length > 0;
    for (const file of files) {
      const type = normalizePasteMime(file.type);
      if (type.startsWith('image/')) {
        if (UNSUPPORTED_PASTE_IMAGE_TYPES.has(type)) {
          sawUnsupportedImage = true;
          continue;
        }
        images.push({
          file,
          mime: type || pasteFileMimeForExtension(pasteExtensionForMime(type)),
          extension: pasteExtensionForMime(type),
        });
      } else {
        const byName = pasteMimeFromFileName(file.name);
        if (byName) {
          if (UNSUPPORTED_PASTE_IMAGE_TYPES.has(byName.mime)) {
            sawUnsupportedImage = true;
          } else {
            images.push({ file, mime: byName.mime, extension: byName.extension });
          }
        }
      }
    }
  }

  const html = typeof clipboardData?.getData === 'function'
    ? clipboardData.getData('text/html') || ''
    : '';
  if (html) {
    sawPastePayload = true;
    for (const match of html.matchAll(DATA_IMAGE_RE)) {
      const mime = normalizePasteMime(match[1]);
      if (UNSUPPORTED_PASTE_IMAGE_TYPES.has(mime)) {
        sawUnsupportedImage = true;
        continue;
      }
      const file = pasteImageFromBase64(mime, match[2]);
      if (!file) continue;
      images.push({
        file,
        mime: file.type || pasteFileMimeForExtension(pasteExtensionForMime(mime)),
        extension: pasteExtensionForMime(mime),
      });
    }
  }
  const plainText = typeof clipboardData?.getData === 'function' && !html
    ? clipboardData.getData('text/plain') || ''
    : '';
  if (plainText) {
    sawPastePayload = true;
  }

  if (images.length > 0) {
    return { images, status: 'ok' };
  }
  if (sawUnsupportedImage) {
    return { status: 'unsupported' };
  }
  if (sawPastePayload) {
    return { status: 'empty' };
  }
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

// 「这个值进入解读了吗」——患者可见的一句诚实说明。
//
// 判据分两段，各自对应一种真实的、患者必须被告知的情况：
//
//   1. **服务端给了判定为空**：值为空（还没解析）、提示比较符、或多值/带符号
//      解析不出一个数、没有参考范围。值本身就是用不了的。
//   2. **服务端根本没给这个字段**（`undefined`，字段出现之前的响应）：退回
//      原始标记的纯展示映射，不自行推导。
//
// 被患者**排除**的指标不算「用不了」——服务端对排除项也返回空判定，但那是
// 患者自己的决定，值本身完全可解析；把它们标成「数值无法识别」是误导。
//
// 两个条件都落在服务端已知的事实上：判定为空（或用不了）**且** 模型宣称它异常
// （说明这条本来会被当成异常项）。合起来正是「模型说它异常、但它没能进入解读」
// ——#129 的场景：多值/带符号的值会被后端整行丢掉，界面必须在患者确认之前说清，
// 而不是让它一路走到「已生成健康提示」。
//
// 取代了原先的 `valueIsUsable`：那个函数在本文件里又复刻了一遍后端的数值与
// 范围解析规则（注释里写着「必须与后端 evidence_bridge._single_number 一致」）。
export function valueUnusable(metric) {
  // 患者排除的指标：服务端同样返回空判定，但那是患者自己的决定，值本身可解析。
  if (metric?.confirmation_status === 'excluded') return false;
  // 模型没宣称异常的行，从来不是这个提示的对象。
  if (!isAbnormal(metric?.abnormal_flag)) return false;
  if (metric?.inferred_abnormal_flag === undefined) {
    // 字段出现之前的响应。这里**不能用 displayFlag**：它对原始异常一律返回
    // 「待核对」（那是对的，患者需要看到异常候选），于是可解析的异常也会被
    // 本谓词判成「用不了」——患者被挡在一个完全能确认的指标前面。
    //
    // 精确的回答需要后端的 `abnormal_flag_reason`，但它在回复里被 Pydantic 的
    // 响应模型过滤掉了（响应只声明了 `inferred_abnormal_flag`）。前置审查披露
    // 后会导致旧客户端不再被挡——本部署里前后端同进同出，不承担这个代价。
    // 所以旧响应下本谓词只能保守返回 false：宁可少提示，不可误报（患者排除
    // 掉那条的值往往完全可解析）。代价是 #129 的多值守卫在旧响应下不生效，
    // 而该场景要成立需要「前端版本早于 #134」，同一部署里这不会发生。
    return false;
  }
  return metric.inferred_abnormal_flag === null;
}

// 一条指标在界面上应显示的异常标记。
//
// 服务端算好的 `inferred_abnormal_flag` 优先 —— 它是「异常判定」的唯一口径，
// 历史摘要的「N 项偏高/偏低」也用它，所以两处永远同口径。
//
// `not_decidable`（患者排除、或值还没解析出来）**刻意不回落**到原始标记：
//   - 患者排除的指标，患者已经表态不要它参与解读，服务端也不会为它计数；
//     这里再按原始标记显示一个红色「H」就自相矛盾了。
//   - 值还没判定的指标，患者要在确认页上看到「待核对」，而不是一个凭解析
//     不到的值推出来的假异常。
// 只有在服务端**根本没有给这个字段**（字段缺失或旧响应，`undefined`）时，
// 才退回原始标记做纯展示映射。
export function displayFlag(metric) {
  const inferred = metric?.inferred_abnormal_flag;
  if (inferred !== undefined && inferred !== null) return inferred;
  const raw = metric?.abnormal_flag;
  if (inferred === null) {
    if (metric?.confirmation_status === 'excluded') return 'excluded';
    // 判不出来，但模型宣称异常 —— 交患者核对（这正是 #129 要保住的场景）。
    if (isAbnormal(raw)) return '待核对';
    // 其余判不出来的行（如模型标 N、或没有参考范围）如实显示原始标记：
    // 把它们一律标成「待核对」会让每一份含无范围指标的报告满屏待核对。
    return raw;
  }
  return isAbnormal(raw) ? '待核对' : raw;
}

function initialDecision(metric) {
  const flag = displayFlag(metric);
  // 用不了的值**不能**默认「确认」：后端会把它连行一起丢掉，而界面却让它一路
  // 走到「已生成健康提示」——用户从没被告知那个值没被采纳（报告 44 的三个异常项
  // 就是这么消失的）。默认「待核对」，逼一次显式选择。
  if (valueUnusable(metric)) return 'pending';
  if ((flag === 'H' || flag === 'L') && metric?.evidence_text && metric?.page_number) return 'confirmed';
  if (flag === 'H' || flag === 'L') return 'pending';
  return flag === '待核对' ? 'pending' : 'excluded';
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

function MetricCard({ metric, draft, metricCatalog, disabled, onUpdateDraft, onOpenSource }) {
  const [expanded, setExpanded] = useState(false);
  const decision = draft?.decision || initialDecision(metric);
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
            {metric.metric_value}
            {metric.unit ? ` ${metric.unit}` : ''}
          </span>
          {abnormalTag(displayFlag(metric))}
          {/* 多值/带符号的行：先告诉用户「这个值用不了」，而不是等它被后端悄悄丢掉 */}
          {valueUnusable(metric) && (
            <Tag color="orange">数值无法识别为单个数字</Tag>
          )}
        </button>
        {metric.page_number ? (
          <Tooltip title="查看原文定位">
            <Button
              type="text"
              icon={<EyeOutlined />}
              aria-label={`查看${metric.metric_name}原文`}
              onClick={() => onOpenSource(metric)}
            />
          </Tooltip>
        ) : null}
      </div>
      {expanded ? (
        <div className="metric-card-details">
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
  const page = metric?.page_number || metric?.source_page || 1;
  const fileIndex = metric?.source_file_index;
  useEffect(() => {
    if (!metric) return undefined;
    let active = true;
    let objectUrl = '';
    setSourceUrl('');
    setSourceError('');
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
  const box = metric.bbox_normalized;
  const highlight = Array.isArray(box) && box.length === 4 ? {
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

function evidenceItemsFor(finding, detail) {
  const items = Array.isArray(finding.evidence_items) && finding.evidence_items.length
    ? finding.evidence_items
    : detail.evidence_items;
  if (Array.isArray(items) && items.length) return items;
  const card = detail.card || (finding.card_id ? {
    id: finding.card_id,
    version: finding.card_version,
    evidence_profile_id: finding.evidence_profile_id,
    patient_visible_body: finding.patient_visible_body,
    sources: finding.sources || [],
    grade: finding.evidence_strength,
  } : null);
  if (!card) return [];
  const sourceObservations = finding.source_observations || detail.source_observations || [];
  return [{
    metric_code: sourceObservations[0]?.metric_code || '',
    metric_label: sourceObservations[0]?.metric_label || sourceObservations[0]?.metric_code || '异常指标',
    card,
    evidence_strength: finding.evidence_strength || card.grade,
    source_observation_ids: finding.source_observation_ids || detail.source_observation_ids || [],
    source_observations: sourceObservations,
  }];
}

function EvidenceSummaryCard({ result, onOpenSource }) {
  if (!result) return null;
  const findings = Array.isArray(result.findings) ? result.findings : [];
  const patientReply = result.patient_reply && typeof result.patient_reply === 'object'
    ? result.patient_reply
    : null;
  const replyFindings = Array.isArray(patientReply?.findings) ? patientReply.findings : findings;
  const unmatched = Array.isArray(result.unmatched) ? result.unmatched : [];
  const skipped = Array.isArray(result.skipped) ? result.skipped : [];
  const findingDetails = new Map(findings.map((finding) => [finding.condition_code, finding]));
  const urgencyLabels = { routine: '常规', soon: '近期', urgent: '紧急', emergency: '危急' };
  const urgencyColors = { routine: 'blue', soon: 'orange', urgent: 'red', emergency: 'magenta' };
  const strengthLabels = { high: '高', moderate: '中等', low: '低', very_low: '极低', mixed: '各指标分别评级' };
  const summary = patientReply?.summary || result.message;
  return (
    <Card
      className="evidence-result-card"
      title={patientReply?.title || '正式知识卡匹配结果'}
      style={{ marginTop: 16 }}
    >
      {summary && (
        <Alert
          type={evidenceAlertType(replyFindings.length > 0, unmatched.length > 0)}
          showIcon
          title={summary}
          style={{ marginBottom: 16 }}
        />
      )}
      {replyFindings.length > 0 && (
        <List
          dataSource={replyFindings}
          renderItem={(finding) => {
            const detail = findingDetails.get(finding.condition_code) || finding;
            const evidenceItems = evidenceItemsFor(finding, detail);
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
                        onClick={() => onOpenSource?.({
                          ...source,
                          page_number: source.source_page,
                          metric_name: item.metric_label,
                          evidence_text: source.evidence_text,
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
      {patientReply?.disclaimer && (
        <Typography.Paragraph type="secondary" style={{ margin: '12px 0 0' }}>
          {patientReply.disclaimer}
        </Typography.Paragraph>
      )}
      {skipped.length > 0 && (
        <Typography.Paragraph type="secondary" style={{ margin: '12px 0 0' }}>
          {skipped.length} 个指标未进入匹配（正常、缺参考范围、原文证据或数值不足）。
        </Typography.Paragraph>
      )}
    </Card>
  );
}

function initialDrafts(metrics) {
  return Object.fromEntries((metrics || []).map((metric) => [
    metric.id,
    {
      decision: initialDecision(metric),
      metric_code: metric.metric_code || '',
      value: metric.metric_value || '',
      unit: metric.unit || '',
      reference_range: metric.reference_range || '',
      evidence_text: metric.evidence_text || '',
    },
  ]));
}

export function TechnicalDetails({ result, subjectConsistency, onSubjectConsistencyChange }) {
  const trace = result.extraction_trace;
  const subjectNeedsConfirmation = result.status === 'pending_confirmation'
    && result.subject_consistency !== 'same';
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
            onChange={onSubjectConsistencyChange}
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
export function EvidenceResult({ result, onOpenSource, reportId = null, assessed = false }) {
  if (!result) return null;
  return (
    <>
      <EvidenceSummaryCard result={result} onOpenSource={onOpenSource} />
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
  const isNarrow = useNarrowViewport();

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

  const subjectGateOpen = result?.status === 'pending_confirmation'
    && result?.subject_consistency !== 'same';
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

  const appendPastedImages = (pastedImages) => {
    const remaining = Math.max(0, 20 - fileList.length);
    if (remaining === 0) {
      message.warning('最多上传 20 个文件');
      return;
    }
    const nextFiles = pastedImages.slice(0, remaining).map(({ file, mime, extension }) => {
      pasteSequenceRef.current += 1;
      const name = `粘贴-${Date.now()}.${extension}`;
      const renamed = new File([file], name, { type: mime || file.type });
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
      message.warning('最多上传 20 个文件');
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
    const pasted = readPastedImages(event);
    if (pasted.status === 'unsupported') {
      message.warning('暂不支持');
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
      // 上限必须**不短于**服务端的回收阈值：`REPORT_JOB_STALE_SECONDS` 默认
      // 1200 秒（20 分钟）—— 一份报告可能一直在重试到那时才被回收。客户端先
      // 放弃，患者就会停在一个再也不动的「解析中」，而服务端随后把它跑完了。
      const POLL_INTERVAL_MS = 2000;
      const MAX_ATTEMPTS = 660; // 22 分钟 > REPORT_JOB_STALE_SECONDS(1200s)
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
    if (result.subject_consistency !== 'same' && subjectConsistency !== 'same') {
      message.warning('请先确认所有文件属于同一主体');
      return;
    }
    const decisionOf = (metric) => drafts[metric.id]?.decision || initialDecision(metric);
    const unresolved = (result.metrics || []).filter((metric) => decisionOf(metric) === 'pending');
    if (unresolved.length > 0) {
      message.warning(`还有 ${unresolved.length} 个异常候选项需要确认、修正或排除`);
      return;
    }
    // 显式选了「确认」但值仍是多值/带符号的行：接受它就等于接受一次**静默丢弃**。
    // 后端要求恰好一个数，这种值它会连行一起丢掉（reason=invalid_value），
    // 而用户会以为已经确认过了。挡在这里，明确要求修正或排除。
    const unusable = (result.metrics || []).filter(
      (metric) => decisionOf(metric) === 'confirmed' && valueUnusable(metric),
    );
    if (unusable.length > 0) {
      message.warning(
        `有 ${unusable.length} 项的数值无法识别为单个数字（如「5.4 5.5」），无法参与匹配；请「修正」为单个数值或「排除」。`,
      );
      return;
    }
    setConfirming(true);
    setError('');
    const observations = (result.metrics || []).map((metric) => {
      const draft = drafts[metric.id] || {};
      const selectedCode = draft.metric_code || metric.metric_code;
      const decision = draft.decision || initialDecision(metric);
      const item = {
        metric_id: metric.id,
        decision,
        metric_code: selectedCode || undefined,
      };
      if (decision === 'corrected') {
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
    { title: '模型值', dataIndex: 'metric_value', width: 90 },
    { title: '单位', dataIndex: 'unit', width: 80 },
    { title: '参考范围', dataIndex: 'reference_range', width: 110 },
    {
      title: '异常', key: 'abnormal_flag', width: 90,
      render: (_, record) => (
        <Space size={4} wrap>
          {abnormalTag(displayFlag(record))}
          {/* 表形态下同样要标出来——这是桌面端默认视图 */}
          {valueUnusable(record) && (
            <Tag color="orange">数值无法识别为单个数字</Tag>
          )}
        </Space>
      ),
    },
    { title: '证据原文', dataIndex: 'evidence_text', width: 220, ellipsis: true },
    {
      title: '原文', key: 'source', width: 62,
      render: (_, record) => record.page_number ? (
        <Tooltip title="查看原文定位">
          <Button type="text" icon={<EyeOutlined />} aria-label={`查看${record.metric_name}原文`} onClick={() => setSourceMetric(record)} />
        </Tooltip>
      ) : '—',
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
          value={drafts[record.id]?.decision || initialDecision(record)}
          options={DECISIONS}
          disabled={result.status !== 'pending_confirmation'}
          onChange={(value) => updateDraft(record.id, 'decision', value)}
          style={{ width: 88 }}
        />
      ),
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
            description="请稍后重试；未加载正式目录前不会使用过期的本地指标列表。"
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
            accept=".pdf,.jpg,.jpeg,.png,.gif,.bmp"
            multiple
            maxCount={20}
            fileList={fileList}
            beforeUpload={() => false}
            onChange={({ fileList: next }) => setFileList(next)}
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">点击或拖拽多张报告文件到此区域</p>
            <p className="ant-upload-hint">文件顺序会保留；每个文件不超过 20MB</p>
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
