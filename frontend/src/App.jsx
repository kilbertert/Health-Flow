import React, { useEffect, useState } from 'react';
import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  BellOutlined,
  EditOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  HistoryOutlined,
  HomeOutlined,
  LockOutlined,
  LogoutOutlined,
  MailOutlined,
  MenuOutlined,
  MessageOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
} from '@ant-design/icons';
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  ConfigProvider,
  Drawer,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Spin,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import Upload from './pages/Upload.jsx';
import ReportDetail from './pages/ReportDetail.jsx';
import {
  getCurrentAccount,
  getReportHistory,
  loginAccount,
  logoutAccount,
  registerAccount,
  updateProfile,
} from './api.js';

const NAV_ITEMS = [
  { key: 'consult', label: '中医问诊' },
  { key: 'blood', label: '验血咨询' },
  { key: 'report', label: '体检解读' },
  { key: 'body', label: '体脂检测' },
];

function reportRouteFromHash() {
  const match = window.location.hash.match(/^#\/report\/(\d+)/);
  if (!match) return null;
  return { view: 'report-detail', reportId: Number(match[1]) };
}

function BrandMark({ large = false }) {
  return <img className={`brand-mark ${large ? 'brand-mark-large' : ''}`} src="/hst-club-logo.png" alt="HST Club" />;
}

function AuthScreen({ onAuthenticated }) {
  const [mode, setMode] = useState('login');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [form] = Form.useForm();

  const submit = async (values) => {
    setBusy(true);
    setError('');
    try {
      const account = mode === 'login'
        ? await loginAccount(values)
        : await registerAccount(values);
      onAuthenticated(account);
      message.success(mode === 'login' ? '登录成功' : '账户创建成功');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const switchMode = () => {
    setMode(mode === 'login' ? 'register' : 'login');
    setError('');
    form.resetFields();
  };

  return (
    <main className="auth-page">
      <div className="auth-brand"><BrandMark large /></div>
      <h1>{mode === 'login' ? '欢迎回来' : '创建健康账户'}</h1>
      <p className="auth-copy">使用邮箱保存您的报告和健康提示。当前为邮箱+密码 Demo。</p>
      <Card className="auth-card">
        {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}
        <Form form={form} layout="vertical" onFinish={submit} requiredMark={false}>
          {mode === 'register' && (
            <Form.Item name="display_name" label="昵称" initialValue="健康用户" rules={[{ required: true, message: '请输入昵称' }]}>
              <Input prefix={<UserOutlined />} placeholder="您的昵称" maxLength={128} />
            </Form.Item>
          )}
          <Form.Item name="email" label="邮箱" rules={[{ required: true, type: 'email', message: '请输入有效邮箱' }]}>
            <Input prefix={<MailOutlined />} placeholder="name@example.com" autoComplete="email" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, min: mode === 'register' ? 8 : 1, message: mode === 'register' ? '密码至少 8 位' : '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="请输入密码" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={busy}>{mode === 'login' ? '登录' : '完成注册'}</Button>
        </Form>
        <button className="auth-switch" type="button" onClick={switchMode}>
          {mode === 'login' ? '还没有账户？注册' : '已有账户？去登录'}
        </button>
      </Card>
      <p className="auth-boundary"><SafetyCertificateOutlined /> 账户用于保存您的报告记录，不替代医生诊断。</p>
    </main>
  );
}

function AppHeader({ view, account, onNavigate, onMenu, onProfile }) {
  const [messageApi, contextHolder] = message.useMessage();
  const handleNav = (key) => {
    if (key === 'report') onNavigate('report');
    else messageApi.info('该服务将在后续阶段开放');
  };

  return (
    <>
      {contextHolder}
      <header className="app-header">
        <Tooltip title="打开菜单"><button className="icon-button menu-button" type="button" aria-label="打开菜单" onClick={onMenu}><MenuOutlined /></button></Tooltip>
        <nav className="service-nav" aria-label="健康服务">
          {NAV_ITEMS.map((item) => <button className={`service-nav-item ${view === item.key ? 'is-active' : ''}`} key={item.key} type="button" aria-current={view === item.key ? 'page' : undefined} onClick={() => handleNav(item.key)}>{item.label}</button>)}
        </nav>
        <div className="header-actions">
          <Tooltip title="个人中心"><button className="icon-button account-button" type="button" aria-label="个人中心" onClick={onProfile}><UserOutlined /></button></Tooltip>
          <Tooltip title="通知"><button className="icon-button" type="button" aria-label="通知" onClick={() => messageApi.info('暂无新通知')}><BellOutlined /></button></Tooltip>
          <Tooltip title="消息"><button className="icon-button" type="button" aria-label="消息" onClick={() => messageApi.info('消息服务将在后续阶段开放')}><MessageOutlined /></button></Tooltip>
        </div>
      </header>
      <div className="account-strip" aria-label="当前账户"><UserOutlined /><span>{account.display_name}</span></div>
    </>
  );
}

function HomePage({ onOpenReport, onOpenProfile }) {
  return (
    <main className="home-page">
      <section className="home-hero" aria-labelledby="home-title"><BrandMark large /><h1 id="home-title">呵护您的健康</h1><p className="hero-copy">从一份体检报告开始，查看异常指标和有依据的健康提示。</p></section>
      <section className="health-actions" aria-label="健康管理">
        <button className="health-action-card health-action-primary" type="button" onClick={onOpenReport}><span className="action-icon"><FileSearchOutlined /></span><span className="action-copy"><strong>体检报告解读</strong><span>上传报告，核对异常项</span></span><ArrowRightOutlined className="action-arrow" /></button>
        <div className="health-action-grid">
          <button className="health-action-tile" type="button" onClick={onOpenReport}><FileTextOutlined /><span>健康报告</span><small>完成报告后查看</small></button>
          <button className="health-action-tile" type="button" onClick={onOpenProfile}><UserOutlined /><span>个人中心</span><small>账户与报告历史</small></button>
        </div>
      </section>
      <section className="home-note" aria-label="服务边界"><SafetyCertificateOutlined /><p>这里提供报告信息整理与健康风险提示，不替代医生诊断。</p></section>
    </main>
  );
}

const BOTTOM_NAV_ITEMS = [
  { key: 'home', label: '首页', icon: <HomeOutlined /> },
  { key: 'report', label: '体检解读', icon: <FileSearchOutlined /> },
  { key: 'profile', label: '我的', icon: <UserOutlined /> },
];

function BottomNav({ view, onNavigate }) {
  return (
    <nav className="bottom-nav" aria-label="移动端主导航">
      {BOTTOM_NAV_ITEMS.map((item) => (
        <button
          className={`bottom-nav-item ${view === item.key ? 'is-active' : ''}`}
          key={item.key}
          type="button"
          aria-current={view === item.key ? 'page' : undefined}
          onClick={() => onNavigate(item.key)}
        >
          {item.icon}
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  );
}

function MenuSheet({ open, onClose, onOpenReport, onOpenProfile, account }) {
  return <Drawer className="menu-sheet" placement="left" width="min(86vw, 340px)" open={open} onClose={onClose} title={<div className="sheet-title"><BrandMark /><strong>健康流</strong></div>}>
    <button className="sheet-link" type="button" onClick={() => { onOpenReport(); onClose(); }}><FileSearchOutlined /><span>体检报告解读</span><ArrowRightOutlined /></button>
    <button className="sheet-link" type="button" onClick={() => { onOpenProfile(); onClose(); }}><UserOutlined /><span>个人中心</span><ArrowRightOutlined /></button>
    <div className="sheet-status"><MailOutlined /><div><strong>{account.display_name}</strong><span>{account.email}</span></div></div>
  </Drawer>;
}

function ReportPage({ account, reportId, onBack, onReportSaved }) {
  return <main className="report-page"><div className="report-heading"><Button type="text" icon={<ArrowLeftOutlined />} onClick={onBack}>返回首页</Button><div><p className="eyebrow">健康管理 · 报告解读</p><h1>体检报告解读</h1><p>只对已确认的异常指标匹配已发布知识卡。</p></div></div><Upload account={account} initialReportId={reportId} onReportSaved={onReportSaved} /></main>;
}

function statusLabel(status) {
  return { processing: '解析中', pending_confirmation: '待确认', confirmed: '已确认', assessed: '已完成', failed: '解析失败' }[status] || status;
}

function abnormalSummary(item) {
  const count = item.abnormal_count ?? 0;
  return count === 0 ? ' · 未见异常' : ` · ${count} 项偏高/偏低`;
}

function ProfilePage({ account, onBack, onLogout, onOpenReport, onAccountChange }) {
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [securityOpen, setSecurityOpen] = useState(false);
  const [form] = Form.useForm();

  const loadHistory = () => getReportHistory().then(setHistory).catch((err) => setError(err.message)).finally(() => setLoading(false));
  useEffect(() => { loadHistory(); }, []);
  const save = async (values) => {
    setSaving(true);
    try {
      const updated = await updateProfile(values);
      onAccountChange(updated);
      setEditing(false);
      message.success('昵称已更新');
    }
    catch (err) { setError(err.message); } finally { setSaving(false); }
  };

  return <main className="profile-page"><div className="profile-heading"><Button type="text" icon={<ArrowLeftOutlined />} onClick={onBack}>返回</Button><h1>个人中心</h1><span /></div>
    <section className="profile-identity"><span className="profile-avatar"><UserOutlined /></span><div><h2>{account.display_name}</h2><p>{account.email}</p></div><Button type="text" icon={<EditOutlined />} aria-label="编辑昵称" onClick={() => { form.setFieldsValue({ display_name: account.display_name }); setEditing(true); }} /></section>
    <div className="profile-links"><button className="profile-link" type="button" onClick={() => setSecurityOpen(true)}><LockOutlined /><span>账户与安全</span><small>邮箱登录 · 会话安全</small><ArrowRightOutlined /></button><button className="profile-link" type="button" onClick={() => document.getElementById('report-history')?.scrollIntoView({ behavior: 'smooth' })}><HistoryOutlined /><span>报告历史</span><small>{history.length} 份报告</small><ArrowRightOutlined /></button><button className="profile-link" type="button" onClick={() => message.info('通知设置将在后续阶段开放')}><BellOutlined /><span>通知设置</span><small>暂未开放</small><ArrowRightOutlined /></button><button className="profile-link" type="button" onClick={() => message.info('帮助与反馈将在后续阶段开放')}><SafetyCertificateOutlined /><span>帮助与反馈</span><small>暂未开放</small><ArrowRightOutlined /></button></div>
    <section className="history-section" id="report-history"><div className="section-title"><h2>报告历史</h2><Typography.Text type="secondary">仅显示当前账户</Typography.Text></div>{error && <Alert type="error" showIcon title={error} />}{loading ? <div className="history-loading"><Spin /></div> : history.length === 0 ? <Empty description="还没有报告记录" /> : <List dataSource={history} renderItem={(item) => <List.Item actions={[<Button type="link" onClick={() => onOpenReport(item.id)} key="open">查看</Button>]}><List.Item.Meta title={`${item.report_type || '体检报告'} · ${new Date(item.created_at).toLocaleDateString('zh-CN')}`} description={<span>{item.department || '未填写科室'} · {item.metric_count} 项指标{abnormalSummary(item)}</span>} /><Tag color={item.status === 'assessed' ? 'green' : 'gold'}>{statusLabel(item.status)}</Tag></List.Item>} />}</section>
    <Button className="logout-button" danger icon={<LogoutOutlined />} onClick={onLogout}>退出登录</Button>
    <Modal title="修改昵称" open={editing} onCancel={() => setEditing(false)} footer={null}><Form form={form} onFinish={save} layout="vertical"><Form.Item name="display_name" label="昵称" rules={[{ required: true, message: '请输入昵称' }]}><Input maxLength={128} /></Form.Item><Button type="primary" htmlType="submit" loading={saving} block>保存</Button></Form></Modal>
    <Modal title="账户与安全" open={securityOpen} onCancel={() => setSecurityOpen(false)} footer={<Button type="primary" onClick={() => setSecurityOpen(false)}>知道了</Button>}><p>登录邮箱：{account.email}</p><p>当前使用服务端会话保存登录状态，退出登录后会立即失效。</p></Modal>
  </main>;
}

function HealthFlowApp({ account, onLogout, onAccountChange }) {
  const [view, setView] = useState(() => reportRouteFromHash()?.view || 'home');
  const [reportId, setReportId] = useState(() => reportRouteFromHash()?.reportId || null);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const applyHash = () => {
      const route = reportRouteFromHash();
      setView(route?.view || 'home');
      setReportId(route?.reportId || null);
    };
    applyHash();
    window.addEventListener('hashchange', applyHash);
    return () => window.removeEventListener('hashchange', applyHash);
  }, []);

  const navigate = (nextView, nextReportId = null) => {
    if (nextView === 'report-detail' && nextReportId) {
      setView(nextView);
      setReportId(nextReportId);
      window.location.hash = `#/report/${nextReportId}`;
      return;
    }
    setView(nextView);
    setReportId(nextReportId);
    if (window.location.hash) {
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
    }
  };

  const openReport = (id = null) => {
    if (id) navigate('report-detail', id);
    else navigate('report');
  };
  const handleBottomNav = (key) => {
    if (key === 'report') navigate('report');
    else navigate(key);
  };
  const reportViewActive = view === 'report' || view === 'report-detail';
  const appClassName = view === 'report-detail'
    ? 'health-flow-app report-detail-print'
    : 'health-flow-app';
  return <div className={appClassName}><AppHeader account={account} view={reportViewActive ? 'report' : undefined} onNavigate={(key) => navigate(key)} onMenu={() => setMenuOpen(true)} onProfile={() => navigate('profile')} />
    {view === 'home' && <HomePage onOpenReport={() => openReport()} onOpenProfile={() => navigate('profile')} />}
    {view === 'report' && <ReportPage account={account} reportId={reportId} onBack={() => navigate('home')} onReportSaved={() => {}} />}
    {view === 'report-detail' && reportId && <ReportDetail account={account} reportId={reportId} onBack={() => navigate('home')} onContinueConfirm={(id) => navigate('report', id)} />}
    {view === 'profile' && <ProfilePage account={account} onBack={() => navigate('home')} onLogout={onLogout} onAccountChange={onAccountChange} onOpenReport={(id) => openReport(id)} />}
    <BottomNav view={view === 'report-detail' ? 'report' : view} onNavigate={handleBottomNav} />
    <MenuSheet account={account} open={menuOpen} onClose={() => setMenuOpen(false)} onOpenReport={() => openReport()} onOpenProfile={() => navigate('profile')} />
  </div>;
}

export default function App() {
  const [account, setAccount] = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => { getCurrentAccount().then(setAccount).catch(() => setAccount(null)).finally(() => setLoading(false)); }, []);
  if (loading) return <div className="app-loading"><Spin size="large" /></div>;
  if (!account) return <ConfigProvider theme={{ token: { colorPrimary: '#c98b28', borderRadius: 14 } }}><AntApp><AuthScreen onAuthenticated={setAccount} /></AntApp></ConfigProvider>;
  const logout = async () => { await logoutAccount().catch(() => {}); setAccount(null); message.success('已退出登录'); };
  return <ConfigProvider theme={{ token: { colorPrimary: '#c98b28', colorInfo: '#c98b28', borderRadius: 14, fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif' } }}><AntApp><HealthFlowApp account={account} onLogout={logout} onAccountChange={setAccount} /></AntApp></ConfigProvider>;
}
