import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Form,
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
import { CheckCircleOutlined, EyeOutlined, InboxOutlined, ReloadOutlined } from '@ant-design/icons';
import { assessReport, confirmReport, getMetricCatalog, getReport, uploadReport } from '../api.js';

const REPORT_TYPES = ['体检', '门诊', '住院', '其他'];
const DECISIONS = [
  { label: '确认', value: 'confirmed' },
  { label: '修正', value: 'corrected' },
  { label: '排除', value: 'excluded' },
];
// 异常标记：H=偏高(红) L=偏低(橙) N=正常(绿)
export function abnormalTag(flag) {
  if (!flag) return <Tag>—</Tag>;
  const f = String(flag).toUpperCase();
  if (f === 'H' || f === 'HIGH' || f === '高') return <Tag color="red">H 偏高</Tag>;
  if (f === 'L' || f === 'LOW' || f === '低') return <Tag color="orange">L 偏低</Tag>;
  if (f === 'A' || f === '*') return <Tag color="red">异常</Tag>;
  if (f === 'N' || f === 'NORMAL' || f === '正常') return <Tag color="green">N 正常</Tag>;
  return <Tag>{String(flag)}</Tag>;
}

function isAbnormal(flag) {
  return ['H', 'HIGH', '高', 'L', 'LOW', '低', 'A', '*'].includes(String(flag || '').toUpperCase());
}

function SourceEvidence({ reportId, metric, file }) {
  if (!metric) return null;
  const page = metric.page_number || 1;
  const box = metric.bbox_normalized;
  const sourceUrl = `/api/health/report/${reportId}/files/${metric.source_file_index}/pages/${page}`;
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
      <div style={{ position: 'relative', width: '100%', maxWidth: 900, margin: '0 auto' }}>
        <img src={sourceUrl} alt={`报告原文第 ${page} 页`} style={{ width: '100%', display: 'block' }} />
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
  if (status === 'processing') return <Tag color="processing">正在解析</Tag>;
  if (status === 'failed') return <Tag color="error">解析失败</Tag>;
  if (status === 'pending_confirmation') return <Tag color="gold">待确认</Tag>;
  if (status === 'confirmed') return <Tag color="blue">已确认，待生成提示</Tag>;
  if (status === 'assessed') return <Tag color="green">已生成健康提示</Tag>;
  return <Tag>{status || '未知状态'}</Tag>;
}

function evidenceAlertType(hasFindings, hasUnmatched) {
  if (hasFindings) return 'success';
  if (hasUnmatched) return 'warning';
  return 'info';
}

function EvidenceResult({ result }) {
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
  const summary = patientReply?.summary || result.message;
  return (
    <Card
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
            const card = detail.card || {};
            const sources = Array.isArray(card.sources) ? card.sources : [];
            const cardVersion = finding.card_version || card.version || '—';
            const cardId = finding.card_id || card.id;
            const body = finding.patient_visible_body || card.patient_visible_body;
            return (
              <List.Item>
                <div style={{ width: '100%' }}>
                  <Space wrap>
                    <Typography.Text strong>{finding.condition_name || finding.condition_code}</Typography.Text>
                    <Tag color="green">知识卡 {cardId ? `${cardId} · ` : ''}v{cardVersion}</Tag>
                    <Tag>证据强度：{finding.evidence_strength || card.grade || '—'}</Tag>
                    {finding.urgency && (
                      <Tag color={urgencyColors[finding.urgency] || 'blue'}>
                        紧急程度：{urgencyLabels[finding.urgency] || finding.urgency}
                      </Tag>
                    )}
                    <Tag>{finding.department || '建议就诊科室未记录'}</Tag>
                  </Space>
                  {body && (
                    <Typography.Paragraph style={{ margin: '8px 0' }}>
                      {body}
                    </Typography.Paragraph>
                  )}
                  <Typography.Text type="secondary">
                    原始证据指标：{(finding.source_observation_ids || []).join('、') || '—'}
                    {finding.needs_recheck ? `；建议复查${finding.recheck_direction ? `：${finding.recheck_direction}` : ''}` : ''}
                  </Typography.Text>
                  {sources.length > 0 && (
                    <List
                      size="small"
                      header="论文与 Claim 来源"
                      dataSource={sources}
                      renderItem={(source) => (
                        <List.Item>
                          <Typography.Text>
                            {source.paper_title || source.paper_id || '未命名论文'}
                            {source.doi ? `（${source.doi}）` : ''}
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
      {unmatched.length > 0 && (
        <Alert
          type="warning"
          showIcon
          title={`有 ${unmatched.length} 个异常指标暂未匹配到已发布知识卡`}
          description="这些指标不会被模型补写结论，后续需先完成对应主题的知识卡审核发布。"
          style={{ marginTop: 12 }}
        />
      )}
      {patientReply?.disclaimer && (
        <Typography.Paragraph type="secondary" style={{ margin: '12px 0 0' }}>
          {patientReply.disclaimer}
        </Typography.Paragraph>
      )}
      {skipped.length > 0 && (
        <Typography.Paragraph type="secondary" style={{ margin: '12px 0 0' }}>
          {skipped.length} 个指标未进入匹配（正常、缺参考范围或证据不足）。
        </Typography.Paragraph>
      )}
    </Card>
  );
}

function initialDrafts(metrics) {
  return Object.fromEntries((metrics || []).map((metric) => [
    metric.id,
    {
      decision: isAbnormal(metric.abnormal_flag) ? 'confirmed' : 'excluded',
      metric_code: metric.metric_code || '',
      value: metric.metric_value || '',
      unit: metric.unit || '',
      reference_range: metric.reference_range || '',
    },
  ]));
}

export default function UploadPage() {
  const [form] = Form.useForm();
  const [fileList, setFileList] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [subjectConsistency, setSubjectConsistency] = useState('');
  const [error, setError] = useState('');
  const [showAllMetrics, setShowAllMetrics] = useState(false);
  const [sourceMetric, setSourceMetric] = useState(null);
  const [metricCatalog, setMetricCatalog] = useState([]);
  const [metricCatalogError, setMetricCatalogError] = useState('');

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

  const updateDraft = (id, field, value) => {
    setDrafts((current) => ({ ...current, [id]: { ...current[id], [field]: value } }));
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
      formData.append('patient_id', values.patient_id.trim());
      fileList.forEach((item) => formData.append('files', item.originFileObj || item));
      if (values.report_type) formData.append('report_type', values.report_type);
      if (values.department && values.department.trim()) formData.append('department', values.department.trim());
      let data = await uploadReport(formData);
      setResult(data);
      message.info('文件已上传，正在后台解析');
      for (let attempt = 0; data.status === 'processing' && attempt < 300; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
        data = await getReport(data.id);
        setResult(data);
      }
      if (data.status === 'failed') throw new Error(data.processing_error || '报告智能解读失败，请重试');
      if (data.status === 'processing') {
        message.warning('报告仍在后台解析，可稍后从报告列表查看');
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
    if (result.subject_consistency !== 'same' && subjectConsistency !== 'same') {
      message.warning('请先确认所有文件属于同一主体');
      return;
    }
    setConfirming(true);
    setError('');
    const observations = (result.metrics || []).map((metric) => {
      const draft = drafts[metric.id] || {};
      const selectedCode = draft.metric_code || metric.metric_code;
      const decision = selectedCode ? (draft.decision || 'confirmed') : 'excluded';
      const item = {
        metric_id: metric.id,
        decision,
        metric_code: selectedCode || undefined,
      };
      if (decision === 'corrected') {
        item.value = draft.value;
        item.unit = draft.unit;
        item.reference_range = draft.reference_range || undefined;
      }
      return item;
    });
    try {
      const data = await confirmReport(result.id, { observations, subject_consistency: subjectConsistency || 'same' });
      setResult(data);
      message.success(data.status === 'assessed' ? '已生成健康风险提示' : '指标已确认，可重试生成健康提示');
    } catch (err) {
      const saved = await getReport(result.id).catch(() => null);
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
      const data = await assessReport(result.id);
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
    { title: '异常', dataIndex: 'abnormal_flag', width: 80, render: (v) => abnormalTag(v) },
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
          value={drafts[record.id]?.decision || (record.metric_code ? 'confirmed' : 'excluded')}
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
  ], [drafts, metricCatalog, result?.status]);

  const visibleMetrics = useMemo(() => {
    const metrics = result?.metrics || [];
    return showAllMetrics
      ? metrics
      : metrics.filter((metric) => isAbnormal(metric.abnormal_flag));
  }, [result?.metrics, showAllMetrics]);
  const abnormalCount = (result?.metrics || []).filter((metric) => isAbnormal(metric.abnormal_flag)).length;

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
          <Form.Item name="patient_id" label="患者编号" rules={[{ required: true, message: '请输入患者编号' }]}>
            <Input placeholder="例如 P001" style={{ width: 200 }} />
          </Form.Item>
          <Form.Item name="report_type" label="报告类型" initialValue="体检">
            <Select style={{ width: 120 }} options={REPORT_TYPES.map((t) => ({ label: t, value: t }))} />
          </Form.Item>
          <Form.Item name="department" label="科室">
            <Input placeholder="可选" style={{ width: 150 }} />
          </Form.Item>
        </Form>

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
          <Descriptions column={4} size="small" bordered style={{ marginBottom: 16 }}>
            <Descriptions.Item label="患者编号">{result.patient_id}</Descriptions.Item>
            <Descriptions.Item label="报告类型">{result.report_type}</Descriptions.Item>
            <Descriptions.Item label="科室">{result.department || '—'}</Descriptions.Item>
            <Descriptions.Item label="文件主体一致性">
              {result.status === 'pending_confirmation' && result.subject_consistency !== 'same' ? (
                <Select
                  aria-label="确认文件属于同一主体"
                  value={subjectConsistency || undefined}
                  placeholder="请选择"
                  options={[
                    { label: '同一主体，继续', value: 'same' },
                    { label: '不同主体，停止', value: 'different' },
                    { label: '无法确认，停止', value: 'uncertain' },
                  ]}
                  onChange={setSubjectConsistency}
                  style={{ width: 150 }}
                />
              ) : (result.subject_consistency || '—')}
            </Descriptions.Item>
          </Descriptions>
          {result.status === 'pending_confirmation' && (
            <Alert
              type="info"
              showIcon
              title={`识别到 ${result.metrics?.length || 0} 项指标，其中 ${abnormalCount} 项异常；当前优先显示异常项和未映射项。`}
              style={{ marginBottom: 16 }}
            />
          )}
          {result.status === 'processing' && (
            <Alert type="info" showIcon title="报告正在后台解析，完成后将自动显示指标。" style={{ marginBottom: 16 }} />
          )}
          <Table
            rowKey="id"
            columns={metricColumns}
            dataSource={visibleMetrics}
            pagination={{ pageSize: 20, showTotal: (total) => `共 ${total} 项` }}
            size="small"
            scroll={{ x: 1500 }}
            locale={{ emptyText: result.status === 'processing' ? '正在解析' : '未解析出指标' }}
          />
          <Space style={{ marginTop: 16 }} wrap>
            <Switch checked={showAllMetrics} onChange={setShowAllMetrics} />
            <Typography.Text type="secondary">显示全部指标</Typography.Text>
            {result.status === 'pending_confirmation' && (
              <Button type="primary" icon={<CheckCircleOutlined />} loading={confirming} onClick={handleConfirm}>
                确认并生成健康提示
              </Button>
            )}
            {result.status === 'confirmed' && (
              <Button type="primary" loading={confirming} onClick={handleAssess}>
                重试生成健康提示
              </Button>
            )}
          </Space>
          <EvidenceResult result={result.evidence_result} />
        </Card>
      )}
      <Modal
        title="报告原文定位"
        open={Boolean(sourceMetric)}
        footer={null}
        width={960}
        onCancel={() => setSourceMetric(null)}
        destroyOnHidden
      >
        <SourceEvidence
          reportId={result?.id}
          metric={sourceMetric}
          file={(result?.files || []).find((item) => item.file_index === sourceMetric?.source_file_index)}
        />
      </Modal>
    </div>
  );
}
