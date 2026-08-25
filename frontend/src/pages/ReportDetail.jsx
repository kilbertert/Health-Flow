import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  FileTextOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Empty,
  Modal,
  Pagination,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { fetchReportPage, getReport } from '../api.js';
import {
  abnormalTag,
  displayFlag,
  EvidenceResult,
  isAbnormal,
  SourceEvidence,
  TechnicalDetails,
} from './Upload.jsx';

const COMPLETED_STATUSES = new Set(['confirmed', 'assessed']);

export function statusLabel(status) {
  return {
    processing: '解析中',
    pending_confirmation: '待确认',
    confirmed: '已确认',
    assessed: '已完成',
    failed: '解析失败',
  }[status] || status;
}

function statusColor(status) {
  if (status === 'assessed') return 'green';
  if (status === 'confirmed') return 'blue';
  if (status === 'pending_confirmation') return 'gold';
  if (status === 'processing') return 'processing';
  if (status === 'failed') return 'error';
  return 'default';
}

function shortReportNumber(reportId) {
  return `#${reportId}`;
}

function formatDateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('zh-CN');
}

function metricColumns(onOpenSource) {
  return [
    { title: '项目', dataIndex: 'metric_name', width: 150 },
    {
      title: '结果',
      key: 'value',
      width: 110,
      render: (_, record) => record.confirmed_value || record.metric_value || '—',
    },
    {
      title: '单位',
      key: 'unit',
      width: 90,
      render: (_, record) => record.confirmed_unit || record.unit || '—',
    },
    {
      title: '参考区间',
      key: 'reference_range',
      width: 140,
      render: (_, record) => record.confirmed_reference_range || record.reference_range || '—',
    },
    {
      title: '异常标记',
      key: 'abnormal_flag',
      width: 110,
      render: (_, record) => abnormalTag(displayFlag(record)),
    },
    {
      title: '原文',
      key: 'source',
      width: 70,
      render: (_, record) => (record.page_number ? (
        <Tooltip title="查看报告原文定位">
          <Button
            type="text"
            icon={<FileTextOutlined />}
            aria-label={`查看${record.metric_name}原文`}
            onClick={() => onOpenSource(record)}
          />
        </Tooltip>
      ) : '—'),
    },
  ];
}

function MetricOverview({ result, onOpenSource }) {
  const columns = useMemo(() => metricColumns(onOpenSource), [onOpenSource]);
  const metrics = result.metrics || [];
  const abnormalMetrics = metrics.filter((metric) => isAbnormal(displayFlag(metric)));
  const abnormalNames = abnormalMetrics.map((metric) => metric.metric_name).join('、');
  return (
    <Card className="metric-overview-card" title="指标总览">
      <Alert
        className="report-abnormal-summary"
        type={abnormalMetrics.length ? 'warning' : 'success'}
        showIcon
        title={abnormalMetrics.length ? `异常指标 ${abnormalMetrics.length} 项` : '未见异常指标'}
        description={abnormalMetrics.length ? abnormalNames : '当前报告的指标均在参考区间内。'}
        style={{ marginBottom: 16 }}
      />
      <Table
        rowKey="id"
        columns={columns}
        dataSource={metrics}
        pagination={{ pageSize: 20, showTotal: (total) => `共 ${total} 项` }}
        size="small"
        scroll={{ x: 640 }}
        rowClassName={(metric) => (isAbnormal(displayFlag(metric)) ? 'report-row-abnormal' : '')}
        locale={{ emptyText: '未解析出指标' }}
      />
    </Card>
  );
}

function OriginalReport({ report, reportToken, active }) {
  const files = Array.isArray(report.files) ? report.files : [];
  const [fileIndex, setFileIndex] = useState(files[0]?.file_index || 1);
  const [pageNumber, setPageNumber] = useState(1);
  const [sourceUrl, setSourceUrl] = useState('');
  const [sourceError, setSourceError] = useState('');

  useEffect(() => {
    if (files[0] && !files.some((file) => file.file_index === fileIndex)) {
      setFileIndex(files[0].file_index);
      setPageNumber(1);
    }
  }, [files, fileIndex]);

  const file = files.find((item) => item.file_index === fileIndex) || files[0];
  useEffect(() => {
    if (!active || !file) return undefined;
    let activeEffect = true;
    let objectUrl = '';
    setSourceUrl('');
    setSourceError('');
    fetchReportPage(report.id, file.file_index, pageNumber, reportToken)
      .then((blob) => {
        if (!activeEffect) return;
        objectUrl = URL.createObjectURL(blob);
        setSourceUrl(objectUrl);
      })
      .catch((err) => {
        if (activeEffect) setSourceError(err.message);
      });
    return () => {
      activeEffect = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [active, pageNumber, report.id, reportToken, file]);

  if (!files.length) return <Empty description="暂无报告原文" />;
  return (
    <div className="original-report">
      <Space wrap style={{ marginBottom: 12 }}>
        {files.length > 1 && (
          <Select
            aria-label="选择报告文件"
            value={fileIndex}
            options={files.map((item) => ({ label: item.original_filename || `文件 #${item.file_index}`, value: item.file_index }))}
            onChange={(value) => { setFileIndex(value); setPageNumber(1); }}
            style={{ minWidth: 180 }}
          />
        )}
        <Typography.Text type="secondary">{file?.original_filename || `文件 #${fileIndex}`}</Typography.Text>
      </Space>
      {sourceError && <Alert type="error" showIcon title={sourceError} style={{ marginBottom: 12 }} />}
      {sourceUrl ? (
        <img className="original-report-page" src={sourceUrl} alt={`报告原文第 ${pageNumber} 页`} />
      ) : (
        <div className="original-report-loading"><Spin /></div>
      )}
      {file && file.page_count > 0 && (
        <div className="original-report-pagination">
          <Pagination
            simple={file.page_count === 1}
            current={pageNumber}
            pageSize={1}
            total={file.page_count}
            onChange={setPageNumber}
            showSizeChanger={false}
          />
        </div>
      )}
    </div>
  );
}

function ReportStatus({ result, onContinueConfirm }) {
  const isPending = result.status === 'pending_confirmation';
  const isFailed = result.status === 'failed';
  const isProcessing = result.status === 'processing';
  const title = isPending
    ? '报告已解析，等待确认'
    : isFailed
      ? '报告解析失败'
      : isProcessing
        ? '报告正在后台解析'
        : '报告当前状态不可查看完整报告单';
  const description = isPending
    ? '完成指标确认后即可生成完整报告单与健康风险提示。'
    : result.processing_error || result.processing_warnings?.join('；') || '请稍后重试。';
  return (
    <Card className="report-status-card">
      <Alert
        type={isFailed ? 'error' : 'info'}
        showIcon
        title={title}
        description={description}
      />
      <Space style={{ marginTop: 20 }} wrap>
        {isPending && (
          <Button type="primary" icon={<CheckCircleOutlined />} onClick={onContinueConfirm}>
            继续确认
          </Button>
        )}
        {isProcessing && (
          <Button icon={<Spin size="small" />} onClick={() => window.location.reload()}>
            刷新状态
          </Button>
        )}
      </Space>
    </Card>
  );
}

export default function ReportDetailPage({ account, reportId, onBack, onContinueConfirm }) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [sourceMetric, setSourceMetric] = useState(null);
  const [originalOpen, setOriginalOpen] = useState(false);
  const [technicalOpen, setTechnicalOpen] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    getReport(reportId)
      .then((data) => {
        if (active) setResult(data);
      })
      .catch((err) => {
        if (active) setError(err.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [reportId]);

  if (loading) {
    return (
      <main className="report-detail-page">
        <div className="report-detail-loading"><Spin size="large" /></div>
      </main>
    );
  }

  if (!result) {
    return (
      <main className="report-detail-page">
        <div className="report-heading">
          <Button type="text" icon={<ArrowLeftOutlined />} onClick={onBack}>返回</Button>
          <div><p className="eyebrow">健康管理 · 报告详情</p><h1>报告详情</h1></div>
        </div>
        <Alert
          type="error"
          showIcon
          icon={<WarningOutlined />}
          title="无法打开报告"
          description={error || '报告不存在或无权访问。'}
        />
      </main>
    );
  }

  const completed = COMPLETED_STATUSES.has(result.status);

  return (
    <main className="report-detail-page">
      <div className="report-heading">
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={onBack}>返回</Button>
        <div>
          <p className="eyebrow">健康管理 · 报告详情</p>
          <h1>报告详情</h1>
        </div>
        <Tag color={statusColor(result.status)}>{statusLabel(result.status)}</Tag>
      </div>

      {!completed ? (
        <ReportStatus result={result} onContinueConfirm={() => onContinueConfirm(result.id)} />
      ) : (
        <>
          <Card className="report-meta-card">
            <Descriptions column={{ xs: 1, sm: 2, md: 4 }} size="small" bordered>
              <Descriptions.Item label="账户昵称">{account?.display_name || '—'}</Descriptions.Item>
              <Descriptions.Item label="报告编号">{shortReportNumber(result.id)}</Descriptions.Item>
              <Descriptions.Item label="生成时间">{formatDateTime(result.created_at)}</Descriptions.Item>
              <Descriptions.Item label="状态">{statusLabel(result.status)}</Descriptions.Item>
            </Descriptions>
          </Card>

          <MetricOverview result={result} onOpenSource={setSourceMetric} />

          <EvidenceResult result={result.evidence_result} onOpenSource={setSourceMetric} />

          <Collapse
            className="report-original-collapse"
            activeKey={originalOpen ? ['report-original'] : []}
            onChange={(keys) => setOriginalOpen(keys.includes('report-original'))}
            items={[{
              key: 'report-original',
              label: '报告原文',
              children: <OriginalReport report={result} reportToken="" active={originalOpen} />,
            }]}
          />

          <Collapse
            className="technical-details"
            activeKey={technicalOpen ? ['technical-details'] : []}
            onChange={(keys) => setTechnicalOpen(keys.includes('technical-details'))}
            items={[{
              key: 'technical-details',
              label: '技术详情',
              children: (
                <TechnicalDetails
                  result={result}
                  subjectConsistency={result.subject_consistency || ''}
                  onSubjectConsistencyChange={() => {}}
                />
              ),
            }]}
          />
        </>
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
          reportId={result.id}
          reportToken=""
          metric={sourceMetric}
          file={(result.files || []).find((item) => item.file_index === sourceMetric?.source_file_index)}
        />
      </Modal>
    </main>
  );
}
