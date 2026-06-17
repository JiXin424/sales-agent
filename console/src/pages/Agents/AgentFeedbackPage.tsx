/** Agent-scoped feedback — only this Agent's feedback (agent_id filter). */

import { Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getAgent, listAgentFeedback } from '@/api/agents';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

interface FbRow {
  id: string;
  conversation_id: string;
  user_id: string;
  rating: string;
  review_status: string;
  created_at: string;
}

export default function AgentFeedbackPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const { data: agent } = useQuery({
    queryKey: ['agent', agentId], queryFn: () => getAgent(agentId!), enabled: !!agentId,
  });
  const { data, isLoading, isError } = useQuery({
    queryKey: ['agent-feedback', agentId],
    queryFn: () => listAgentFeedback(agentId!, 100, 0),
    enabled: !!agentId,
  });

  if (isLoading) return <LoadingState />;
  if (isError) return <ErrorState />;

  const columns = [
    { title: '评分', dataIndex: 'rating', key: 'rating', width: 90,
      render: (r: string) => <Tag color={r === 'up' ? 'green' : 'red'}>{r === 'up' ? '👍' : '👎'}</Tag> },
    { title: '会话', dataIndex: 'conversation_id', key: 'conversation_id', width: 150, ellipsis: true,
      render: (c: string) => <Typography.Text ellipsis style={{ maxWidth: 140 }}>{c}</Typography.Text> },
    { title: '用户', dataIndex: 'user_id', key: 'user_id', width: 140, ellipsis: true },
    { title: '审查状态', dataIndex: 'review_status', key: 'review_status', width: 110,
      render: (s: string) => <Tag>{s}</Tag> },
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 160,
      render: (v: string) => (v ? <RelativeTime iso={v} /> : '-') },
  ];

  return (
    <div>
      <PageHeader title="反馈管理" description={`作用域：Agent「${agent?.name ?? agentId}」（仅该 Agent 的反馈）`} />
      <Table
        rowKey="id"
        dataSource={(data?.items ?? []) as FbRow[]}
        columns={columns}
        pagination={{ pageSize: 20, total: data?.total ?? 0 }}
      />
    </div>
  );
}
