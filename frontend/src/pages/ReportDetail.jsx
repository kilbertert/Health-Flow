import React, { useEffect, useMemo, useState } from 'react';
import { NO_CAUSE_TEXT, interpretationFailureText } from '../interpretationFailure.js';
import { reportStatusColor, reportStatusLabel } from '../reportStatus.js';
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
import Recommendations from '../components/Recommendations.jsx';
import {
  abnormalTag,
  displayFlag,
  EvidenceResult,
  isAbnormal,
  SourceEvidence,
  TechnicalDetails,
} from './Upload.jsx';

const COMPLETED_STATUSES = new Set(['confirmed', 'assessed']);

function shortReportNumber(reportId) {
  return `#${reportId}`;
}

function formatDateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('zh-CN');
}

function isWeChatBrowser() {
  return typeof navigator !== 'undefined' && /MicroMessenger/i.test(navigator.userAgent);
}

function metricColumns(onOpenSource) {
  return [
    { title: '项目', dataIndex: 'metric_name', width: 150 },
    {
      title: '结果',
      key: 'value',
      width: 110,
      // 生效值由服务端算好（GLOSSARY.md 的「指标生效值」）——前端不再自己回退。
      render: (_, record) => record.effective_value || '—',
    },
    {
      title: '单位',
      key: 'unit',
      width: 90,
      render: (_, record) => record.effective_unit || '—',
    },
    {
      title: '参考区间',
      key: 'reference_range',
      width: 140,
      render: (_, record) => record.effective_reference_range || '—',
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
      // 定位缺失时不再是一个沉默的「—」：按钮照常在，弹窗如实说明
      // 「无原文定位」（#163）。
      render: (_, record) => (
        <Tooltip title="查看报告原文定位">
          <Button
            type="text"
            icon={<FileTextOutlined />}
            aria-label={`查看${record.metric_name}原文`}
            onClick={() => onOpenSource(record)}
          />
        </Tooltip>
      ),
    },
  ];
}

/**
 * 告警块的标题与描述 —— **数字来自服务端**（GLOSSARY.md 的「报告指标计数」）。
 *
 * 导出是为了能在测试里直接钉住结论句的规则：有未判定的行时**不许**宣告「均在参考区间内」
 * ——那些行是**没判出来**，不是「在区间内」，而患者会据此放心。这是本改动可见的行为变化。
 */
export function abnormalSummaryText(result) {
  const counts = result?.counts || {};
  const abnormalCount = counts.abnormal ?? 0;
  const undecidedCount = counts.undecided ?? 0;
  // 名字列表仍是行渲染：服务端的数决定不了顺序，也带不了名字。
  const names = (result?.metrics || [])
    .filter((metric) => metric.confirmation_status !== 'excluded' && isAbnormal(displayFlag(metric)))
    .map((metric) => metric.metric_name)
    .join('、');
  // **有未判定的行时不许宣告「均在参考区间内」。** 那些行是**没判出来**，不是「在区间内」
  // —— 在它们存在时说那句话是一句假话，而患者会据此放心。这是本改动可见的行为变化。
  //
  // 少了 `counts` 字段时（旧服务端响应、或测试夹具）**不说那句话**：`undecided` 无法从
  // 逐行数据推导出来（它要「判定为空」与「患者没排除」两条，而前端那份展示映射对
  // 「模型没标异常、也没有参考范围」的行无法与「判不了」区分）。一句说不出口的结论，
  // 比一句可能为假的结论好。
  const knowsCounts = typeof counts.undecided === 'number';
  return {
    type: abnormalCount || undecidedCount ? 'warning' : 'success',
    title: abnormalCount ? `异常指标 ${abnormalCount} 项` : '未见异常指标',
    description: abnormalCount
      ? names
      : undecidedCount > 0
        ? `另有 ${undecidedCount} 项未能判定，未参与解读，请查看下方说明。`
        : knowsCounts
          ? '当前报告的指标均在参考区间内。'
          : '未发现异常标记的指标。',
  };
}

function MetricOverview({ result, onOpenSource }) {
  const columns = useMemo(() => metricColumns(onOpenSource), [onOpenSource]);
  // 患者明确排除的指标没有生效值（服务端语义），不进报告单总览 ——
  // 否则患者会看到自己刚排除的指标还挂在报告上。
  const metrics = (result.metrics || []).filter((metric) => metric.confirmation_status !== 'excluded');
  const summary = abnormalSummaryText(result);
  return (
    <Card className="metric-overview-card" title="指标总览">
      <Alert
        className="report-abnormal-summary"
        type={summary.type}
        showIcon
        title={summary.title}
        description={summary.description}
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

/** 这份材料有没有确定的、可翻的页数。`null` / `undefined` 是「读不出页数」。

导出是为了能在测试里直接钉住它：`page_count` 为未知时**不能**退化成 1 页
（`undefined === 1` 为 false，`undefined > 0` 也为 false —— 这个谓词把两处
容易写错的地方收成一处）。
*/
export function hasKnownPageCount(file) {
  return typeof file?.page_count === 'number' && file.page_count > 0;
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
      {/* 页数未知时**隐藏翻页器**：显示「共 1 页」是在说一件我们并不知道的事。 */}
      {hasKnownPageCount(file) && (
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
  // 失败的话由**成因**给出（唯一之家 `frontend/src/interpretationFailure.js`）。
  // 「这份报告没有成因可给」（历史行）时退回一句明说我们不知道的话 —— 那句话也住在
  // 同一个模块里，这样「失败时患者看到什么」只有一处可改。
  const failure = interpretationFailureText(result.interpretation_failure);
  const description = isPending
    ? '完成指标确认后即可生成完整报告单与健康风险提示。'
    : isFailed
      ? failure
        ? failure.description
        : NO_CAUSE_TEXT
      : '请稍后重试。';
  return (
    <Card className="report-status-card">
      <Alert
        // 颜色只在**终态失败**时取自成因。`processing` 的报告可能带着一个成因
        // （一次可重试的失败刚把队列放回去），但那时它还在解析中 —— 用失败色和失败
        // 标题会让患者以为已经结束了。
        type={isFailed ? failure?.color || 'error' : 'info'}
        showIcon
        title={isFailed && failure ? failure.title : title}
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
  const wechatBrowser = isWeChatBrowser();

  return (
    <main className="report-detail-page">
      <div className="report-heading">
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={onBack}>返回</Button>
        <div>
          <p className="eyebrow">健康管理 · 报告详情</p>
          <h1>报告详情</h1>
        </div>
        <Tag color={reportStatusColor(result.status)}>{reportStatusLabel(result.status)}</Tag>
      </div>

      {wechatBrowser && (
        <Alert
          className="wechat-print-guide"
          type="info"
          showIcon
          icon={<WarningOutlined />}
          title="请用系统浏览器打开报告页"
          description="微信内置浏览器打印受限。点击右上角“…”选择“在浏览器打开”，用系统浏览器打开后再打印或另存为 PDF。"
        />
      )}

      {!completed ? (
        <ReportStatus result={result} onContinueConfirm={() => onContinueConfirm(result.id)} />
      ) : (
        <>
          <Card className="report-meta-card">
            <Descriptions column={{ xs: 1, sm: 2, md: 4 }} size="small" bordered>
              <Descriptions.Item label="账户昵称">{account?.display_name || '—'}</Descriptions.Item>
              <Descriptions.Item label="报告编号">{shortReportNumber(result.id)}</Descriptions.Item>
              <Descriptions.Item label="生成时间">{formatDateTime(result.created_at)}</Descriptions.Item>
              <Descriptions.Item label="状态">{reportStatusLabel(result.status)}</Descriptions.Item>
            </Descriptions>
          </Card>

          <MetricOverview result={result} onOpenSource={setSourceMetric} />

          {/* 详情页与解读页共用同一个卡片，所以它也读同一份报告级台账 —— 服务端算好的
              四类行数。漏传这个参数不会报错，但会让「有 N 项未进入解读」那句话在详情页
              消失（解读页有、详情页没有），而两处说的是同一份报告。 */}
          <EvidenceResult
            result={result.evidence_result}
            admissionLedger={result.admission}
            onOpenSource={setSourceMetric}
          />

          <Recommendations reportId={result.id} reportToken="" />

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
