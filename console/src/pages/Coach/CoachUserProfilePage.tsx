/** Coach user profile — rank/level, six scores, latest report. */

import { Button, Card, Col, Descriptions, Row, Statistic, Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getCoachReport, getCoachUserProfile } from '@/api/coach';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

const RANK_COLOR: Record<string, string> = {
  bronze: 'default', silver: 'blue', samurai: 'gold', master: 'purple', king: 'red',
};

export default function CoachUserProfilePage() {
  const { agentId, userId } = useParams<{ agentId: string; userId: string }>();
  const { data: profile, isLoading, isError } = useQuery({
    queryKey: ['coach-profile', agentId, userId],
    queryFn: () => getCoachUserProfile(agentId!, userId!),
    enabled: !!agentId && !!userId,
  });
  const { data: fullReport } = useQuery({
    queryKey: ['coach-report', agentId, userId, 'full'],
    queryFn: () => getCoachReport(agentId!, userId!, 'full'),
    enabled: !!agentId && !!userId && !!profile?.has_data,
  });

  if (isLoading) return <LoadingState />;
  if (isError) return <ErrorState />;

  if (!profile?.has_data) {
    return (
      <div>
        <PageHeader title="学员详情" description={`用户：${userId}`} />
        <Typography.Text type="secondary">{profile?.message ?? '尚无教练数据'}</Typography.Text>
      </div>
    );
  }

  const scores = (profile?.scores ?? []).map((s) => ({ ...s, key: s.dimension }));

  return (
    <div>
      <PageHeader title="学员详情" description={`用户：${userId}`} />
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card>
            <Statistic title="段位" value={profile!.rank ?? '-'} />
          </Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="等级" value={`Lv.${profile!.level}`} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="成长积分" value={profile!.total_points ?? 0} /></Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="最近评估" value={profile!.last_evaluated_date ?? '-'} />
          </Card>
        </Col>
      </Row>

      <Typography.Title level={5}>六维能力</Typography.Title>
      <Table
        rowKey="dimension"
        dataSource={scores}
        pagination={false}
        size="small"
        style={{ marginBottom: 16 }}
        columns={[
          { title: '维度', dataIndex: 'dimension_label', key: 'dimension_label' },
          {
            title: '当前分', dataIndex: 'score', key: 'score', width: 100,
            render: (v: number) => <Tag color={v >= 70 ? 'green' : v >= 40 ? 'orange' : 'red'}>{v}</Tag>,
          },
          {
            title: '上次变动', dataIndex: 'last_delta', key: 'last_delta', width: 100,
            render: (d: number) => (d === 0 ? '-' : d > 0 ? `+${d}` : `${d}`),
          },
        ]}
      />

      <Typography.Title level={5}>教练报告（完整）</Typography.Title>
      <Card size="small" style={{ marginBottom: 8 }}>
        <Typography.Paragraph>{fullReport?.summary ?? ''}</Typography.Paragraph>
      </Card>
      {(fullReport?.sections ?? []).map((s) => (
        <Card key={s.title} size="small" style={{ marginBottom: 8 }} title={s.title}>
          <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>{s.content}</Typography.Paragraph>
        </Card>
      ))}
    </div>
  );
}
