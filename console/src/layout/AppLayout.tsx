/** Main app layout — sidebar + header + content area. */

import { Layout, Typography, Alert } from 'antd';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import TenantSelector from './TenantSelector';
import { useTenant } from '@/context/TenantContext';
import GradientText from '@/components/react-bits/GradientText';

const { Header, Sider, Content } = Layout;

export default function AppLayout() {
  const { isTenantSelected } = useTenant();

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={200} style={{ background: '#fff' }}>
        <div style={{ padding: '16px', textAlign: 'center', borderBottom: '1px solid #f0f0f0' }}>
          <GradientText
            colors={['#1890ff', '#722ed1', '#eb2f96', '#1890ff']}
            animationSpeed={6}
            className="sidebar-logo"
          >
            <span style={{ fontSize: 15, fontWeight: 600 }}>🤖 销售助手管理</span>
          </GradientText>
        </div>
        <Sidebar />
      </Sider>
      <Layout>
        <Header
          style={{
            background: '#fff',
            padding: '0 24px',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            borderBottom: '1px solid #f0f0f0',
          }}
        >
          <TenantSelector />
        </Header>
        <Content style={{ margin: 24, padding: 24, background: '#fff', borderRadius: 8, minHeight: 280 }}>
          {!isTenantSelected ? (
            <Alert
              type="info"
              message="请选择租户"
              description="在顶部输入租户 ID 以开始使用管理后台"
              showIcon
              style={{ marginTop: 24 }}
            />
          ) : (
            <Outlet />
          )}
        </Content>
      </Layout>
    </Layout>
  );
}
