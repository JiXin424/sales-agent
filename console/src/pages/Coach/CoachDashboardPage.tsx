/** Coach dashboard — team-level coaching status. */

import { Card, Col, Row, Statistic, Table, Tag, Typography, Space } from 'antd';
import { TrophyOutlined, RiseOutlined, WarningOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { getCoachDashboard } from '@/api/coach';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RadarHexagon, { type RadarSeries } from '@/components/RadarHexagon';

// 六维标签（顺序与后端 DIMENSIONS 一致）
const DIMENSION_LABELS = [
  '客户识别', '需求挖掘', '价值传递', '信任建立', '交易推进', '复盘反思',
];

// 临时 fake 数据，用于预览六边形雷达效果（后续替换为真实 avg_scores）
const DEMO_SERIES: RadarSeries[] = [
  { name: '本周团队均值', color: '#1890ff', values: [68, 55, 74, 62, 48, 52] },
  { name: '上周团队均值', color: '#fa8c16', values: [60, 50, 70, 58, 52, 40] },
];

export default function CoachDashboardPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const { data, isLoading, isError } = useQuery({
    queryKey: ['coach-dashboard', agentId],
    queryFn: () => getCoachDashboard(agentId!),
    enabled: !!agentId,
  });

  if (isLoading) return <LoadingState />;
  if (isError) return <ErrorState />;

  const avgRows = (data?.avg_scores ?? []).map((s) => ({ ...s, key: s.dimension }));

  return (
    <div>
      <PageHeader title="教练看板" description="团队六维均分、每日评估与奖励概况" />
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Card>
            <Statistic
              title="今日已评估用户"
              value={data?.evaluated_today ?? 0}
              prefix={<RiseOutlined />}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="段位/等级成长中"
              value={data?.avg_scores?.length ?? 0}
              prefix={<TrophyOutlined />}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="跳过/失败累计"
              value={data?.skipped_or_failed_total ?? 0}
              prefix={<WarningOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={12}>
          <Card title="六维能力雷达" size="small">
            <div style={{ display: 'flex', justifyContent: 'center' }}>
              <RadarHexagon labels={DIMENSION_LABELS} series={DEMO_SERIES} size={380} />
            </div>
            <Space style={{ width: '100%', justifyContent: 'center', marginTop: 4 }}>
              {DEMO_SERIES.map((s) => (
                <Space key={s.name} size={6}>
                  <span style={{ display: 'inline-block', width: 12, height: 12, borderRadius: 2, background: s.color }} />
                  <Typography.Text style={{ fontSize: 12 }}>{s.name}</Typography.Text>
                </Space>
              ))}
            </Space>
            <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', textAlign: 'center', marginTop: 8 }}>
              示例数据预览（满分 100）
            </Typography.Text>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Typography.Title level={5}>六维均分明细</Typography.Title>
          <Table
        rowKey="dimension"
        dataSource={avgRows}
        pagination={false}
        size="small"
        columns={[
          { title: '维度', dataIndex: 'dimension_label', key: 'dimension_label' },
          {
            title: '平均分', dataIndex: 'avg_score', key: 'avg_score', width: 120,
            render: (v: number) => (
              <Tag color={v >= 70 ? 'green' : v >= 40 ? 'orange' : 'red'}>{v}</Tag>
            ),
          },
          ]}
          onRow={(row) => ({ onClick: () => navigate(`/agents/${agentId}/coach/users`) })}
        />
        </Col>
      </Row>

      <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: 12, display: 'block' }}>
        {data?.note}
      </Typography.Text>
    </div>
  );
}
