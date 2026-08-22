import React, { useState } from 'react';
import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  BellOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  MenuOutlined,
  MessageOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { App as AntApp, Button, ConfigProvider, Drawer, Tooltip, message } from 'antd';
import Upload from './pages/Upload.jsx';

const NAV_ITEMS = [
  { key: 'consult', label: '中医问诊' },
  { key: 'blood', label: '验血咨询' },
  { key: 'report', label: '体检解读' },
  { key: 'body', label: '体脂检测' },
];

function BrandMark({ large = false }) {
  return (
    <img
      className={`brand-mark ${large ? 'brand-mark-large' : ''}`}
      src="/hst-club-logo.png"
      alt="HST Club"
    />
  );
}

function AppHeader({ view, onNavigate, onMenu }) {
  const [messageApi, contextHolder] = message.useMessage();
  const handleNav = (key) => {
    if (key === 'report') {
      onNavigate('report');
      return;
    }
    messageApi.info('该服务将在后续阶段开放');
  };

  return (
    <>
      {contextHolder}
      <header className="app-header">
        <Tooltip title="打开菜单">
          <button className="icon-button" type="button" aria-label="打开菜单" onClick={onMenu}>
            <MenuOutlined />
          </button>
        </Tooltip>
        <nav className="service-nav" aria-label="健康服务">
          {NAV_ITEMS.map((item) => (
            <button
              className={`service-nav-item ${view === item.key ? 'is-active' : ''}`}
              key={item.key}
              type="button"
              aria-current={view === item.key ? 'page' : undefined}
              onClick={() => handleNav(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="header-actions">
          <Tooltip title="通知">
            <button className="icon-button" type="button" aria-label="通知" onClick={() => messageApi.info('暂无新通知')}>
              <BellOutlined />
            </button>
          </Tooltip>
          <Tooltip title="消息">
            <button className="icon-button" type="button" aria-label="消息" onClick={() => messageApi.info('消息服务将在后续阶段开放')}>
              <MessageOutlined />
            </button>
          </Tooltip>
        </div>
      </header>
    </>
  );
}

function HomePage({ onOpenReport }) {
  return (
    <main className="home-page">
      <section className="home-hero" aria-labelledby="home-title">
        <BrandMark large />
        <h1 id="home-title">呵护您的健康</h1>
        <p className="hero-copy">从一份体检报告开始，查看异常指标和有依据的健康提示。</p>
      </section>

      <section className="health-actions" aria-label="健康管理">
        <button className="health-action-card health-action-primary" type="button" onClick={onOpenReport}>
          <span className="action-icon"><FileSearchOutlined /></span>
          <span className="action-copy">
            <strong>体检报告解读</strong>
            <span>上传报告，核对异常项</span>
          </span>
          <ArrowRightOutlined className="action-arrow" />
        </button>
        <div className="health-action-grid">
          <div className="health-action-tile">
            <FileTextOutlined />
            <span>健康报告</span>
            <small>完成报告后查看</small>
          </div>
          <div className="health-action-tile">
            <SafetyCertificateOutlined />
            <span>证据来源</span>
            <small>论文与 Claim 可追溯</small>
          </div>
        </div>
      </section>

      <section className="home-note" aria-label="服务边界">
        <SafetyCertificateOutlined />
        <p>这里提供报告信息整理与健康风险提示，不替代医生诊断。</p>
      </section>
    </main>
  );
}

function MenuSheet({ open, onClose, onOpenReport }) {
  return (
    <Drawer
      className="menu-sheet"
      placement="left"
      width="min(86vw, 340px)"
      open={open}
      onClose={onClose}
      title={<div className="sheet-title"><BrandMark /><strong>健康流</strong></div>}
    >
        <button className="sheet-link" type="button" onClick={() => { onOpenReport(); onClose(); }}>
          <FileSearchOutlined />
          <span>体检报告解读</span>
          <ArrowRightOutlined />
        </button>
        <div className="sheet-status">
          <UserOutlined />
          <div>
            <strong>当前会话</strong>
            <span>报告访问凭证仅用于本次追溯</span>
          </div>
        </div>
    </Drawer>
  );
}

function ReportPage({ onBack }) {
  return (
    <main className="report-page">
      <div className="report-heading">
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={onBack}>返回首页</Button>
        <div>
          <p className="eyebrow">健康管理 · 报告解读</p>
          <h1>体检报告解读</h1>
          <p>只对已确认的异常指标匹配已发布知识卡。</p>
        </div>
      </div>
      <Upload />
    </main>
  );
}

function HealthFlowApp() {
  const [view, setView] = useState('home');
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="health-flow-app">
      <AppHeader
        view={view === 'report' ? 'report' : undefined}
        onNavigate={setView}
        onMenu={() => setMenuOpen(true)}
      />
      {view === 'home' && <HomePage onOpenReport={() => setView('report')} />}
      {view === 'report' && <ReportPage onBack={() => setView('home')} />}
      <MenuSheet open={menuOpen} onClose={() => setMenuOpen(false)} onOpenReport={() => setView('report')} />
    </div>
  );
}

export default function App() {
  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: '#c98b28',
          colorInfo: '#c98b28',
          borderRadius: 14,
          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        },
      }}
    >
      <AntApp>
        <HealthFlowApp />
      </AntApp>
    </ConfigProvider>
  );
}
