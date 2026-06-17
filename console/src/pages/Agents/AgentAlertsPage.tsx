/** Agent-scoped ops alerts. */

import { Table, Tag } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getAgent, listAgentAlerts } from '@/api/agents';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

const SEV_COLOR: Record<string, string> = { info: 'blue', warning: 'orange', critical: 'red' };

interface AlertRow {
  id: string; severity: string; metric: string; status: string;
  observed_value: number; last_seen_at: string | null; created_at: string;
}

export default function AgentAlertsPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const { data: agent } = useQuery({
    queryKey: ['agent', agentId], queryFn: () => getAgent(agentId!), enabled: !!agentId,
  });
  const { data, isLoading, isError } = useQuery({
    queryKey: ['agent-alerts', agentId],
    queryFn: () => listAgentAlerts(agentId!, 100, 0),
    enabled: !!agentId,
  });

  if (isLoading) return <LoadingState />;
  if (isError || !agent) return <ErrorState />;

  const columns = [
    { title: '级别', dataIndex: 'severity', key: 'severity', width: 100,
      render: (s: string) => <Tag color={SEV_COLOR[s] || 'default'}>{s}</Tag> },
    { title: '指标', dataIndex: 'metric', key: 'metric', width: 180 },
    { title: '状态', dataIndex: 'status', key: 'status', width: 120 },
    { title: '观测值', dataIndex: 'observed_value', key: 'observed_value', width: 100 },
    { title: '最近触发', dataIndex: 'last_seen_at', key: 'last_seen_at', width: 160,
      render: (v: string | null) => (v ? <RelativeTime iso={v} /> : '-') },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 160,
      render: (v: string) => (v ? <RelativeTime iso={v} /> : '-') },
  ];

  return (
    <div>
      <PageHeader title="运维告警" description={`作用域：Agent「${agent.name}」（仅该 Agent 的告警）`} />
      <Table
        rowKey="id"
        dataSource={(data?.items ?? []) as AlertRow[]}
        columns={columns}
        pagination={{ pageSize: 20, total: data?.total ?? 0 }}
      />
    </div>
  );
}
