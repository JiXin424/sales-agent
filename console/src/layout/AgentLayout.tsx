/** Layout for Agent-scoped routes /agents/:agentId/*.

Loads the opened Agent, sets it into AgentContext, renders an Agent-scoped
sidebar (no TenantSelector) and a status badge + Agent switcher in the header.
*/

import { useEffect } from 'react';
import { Layout, Menu, Tag, Typography, Alert, Spin } from 'antd';
import { Outlet, useNavigate, useLocation, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  AppstoreOutlined, BookOutlined, EditOutlined, MessageOutlined,
  SettingOutlined, ExperimentOutlined, AlertOutlined, FileTextOutlined,
  RocketOutlined, CheckCircleOutlined, LikeOutlined, AuditOutlined, TrophyOutlined,
} from '@ant-design/icons';
import { getAgent } from '@/api/agents';
import { useAgent } from '@/context/AgentContext';
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
    { key: `${base}/overview`, icon: <AppstoreOutlined />, label: '概览' },
    { key: `${base}/setup`, icon: <CheckCircleOutlined />, label: '就绪/配置' },
    { key: `${base}/knowledge`, icon: <BookOutlined />, label: '知识库' },
    { key: `${base}/prompts`, icon: <EditOutlined />, label: 'Prompt' },
    { key: `${base}/channels`, icon: <MessageOutlined />, label: '渠道' },
    { key: `${base}/conversations`, icon: <MessageOutlined />, label: '对话记录' },
    { key: `${base}/feedback`, icon: <LikeOutlined />, label: '反馈管理' },
    { key: `${base}/review`, icon: <AuditOutlined />, label: '质量审查' },
    { key: `${base}/eval`, icon: <ExperimentOutlined />, label: 'Eval 回归' },
    { key: `${base}/alerts`, icon: <AlertOutlined />, label: '运维告警' },
    { key: `${base}/reports`, icon: <FileTextOutlined />, label: '报告' },
    { key: `${base}/coach`, icon: <TrophyOutlined />, label: '教练' },
    { key: `${base}/settings`, icon: <SettingOutlined />, label: '设置' },
  ];
}

export default function AgentLayout() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const { setAgent } = useAgent();

  const { data: agent, isLoading, isError } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => getAgent(agentId!),
    enabled: !!agentId,
  });

  // Sync opened agent into context whenever it loads / changes.
  useEffect(() => {
    if (agent) setAgent(agent as AgentInstance);
  }, [agent, setAgent]);

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
        <Content style={{ margin: 24, padding: 24, background: '#fff', borderRadius: 8, minHeight: 280 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
