/** Agent-scoped knowledge — documents visible to this Agent + scope mode. */

import { Table, Tag, Typography, Card, Descriptions, Alert } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getAgent, listAgentDocuments } from '@/api/agents';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

interface DocRow {
  id: string;
  title: string;
  status: string;
  created_at: string;
}

const SCOPE_LABEL: Record<string, string> = {
  tenant_all: '租户全量知识',
  document_subset: '指定文档子集',
  source_file_subset: '指定源文件子集',
};

export default function AgentKnowledgePage() {
  const { agentId } = useParams<{ agentId: string }>();
  const { data: agent, isLoading: agentLoading } = useQuery({
    queryKey: ['agent', agentId], queryFn: () => getAgent(agentId!), enabled: !!agentId,
  });
  const { data, isLoading, isError } = useQuery({
    queryKey: ['agent-documents', agentId],
    queryFn: () => listAgentDocuments(agentId!, 100, 0),
    enabled: !!agentId,
  });

  if (agentLoading || isLoading) return <LoadingState />;
  if (isError || !agent) return <ErrorState />;

  const columns = [
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true,
      render: (t: string) => <Typography.Text ellipsis style={{ maxWidth: 360 }}>{t}</Typography.Text> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 110,
      render: (s: string) => <Tag color={s === 'active' ? 'green' : 'default'}>{s}</Tag> },
    { title: '文档 ID', dataIndex: 'id', key: 'id', width: 150, ellipsis: true },
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 160,
      render: (v: string) => (v ? <RelativeTime iso={v} /> : '-') },
  ];

  return (
    <div>
      <PageHeader title="知识库" description={`作用域：Agent「${agent.name}」`} />
      <Card size="small" style={{ marginBottom: 16 }}>
        <Descriptions column={2} size="small">
          <Descriptions.Item label="知识作用域">
            <Tag color="blue">{SCOPE_LABEL['tenant_all']}</Tag>
            {agent.knowledge_scope_id ? '（已配置）' : '（未配置，回退租户全量）'}
          </Descriptions.Item>
          <Descriptions.Item label="作用域 ID">{agent.knowledge_scope_id ?? '—'}</Descriptions.Item>
        </Descriptions>
      </Card>
      {(data?.total ?? 0) === 0 && (
        <Alert
          type="info" showIcon style={{ marginBottom: 16 }}
          message="该 Agent 作用域内暂无文档"
          description="租户全量模式下需先为租户导入知识；子集模式需在设置中配置文档 ID。"
        />
      )}
      <Table
        rowKey="id"
        dataSource={(data?.items ?? []) as DocRow[]}
        columns={columns}
        pagination={{ pageSize: 20, total: data?.total ?? 0 }}
      />
    </div>
  );
}
