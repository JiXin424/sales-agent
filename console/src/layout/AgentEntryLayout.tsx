/** Layout for the Agent-first entry routes (/agents list, /agents/new).

No TenantSelector and no tenant gate — dedicated mode auto-resolves the
instance tenant on the backend, so the list is the product home without
requiring the user to pick a tenant first. Legacy tenant-aggregate routes
keep using AppLayout (with the selector).
*/

import { Layout, Typography, Button } from 'antd';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import GradientText from '@/components/react-bits/GradientText';

const { Header, Sider, Content } = Layout;

export default function AgentEntryLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const isList = location.pathname === '/agents' || location.pathname === '/agents/';

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={200} style={{ background: '#fff' }}>
        <div style={{ padding: '16px', textAlign: 'center', borderBottom: '1px solid #f0f0f0' }}>
          <GradientText
            colors={['#1890ff', '#722ed1', '#eb2f96', '#1890ff']}
            animationSpeed={6}
            className="sidebar-logo"
          >
            <span style={{ fontSize: 15, fontWeight: 600 }}>🤖 销售助手</span>
          </GradientText>
        </div>
        <div style={{ padding: 12 }}>
          <Button
            block type={isList ? 'primary' : 'default'}
            onClick={() => navigate('/agents')}
          >
            Agent 列表
          </Button>
        </div>
      </Sider>
      <Layout>
        <Header
          style={{
            background: '#fff', padding: '0 24px',
            display: 'flex', alignItems: 'center',
            borderBottom: '1px solid #f0f0f0',
          }}
        >
          <Typography.Text type="secondary">一个 Agent，一个管理页</Typography.Text>
        </Header>
        <Content style={{ margin: 24, padding: 24, background: '#fff', borderRadius: 8, minHeight: 280 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
