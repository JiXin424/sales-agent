import { useState } from 'react';
import { Table, Tag, Button, Modal, Form, Input, Select, Space, message } from 'antd';
import { PlusOutlined, LinkOutlined, CheckCircleOutlined } from '@ant-design/icons';
import { useTenant } from '@/context/TenantContext';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import * as pilotApi from '@/api/pilot';
import { queryKeys } from '@/utils/queryKeys';
import type { KnowledgeGap, GapStatus } from '@/api/types';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

const STATUS_COLORS: Record<string, string> = {
  open: 'red',
  document_needed: 'orange',
  uploaded: 'blue',
  verified: 'green',
  ignored: 'default',
};

const STATUS_LABELS: Record<string, string> = {
  open: '待补充',
  document_needed: '需要文档',
  uploaded: '已上传',
  verified: '已验证',
  ignored: '已忽略',
};

const NEXT_TRANSITION: Record<string, GapStatus> = {
  open: 'document_needed',
  document_needed: 'uploaded',
  uploaded: 'verified',
};

export default function KnowledgeGapsPage() {
  const { tenantId } = useTenant();
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [createOpen, setCreateOpen] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [selectedGap, setSelectedGap] = useState<KnowledgeGap | null>(null);
  const [form] = Form.useForm();
  const [linkForm] = Form.useForm();

  const query = useQuery({
    queryKey: queryKeys.knowledgeGaps(tenantId!, { status: statusFilter }),
    queryFn: () => pilotApi.listKnowledgeGaps(tenantId!, statusFilter),
    enabled: !!tenantId,
  });

  const summaryQuery = useQuery({
    queryKey: queryKeys.knowledgeGapsSummary(tenantId!),
    queryFn: () => pilotApi.getKnowledgeGapsSummary(tenantId!),
    enabled: !!tenantId,
  });

  const createMutation = useMutation({
    mutationFn: (values: { title: string; description?: string; source_conversation_id?: string; priority?: string }) =>
      pilotApi.createKnowledgeGap(tenantId!, values),
    onSuccess: () => {
      message.success('知识缺口已创建');
      qc.invalidateQueries({ queryKey: queryKeys.knowledgeGaps(tenantId!) });
      qc.invalidateQueries({ queryKey: queryKeys.knowledgeGapsSummary(tenantId!) });
      setCreateOpen(false);
      form.resetFields();
    },
  });

  const transitionMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: GapStatus }) =>
      pilotApi.transitionKnowledgeGap(tenantId!, id, status),
    onSuccess: () => {
      message.success('状态已更新');
      qc.invalidateQueries({ queryKey: queryKeys.knowledgeGaps(tenantId!) });
      qc.invalidateQueries({ queryKey: queryKeys.knowledgeGapsSummary(tenantId!) });
    },
  });

  const linkMutation = useMutation({
    mutationFn: ({ gapId, docId }: { gapId: string; docId: string }) =>
      pilotApi.linkDocumentToGap(tenantId!, gapId, docId),
    onSuccess: () => {
      message.success('文档已关联');
      qc.invalidateQueries({ queryKey: queryKeys.knowledgeGaps(tenantId!) });
      setLinkOpen(false);
      linkForm.resetFields();
    },
  });

  if (query.isLoading) {
    return (
      <>
        <PageHeader title="知识缺口" description="追踪和管理知识库中的缺失内容" />
        <LoadingState tip="加载知识缺口..." />
      </>
    );
  }

  if (query.isError) {
    return (
      <>
        <PageHeader title="知识缺口" description="追踪和管理知识库中的缺失内容" />
        <ErrorState title="加载失败" message="知识缺口加载失败" onRetry={() => query.refetch()} />
      </>
    );
  }

  const summary = summaryQuery.data;
  const columns = [
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (s: string) => <Tag color={STATUS_COLORS[s]}>{STATUS_LABELS[s] || s}</Tag>,
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      key: 'priority',
      render: (p: string) => <Tag color={p === 'critical' ? 'red' : p === 'high' ? 'orange' : 'blue'}>{p}</Tag>,
    },
    {
      title: '关联文档',
      dataIndex: 'linked_document_id',
      key: 'linked_document_id',
      render: (id: string | null) => id ? <Tag color="green">已关联</Tag> : <Tag>未关联</Tag>,
    },
    {
      title: '来源会话',
      dataIndex: 'source_conversation_id',
      key: 'source_conversation_id',
      render: (id: string | null) => id ? <a href={`/conversations/${id}`}>{id.slice(0, 8)}...</a> : '-',
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: KnowledgeGap) => (
        <Space>
          {NEXT_TRANSITION[record.status] && (
            <Button size="small" type="primary" onClick={() => transitionMutation.mutate({ id: record.id, status: NEXT_TRANSITION[record.status] })}>
              {STATUS_LABELS[NEXT_TRANSITION[record.status]]}
            </Button>
          )}
          {!record.linked_document_id && (record.status === 'open' || record.status === 'document_needed') && (
            <Button size="small" icon={<LinkOutlined />} onClick={() => { setSelectedGap(record); setLinkOpen(true); }}>
              关联文档
            </Button>
          )}
          {record.status !== 'ignored' && record.status !== 'verified' && (
            <Button size="small" onClick={() => transitionMutation.mutate({ id: record.id, status: 'ignored' })}>
              忽略
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <>
      <PageHeader title="知识缺口" description="追踪和管理知识库中的缺失内容" />

      {/* Summary Cards */}
      {summary && (
        <div style={{ marginBottom: 16, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {Object.entries(STATUS_LABELS).map(([status, label]) => (
            <Tag key={status} color={STATUS_COLORS[status]}>
              {label}: {summary[status as keyof typeof summary] ?? 0}
            </Tag>
          ))}
        </div>
      )}

      <div style={{ marginBottom: 16, display: 'flex', gap: 8 }}>
        <Select
          placeholder="筛选状态"
          allowClear
          style={{ width: 140 }}
          value={statusFilter}
          onChange={setStatusFilter}
          options={Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label }))}
        />
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
          新建缺口
        </Button>
      </div>

      <Table
        dataSource={query.data?.items ?? []}
        columns={columns}
        rowKey="id"
        size="small"
        pagination={{ total: query.data?.total ?? 0, pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
      />

      {/* Create Modal */}
      <Modal title="新建知识缺口" open={createOpen} onCancel={() => setCreateOpen(false)}
        onOk={() => form.validateFields().then(v => createMutation.mutate(v))}>
        <Form form={form} layout="vertical">
          <Form.Item name="title" label="标题" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="source_conversation_id" label="来源会话 ID">
            <Input />
          </Form.Item>
          <Form.Item name="priority" label="优先级" initialValue="medium">
            <Select options={[
              { value: 'low', label: '低' },
              { value: 'medium', label: '中' },
              { value: 'high', label: '高' },
              { value: 'critical', label: '紧急' },
            ]} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Link Document Modal */}
      <Modal title="关联文档" open={linkOpen} onCancel={() => setLinkOpen(false)}
        onOk={() => linkForm.validateFields().then(v => selectedGap && linkMutation.mutate({ gapId: selectedGap.id, docId: v.document_id }))}>
        <Form form={linkForm} layout="vertical">
          <Form.Item name="document_id" label="文档 ID" rules={[{ required: true }]}>
            <Input placeholder="输入要关联的文档 ID" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
