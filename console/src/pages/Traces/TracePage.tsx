import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, Descriptions, Timeline, Tag, Collapse } from 'antd';
import { useTenant } from '@/context/TenantContext';
import { getRunDetail, getRunSteps } from '@/api/admin';
import { queryKeys } from '@/utils/queryKeys';
import { formatLatency, formatDate } from '@/utils/formatters';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import StatusTag from '@/components/StatusTag';

export default function TracePage() {
  const { runId } = useParams<{ runId: string }>();
  const { tenantId } = useTenant();

  const detailQuery = useQuery({
    queryKey: queryKeys.runDetail(tenantId!, runId!),
    queryFn: () => getRunDetail(tenantId!, runId!),
    enabled: !!tenantId && !!runId,
  });

  const stepsQuery = useQuery({
    queryKey: queryKeys.runSteps(tenantId!, runId!),
    queryFn: () => getRunSteps(tenantId!, runId!),
    enabled: !!tenantId && !!runId,
  });

  if (detailQuery.isLoading || stepsQuery.isLoading) return <LoadingState />;
  if (detailQuery.error) {
    return <ErrorState message={(detailQuery.error as Error).message} onRetry={() => detailQuery.refetch()} />;
  }
  if (!detailQuery.data) return <ErrorState message="Run 不存在" />;

  const run = detailQuery.data;
  const steps = stepsQuery.data?.steps || [];

  return (
    <>
      <PageHeader title="Run 追踪" description={"Run ID: " + (runId || '')} />
      <Card size="small" style={{ marginBottom: 16 }}>
        <Descriptions column={3} size="small">
          <Descriptions.Item label="Run ID">{run.id}</Descriptions.Item>
          <Descriptions.Item label="对话 ID">{run.conversation_id}</Descriptions.Item>
          <Descriptions.Item label="用户">{run.user_id}</Descriptions.Item>
          <Descriptions.Item label="任务类型">{run.task_type || '-'}</Descriptions.Item>
          <Descriptions.Item label="路径">{run.path || '-'}</Descriptions.Item>
          <Descriptions.Item label="状态"><StatusTag status={run.status} /></Descriptions.Item>
          <Descriptions.Item label="总延迟">{formatLatency(run.total_latency_ms)}</Descriptions.Item>
          <Descriptions.Item label="路由置信度">{run.route_confidence?.toFixed(2) || '-'}</Descriptions.Item>
          <Descriptions.Item label="创建时间">{formatDate(run.created_at)}</Descriptions.Item>
        </Descriptions>
        {run.error && (
          <div style={{ marginTop: 8, color: '#cf1322' }}>
            错误: {JSON.stringify(run.error)}
          </div>
        )}
      </Card>

      <Card title="步骤时间线" size="small">
        <Timeline
          items={steps.map((step) => {
            const color = step.status === 'failed' ? 'red' : step.status === 'skipped' ? 'gray' : 'green';
            return {
              color,
              children: (
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <strong>{step.step_name}</strong>
                    <StatusTag status={step.status} />
                    {step.latency_ms != null && <Tag>{formatLatency(step.latency_ms)}</Tag>}
                  </div>
                  {step.error_summary && (
                    <div style={{ color: '#cf1322', marginTop: 4 }}>{step.error_summary}</div>
                  )}
                  {step.metadata && Object.keys(step.metadata).length > 0 && (
                    <Collapse
                      size="small"
                      style={{ marginTop: 4 }}
                      items={[{
                        key: 'meta',
                        label: '元数据',
                        children: (
                          <pre style={{ whiteSpace: 'pre-wrap', margin: 0, fontSize: 12 }}>
                            {JSON.stringify(step.metadata, null, 2)}
                          </pre>
                        ),
                      }]}
                    />
                  )}
                </div>
              ),
            };
          })}
        />
      </Card>
    </>
  );
}
