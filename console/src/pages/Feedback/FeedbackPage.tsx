import { useState } from 'react';
import { Select, Table, Space, Typography, Tag } from 'antd';
import { LikeOutlined, DislikeOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useTenant } from '@/context/TenantContext';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listFeedback, updateFeedbackReviewStatus } from '@/api/feedback';
import { queryKeys } from '@/utils/queryKeys';
import { REVIEW_STATUS_OPTIONS } from '@/utils/constants';
import { truncate } from '@/utils/formatters';
import type { ReviewStatus } from '@/api/types';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

const { Text } = Typography;

export default function FeedbackPage() {
  const { tenantId } = useTenant();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // ---- Filters ----
  const [rating, setRating] = useState<'up' | 'down' | undefined>(undefined);
  const [reviewStatus, setReviewStatus] = useState<ReviewStatus | undefined>(undefined);

  // ---- Query ----
  const query = useQuery({
    queryKey: queryKeys.feedback(tenantId!, { rating, review_status: reviewStatus }),
    queryFn: () =>
      listFeedback(tenantId!, {
        rating,
        review_status: reviewStatus,
        limit: 100,
      }),
    enabled: !!tenantId,
  });

  // ---- Review status update mutation ----
  const updateMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: ReviewStatus }) =>
      updateFeedbackReviewStatus(tenantId!, id, status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.feedback(tenantId!) });
    },
  });

  // ---- Column definitions ----
  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 120,
      render: (id: string) => (
        <a onClick={() => navigate(`/feedback/${id}`)}>
          <Text style={{ fontSize: 12 }}>{truncate(id, 10)}</Text>
        </a>
      ),
    },
    {
      title: '对话',
      dataIndex: 'conversation_id',
      key: 'conversation_id',
      width: 130,
      render: (conversationId: string) => (
        <a onClick={() => navigate(`/conversations/${conversationId}`)}>
          <Text style={{ fontSize: 12 }}>{truncate(conversationId, 10)}</Text>
        </a>
      ),
    },
    {
      title: '用户',
      dataIndex: 'user_id',
      key: 'user_id',
      width: 120,
      ellipsis: true,
    },
    {
      title: '评价',
      dataIndex: 'rating',
      key: 'rating',
      width: 80,
      align: 'center' as const,
      render: (r: 'up' | 'down') =>
        r === 'up' ? (
          <LikeOutlined style={{ color: '#52c41a', fontSize: 18 }} />
        ) : (
          <DislikeOutlined style={{ color: '#ff4d4f', fontSize: 18 }} />
        ),
    },
    {
      title: '反馈内容',
      dataIndex: 'feedback_text',
      key: 'feedback_text',
      ellipsis: true,
      render: (text: string) => truncate(text, 60),
    },
    {
      title: '处理状态',
      dataIndex: 'review_status',
      key: 'review_status',
      width: 140,
      render: (status: ReviewStatus, row: { id: string }) => (
        <Select
          size="small"
          value={status}
          style={{ width: 110 }}
          onChange={(value: ReviewStatus) =>
            updateMutation.mutate({ id: row.id, status: value })
          }
          options={REVIEW_STATUS_OPTIONS.map((opt) => ({
            value: opt.value,
            label: (
              <span>
                <Tag color={opt.color} style={{ marginRight: 4 }}>
                  {opt.label}
                </Tag>
              </span>
            ),
          }))}
        />
      ),
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 140,
      render: (iso: string) => <RelativeTime iso={iso} />,
    },
  ];

  // ---- Render ----
  return (
    <>
      <PageHeader title="用户反馈" description="查看和管理用户反馈" />

      {/* Filters */}
      <Space style={{ marginBottom: 16 }} size={12}>
        <Select
          allowClear
          placeholder="评价类型"
          style={{ width: 140 }}
          value={rating}
          onChange={setRating}
          options={[
            { value: 'up', label: '👍 正面' },
            { value: 'down', label: '👎 负面' },
          ]}
        />
        <Select
          allowClear
          placeholder="处理状态"
          style={{ width: 140 }}
          value={reviewStatus}
          onChange={setReviewStatus}
          options={REVIEW_STATUS_OPTIONS.map((opt) => ({
            value: opt.value,
            label: opt.label,
          }))}
        />
      </Space>

      {/* Table */}
      {query.isLoading ? (
        <LoadingState tip="加载反馈列表..." />
      ) : query.isError ? (
        <ErrorState
          message="加载反馈列表失败"
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
