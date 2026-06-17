import { useState } from 'react';
import { Button, Select, Table, Space, Modal, message, Typography } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useTenant } from '@/context/TenantContext';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listPromptVersions,
  activatePromptVersion,
  archivePromptVersion,
} from '@/api/prompts';
import { queryKeys } from '@/utils/queryKeys';
import { TASK_TYPE_LABELS } from '@/utils/constants';
import { truncate } from '@/utils/formatters';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import StatusTag from '@/components/StatusTag';
import RelativeTime from '@/components/RelativeTime';

const { Text } = Typography;

export default function PromptListPage() {
  const { tenantId } = useTenant();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // ---- Filters ----
  const [taskType, setTaskType] = useState<string | undefined>(undefined);
  const [status, setStatus] = useState<string | undefined>(undefined);

  // ---- Query ----
  const query = useQuery({
    queryKey: queryKeys.prompts(tenantId!, { task_type: taskType, status }),
    queryFn: () =>
      listPromptVersions(tenantId!, {
        task_type: taskType,
        status,
        limit: 100,
      }),
    enabled: !!tenantId,
  });

  // ---- Mutations ----
  const activateMutation = useMutation({
    mutationFn: (id: string) => activatePromptVersion(tenantId!, id),
    onSuccess: () => {
      message.success('已激活');
      queryClient.invalidateQueries({ queryKey: queryKeys.prompts(tenantId!) });
    },
    onError: () => message.error('激活失败'),
  });

  const archiveMutation = useMutation({
    mutationFn: (id: string) => archivePromptVersion(tenantId!, id),
    onSuccess: () => {
      message.success('已归档');
      queryClient.invalidateQueries({ queryKey: queryKeys.prompts(tenantId!) });
    },
    onError: () => message.error('归档失败'),
  });

  // ---- Task type options ----
  const taskTypeOptions = Object.entries(TASK_TYPE_LABELS).map(([value, label]) => ({
    value,
    label,
  }));

  const statusOptions = [
    { value: 'draft', label: '草稿' },
    { value: 'active', label: '活跃' },
    { value: 'archived', label: '已归档' },
  ];

  // ---- Columns ----
  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 140,
      render: (id: string) => (
        <a onClick={() => navigate(`/prompts/${id}/edit`)}>
          <Text style={{ fontSize: 12 }}>{truncate(id, 12)}</Text>
        </a>
      ),
    },
    {
      title: '任务类型',
      dataIndex: 'task_type',
      key: 'task_type',
      width: 140,
      render: (type: string) => TASK_TYPE_LABELS[type] || type,
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 100,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (s: string) => (
        <StatusTag status={s} label={s === 'active' ? '活跃' : s === 'draft' ? '草稿' : '已归档'} />
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (desc: string) => truncate(desc, 60),
    },
    {
      title: '时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 140,
      render: (iso: string) => <RelativeTime iso={iso} />,
    },
    {
      title: '操作',
      key: 'actions',
      width: 240,
      render: (_: unknown, row: { id: string; status: string }) => (
        <Space size={4}>
          <Button size="small" onClick={() => navigate(`/prompts/${row.id}/edit`)}>
            编辑
          </Button>
          {row.status === 'draft' && (
            <Button
              size="small"
              type="primary"
              onClick={() => {
                Modal.confirm({
                  title: '确认激活',
                  content: '激活后该版本将用于生产环境，确定激活？',
                  onOk: () => activateMutation.mutate(row.id),
                });
              }}
            >
              激活
            </Button>
          )}
          {(row.status === 'draft' || row.status === 'active') && (
            <Button
              size="small"
              danger
              onClick={() => {
                Modal.confirm({
                  title: '确认归档',
                  content: '归档后该版本将不再可用，确定归档？',
                  onOk: () => archiveMutation.mutate(row.id),
                });
              }}
            >
              归档
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="提示词管理"
        description="管理各任务类型的提示词版本"
        actions={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => navigate('/prompts/new')}
          >
            新建 Prompt
          </Button>
        }
      />

      {/* Filters */}
      <Space style={{ marginBottom: 16 }} size={12}>
        <Select
          allowClear
          placeholder="任务类型"
          style={{ width: 180 }}
          value={taskType}
          onChange={setTaskType}
          options={taskTypeOptions}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 140 }}
          value={status}
          onChange={setStatus}
          options={statusOptions}
        />
      </Space>

      {/* Table */}
      {query.isLoading ? (
        <LoadingState tip="加载提示词列表..." />
      ) : query.isError ? (
        <ErrorState
          message="加载提示词列表失败"
          onRetry={() => query.refetch()}
        />
      ) : (
        <Table
          dataSource={query.data?.items ?? []}
          columns={columns}
          rowKey="id"
          size="small"
          pagination={{ pageSize: 20, showSizeChanger: true }}
        />
      )}
    </>
  );
}
