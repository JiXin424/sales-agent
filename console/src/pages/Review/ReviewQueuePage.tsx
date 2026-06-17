import { useState } from 'react';
import { Table, Tag, Button, Select, Space, Modal, message } from 'antd';
import { ScanOutlined, CheckCircleOutlined } from '@ant-design/icons';
import { useTenant } from '@/context/TenantContext';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import * as pilotApi from '@/api/pilot';
import { queryKeys } from '@/utils/queryKeys';
import type { ReviewItem, ReviewReason, ReviewItemStatus } from '@/api/types';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

const REASON_LABELS: Record<string, string> = {
  negative_feedback: '负反馈',
  high_risk: '高风险',
  low_score: '低评分',
  model_error: '模型错误',
  retrieval_miss: '检索缺失',
  manual_flag: '手动标记',
};

const REASON_COLORS: Record<string, string> = {
  negative_feedback: 'red',
  high_risk: 'volcano',
  low_score: 'orange',
  model_error: 'magenta',
  retrieval_miss: 'gold',
  manual_flag: 'blue',
};

const STATUS_COLORS: Record<string, string> = {
  open: 'red',
  in_progress: 'blue',
  resolved: 'green',
  ignored: 'default',
};

export default function ReviewQueuePage() {
  const { tenantId } = useTenant();
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [reasonFilter, setReasonFilter] = useState<string | undefined>();

  const query = useQuery({
    queryKey: queryKeys.reviewQueue(tenantId!, { status: statusFilter, reason: reasonFilter }),
    queryFn: () => pilotApi.listReviewQueue(tenantId!, { status: statusFilter as ReviewItemStatus, reason: reasonFilter as ReviewReason }),
    enabled: !!tenantId,
  });

  const scanMutation = useMutation({
    mutationFn: () => pilotApi.scanReviewQueue(tenantId!),
    onSuccess: (data) => {
      message.success(`扫描完成，新增 ${data.created} 条审查条目`);
      qc.invalidateQueries({ queryKey: queryKeys.reviewQueue(tenantId!) });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      pilotApi.updateReviewItem(tenantId!, id, { status }),
    onSuccess: () => {
      message.success('状态已更新');
      qc.invalidateQueries({ queryKey: queryKeys.reviewQueue(tenantId!) });
    },
  });

  if (query.isLoading) {
    return (
      <>
        <PageHeader title="质量审查" description="需要人工审查的对话队列" />
        <LoadingState tip="加载审查队列..." />
      </>
    );
  }

  if (query.isError) {
    return (
      <>
        <PageHeader title="质量审查" description="需要人工审查的对话队列" />
        <ErrorState title="加载失败" message="审查队列加载失败" onRetry={() => query.refetch()} />
      </>
    );
  }

  const columns = [
    {
      title: '原因',
      dataIndex: 'reason',
      key: 'reason',
      render: (reason: string) => (
        <Tag color={REASON_COLORS[reason] || 'default'}>{REASON_LABELS[reason] || reason}</Tag>
      ),
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      key: 'priority',
      render: (p: string) => <Tag color={p === 'critical' ? 'red' : p === 'high' ? 'orange' : 'blue'}>{p}</Tag>,
    },
    {
      title: '会话',
      dataIndex: 'conversation_id',
      key: 'conversation_id',
      render: (id: string) => <a href={`/conversations/${id}`}>{id.slice(0, 8)}...</a>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (s: string) => <Tag color={STATUS_COLORS[s]}>{s}</Tag>,
    },
    {
      title: '负责人',
      dataIndex: 'assignee',
      key: 'assignee',
      render: (a: string | null) => a || '-',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (t: string) => t ? t.slice(0, 19).replace('T', ' ') : '-',
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: ReviewItem) => (
        <Space>
          {record.status === 'open' && (
            <Button size="small" onClick={() => updateMutation.mutate({ id: record.id, status: 'in_progress' })}>
              开始审查
            </Button>
          )}
          {record.status === 'in_progress' && (
            <Button size="small" type="primary" icon={<CheckCircleOutlined />}
              onClick={() => updateMutation.mutate({ id: record.id, status: 'resolved' })}>
              已解决
            </Button>
          )}
          {(record.status === 'open' || record.status === 'in_progress') && (
            <Button size="small" onClick={() => updateMutation.mutate({ id: record.id, status: 'ignored' })}>
              忽略
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <>
      <PageHeader title="质量审查" description="需要人工审查的对话队列" />

      <div style={{ marginBottom: 16, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Select
          placeholder="筛选状态"
          allowClear
          style={{ width: 140 }}
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: 'open', label: '待审查' },
            { value: 'in_progress', label: '审查中' },
            { value: 'resolved', label: '已解决' },
            { value: 'ignored', label: '已忽略' },
          ]}
        />
        <Select
          placeholder="筛选原因"
          allowClear
          style={{ width: 140 }}
          value={reasonFilter}
          onChange={setReasonFilter}
          options={Object.entries(REASON_LABELS).map(([value, label]) => ({ value, label }))}
        />
        <Button icon={<ScanOutlined />} onClick={() => scanMutation.mutate()} loading={scanMutation.isPending}>
          立即扫描
        </Button>
      </div>

      <Table
        dataSource={query.data?.items ?? []}
        columns={columns}
        rowKey="id"
        size="small"
        pagination={{
          total: query.data?.total ?? 0,
          pageSize: 20,
          showTotal: (total) => `共 ${total} 条`,
        }}
      />
    </>
  );
}
