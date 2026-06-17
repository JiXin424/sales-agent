/** Coach dashboard — team-level coaching status. */

import { Card, Col, Row, Statistic, Table, Tag, Typography } from 'antd';
import { TrophyOutlined, RiseOutlined, WarningOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { getCoachDashboard } from '@/api/coach';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

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

      <Typography.Title level={5}>六维均分雷达（表格）</Typography.Title>
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
      <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: 12, display: 'block' }}>
        {data?.note}
      </Typography.Text>
    </div>
  );
}
