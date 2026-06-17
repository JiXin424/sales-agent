import { useState, useMemo } from 'react';
import { Row, Col, Card, Statistic, DatePicker, Tag, Typography, Descriptions, Button } from 'antd';
import {
  RocketOutlined,
  TeamOutlined,
  LikeOutlined,
  WarningOutlined,
  ClockCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useTenant } from '@/context/TenantContext';
import { useQuery } from '@tanstack/react-query';
import * as pilotApi from '@/api/pilot';
import { queryKeys } from '@/utils/queryKeys';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import { CountUp, SpotlightCard } from '@/components/react-bits';

const { Text } = Typography;
const { RangePicker } = DatePicker;

const STATUS_COLORS: Record<string, string> = {
  expand: 'green',
  continue_pilot: 'blue',
  needs_remediation: 'orange',
  stop: 'red',
};

const STATUS_LABELS: Record<string, string> = {
  expand: '可扩展',
  continue_pilot: '继续 Pilot',
  needs_remediation: '需要修复',
  stop: '暂停',
};

export default function PilotDashboardPage() {
  const { tenantId } = useTenant();
  const [dateRange, setDateRange] = useState<[string, string]>([
    new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10),
    new Date().toISOString().slice(0, 10),
  ]);

  const metricsQuery = useQuery({
    queryKey: queryKeys.pilotMetrics(tenantId!, dateRange[0], dateRange[1]),
    queryFn: () => pilotApi.getPilotMetrics(tenantId!, dateRange[0], dateRange[1]),
    enabled: !!tenantId,
  });

  const statusQuery = useQuery({
    queryKey: queryKeys.pilotStatus(tenantId!),
    queryFn: () => pilotApi.getPilotStatus(tenantId!),
    enabled: !!tenantId,
  });

  if (metricsQuery.isLoading || statusQuery.isLoading) {
    return (
      <>
        <PageHeader title="Pilot 指标" description="Pilot 阶段成功指标和退出决策" />
        <LoadingState tip="加载 Pilot 数据..." />
      </>
    );
  }

  if (metricsQuery.isError || statusQuery.isError) {
    return (
      <>
        <PageHeader title="Pilot 指标" description="Pilot 阶段成功指标和退出决策" />
        <ErrorState
          title="加载失败"
          message="Pilot 数据加载失败，请重试"
          onRetry={() => { metricsQuery.refetch(); statusQuery.refetch(); }}
        />
      </>
    );
  }

  const metrics = metricsQuery.data;
  const status = statusQuery.data;
  const usage = metrics?.usage ?? { total_conversations: 0, unique_users: 0, avg_dau: 0, questions_per_user: 0, repeat_users: 0, repeat_usage_rate: 0 };
  const quality = metrics?.quality ?? { feedback_up: 0, feedback_down: 0, feedback_positive_ratio: 0, rag_usage_rate: 0, risk_interception_count: 0 };
  const perf = metrics?.performance ?? { latency_p50_ms: 0, latency_p95_ms: 0, error_rate: 0 };

  const taskDist = metrics?.distribution?.task_distribution ?? {};
  const taskDistRows = Object.entries(taskDist).sort((a, b) => b[1] - a[1]);

  return (
    <>
      <PageHeader title="Pilot 指标" description="Pilot 阶段成功指标和退出决策" />

      {/* Date Range Picker */}
      <div style={{ marginBottom: 16 }}>
        <RangePicker
          onChange={(_, dateStrings) => {
            if (dateStrings[0] && dateStrings[1]) {
              setDateRange([dateStrings[0], dateStrings[1]]);
            }
          }}
        />
      </div>

      {/* Pilot Status Badge */}
      {status && (
        <Card style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <Tag color={STATUS_COLORS[status.classification]} style={{ fontSize: 16, padding: '4px 16px' }}>
              <RocketOutlined /> {STATUS_LABELS[status.classification] || status.classification}
            </Tag>
            <div>
              {status.reasons.map((r, i) => (
                <Text key={i} type="secondary" style={{ display: 'block' }}>• {r}</Text>
              ))}
            </div>
          </div>
          {status.next_actions.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <Text strong>建议操作：</Text>
              {status.next_actions.map((a, i) => (
                <Text key={i} style={{ display: 'block', marginLeft: 8 }} type="secondary">→ {a}</Text>
              ))}
            </div>
          )}
        </Card>
      )}

      {/* Metric Cards with SpotlightCard glow + CountUp animation */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={6}>
          <SpotlightCard>
            <Card bordered={false}>
              <Statistic
                title="总会话数"
                value={usage.total_conversations}
                formatter={() => <CountUp to={usage.total_conversations} duration={2} separator="," />}
                prefix={<RocketOutlined />}
              />
            </Card>
          </SpotlightCard>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <SpotlightCard>
            <Card bordered={false}>
              <Statistic
                title="独立用户"
                value={usage.unique_users}
                formatter={() => <CountUp to={usage.unique_users} duration={2} separator="," />}
                prefix={<TeamOutlined />}
              />
            </Card>
          </SpotlightCard>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <SpotlightCard>
            <Card bordered={false}>
              <Statistic
                title="正反馈率"
                value={quality.feedback_positive_ratio}
                formatter={() => (
                  <span style={quality.feedback_positive_ratio >= 0.7 ? { color: '#52c41a' } : undefined}>
                    <CountUp to={quality.feedback_positive_ratio * 100} duration={1.5} from={0} />%
                  </span>
                )}
                prefix={<LikeOutlined />}
              />
            </Card>
          </SpotlightCard>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <SpotlightCard>
            <Card bordered={false}>
              <Statistic
                title="错误率"
                value={perf.error_rate}
                formatter={() => (
                  <span style={perf.error_rate > 0.1 ? { color: '#ff4d4f' } : undefined}>
                    <CountUp to={perf.error_rate * 100} duration={1.5} from={0} />%
                  </span>
                )}
                prefix={<WarningOutlined />}
              />
            </Card>
          </SpotlightCard>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={6}>
          <SpotlightCard>
            <Card bordered={false}>
              <Statistic
                title="DAU (日均)"
                value={usage.avg_dau}
                formatter={() => <CountUp to={usage.avg_dau} duration={2} separator="," />}
                prefix={<TeamOutlined />}
              />
            </Card>
          </SpotlightCard>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <SpotlightCard>
            <Card bordered={false}>
              <Statistic
                title="人均问题数"
                value={usage.questions_per_user}
                formatter={() => <CountUp to={usage.questions_per_user} duration={1.5} />}
                prefix={<RocketOutlined />}
              />
            </Card>
          </SpotlightCard>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <SpotlightCard>
            <Card bordered={false}>
              <Statistic
                title="P50 延迟"
                value={perf.latency_p50_ms}
                formatter={() => <><CountUp to={perf.latency_p50_ms} duration={1.5} /> ms</>}
                prefix={<ClockCircleOutlined />}
              />
            </Card>
          </SpotlightCard>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <SpotlightCard>
            <Card bordered={false}>
              <Statistic
                title="P95 延迟"
                value={perf.latency_p95_ms}
                formatter={() => <><CountUp to={perf.latency_p95_ms} duration={1.5} /> ms</>}
                prefix={<ThunderboltOutlined />}
              />
            </Card>
          </SpotlightCard>
        </Col>
      </Row>

      {/* Task Distribution */}
      {taskDistRows.length > 0 && (
        <Card title="任务类型分布" size="small" style={{ marginBottom: 16 }}>
          {taskDistRows.map(([type, count]) => (
            <div key={type} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
              <Tag>{type}</Tag>
              <Text>{count}</Text>
            </div>
          ))}
        </Card>
      )}
    </>
  );
}
