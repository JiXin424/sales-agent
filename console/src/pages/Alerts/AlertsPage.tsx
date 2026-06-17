import { useState } from 'react';
import { Tabs, Table, Tag, Button, Space, Modal, Form, Input, InputNumber, Select, message } from 'antd';
import { PlusOutlined, ThunderboltOutlined, CheckCircleOutlined } from '@ant-design/icons';
import { useTenant } from '@/context/TenantContext';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import * as pilotApi from '@/api/pilot';
import { queryKeys } from '@/utils/queryKeys';
import type { AlertRule, AlertItem, AlertSeverity } from '@/api/types';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

const SEVERITY_COLORS: Record<string, string> = {
  info: 'blue',
  warning: 'orange',
  critical: 'red',
};

const METRIC_OPTIONS = [
  { value: 'model_failures', label: '模型调用失败' },
  { value: 'dingtalk_failures', label: '钉钉推送失败' },
  { value: 'ingestion_failures', label: '导入失败' },
  { value: 'slow_p95', label: 'P95 延迟过高' },
  { value: 'high_error_rate', label: '错误率过高' },
  { value: 'high_negative_feedback', label: '负反馈率过高' },
  { value: 'retrieval_misses', label: '检索缺失' },
];

export default function AlertsPage() {
  const { tenantId } = useTenant();
  const qc = useQueryClient();
  const [activeTab, setActiveTab] = useState('alerts');
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();

  const rulesQuery = useQuery({
    queryKey: queryKeys.alertRules(tenantId!),
    queryFn: () => pilotApi.listAlertRules(tenantId!),
    enabled: !!tenantId,
  });

  const alertsQuery = useQuery({
    queryKey: queryKeys.alerts(tenantId!),
    queryFn: () => pilotApi.listAlerts(tenantId!),
    enabled: !!tenantId,
  });

  const evaluateMutation = useMutation({
    mutationFn: () => pilotApi.evaluateAlerts(tenantId!),
    onSuccess: (data) => {
      message.success(`评估完成，触发 ${data.triggered} 条告警`);
      qc.invalidateQueries({ queryKey: queryKeys.alerts(tenantId!) });
    },
  });

  const createMutation = useMutation({
    mutationFn: (values: { name: string; metric: string; threshold: number; condition?: string; window_minutes?: number; severity?: string }) =>
      pilotApi.createAlertRule(tenantId!, values),
    onSuccess: () => {
      message.success('规则已创建');
      qc.invalidateQueries({ queryKey: queryKeys.alertRules(tenantId!) });
      setCreateOpen(false);
      form.resetFields();
    },
  });

  const seedMutation = useMutation({
    mutationFn: () => pilotApi.seedDefaultAlertRules(tenantId!),
    onSuccess: (data) => {
      message.success(`已创建 ${data.created} 条默认规则`);
      qc.invalidateQueries({ queryKey: queryKeys.alertRules(tenantId!) });
    },
  });

  const acknowledgeMutation = useMutation({
    mutationFn: (alertId: string) => pilotApi.acknowledgeAlert(tenantId!, alertId),
    onSuccess: () => {
      message.success('已确认');
      qc.invalidateQueries({ queryKey: queryKeys.alerts(tenantId!) });
    },
  });

  const resolveMutation = useMutation({
    mutationFn: (alertId: string) => pilotApi.resolveAlert(tenantId!, alertId),
    onSuccess: () => {
      message.success('已解决');
      qc.invalidateQueries({ queryKey: queryKeys.alerts(tenantId!) });
    },
  });

  if (rulesQuery.isLoading || alertsQuery.isLoading) {
    return (
      <>
        <PageHeader title="运维告警" description="告警规则管理与活跃告警" />
        <LoadingState tip="加载告警数据..." />
      </>
    );
  }

  const ruleColumns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '指标', dataIndex: 'metric', key: 'metric', render: (m: string) => <Tag>{m}</Tag> },
    { title: '条件', dataIndex: 'condition', key: 'condition' },
    { title: '阈值', dataIndex: 'threshold', key: 'threshold' },
    { title: '窗口', dataIndex: 'window_minutes', key: 'window_minutes', render: (m: number) => `${m}min` },
    {
      title: '级别',
      dataIndex: 'severity',
      key: 'severity',
      render: (s: AlertSeverity) => <Tag color={SEVERITY_COLORS[s]}>{s}</Tag>,
    },
    { title: '启用', dataIndex: 'enabled', key: 'enabled', render: (e: boolean) => <Tag color={e ? 'green' : 'default'}>{e ? '是' : '否'}</Tag> },
  ];

  const alertColumns = [
    {
      title: '级别',
      dataIndex: 'severity',
      key: 'severity',
      render: (s: AlertSeverity) => <Tag color={SEVERITY_COLORS[s]}>{s}</Tag>,
    },
    { title: '指标', dataIndex: 'metric', key: 'metric' },
    { title: '阈值', dataIndex: 'threshold_value', key: 'threshold_value' },
    { title: '观测值', dataIndex: 'observed_value', key: 'observed_value', render: (v: number) => typeof v === 'number' ? v.toFixed(3) : v },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (s: string) => <Tag color={s === 'active' ? 'red' : s === 'acknowledged' ? 'blue' : 'green'}>{s}</Tag>,
    },
    { title: '首次发现', dataIndex: 'first_seen_at', key: 'first_seen_at', render: (t: string) => t ? t.slice(0, 19).replace('T', ' ') : '-' },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: AlertItem) => (
        <Space>
          {record.status === 'active' && (
            <Button size="small" onClick={() => acknowledgeMutation.mutate(record.id)}>确认</Button>
          )}
          {record.status !== 'resolved' && (
            <Button size="small" type="primary" icon={<CheckCircleOutlined />}
              onClick={() => resolveMutation.mutate(record.id)}>
              解决
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <>
      <PageHeader title="运维告警" description="告警规则管理与活跃告警" />

      <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
        {
          key: 'alerts',
          label: `活跃告警 (${alertsQuery.data?.total ?? 0})`,
          children: (
            <>
              <div style={{ marginBottom: 16, display: 'flex', gap: 8 }}>
                <Button icon={<ThunderboltOutlined />} onClick={() => evaluateMutation.mutate()} loading={evaluateMutation.isPending}>
                  立即评估
                </Button>
              </div>
              <Table
                dataSource={alertsQuery.data?.items ?? []}
                columns={alertColumns}
                rowKey="id"
                size="small"
                pagination={{ pageSize: 20 }}
              />
            </>
          ),
        },
        {
          key: 'rules',
          label: `告警规则 (${rulesQuery.data?.total ?? 0})`,
          children: (
            <>
              <div style={{ marginBottom: 16, display: 'flex', gap: 8 }}>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                  新建规则
                </Button>
                <Button onClick={() => seedMutation.mutate()} loading={seedMutation.isPending}>
                  加载默认规则
                </Button>
              </div>
              <Table
                dataSource={rulesQuery.data?.items ?? []}
                columns={ruleColumns}
                rowKey="id"
                size="small"
                pagination={{ pageSize: 20 }}
              />
            </>
          ),
        },
      ]} />

      {/* Create Rule Modal */}
      <Modal title="新建告警规则" open={createOpen} onCancel={() => setCreateOpen(false)}
        onOk={() => form.validateFields().then(v => createMutation.mutate(v))}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="规则名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="metric" label="监控指标" rules={[{ required: true }]}>
            <Select options={METRIC_OPTIONS} />
          </Form.Item>
          <Form.Item name="threshold" label="阈值" rules={[{ required: true }]}>
            <InputNumber style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="condition" label="条件" initialValue="gt">
            <Select options={[
              { value: 'gt', label: '大于 (>)' },
              { value: 'gte', label: '大于等于 (>=)' },
              { value: 'lt', label: '小于 (<)' },
              { value: 'lte', label: '小于等于 (<=)' },
            ]} />
          </Form.Item>
          <Form.Item name="window_minutes" label="时间窗口 (分钟)" initialValue={60}>
            <InputNumber style={{ width: '100%' }} min={1} />
          </Form.Item>
          <Form.Item name="severity" label="严重级别" initialValue="warning">
            <Select options={[
              { value: 'info', label: '信息' },
              { value: 'warning', label: '警告' },
              { value: 'critical', label: '严重' },
            ]} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
