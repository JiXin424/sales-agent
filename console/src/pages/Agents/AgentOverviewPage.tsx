/** Agent Overview — runtime status, readiness summary, recent activity. */

import { Card, Descriptions, Tag, Typography, Alert, List } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';
import { getAgent, getReadiness } from '@/api/agents';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

export default function AgentOverviewPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();

  const { data: agent, isLoading, isError } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => getAgent(agentId!),
    enabled: !!agentId,
  });

  const { data: readiness } = useQuery({
    queryKey: ['agent-readiness', agentId],
    queryFn: () => getReadiness(agentId!),
    enabled: !!agentId,
  });

  if (isLoading) return <LoadingState />;
  if (isError || !agent) return <ErrorState />;

  return (
    <div>
      <PageHeader title={`概览：${agent.name}`} description={agent.description || '—'} />
      {agent.status === 'draft' && (
        <Alert
          type="info" showIcon style={{ marginBottom: 16 }}
          message="该 Agent 尚未激活"
          description={
            <Typography.Link onClick={() => navigate(`/agents/${agent.id}/setup`)}>
              前往就绪检查并激活 →
            </Typography.Link>
          }
        />
      )}
      <Card style={{ marginBottom: 16 }}>
        <Descriptions column={2} size="small">
          <Descriptions.Item label="状态"><Tag>{agent.status}</Tag></Descriptions.Item>
          <Descriptions.Item label="类型">{agent.agent_type}</Descriptions.Item>
          <Descriptions.Item label="租户">{agent.tenant_id}</Descriptions.Item>
          <Descriptions.Item label="来源 Agent">{agent.source_agent_id ?? '原生创建'}</Descriptions.Item>
          <Descriptions.Item label="模型配置">{agent.model_config_ref}</Descriptions.Item>
          <Descriptions.Item label="默认 Agent">{agent.is_tenant_default ? '是' : '否'}</Descriptions.Item>
          <Descriptions.Item label="激活时间">
            {agent.activated_at ? <RelativeTime iso={agent.activated_at} /> : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {agent.created_at ? <RelativeTime iso={agent.created_at} /> : '—'}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="就绪检查摘要" size="small">
        {readiness ? (
          <List
            size="small"
            dataSource={readiness.checks}
            renderItem={(c) => (
              <List.Item>
                <List.Item.Meta
                  title={<span>{c.label}{c.required ? <Tag color="red" style={{ marginLeft: 8 }}>必填</Tag> : null}</span>}
                  description={c.reason || (c.passed ? '通过' : '未通过')}
                />
                <Tag color={c.passed ? 'green' : 'default'}>{c.passed ? '✓' : '✗'}</Tag>
              </List.Item>
            )}
          />
        ) : (
          <LoadingState />
        )}
      </Card>
    </div>
  );
}
