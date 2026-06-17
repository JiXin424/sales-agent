/** Agent-scoped eval runs. */

import { Table, Tag } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getAgent, listAgentEvalRuns } from '@/api/agents';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

const STATUS_COLOR: Record<string, string> = {
  running: 'processing', completed: 'green', failed: 'red',
};

interface EvalRow {
  id: string; eval_suite_id: string; status: string;
  total_cases: number; passed: number; failed: number; skipped: number;
  started_at: string | null; completed_at: string | null; created_at: string;
}

export default function AgentEvalRunsPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const { data: agent } = useQuery({
    queryKey: ['agent', agentId], queryFn: () => getAgent(agentId!), enabled: !!agentId,
  });
  const { data, isLoading, isError } = useQuery({
    queryKey: ['agent-eval-runs', agentId],
    queryFn: () => listAgentEvalRuns(agentId!, 100, 0),
    enabled: !!agentId,
  });

  if (isLoading) return <LoadingState />;
  if (isError || !agent) return <ErrorState />;

  const columns = [
    { title: '套件', dataIndex: 'eval_suite_id', key: 'eval_suite_id', width: 150, ellipsis: true },
    { title: '状态', dataIndex: 'status', key: 'status', width: 110,
      render: (s: string) => <Tag color={STATUS_COLOR[s] || 'default'}>{s}</Tag> },
    { title: '用例数', dataIndex: 'total_cases', key: 'total_cases', width: 90 },
    { title: '通过', dataIndex: 'passed', key: 'passed', width: 80,
      render: (n: number) => <Tag color="green">{n}</Tag> },
    { title: '失败', dataIndex: 'failed', key: 'failed', width: 80,
      render: (n: number) => (n ? <Tag color="red">{n}</Tag> : n) },
    { title: '完成时间', dataIndex: 'completed_at', key: 'completed_at', width: 160,
      render: (v: string | null) => (v ? <RelativeTime iso={v} /> : '-') },
  ];

  return (
    <div>
      <PageHeader title="Eval 回归" description={`作用域：Agent「${agent.name}」`} />
      <Table
        rowKey="id"
        dataSource={(data?.items ?? []) as EvalRow[]}
        columns={columns}
        pagination={{ pageSize: 20, total: data?.total ?? 0 }}
      />
    </div>
  );
}
