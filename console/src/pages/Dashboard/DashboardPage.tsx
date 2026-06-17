import { useMemo } from 'react';
import { Row, Col, Card, Statistic, Table, Tag, Typography } from 'antd';
import {
  MessageOutlined,
  LikeOutlined,
  ClockCircleOutlined,
  CloudServerOutlined,
} from '@ant-design/icons';
import { useTenant } from '@/context/TenantContext';
import { useQuery } from '@tanstack/react-query';
import * as api from '@/api';
import { queryKeys } from '@/utils/queryKeys';
import { formatLatency, formatNumber } from '@/utils/formatters';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import EmptyState from '@/components/EmptyState';
import { CountUp, SpotlightCard } from '@/components/react-bits';

const { Text } = Typography;

export default function DashboardPage() {
  const { tenantId } = useTenant();

  const conversationsQuery = useQuery({
    queryKey: queryKeys.conversations(tenantId!, { limit: 1 }),
    queryFn: () => api.listConversations(tenantId!, { limit: 1 }),
    enabled: !!tenantId,
  });

  const feedbackQuery = useQuery({
    queryKey: queryKeys.feedbackSummary(tenantId!),
    queryFn: () => api.getFeedbackSummary(tenantId!),
    enabled: !!tenantId,
  });

  const latencyQuery = useQuery({
    queryKey: queryKeys.latencyStats(tenantId!),
    queryFn: () => api.getTenantLatencyStats(tenantId!),
    enabled: !!tenantId,
  });

  const modelCallsQuery = useQuery({
    queryKey: queryKeys.modelCallsSummary(tenantId!),
    queryFn: () => api.getModelCallsSummary(tenantId!),
    enabled: !!tenantId,
  });

  const recentConversationsQuery = useQuery({
    queryKey: queryKeys.conversations(tenantId!, { limit: 10 }),
    queryFn: () => api.listConversations(tenantId!, { limit: 10 }),
    enabled: !!tenantId,
  });

  const isLoading =
    conversationsQuery.isLoading ||
    feedbackQuery.isLoading ||
    latencyQuery.isLoading ||
    modelCallsQuery.isLoading;

  const hasError =
    conversationsQuery.isError ||
    feedbackQuery.isError ||
    latencyQuery.isError ||
    modelCallsQuery.isError;

  // --- Derived values ---

  const conversationTotal = conversationsQuery.data?.total ?? 0;

  const positiveRate = useMemo(() => {
    const data = feedbackQuery.data;
    if (!data || data.total === 0) return null;
    return data.up / data.total;
  }, [feedbackQuery.data]);

  const p50Latency = useMemo(() => {
    const data = latencyQuery.data as Record<string, unknown> | undefined;
    if (!data) return null;
    const stats = data.stats as Record<string, unknown> | undefined;
    if (!stats) return null;
    return (stats.p50_latency_ms as number) ?? null;
  }, [latencyQuery.data]);

  const modelCallTotal = useMemo(() => {
    const data = modelCallsQuery.data;
    if (!data) return 0;
    let total = 0;
    for (const provider of Object.values(data)) {
      for (const entry of Object.values(provider as Record<string, { count: number }>) ) {
        total += entry.count;
      }
    }
    return total;
  }, [modelCallsQuery.data]);

  // Task type distribution from recent conversations
  const taskTypeDistribution = useMemo(() => {
    const items = recentConversationsQuery.data?.items ?? [];
    if (items.length === 0) return [];
    const counts: Record<string, number> = {};
    for (const conv of items) {
      const type = conv.task_type || '未分类';
      counts[type] = (counts[type] || 0) + 1;
    }
    return Object.entries(counts)
      .map(([type, count]) => ({ type, count }))
      .sort((a, b) => b.count - a.count);
  }, [recentConversationsQuery.data]);

  // Model call success/failure table data
  const modelCallRows = useMemo(() => {
    const data = modelCallsQuery.data;
    if (!data) return [];
    const rows: { key: string; provider: string; status: string; count: number; avgLatency: number | null }[] = [];
    for (const [provider, statuses] of Object.entries(data)) {
      for (const [status, info] of Object.entries(statuses as Record<string, { count: number; avg_latency_ms: number | null }>)) {
        rows.push({
          key: `${provider}-${status}`,
          provider,
          status,
          count: info.count,
          avgLatency: info.avg_latency_ms,
        });
      }
    }
    return rows;
  }, [modelCallsQuery.data]);

  // --- Loading / Error states ---

  if (isLoading) {
    return (
      <>
        <PageHeader title="运营面板" description="系统运营指标总览" />
        <LoadingState tip="加载运营数据..." />
      </>
    );
  }

  if (hasError) {
    return (
      <>
        <PageHeader title="运营面板" description="系统运营指标总览" />
        <ErrorState
          title="加载运营数据失败"
          message="部分指标数据加载失败，请重试"
          onRetry={() => {
            conversationsQuery.refetch();
            feedbackQuery.refetch();
            latencyQuery.refetch();
            modelCallsQuery.refetch();
            recentConversationsQuery.refetch();
          }}
        />
      </>
    );
  }

  // --- Columns ---

  const taskTypeColumns = [
    {
      title: '任务类型',
      dataIndex: 'type',
      key: 'type',
      render: (type: string) => <Tag>{type}</Tag>,
    },
    {
      title: '数量',
      dataIndex: 'count',
      key: 'count',
      align: 'right' as const,
      render: (count: number) => formatNumber(count),
    },
  ];

  const modelCallColumns = [
    {
      title: '模型提供商',
      dataIndex: 'provider',
      key: 'provider',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => (
        <Tag color={status === 'success' ? 'green' : 'red'}>
          {status === 'success' ? '成功' : '失败'}
        </Tag>
      ),
    },
    {
      title: '调用次数',
      dataIndex: 'count',
      key: 'count',
      align: 'right' as const,
      render: (count: number) => formatNumber(count),
    },
    {
      title: '平均延迟',
      dataIndex: 'avgLatency',
      key: 'avgLatency',
      align: 'right' as const,
      render: (ms: number | null) => formatLatency(ms),
    },
  ];

  return (
    <>
      <PageHeader title="运营面板" description="系统运营指标总览" />

      {/* Top row: 4 statistic cards with SpotlightCard glow + CountUp animation */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={6}>
          <SpotlightCard>
            <Card bordered={false}>
              <Statistic
                title="对话总量"
                value={conversationTotal}
                formatter={() => <CountUp to={conversationTotal} duration={2} separator="," />}
                prefix={<MessageOutlined />}
              />
            </Card>
          </SpotlightCard>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <SpotlightCard>
            <Card bordered={false}>
              <Statistic
                title="反馈好评率"
                value={positiveRate ?? 0}
                formatter={() =>
                  positiveRate != null ? (
                    <span style={positiveRate >= 0.8 ? { color: '#52c41a' } : undefined}>
                      <CountUp to={positiveRate * 100} duration={2} />%
                    </span>
                  ) : (
                    <Text type="secondary">-</Text>
                  )
                }
                prefix={<LikeOutlined />}
              />
            </Card>
          </SpotlightCard>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <SpotlightCard>
            <Card bordered={false}>
              <Statistic
                title="P50 延迟"
                value={p50Latency ?? 0}
                formatter={() =>
                  p50Latency != null ? (
                    <span style={p50Latency > 3000 ? { color: '#faad14' } : undefined}>
                      <CountUp to={p50Latency} duration={1.5} /> ms
                    </span>
                  ) : (
                    <Text type="secondary">-</Text>
                  )
                }
                prefix={<ClockCircleOutlined />}
              />
            </Card>
          </SpotlightCard>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <SpotlightCard>
            <Card bordered={false}>
              <Statistic
                title="模型调用总量"
                value={modelCallTotal}
                formatter={() => <CountUp to={modelCallTotal} duration={2} separator="," />}
                prefix={<CloudServerOutlined />}
              />
            </Card>
          </SpotlightCard>
        </Col>
      </Row>

      {/* Bottom row: two tables */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="近期任务类型分布" size="small">
            {taskTypeDistribution.length === 0 ? (
              <EmptyState description="暂无对话数据" />
            ) : (
              <Table
                dataSource={taskTypeDistribution}
                columns={taskTypeColumns}
                rowKey="type"
                pagination={false}
                size="small"
              />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="模型调用状态概览" size="small">
            {modelCallRows.length === 0 ? (
              <EmptyState description="暂无模型调用数据" />
            ) : (
              <Table
                dataSource={modelCallRows}
                columns={modelCallColumns}
                rowKey="key"
                pagination={false}
                size="small"
              />
            )}
          </Card>
        </Col>
      </Row>
    </>
  );
}
