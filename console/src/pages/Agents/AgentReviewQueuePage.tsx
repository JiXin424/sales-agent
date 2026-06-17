/** Agent-scoped quality review queue. */

import { Table, Tag } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getAgent, listAgentReviewQueue } from '@/api/agents';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

const PRIO_COLOR: Record<string, string> = {
  low: 'default', medium: 'blue', high: 'orange', critical: 'red',
};

interface ReviewRow {
  id: string; conversation_id: string; reason: string;
  priority: string; status: string; assignee: string | null; created_at: string;
}

export default function AgentReviewQueuePage() {
  const { agentId } = useParams<{ agentId: string }>();
  const { data: agent } = useQuery({
    queryKey: ['agent', agentId], queryFn: () => getAgent(agentId!), enabled: !!agentId,
  });
  const { data, isLoading, isError } = useQuery({
    queryKey: ['agent-review-queue', agentId],
    queryFn: () => listAgentReviewQueue(agentId!, 100, 0),
    enabled: !!agentId,
  });

  if (isLoading) return <LoadingState />;
  if (isError || !agent) return <ErrorState />;

  const columns = [
    { title: '原因', dataIndex: 'reason', key: 'reason', width: 160 },
    { title: '优先级', dataIndex: 'priority', key: 'priority', width: 100,
      render: (p: string) => <Tag color={PRIO_COLOR[p] || 'default'}>{p}</Tag> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 120 },
    { title: '会话', dataIndex: 'conversation_id', key: 'conversation_id', width: 150, ellipsis: true },
    { title: '负责人', dataIndex: 'assignee', key: 'assignee', width: 120,
      render: (a: string | null) => a ?? '-' },
    { title: '入队时间', dataIndex: 'created_at', key: 'created_at', width: 160,
      render: (v: string) => (v ? <RelativeTime iso={v} /> : '-') },
  ];

  return (
    <div>
      <PageHeader title="质量审查" description={`作用域：Agent「${agent.name}」`} />
      <Table
        rowKey="id"
        dataSource={(data?.items ?? []) as ReviewRow[]}
        columns={columns}
        pagination={{ pageSize: 20, total: data?.total ?? 0 }}
      />
    </div>
  );
}
