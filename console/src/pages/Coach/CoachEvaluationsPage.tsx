/** Coach evaluations — operational daily-evaluation records + manual run. */

import { useState } from 'react';
import { Badge, Button, DatePicker, Form, Input, Modal, Space, Table, Tag, Typography, message } from 'antd';
import { PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import dayjs from 'dayjs';
import { listCoachEvaluations, rerunCoachEvaluation, runCoachDaily } from '@/api/coach';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

const STATUS_COLOR: Record<string, string> = {
  success: 'green', skipped: 'orange', failed: 'red', dry_run: 'blue',
};

export default function CoachEvaluationsPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();

  const { data, isLoading, isError } = useQuery({
    queryKey: ['coach-evaluations', agentId],
    queryFn: () => listCoachEvaluations(agentId!, 100, 0),
    enabled: !!agentId,
  });

  const runMutation = useMutation({
    mutationFn: (vals: { user_id?: string; date?: string; dry_run?: boolean }) =>
      runCoachDaily(agentId!, vals),
    onSuccess: (res) => {
      message.success(`评估完成：成功 ${res.summary?.success ?? 0} / 跳过 ${res.summary?.skipped ?? 0} / 失败 ${res.summary?.failed ?? 0}`);
      qc.invalidateQueries({ queryKey: ['coach-evaluations', agentId] });
      setOpen(false);
      form.resetFields();
    },
    onError: (e: Error) => message.error(`评估失败：${e.message}`),
  });

  const rerunMutation = useMutation({
    mutationFn: (evalId: string) => rerunCoachEvaluation(agentId!, evalId),
    onSuccess: () => message.success('已以 dry_run 方式重新评估'),
    onError: (e: Error) => message.error(`重跑失败：${e.message}`),
  });

  if (isLoading) return <LoadingState />;
  if (isError) return <ErrorState />;

  const rows = (data?.items ?? []).map((r) => ({ ...r, key: r.id }));

  return (
    <div>
      <PageHeader
        title="每日评估"
        description="每日 23:00 自动评估的运行记录；可手动触发"
        actions={
          <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => setOpen(true)}>
            手动运行
          </Button>
        }
      />
      <Table
        rowKey="id"
        dataSource={rows}
        pagination={{ pageSize: 20, total: data?.total ?? 0 }}
        size="small"
        columns={[
          { title: '评估日期', dataIndex: 'evaluation_date', key: 'evaluation_date', width: 120 },
          { title: '用户', dataIndex: 'user_id', key: 'user_id', ellipsis: true },
          {
            title: '状态', dataIndex: 'status', key: 'status', width: 100,
            render: (s: string) => <Tag color={STATUS_COLOR[s] ?? 'default'}>{s}</Tag>,
          },
          { title: '会话数', dataIndex: 'conversation_count', key: 'conversation_count', width: 90 },
          { title: '用户消息', dataIndex: 'user_message_count', key: 'user_message_count', width: 100 },
          { title: '积分', dataIndex: 'points_delta', key: 'points_delta', width: 80 },
          { title: '耗时(ms)', dataIndex: 'latency_ms', key: 'latency_ms', width: 100 },
          {
            title: '操作', key: 'actions', width: 100,
            render: (_, row) => (
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={() => rerunMutation.mutate(row.id)}
                loading={rerunMutation.isPending}
              >
                重跑
              </Button>
            ),
          },
        ]}
      />

      <Modal
        title="手动运行每日评估"
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
      >
        <Typography.Paragraph type="secondary">
          可指定单个用户和日期；不填则评估昨天该 Agent 下所有活跃用户。
        </Typography.Paragraph>
        <Form
          form={form}
          layout="vertical"
          onFinish={(vals) => {
            const date = vals.date ? dayjs(vals.date).format('YYYY-MM-DD') : undefined;
            runMutation.mutate({ user_id: vals.user_id || undefined, date, dry_run: !!vals.dry_run });
          }}
        >
          <Form.Item name="user_id" label="用户 ID（可选）"><Input placeholder="留空评估全部用户" /></Form.Item>
          <Form.Item name="date" label="评估日期（可选）"><DatePicker style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="dry_run" valuePropName="checked"><Space><Badge status="default" /><Typography.Text>仅预览（dry_run，不修改分数）</Typography.Text></Space></Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit" loading={runMutation.isPending}>运行</Button>
              <Button onClick={() => setOpen(false)}>取消</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
