/** Coach rewards — reward records + admin status patch. */

import { Select, Table, Tag, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { listCoachRewards, patchCoachReward } from '@/api/coach';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

const STATUS_COLOR: Record<string, string> = {
  delivered: 'green', pending: 'blue', suggested: 'orange', failed: 'red',
};

const STATUS_OPTIONS = [
  { value: 'delivered', label: 'delivered' },
  { value: 'pending', label: 'pending' },
  { value: 'suggested', label: 'suggested' },
  { value: 'failed', label: 'failed' },
];

export default function CoachRewardsPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: ['coach-rewards', agentId],
    queryFn: () => listCoachRewards(agentId!, 100, 0),
    enabled: !!agentId,
  });

  const patchMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      patchCoachReward(agentId!, id, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['coach-rewards', agentId] }),
  });

  if (isLoading) return <LoadingState />;
  if (isError) return <ErrorState />;

  const rows = (data?.items ?? []).map((r) => ({ ...r, key: r.id }));

  return (
    <div>
      <PageHeader title="奖励" description="里程碑奖励、徽章、红包提醒的处理记录" />
      <Table
        rowKey="id"
        dataSource={rows}
        pagination={{ pageSize: 20, total: data?.total ?? 0 }}
        size="small"
        columns={[
          { title: '用户', dataIndex: 'user_id', key: 'user_id', ellipsis: true, width: 140 },
          {
            title: '类型', dataIndex: 'reward_type', key: 'reward_type', width: 160,
            render: (t: string) => <Tag>{t}</Tag>,
          },
          { title: '内容', dataIndex: 'message', key: 'message', ellipsis: true },
          {
            title: '状态', dataIndex: 'status', key: 'status', width: 130,
            render: (s: string) => <Tag color={STATUS_COLOR[s] ?? 'default'}>{s}</Tag>,
          },
          {
            title: '送达时间', dataIndex: 'delivered_at', key: 'delivered_at', width: 150,
            render: (v: string | null) => (v ? <RelativeTime iso={v} /> : '-'),
          },
          {
            title: '管理员处理', key: 'admin', width: 140,
            render: (_, row) => (
              <Select
                size="small"
                defaultValue={row.status}
                style={{ width: 120 }}
                options={STATUS_OPTIONS}
                loading={patchMutation.isPending}
                onChange={(status) => patchMutation.mutate({ id: row.id, status })}
              />
            ),
          },
        ]}
      />
      {rows.length === 0 && <Typography.Text type="secondary">暂无奖励记录。</Typography.Text>}
    </div>
  );
}
