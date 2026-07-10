/** Agent-scoped read-only sales actions operations page.

Lists sales action cards for the agent (cross-user, filterable) and shows a
detail drawer with the card's reminders, deliveries, and events. No edit
buttons - this is an operations/observability view only.
*/

import { useState } from 'react';
import { Table, Tag, Input, Select, DatePicker, Drawer, Descriptions, Typography, Space, Empty } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import {
  listSalesActions,
  getSalesAction,
  type SalesAction,
  type SalesActionFilters,
} from '@/api/salesActions';
import { getAgent } from '@/api/agents';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

const { RangePicker } = DatePicker;

const STATUS_COLOR: Record<string, string> = {
  pending: 'blue',
  done: 'green',
  cancelled: 'default',
  expired: 'orange',
};
const REMINDER_STATUS_COLOR: Record<string, string> = {
  scheduled: 'blue',
  sending: 'processing',
  delivered: 'green',
  failed: 'red',
  cancelled: 'default',
};
const DELIVERY_STATUS_COLOR: Record<string, string> = {
  success: 'green',
  failed: 'red',
};

const ACTION_TYPE_LABEL: Record<string, string> = {
  call_back: '回访电话',
  send_proposal: '发方案',
  follow_up_quote: '跟进报价',
  visit_prepare: '拜访准备',
  post_visit_review: '拜访复盘',
  send_material: '发资料',
  other: '其他',
};

const STATUS_OPTIONS = ['pending', 'done', 'cancelled', 'expired'].map((v) => ({ value: v, label: v }));
const ACTION_TYPE_OPTIONS = Object.entries(ACTION_TYPE_LABEL).map(([value, label]) => ({ value, label }));

export default function AgentSalesActionsPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const [filters, setFilters] = useState<SalesActionFilters>({});
  const [range, setRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data: agent } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => getAgent(agentId!),
    enabled: !!agentId,
  });

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['sales-actions', agentId, filters],
    queryFn: () => listSalesActions(agentId!, filters),
    enabled: !!agentId,
  });

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['sales-action-detail', agentId, selectedId],
    queryFn: () => getSalesAction(agentId!, selectedId!),
    enabled: !!agentId && !!selectedId,
  });

  if (isLoading) return <LoadingState />;
  if (isError || !agent) return <ErrorState onRetry={() => refetch()} />;

  const columns: ColumnsType<SalesAction> = [
    { title: '任务', dataIndex: 'title', key: 'title', width: 220,
      render: (t: string, r: SalesAction) => (
        <Typography.Link onClick={() => setSelectedId(r.id)}>{t}</Typography.Link>
      ) },
    { title: '客户', dataIndex: 'customer_name', key: 'customer_name', width: 120,
      render: (v: string | null) => v || '-' },
    { title: '类型', dataIndex: 'action_type', key: 'action_type', width: 110,
      render: (v: string) => ACTION_TYPE_LABEL[v] || v },
    { title: '计划时间', dataIndex: 'scheduled_at', key: 'scheduled_at', width: 170,
      render: (v: string) => (v ? <RelativeTime iso={v} /> : '-') },
    { title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (s: string) => <Tag color={STATUS_COLOR[s] || 'default'}>{s}</Tag> },
    { title: '用户', dataIndex: 'user_id', key: 'user_id', width: 120 },
    { title: '来源', dataIndex: 'source_kind', key: 'source_kind', width: 120 },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 170,
      render: (v: string) => (v ? <RelativeTime iso={v} /> : '-') },
  ];

  const reminderCols = [
    { title: '类型', dataIndex: 'reminder_type', key: 'reminder_type', width: 120 },
    { title: '提醒时间', dataIndex: 'remind_at', key: 'remind_at', width: 170,
      render: (v: string) => (v ? <RelativeTime iso={v} /> : '-') },
    { title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (s: string) => <Tag color={REMINDER_STATUS_COLOR[s] || 'default'}>{s}</Tag> },
    { title: '尝试', dataIndex: 'attempts', key: 'attempts', width: 60 },
    { title: '下次重试', dataIndex: 'next_attempt_at', key: 'next_attempt_at', width: 170,
      render: (v: string | null) => (v ? <RelativeTime iso={v} /> : '-') },
    { title: '最近错误', dataIndex: 'last_error', key: 'last_error',
      render: (v: string | null) => v || '-' },
  ];

  const deliveryCols = [
    { title: '类型', dataIndex: 'delivery_type', key: 'delivery_type', width: 120 },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: (s: string) => <Tag color={DELIVERY_STATUS_COLOR[s] || 'default'}>{s}</Tag> },
    { title: '卡片实例', dataIndex: 'card_instance_id', key: 'card_instance_id', width: 160,
      render: (v: string | null) => v || '-' },
    { title: '内容', dataIndex: 'rendered_text', key: 'rendered_text',
      render: (v: string) => v || '-' },
    { title: '错误', dataIndex: 'error', key: 'error',
      render: (v: string | null) => v || '-' },
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 170,
      render: (v: string) => (v ? <RelativeTime iso={v} /> : '-') },
  ];

  return (
    <div>
      <PageHeader title="销售任务" description={`作用域：Agent「${agent.name}」（该 Agent 下所有用户的任务，只读）`} />

      <Space style={{ marginBottom: 16, flexWrap: 'wrap' }} size="middle">
        <Select
          allowClear placeholder="状态" style={{ width: 140 }}
          options={STATUS_OPTIONS}
          value={filters.status}
          onChange={(v) => setFilters((f) => ({ ...f, status: v }))}
        />
        <Select
          allowClear placeholder="任务类型" style={{ width: 140 }}
          options={ACTION_TYPE_OPTIONS}
          value={filters.action_type}
          onChange={(v) => setFilters((f) => ({ ...f, action_type: v }))}
        />
        <Input
          allowClear placeholder="用户 ID" style={{ width: 180 }}
          value={filters.user_id ?? ''}
          onChange={(e) => setFilters((f) => ({ ...f, user_id: e.target.value || undefined }))}
        />
        <RangePicker
          showTime
          value={range}
          onChange={(v) => {
            setRange(v as [dayjs.Dayjs | null, dayjs.Dayjs | null] | null);
            setFilters((f) => ({
              ...f,
              scheduled_from: v?.[0]?.toISOString() || undefined,
              scheduled_to: v?.[1]?.toISOString() || undefined,
            }));
          }}
        />
      </Space>

      <Table
        rowKey="id"
        dataSource={(data?.items ?? []) as SalesAction[]}
        columns={columns}
        pagination={{ pageSize: 20, total: data?.total ?? 0 }}
      />

      <Drawer
        title="任务详情"
        width={760}
        open={!!selectedId}
        onClose={() => setSelectedId(null)}
        destroyOnClose
      >
        {detailLoading || !detail ? (
          <LoadingState />
        ) : (
          <Space direction="vertical" size="large" style={{ width: '100%' }}>
            <Descriptions title="任务卡片" column={2} bordered size="small">
              <Descriptions.Item label="任务" span={2}>{detail.card.title}</Descriptions.Item>
              <Descriptions.Item label="客户">{detail.card.customer_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="类型">
                {ACTION_TYPE_LABEL[detail.card.action_type] || detail.card.action_type}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={STATUS_COLOR[detail.card.status] || 'default'}>{detail.card.status}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="优先级">{detail.card.priority}</Descriptions.Item>
              <Descriptions.Item label="计划时间"><RelativeTime iso={detail.card.scheduled_at} /></Descriptions.Item>
              <Descriptions.Item label="时区">{detail.card.timezone}</Descriptions.Item>
              <Descriptions.Item label="用户">{detail.card.user_id}</Descriptions.Item>
              <Descriptions.Item label="来源">{detail.card.source_kind}</Descriptions.Item>
              <Descriptions.Item label="会话">{detail.card.conversation_id}</Descriptions.Item>
            </Descriptions>

            <div>
              <Typography.Title level={5}>提醒（{detail.reminders.length}）</Typography.Title>
              {detail.reminders.length ? (
                <Table rowKey="id" size="small" pagination={false}
                  dataSource={detail.reminders} columns={reminderCols as ColumnsType<typeof detail.reminders[number]>} />
              ) : <Empty description="无提醒" />}
            </div>

            <div>
              <Typography.Title level={5}>投递记录（{detail.deliveries.length}）</Typography.Title>
              {detail.deliveries.length ? (
                <Table rowKey="id" size="small" pagination={false}
                  dataSource={detail.deliveries} columns={deliveryCols as ColumnsType<typeof detail.deliveries[number]>} />
              ) : <Empty description="无投递记录" />}
            </div>

            <div>
              <Typography.Title level={5}>事件（{detail.events.length}）</Typography.Title>
              {detail.events.length ? (
                <Space direction="vertical" style={{ width: '100%' }}>
                  {detail.events.map((e) => (
                    <div key={e.id}>
                      <Tag>{e.event_type}</Tag>
                      <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                        <RelativeTime iso={e.created_at} />
                      </Typography.Text>
                    </div>
                  ))}
                </Space>
              ) : <Empty description="无事件" />}
            </div>
          </Space>
        )}
      </Drawer>
    </div>
  );
}
