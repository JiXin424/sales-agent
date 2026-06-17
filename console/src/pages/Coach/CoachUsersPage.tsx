/** Coach users — list of users with a coach profile, links to profile. */

import { Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { listCoachUsers } from '@/api/coach';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

const RANK_COLOR: Record<string, string> = {
  bronze: 'default', silver: 'blue', samurai: 'gold', master: 'purple', king: 'red',
};

export default function CoachUsersPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const { data, isLoading, isError } = useQuery({
    queryKey: ['coach-users', agentId],
    queryFn: () => listCoachUsers(agentId!, 100, 0),
    enabled: !!agentId,
  });

  if (isLoading) return <LoadingState />;
  if (isError) return <ErrorState />;

  return (
    <div>
      <PageHeader title="学员档案" description="该 Agent 下有教练档案的用户（点击查看详情）" />
      <Table
        rowKey="user_id"
        dataSource={data?.items ?? []}
        pagination={{ pageSize: 20, total: data?.total ?? 0 }}
        onRow={(row) => ({ onClick: () => navigate(`/agents/${agentId}/coach/users/${row.user_id}`), style: { cursor: 'pointer' } })}
        columns={[
          { title: '用户', dataIndex: 'user_id', key: 'user_id', ellipsis: true },
          {
            title: '段位', dataIndex: 'rank', key: 'rank', width: 110,
            render: (r: string) => <Tag color={RANK_COLOR[r] ?? 'default'}>{r}</Tag>,
          },
          { title: '等级', dataIndex: 'level', key: 'level', width: 80, render: (l: number) => `Lv.${l}` },
          { title: '成长积分', dataIndex: 'total_points', key: 'total_points', width: 110 },
          {
            title: '最近评估', dataIndex: 'last_evaluated_date', key: 'last_evaluated_date', width: 140,
            render: (v: string | null) => (v ?? '-'),
          },
        ]}
      />
      {(data?.items ?? []).length === 0 && (
        <Typography.Text type="secondary">暂无学员档案——运行每日评估后会出现。</Typography.Text>
      )}
    </div>
  );
}
