import { useState } from 'react';
import { DatePicker, Card, Row, Col, Table, Typography } from 'antd';
import type { Dayjs } from 'dayjs';
import { useTenant } from '@/context/TenantContext';
import { useQuery } from '@tanstack/react-query';
import { getWorkflowMetrics } from '@/api/admin';
import { queryKeys } from '@/utils/queryKeys';
import { TASK_TYPE_LABELS, STAGE_LABELS } from '@/utils/constants';
import { formatNumber } from '@/utils/formatters';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import EmptyState from '@/components/EmptyState';

const { Text } = Typography;

export default function WorkflowPage() {
  const { tenantId } = useTenant();
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs] | null>(null);

  // ---- Query ----
  const metricsQuery = useQuery({
    queryKey: queryKeys.workflowMetrics(
      tenantId!,
      dateRange
        ? {
            start_date: dateRange[0].format('YYYY-MM-DD'),
            end_date: dateRange[1].format('YYYY-MM-DD'),
          }
        : undefined,
    ),
    queryFn: () =>
      getWorkflowMetrics(
        tenantId!,
        dateRange
          ? {
              start_date: dateRange[0].format('YYYY-MM-DD'),
              end_date: dateRange[1].format('YYYY-MM-DD'),
            }
          : undefined,
      ),
    enabled: !!tenantId,
  });

  const data = metricsQuery.data;

  // ---- Derived table data ----
  const taskTypeRows = data
    ? Object.entries(data.task_type_distribution).map(([type, count]) => ({
        key: type,
        type,
        count,
      }))
    : [];

  const stageRows = data
    ? Object.entries(data.stage_distribution).map(([stage, count]) => ({
        key: stage,
        stage,
        count,
      }))
    : [];

  const lowScoreRows = (data?.low_score_conversations ?? []).map((item) => ({
    key: item.conversation_id,
    conversation_id: item.conversation_id,
    score: item.overall_score,
  }));

  const feedbackRows = data
    ? Object.entries(data.feedback_by_workflow_task).map(([taskType, counts]) => ({
        key: taskType,
        taskType,
        positive: counts['positive'] ?? counts['up'] ?? 0,
        negative: counts['negative'] ?? counts['down'] ?? 0,
      }))
    : [];

  const missingFieldRows = (data?.common_missing_fields ?? []).map((item) => ({
    key: item.field,
    field: item.field,
    count: item.count,
  }));

  // ---- Loading / Error ----
  if (metricsQuery.isLoading) {
    return (
      <>
        <PageHeader title="工作流分析" description="工作流指标和数据分析" />
        <LoadingState tip="加载工作流指标..." />
      </>
    );
  }

  if (metricsQuery.isError) {
    return (
      <>
        <PageHeader title="工作流分析" description="工作流指标和数据分析" />
        <ErrorState
          message="加载工作流指标失败"
          onRetry={() => metricsQuery.refetch()}
        />
      </>
    );
  }

  // ---- Column definitions ----
  const taskTypeColumns = [
    {
      title: '任务类型',
      dataIndex: 'type',
      key: 'type',
      render: (type: string) => TASK_TYPE_LABELS[type] || type,
    },
    {
      title: '数量',
      dataIndex: 'count',
      key: 'count',
      align: 'right' as const,
      render: (count: number) => formatNumber(count),
    },
  ];

  const stageColumns = [
    {
      title: '阶段',
      dataIndex: 'stage',
      key: 'stage',
      render: (stage: string) => STAGE_LABELS[stage] || stage,
    },
    {
      title: '数量',
      dataIndex: 'count',
      key: 'count',
      align: 'right' as const,
      render: (count: number) => formatNumber(count),
    },
  ];

  const lowScoreColumns = [
    {
      title: '对话 ID',
      dataIndex: 'conversation_id',
      key: 'conversation_id',
      render: (id: string) => (
        <a href={`/conversations/${id}`}>
          <Text style={{ fontSize: 12 }}>
            {id.length > 12 ? `${id.slice(0, 12)}...` : id}
          </Text>
        </a>
      ),
    },
    {
      title: '分数',
      dataIndex: 'score',
      key: 'score',
      align: 'right' as const,
      render: (score: number) => (
        <Text type={score < 0.4 ? 'danger' : 'warning'}>{score.toFixed(2)}</Text>
      ),
    },
  ];

  const feedbackColumns = [
    {
      title: '任务类型',
      dataIndex: 'taskType',
      key: 'taskType',
      render: (type: string) => TASK_TYPE_LABELS[type] || type,
    },
    {
      title: '正面',
      dataIndex: 'positive',
      key: 'positive',
      align: 'right' as const,
      render: (count: number) => <Text style={{ color: '#52c41a' }}>{formatNumber(count)}</Text>,
    },
    {
      title: '负面',
      dataIndex: 'negative',
      key: 'negative',
      align: 'right' as const,
      render: (count: number) => <Text style={{ color: '#ff4d4f' }}>{formatNumber(count)}</Text>,
    },
  ];

  const missingFieldColumns = [
    {
      title: '字段',
      dataIndex: 'field',
      key: 'field',
    },
    {
      title: '次数',
      dataIndex: 'count',
      key: 'count',
      align: 'right' as const,
      render: (count: number) => formatNumber(count),
    },
  ];

  return (
    <>
      <PageHeader title="工作流分析" description="工作流指标和数据分析" />

      <div style={{ marginBottom: 16 }}>
        <DatePicker.RangePicker
          value={dateRange}
          onChange={(dates) => setDateRange(dates as [Dayjs, Dayjs] | null)}
          format="YYYY-MM-DD"
          placeholder={['开始日期', '结束日期']}
        />
      </div>

      {!data ? (
        <EmptyState description="暂无工作流数据" />
      ) : (
        <Row gutter={[16, 16]}>
          {/* 1. Task type distribution */}
          <Col xs={24} lg={12}>
            <Card title="任务类型分布" size="small">
              {taskTypeRows.length === 0 ? (
                <EmptyState description="暂无数据" />
              ) : (
                <Table
                  dataSource={taskTypeRows}
                  columns={taskTypeColumns}
                  rowKey="key"
                  pagination={false}
                  size="small"
                />
              )}
            </Card>
          </Col>

          {/* 2. Stage distribution */}
          <Col xs={24} lg={12}>
            <Card title="销售阶段分布" size="small">
              {stageRows.length === 0 ? (
                <EmptyState description="暂无数据" />
              ) : (
                <Table
                  dataSource={stageRows}
                  columns={stageColumns}
                  rowKey="key"
                  pagination={false}
                  size="small"
                />
              )}
            </Card>
          </Col>

          {/* 3. Low-score conversations */}
          <Col xs={24} lg={12}>
            <Card title="低分对话" size="small">
              {lowScoreRows.length === 0 ? (
                <EmptyState description="暂无低分对话" />
              ) : (
                <Table
                  dataSource={lowScoreRows}
                  columns={lowScoreColumns}
                  rowKey="key"
                  pagination={false}
                  size="small"
                />
              )}
            </Card>
          </Col>

          {/* 4. Workflow feedback */}
          <Col xs={24} lg={12}>
            <Card title="工作流反馈" size="small">
              {feedbackRows.length === 0 ? (
                <EmptyState description="暂无反馈数据" />
              ) : (
                <Table
                  dataSource={feedbackRows}
                  columns={feedbackColumns}
                  rowKey="key"
                  pagination={false}
                  size="small"
                />
              )}
            </Card>
          </Col>

          {/* 5. Common missing fields */}
          <Col xs={24} lg={12}>
            <Card title="常见缺失字段" size="small">
              {missingFieldRows.length === 0 ? (
                <EmptyState description="暂无缺失字段数据" />
              ) : (
                <Table
                  dataSource={missingFieldRows}
                  columns={missingFieldColumns}
                  rowKey="key"
                  pagination={false}
                  size="small"
                />
              )}
            </Card>
          </Col>
        </Row>
      )}
    </>
  );
}
