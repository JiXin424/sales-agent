/** Layout for Agent-scoped routes /agents/:agentId/*.

Loads the opened Agent, sets it into AgentContext, renders an Agent-scoped
sidebar (no TenantSelector) and a status badge + Agent switcher in the header.
*/

import { useEffect } from 'react';
import { Layout, Menu, Tag, Typography, Alert, Spin } from 'antd';
import { Outlet, useNavigate, useLocation, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  AppstoreOutlined, BookOutlined, DashboardOutlined, EditOutlined, ExperimentOutlined, MessageOutlined,
  NodeIndexOutlined, SettingOutlined, TrophyOutlined, ApartmentOutlined, HistoryOutlined, ScheduleOutlined,
} from '@ant-design/icons';
import { getAgent } from '@/api/agents';
import { useAgent } from '@/context/AgentContext';
import { useTenant } from '@/context/TenantContext';
import type { AgentInstance, AgentStatus } from '@/api/types';

const { Header, Sider, Content } = Layout;

const STATUS_COLOR: Record<AgentStatus, string> = {
  draft: 'default',
  active: 'green',
  paused: 'orange',
  archived: 'red',
};

function agentNavItems(agentId: string) {
  const base = `/agents/${agentId}`;
  return [
    { key: `${base}/dashboard`, icon: <DashboardOutlined />, label: '运营面板' },
    { key: `${base}/overview`, icon: <AppstoreOutlined />, label: '概览' },
    { key: `${base}/knowledge`, icon: <BookOutlined />, label: '知识库' },
    { key: `${base}/ontology`, icon: <NodeIndexOutlined />, label: '本体探索' },
    { key: `${base}/graph-debug`, icon: <ApartmentOutlined />, label: '图调试' },
    { key: `${base}/history`, icon: <HistoryOutlined />, label: '会话历史' },
    { key: `${base}/optimization`, icon: <ExperimentOutlined />, label: '知识迭代' },
    { key: `${base}/prompts`, icon: <EditOutlined />, label: 'Prompt' },
    { key: `${base}/conversations`, icon: <MessageOutlined />, label: '对话记录' },
    { key: `${base}/sales-actions`, icon: <ScheduleOutlined />, label: '销售任务' },
    { key: `${base}/coach`, icon: <TrophyOutlined />, label: '教练' },
    { key: `${base}/settings`, icon: <SettingOutlined />, label: '设置' },
  ];
}

export default function AgentLayout() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const { setAgent } = useAgent();
  const { tenantId, setTenant } = useTenant();

  const { data: agent, isLoading, isError } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => getAgent(agentId!),
    enabled: !!agentId,
  });

  // Sync opened agent into context whenever it loads / changes.
  useEffect(() => {
    if (agent) setAgent(agent as AgentInstance);
  }, [agent, setAgent]);

  // 单 Agent 实例：把 agent 的 tenant 同步进 TenantContext，让运营面板等
  // 依赖 useTenant() 的页面能加载该租户的指标。仅在 tenant 不一致时同步一次，
  // 避免与 setTenant 的全量 query invalidate 形成循环。
  useEffect(() => {
    if (agent && agent.tenant_id && tenantId !== agent.tenant_id) {
      setTenant(agent.tenant_id, agent.name);
    }
  }, [agent, tenantId, setTenant]);

  if (!agentId) return null;
  if (isLoading) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin tip="加载 Agent…" />
      </div>
    );
  }
  if (isError || !agent) {
    return (
      <Alert
        type="error"
        showIcon
        message="Agent 不存在或无权访问"
        description={
          <Typography.Link onClick={() => navigate('/agents')}>返回 Agent 列表</Typography.Link>
        }
      />
    );
  }

  const items = agentNavItems(agentId);
  const selectedKey =
    items.find((it) => location.pathname.startsWith(it.key))?.key || `${agentId}/overview`;

  // graph-debug 保留侧边栏 + Header（导航不变），但去掉 Content 的
  // margin/padding 让 Mermaid 图在主区最大化放大。其它路由保持白卡片样式。
  const isGraphDebug = location.pathname.includes('/graph-debug');

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={208} style={{ background: '#fff' }}>
        <div style={{ padding: '14px 16px', borderBottom: '1px solid #f0f0f0' }}>
          <Typography.Text strong style={{ fontSize: 14 }}>🤖 {agent.name}</Typography.Text>
          <div style={{ marginTop: 6 }}>
            <Tag color={STATUS_COLOR[agent.status]}>{agent.status}</Tag>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {agent.agent_type}
            </Typography.Text>
          </div>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          items={items}
          onClick={({ key }) => navigate(key)}
          style={{ height: 'calc(100% - 70px)', borderRight: 0 }}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: '#fff', padding: '0 24px', display: 'flex',
            alignItems: 'center', borderBottom: '1px solid #f0f0f0', gap: 12,
          }}
        >
          <Typography.Text type="secondary">Agent 实例：</Typography.Text>
          <Typography.Text strong>{agent.name}</Typography.Text>
          <Tag color={STATUS_COLOR[agent.status]}>{agent.status}</Tag>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {agent.tenant_id}
          </Typography.Text>
        </Header>
        <Content
          style={
            isGraphDebug
              ? { margin: 0, padding: 0, background: '#fff', minHeight: 0 }
              : { margin: 24, padding: 24, background: '#fff', borderRadius: 8, minHeight: 280 }
          }
        >
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
