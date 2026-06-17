/** Agent-scoped channels — config WITHOUT secrets (only ref key names shown). */

import { Table, Tag, Typography, Card, Alert, Empty } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getAgent, listAgentChannels } from '@/api/agents';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

interface ChannelRow {
  id: string; channel: string; status: string;
  config: Record<string, unknown>; secret_ref_keys: string[];
  created_at: string; updated_at: string;
}

const STATUS_COLOR: Record<string, string> = {
  not_configured: 'default', configured: 'blue', verified: 'green', disabled: 'red',
};

export default function AgentChannelsPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const { data: agent } = useQuery({
    queryKey: ['agent', agentId], queryFn: () => getAgent(agentId!), enabled: !!agentId,
  });
  const { data, isLoading, isError } = useQuery({
    queryKey: ['agent-channels', agentId],
    queryFn: () => listAgentChannels(agentId!),
    enabled: !!agentId,
  });

  if (isLoading) return <LoadingState />;
  if (isError || !agent) return <ErrorState />;

  const columns = [
    { title: '渠道', dataIndex: 'channel', key: 'channel', width: 120 },
    { title: '状态', dataIndex: 'status', key: 'status', width: 130,
      render: (s: string) => <Tag color={STATUS_COLOR[s] || 'default'}>{s}</Tag> },
    { title: '配置', dataIndex: 'config', key: 'config', ellipsis: true,
      render: (c: Record<string, unknown>) => (
        <Typography.Text ellipsis style={{ maxWidth: 280 }}>{JSON.stringify(c)}</Typography.Text>
      ) },
    { title: '密钥引用', dataIndex: 'secret_ref_keys', key: 'secret_ref_keys', width: 200,
      render: (keys: string[]) => keys.length
        ? keys.map((k) => <Tag key={k}>{k}</Tag>)
        : <Typography.Text type="secondary">无</Typography.Text> },
    { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', width: 160,
      render: (v: string) => (v ? <RelativeTime iso={v} /> : '-') },
  ];

  return (
    <div>
      <PageHeader title="渠道配置" description={`作用域：Agent「${agent.name}」`} />
      <Alert
        type="info" showIcon style={{ marginBottom: 16 }}
        message="密钥安全"
        description="仅展示密钥引用名称，绝不返回明文；克隆渠道仅创建空壳。"
      />
      <Card size="small" style={{ marginBottom: 16 }}>
        <Typography.Text type="secondary">
          渠道当前用于运行时消息路由（如钉钉）。状态变为 verified 后，该渠道消息将绑定到本 Agent。
        </Typography.Text>
      </Card>
      <Table
        rowKey="id"
        dataSource={(data?.items ?? []) as ChannelRow[]}
        columns={columns}
        locale={{ emptyText: <Empty description="该 Agent 未配置渠道" /> }}
        pagination={false}
      />
    </div>
  );
}
