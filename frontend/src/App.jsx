import React from 'react';
import { Layout, Menu, Typography, Tag } from 'antd';
import {
  UploadOutlined,
} from '@ant-design/icons';
import Upload from './pages/Upload.jsx';

const { Sider, Header, Content } = Layout;

const NAV_ITEMS = [
  { key: 'upload', label: '报告上传与解读', icon: <UploadOutlined /> },
];

export default function App() {
  const current = NAV_ITEMS[0];

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={220} theme="light" style={{ borderRight: '1px solid #eef0f4' }}>
        <div className="brand">
          <span className="brand-logo">💙</span>
          <div>
            <div className="brand-name">健康流</div>
            <div className="brand-sub">HealthFlow</div>
          </div>
        </div>
        <Menu
          mode="inline"
          selectedKeys={['upload']}
          items={NAV_ITEMS}
          style={{ borderInlineEnd: 'none' }}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            padding: '0 24px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid #eef0f4',
          }}
        >
          <Typography.Title level={4} style={{ margin: 0 }}>
            {current?.label}
          </Typography.Title>
          <Tag color="blue" style={{ marginRight: 0 }}>后端服务：已连接</Tag>
        </Header>
        <Content style={{ padding: 24 }}><Upload /></Content>
      </Layout>
    </Layout>
  );
}
