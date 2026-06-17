/** Agent-scoped conversations — only this Agent's conversations (agent_id filter). */

import { Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getAgent, listAgentConversations } from '@/api/agents';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

interface ConvRow {
  id: string;
  user_id: string;
  channel: string;
  message: string;
  task_type: string | null;
  status: string;
  created_at: string;
}

export default function AgentConversationsPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const { data: agent } = useQuery({
    queryKey: ['agent', agentId], queryFn: () => getAgent(agentId!), enabled: !!agentId,
  });
  const { data, isLoading, isError } = useQuery({
    queryKey: ['agent-conversations', agentId],
    queryFn: () => listAgentConversations(agentId!, 100, 0),
    enabled: !!agentId,
  });

  if (isLoading) return <LoadingState />;
  if (isError) return <ErrorState />;

  const columns = [
    { title: '消息', dataIndex: 'message', key: 'message', ellipsis: true,
      render: (m: string) => <Typography.Text ellipsis style={{ maxWidth: 320 }}>{m}</Typography.Text> },
    { title: '任务', dataIndex: 'task_type', key: 'task_type', width: 150,
      render: (t: string | null) => (t ? <Tag>{t}</Tag> : <Tag>fast</Tag>) },
    { title: '渠道', dataIndex: 'channel', key: 'channel', width: 110 },
    { title: '用户', dataIndex: 'user_id', key: 'user_id', width: 140, ellipsis: true },
    { title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (s: string) => <Tag color={s === 'completed' ? 'green' : 'default'}>{s}</Tag> },
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 160,
      render: (v: string) => (v ? <RelativeTime iso={v} /> : '-') },
  ];

  return (
    <div>
      <PageHeader
        title="对话记录"
        description={`作用域：Agent「${agent?.name ?? agentId}」（仅该 Agent 的会话）`}
      />
      <Table
        rowKey="id"
        dataSource={(data?.items ?? []) as ConvRow[]}
        columns={columns}
        pagination={{ pageSize: 20, total: data?.total ?? 0 }}
      />
    </div>
  );
}
