/** Agent-scoped pilot reports. */

import { Table, Tag } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getAgent, listAgentReports } from '@/api/agents';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

const STATUS_COLOR: Record<string, string> = {
  generating: 'processing', completed: 'green', failed: 'red',
};

interface ReportRow {
  id: string; report_type: string; start_date: string; end_date: string;
  status: string; created_at: string; updated_at: string;
}

export default function AgentReportsPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const { data: agent } = useQuery({
    queryKey: ['agent', agentId], queryFn: () => getAgent(agentId!), enabled: !!agentId,
  });
  const { data, isLoading, isError } = useQuery({
    queryKey: ['agent-reports', agentId],
    queryFn: () => listAgentReports(agentId!, 100, 0),
    enabled: !!agentId,
  });

  if (isLoading) return <LoadingState />;
  if (isError || !agent) return <ErrorState />;

  const columns = [
    { title: '类型', dataIndex: 'report_type', key: 'report_type', width: 110 },
    { title: '区间', key: 'range', width: 220,
      render: (_: unknown, r: ReportRow) => `${r.start_date} ~ ${r.end_date}` },
    { title: '状态', dataIndex: 'status', key: 'status', width: 120,
      render: (s: string) => <Tag color={STATUS_COLOR[s] || 'default'}>{s}</Tag> },
    { title: '生成时间', dataIndex: 'created_at', key: 'created_at', width: 160,
      render: (v: string) => (v ? <RelativeTime iso={v} /> : '-') },
  ];

  return (
    <div>
      <PageHeader title="Pilot 报告" description={`作用域：Agent「${agent.name}」`} />
      <Table
        rowKey="id"
        dataSource={(data?.items ?? []) as ReportRow[]}
        columns={columns}
        pagination={{ pageSize: 20, total: data?.total ?? 0 }}
      />
    </div>
  );
}
