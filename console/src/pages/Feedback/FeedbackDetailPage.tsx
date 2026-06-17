import { useNavigate, useParams } from 'react-router-dom';
import { Card, Descriptions, Select, Tag, Space, Button, Typography, Divider } from 'antd';
import { ArrowLeftOutlined, LikeOutlined, DislikeOutlined } from '@ant-design/icons';
import { useTenant } from '@/context/TenantContext';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getFeedbackDetail, updateFeedbackReviewStatus } from '@/api/feedback';
import { queryKeys } from '@/utils/queryKeys';
import { REVIEW_STATUS_OPTIONS } from '@/utils/constants';
import { formatDate } from '@/utils/formatters';
import type { ReviewStatus } from '@/api/types';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

const { Text, Paragraph } = Typography;

export default function FeedbackDetailPage() {
  const { tenantId } = useTenant();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { id } = useParams<{ id: string }>();

  // ---- Query ----
  const query = useQuery({
    queryKey: queryKeys.feedbackDetail(tenantId!, id!),
    queryFn: () => getFeedbackDetail(tenantId!, id!),
    enabled: !!tenantId && !!id,
  });

  // ---- Mutation ----
  const updateMutation = useMutation({
    mutationFn: (status: ReviewStatus) =>
      updateFeedbackReviewStatus(tenantId!, id!, status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.feedbackDetail(tenantId!, id!) });
      queryClient.invalidateQueries({ queryKey: queryKeys.feedback(tenantId!) });
    },
  });

  // ---- Loading / Error ----
  if (query.isLoading) {
    return (
      <>
        <PageHeader title="反馈详情" />
        <LoadingState tip="加载反馈详情..." />
      </>
    );
  }

  if (query.isError || !query.data) {
    return (
      <>
        <PageHeader title="反馈详情" />
        <ErrorState
          message="加载反馈详情失败"
          onRetry={() => query.refetch()}
        />
      </>
    );
  }

  const feedback = query.data;

  const currentReviewOption = REVIEW_STATUS_OPTIONS.find(
    (opt) => opt.value === feedback.review_status,
  );

  return (
    <>
      <PageHeader
        title="反馈详情"
        actions={
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/feedback')}>
            返回列表
          </Button>
        }
      />

      {/* Metadata card */}
      <Card style={{ marginBottom: 24 }}>
        <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} bordered size="small">
          <Descriptions.Item label="ID">
            <Text copyable style={{ fontSize: 12 }}>
              {feedback.id}
            </Text>
          </Descriptions.Item>

          <Descriptions.Item label="对话">
            <a onClick={() => navigate(`/conversations/${feedback.conversation_id}`)}>
              查看对话详情
            </a>
          </Descriptions.Item>

          <Descriptions.Item label="用户">{feedback.user_id}</Descriptions.Item>

          <Descriptions.Item label="评价">
            {feedback.rating === 'up' ? (
              <Space>
                <LikeOutlined style={{ color: '#52c41a', fontSize: 18 }} />
                <Text style={{ color: '#52c41a' }}>正面</Text>
              </Space>
            ) : (
              <Space>
                <DislikeOutlined style={{ color: '#ff4d4f', fontSize: 18 }} />
                <Text style={{ color: '#ff4d4f' }}>负面</Text>
              </Space>
            )}
          </Descriptions.Item>

          <Descriptions.Item label="反馈内容" span={2}>
            <Paragraph style={{ margin: 0 }}>
              {feedback.feedback_text || '-'}
            </Paragraph>
          </Descriptions.Item>

          <Descriptions.Item label="标签">
            {feedback.labels?.length > 0
              ? feedback.labels.map((label) => (
                  <Tag key={label}>{label}</Tag>
                ))
              : <Text type="secondary">-</Text>}
          </Descriptions.Item>

          <Descriptions.Item label="创建时间">
            {formatDate(feedback.created_at)}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* Review status */}
      <Card title="处理状态" size="small" style={{ marginBottom: 24 }}>
        <Space>
          <span>当前状态:</span>
          <Tag color={currentReviewOption?.color || 'default'}>
            {currentReviewOption?.label || feedback.review_status}
          </Tag>
          <span style={{ marginLeft: 8 }}>变更为:</span>
          <Select
            value={feedback.review_status}
            style={{ width: 140 }}
            onChange={(value: ReviewStatus) => updateMutation.mutate(value)}
            options={REVIEW_STATUS_OPTIONS.map((opt) => ({
              value: opt.value,
              label: opt.label,
            }))}
          />
        </Space>
      </Card>

      {/* Evidence links */}
      <Card title="关联信息" size="small">
        <Space direction="vertical">
          <div>
            <Text type="secondary">关联对话: </Text>
            <a onClick={() => navigate(`/conversations/${feedback.conversation_id}`)}>
              {feedback.conversation_id}
            </a>
          </div>
        </Space>
      </Card>
    </>
  );
}
