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
  exchangeTicket,
  getCurrentAccount,
  getReportHistory,
  logoutAccount,
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

function NoSessionScreen() {
  // #172 之后本应用**不再有登录页**。没有会话时不能回退到登录界面
  // （票面：回退会让整个应用白屏），而是在这里说明「请从商城入口进入」。
  // 商城入口 URL 由部署配置提供；未配置时只说明、不给一个猜的链接。
  return (
    <main className="auth-page">
      <div className="auth-brand"><BrandMark large /></div>
      <h1>请从商城入口进入</h1>
      <p className="auth-copy">本页需要由商城签发的一次性登录票据建立会话。</p>
      <Card className="auth-card">
        <Alert
          type="info"
          showIcon
          title="当前没有有效会话"
          description="请回到商城，在健康检测入口重新进入本页。"
        />
      </Card>
      <p className="auth-boundary"><SafetyCertificateOutlined /> 本页只提供报告解读与健康信息参考，不替代医生诊断。</p>
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
    <div className="sheet-status"><MailOutlined /><div><strong>{account.display_name}</strong><span>来自商城的会话</span></div></div>
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

function ProfilePage({ account, onBack, onLogout, onOpenReport }) {
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [securityOpen, setSecurityOpen] = useState(false);

  const loadHistory = () => getReportHistory().then(setHistory).catch((err) => setError(err.message)).finally(() => setLoading(false));
  useEffect(() => { loadHistory(); }, []);
  return <main className="profile-page"><div className="profile-heading"><Button type="text" icon={<ArrowLeftOutlined />} onClick={onBack}>返回</Button><h1>个人中心</h1><span /></div>
    <section className="profile-identity"><span className="profile-avatar"><UserOutlined /></span><div><h2>{account.display_name}</h2><p>来自商城的会话</p></div></section>
    <div className="profile-links"><button className="profile-link" type="button" onClick={() => setSecurityOpen(true)}><LockOutlined /><span>账户与安全</span><small>商城票据 · 会话安全</small><ArrowRightOutlined /></button><button className="profile-link" type="button" onClick={() => document.getElementById('report-history')?.scrollIntoView({ behavior: 'smooth' })}><HistoryOutlined /><span>报告历史</span><small>{history.length} 份报告</small><ArrowRightOutlined /></button><button className="profile-link" type="button" onClick={() => message.info('通知设置将在后续阶段开放')}><BellOutlined /><span>通知设置</span><small>暂未开放</small><ArrowRightOutlined /></button><button className="profile-link" type="button" onClick={() => message.info('帮助与反馈将在后续阶段开放')}><SafetyCertificateOutlined /><span>帮助与反馈</span><small>暂未开放</small><ArrowRightOutlined /></button></div>
    <section className="history-section" id="report-history"><div className="section-title"><h2>报告历史</h2><Typography.Text type="secondary">仅显示当前账户</Typography.Text></div>{error && <Alert type="error" showIcon title={error} />}{loading ? <div className="history-loading"><Spin /></div> : history.length === 0 ? <Empty description="还没有报告记录" /> : <List dataSource={history} renderItem={(item) => <List.Item actions={[<Button type="link" onClick={() => onOpenReport(item.id)} key="open">查看</Button>]}><List.Item.Meta title={`${item.report_type || '体检报告'} · ${new Date(item.created_at).toLocaleDateString('zh-CN')}`} description={<span>{item.department || '未填写科室'} · {item.metric_count} 项指标{abnormalSummary(item)}</span>} /><Tag color={item.status === 'assessed' ? 'green' : 'gold'}>{statusLabel(item.status)}</Tag></List.Item>} />}</section>
    <Button className="logout-button" danger icon={<LogoutOutlined />} onClick={onLogout}>退出登录</Button>
    <Modal title="账户与安全" open={securityOpen} onCancel={() => setSecurityOpen(false)} footer={<Button type="primary" onClick={() => setSecurityOpen(false)}>知道了</Button>}><p>本应用的会话由商城签发的登录票据建立。</p><p>退出登录后会话立即失效；再次使用请从商城入口重新进入。</p></Modal>
  </main>;
}

function HealthFlowApp({ account, onLogout }) {
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
    {view === 'profile' && <ProfilePage account={account} onBack={() => navigate('home')} onLogout={onLogout} onOpenReport={(id) => openReport(id)} />}
    <BottomNav view={view === 'report-detail' ? 'report' : view} onNavigate={handleBottomNav} />
    <MenuSheet account={account} open={menuOpen} onClose={() => setMenuOpen(false)} onOpenReport={() => openReport()} onOpenProfile={() => navigate('profile')} />
  </div>;
}

export default function App() {
  const [account, setAccount] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // #172 之后**唯一的建会话入口**是商城跳转带来的票据。启动顺序：
    // 地址上有 `ticket` 就先兑换（兑换会种下会话 cookie），再读当前主体。
    // 兑换失败不渲染登录页——那是票面禁止的回退；这里交给 NoSessionScreen
    // 说明「请从商城入口进入」。
    const params = new URLSearchParams(window.location.search);
    const ticket = params.get('ticket');
    // **票据是一次性的，绝不能被同一个页面加载花两次。** React StrictMode 在开发态
    // 会把 effect 跑两遍，两次请求里第二次必定失败；若它的 rejection 后到，
    // `setAccount(null)` 会把刚建好的会话盖掉。这里用一个模块级标记保证
    // 「同一张票在本页只兑换一次」（生产也可能因用户重试/重复挂载遇到同一形状）。
    const ticketKey = ticket ? `healthflow.ticket.spent.${ticket.slice(-12)}` : '';
    const alreadySpent = ticketKey && window.sessionStorage.getItem(ticketKey);
    if (alreadySpent) {
      params.delete('ticket');
      window.history.replaceState({}, '', `${window.location.pathname}${params.toString() ? `?${params}` : ''}${window.location.hash}`);
    }
    const bootstrap = ticket && !alreadySpent
      ? exchangeTicket(ticket)
        .then((subject) => {
          // 票据是一次性的：留在地址栏里会让用户刷新时拿到一个已被消费的票。
          if (ticketKey) window.sessionStorage.setItem(ticketKey, '1');
          params.delete('ticket');
          const query = params.toString();
          window.history.replaceState({}, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`);
          return subject;
        })
      : getCurrentAccount();
    bootstrap.then(setAccount).catch(() => setAccount(null)).finally(() => setLoading(false));
  }, []);
  if (loading) return <div className="app-loading"><Spin size="large" /></div>;
  if (!account) return <ConfigProvider theme={{ token: { colorPrimary: '#c98b28', borderRadius: 14 } }}><AntApp><NoSessionScreen /></AntApp></ConfigProvider>;
  const logout = async () => { await logoutAccount().catch(() => {}); setAccount(null); message.success('已退出登录'); };
  return <ConfigProvider theme={{ token: { colorPrimary: '#c98b28', colorInfo: '#c98b28', borderRadius: 14, fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif' } }}><AntApp><HealthFlowApp account={account} onLogout={logout} /></AntApp></ConfigProvider>;
}
