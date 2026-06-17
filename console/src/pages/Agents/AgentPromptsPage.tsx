/** Agent-scoped prompts — tenant active versions + this Agent's independent copies. */

import { Table, Tag, Typography, Card, Descriptions, Empty } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getAgent, listAgentPrompts } from '@/api/agents';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

interface PromptRow {
  id: string; task_type: string; version: string; status: string;
  description: string; agent_scoped: boolean; created_at: string; updated_at: string;
}

export default function AgentPromptsPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const { data: agent } = useQuery({
    queryKey: ['agent', agentId], queryFn: () => getAgent(agentId!), enabled: !!agentId,
  });
  const { data, isLoading, isError } = useQuery({
    queryKey: ['agent-prompts', agentId],
    queryFn: () => listAgentPrompts(agentId!, 100, 0),
    enabled: !!agentId,
  });

  if (isLoading) return <LoadingState />;
  if (isError || !agent) return <ErrorState />;

  const columns = [
    { title: '任务类型', dataIndex: 'task_type', key: 'task_type', width: 180 },
    { title: '版本', dataIndex: 'version', key: 'version', width: 120 },
    { title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (s: string) => <Tag color={s === 'active' ? 'green' : 'default'}>{s}</Tag> },
    { title: '归属', dataIndex: 'agent_scoped', key: 'agent_scoped', width: 110,
      render: (b: boolean) => b ? <Tag color="blue">本 Agent 副本</Tag> : <Tag>租户级</Tag> },
    { title: '说明', dataIndex: 'description', key: 'description', ellipsis: true },
    { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', width: 160,
      render: (v: string) => (v ? <RelativeTime iso={v} /> : '-') },
  ];

  return (
    <div>
      <PageHeader title="Prompt 管理" description={`作用域：Agent「${agent.name}」`} />
      <Card size="small" style={{ marginBottom: 16 }}>
        <Descriptions column={2} size="small">
          <Descriptions.Item label="Prompt 集合">{agent.prompt_set_id ?? '—（回退租户默认）'}</Descriptions.Item>
          <Descriptions.Item label="活跃映射">
            {data?.prompt_set_mapping && Object.keys(data.prompt_set_mapping).length
              ? Object.entries(data.prompt_set_mapping).map(([k]) => <Tag key={k}>{k}</Tag>)
              : <Typography.Text type="secondary">空（使用租户/默认）</Typography.Text>}
          </Descriptions.Item>
        </Descriptions>
      </Card>
      <Table
        rowKey="id"
        dataSource={(data?.items ?? []) as PromptRow[]}
        columns={columns}
        locale={{ emptyText: <Empty description="暂无 Prompt 版本" /> }}
        pagination={{ pageSize: 20, total: data?.total ?? 0 }}
      />
    </div>
  );
}
