import { useState } from 'react';
import { Table, Input, Select, Space, Button } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTenant } from '@/context/TenantContext';
import { listConversations } from '@/api/admin';
import { queryKeys } from '@/utils/queryKeys';
import { TASK_TYPE_LABELS } from '@/utils/constants';
import { truncate } from '@/utils/formatters';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import StatusTag from '@/components/StatusTag';
import RelativeTime from '@/components/RelativeTime';

const taskTypeOptions = Object.entries(TASK_TYPE_LABELS).map(([value, label]) => ({ value, label }));

export default function ConversationListPage() {
  const { tenantId } = useTenant();
  const navigate = useNavigate();
  const [filters, setFilters] = useState<{ user_id?: string; task_type?: string; status?: string }>({});
  const [page, setPage] = useState(1);
  const pageSize = 20;

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: queryKeys.conversations(tenantId!, { ...filters, page }),
    queryFn: () =>
      listConversations(tenantId!, {
        ...filters,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      }),
    enabled: !!tenantId,
  });

  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState message={(error as Error).message} onRetry={() => refetch()} />;

  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 120,
      render: (id: string) => (
        <a onClick={() => navigate(`/conversations/${id}`)}>{id.slice(0, 8)}</a>
      ),
    },
    { title: '用户', dataIndex: 'user_id', key: 'user_id', width: 100 },
    {
      title: '消息',
      dataIndex: 'message',
      key: 'message',
      ellipsis: true,
      render: (msg: string) => truncate(msg, 60),
    },
    {
      title: '任务类型',
      dataIndex: 'task_type',
      key: 'task_type',
      width: 120,
      render: (tt: string) => TASK_TYPE_LABELS[tt] || tt || '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (s: string) => <StatusTag status={s} />,
    },
    {
      title: '风险',
      dataIndex: 'risk',
      key: 'risk',
      width: 80,
      render: (risk: Record<string, unknown> | null) => {
        if (!risk) return '-';
        const level = (risk as Record<string, string>).level || 'none';
        const colorMap: Record<string, string> = { none: 'default', low: 'blue', medium: 'orange', high: 'red' };
        return <span style={{ color: colorMap[level] || 'default' }}>{level}</span>;
      },
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 130,
      render: (t: string) => <RelativeTime iso={t} />,
    },
  ];

  return (
    <>
      <PageHeader title="对话记录" description="查看和管理所有销售对话" />
      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          placeholder="用户 ID"
          allowClear
          style={{ width: 150 }}
          onChange={(e) => setFilters((f) => ({ ...f, user_id: e.target.value || undefined }))}
        />
        <Select
          placeholder="任务类型"
          allowClear
          style={{ width: 150 }}
          options={taskTypeOptions}
          onChange={(v) => setFilters((f) => ({ ...f, task_type: v }))}
        />
        <Select
          placeholder="状态"
          allowClear
          style={{ width: 120 }}
          options={[
            { value: 'completed', label: '完成' },
            { value: 'failed', label: '失败' },
          ]}
          onChange={(v) => setFilters((f) => ({ ...f, status: v }))}
        />
        <Button onClick={() => { setPage(1); refetch(); }}>查询</Button>
      </Space>
      <Table
        rowKey="id"
        columns={columns}
        dataSource={data?.items || []}
        pagination={{
          current: page,
          pageSize,
          total: data?.total || 0,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
        size="small"
        onRow={(record) => ({ onClick: () => navigate(`/conversations/${record.id}`), style: { cursor: 'pointer' } })}
      />
    </>
  );
}
