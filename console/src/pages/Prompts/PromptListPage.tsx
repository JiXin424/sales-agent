import { useState } from 'react';
import {
  Button,
  Select,
  Table,
  Space,
  Modal,
  message,
  Typography,
  Tabs,
  Tag,
  Collapse,
  Input,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useTenant } from '@/context/TenantContext';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listPromptVersions,
  activatePromptVersion,
  archivePromptVersion,
  createPromptVersion,
  listEffectivePrompts,
} from '@/api/prompts';
import { queryKeys } from '@/utils/queryKeys';
import { TASK_TYPE_LABELS, PROMPT_CATEGORY_LABELS } from '@/utils/constants';
import type { PromptCategory, EffectivePrompt } from '@/api/types';
import { truncate } from '@/utils/formatters';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import StatusTag from '@/components/StatusTag';
import RelativeTime from '@/components/RelativeTime';

const { Text } = Typography;
const { TextArea } = Input;

const CATEGORY_ORDER = ['task', 'system', 'router', 'risk', 'coach'];

export default function PromptListPage() {
  const { tenantId } = useTenant();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [tab, setTab] = useState('effective');

  // ---- 当前生效（effective）----
  const effQuery = useQuery({
    queryKey: ['effective-prompts', tenantId],
    queryFn: () => listEffectivePrompts(tenantId!),
    enabled: !!tenantId && tab === 'effective',
  });
  const [editing, setEditing] = useState<EffectivePrompt | null>(null);
  const [editText, setEditText] = useState('');

  const saveMutation = useMutation({
    mutationFn: async (p: { category: string; key: string; template: string }) => {
      const created = await createPromptVersion(tenantId!, {
        prompt_category: p.category,
        prompt_key: p.key,
        task_type: p.category === 'task' ? p.key : '',
        template_text: p.template,
        version: `自定义 ${new Date().toLocaleString('zh-CN')}`,
      });
      await activatePromptVersion(tenantId!, created.id);
    },
    onSuccess: () => {
      message.success('已保存并生效（运行时即时使用新版本）');
      setEditing(null);
      queryClient.invalidateQueries({ queryKey: ['effective-prompts', tenantId] });
      queryClient.invalidateQueries({ queryKey: queryKeys.prompts(tenantId!) });
    },
    onError: () => message.error('保存失败'),
  });

  // ---- 版本历史（versions）----
  const [category, setCategory] = useState<PromptCategory | undefined>(undefined);
  const [taskType, setTaskType] = useState<string | undefined>(undefined);
  const [status, setStatus] = useState<string | undefined>(undefined);
  const verQuery = useQuery({
    queryKey: queryKeys.prompts(tenantId!, {
      prompt_category: category,
      task_type: taskType,
      status,
    }),
    queryFn: () =>
      listPromptVersions(tenantId!, {
        prompt_category: category,
        task_type: taskType,
        status,
        limit: 100,
      }),
    enabled: !!tenantId && tab === 'versions',
  });

  const activateMutation = useMutation({
    mutationFn: (id: string) => activatePromptVersion(tenantId!, id),
    onSuccess: () => {
      message.success('已激活');
      queryClient.invalidateQueries({ queryKey: queryKeys.prompts(tenantId!) });
      queryClient.invalidateQueries({ queryKey: ['effective-prompts', tenantId] });
    },
    onError: () => message.error('激活失败'),
  });
  const archiveMutation = useMutation({
    mutationFn: (id: string) => archivePromptVersion(tenantId!, id),
    onSuccess: () => {
      message.success('已归档');
      queryClient.invalidateQueries({ queryKey: queryKeys.prompts(tenantId!) });
    },
    onError: () => message.error('归档失败'),
  });

  const taskTypeOptions = Object.entries(TASK_TYPE_LABELS).map(([value, label]) => ({
    value,
    label,
  }));
  const categoryOptions = Object.entries(PROMPT_CATEGORY_LABELS).map(([value, label]) => ({
    value,
    label,
  }));
  const statusOptions = [
    { value: 'draft', label: '草稿' },
    { value: 'active', label: '活跃' },
    { value: 'archived', label: '已归档' },
  ];

  const openEdit = (p: EffectivePrompt) => {
    setEditing(p);
    setEditText(p.template);
  };
  const handleSave = () => {
    if (!editing) return;
    if (!editText.trim()) {
      message.warning('模板不能为空');
      return;
    }
    saveMutation.mutate({
      category: editing.prompt_category,
      key: editing.prompt_key,
      template: editText,
    });
  };

  // effective 按 category 分组（固定顺序）
  const effList = effQuery.data ?? [];
  const effByCategory: Record<string, EffectivePrompt[]> = {};
  for (const cat of CATEGORY_ORDER) {
    effByCategory[cat] = effList.filter((p) => p.prompt_category === cat);
  }

  const effColumns = [
    {
      title: '名称',
      key: 'desc',
      render: (_: unknown, r: EffectivePrompt) =>
        r.description ||
        (r.prompt_category === 'task' ? TASK_TYPE_LABELS[r.prompt_key] : r.prompt_key),
    },
    {
      title: '标识',
      key: 'key',
      width: 220,
      render: (_: unknown, r: EffectivePrompt) =>
        r.prompt_category === 'task' ? TASK_TYPE_LABELS[r.prompt_key] || r.prompt_key : r.prompt_key,
    },
    {
      title: '当前来源',
      key: 'source',
      width: 200,
      render: (_: unknown, r: EffectivePrompt) =>
        r.source === 'db_active' ? (
          <Tag color="green">已自定义 · {r.version || 'v?'}</Tag>
        ) : (
          <Tag>内置默认</Tag>
        ),
    },
    {
      title: '操作',
      key: 'op',
      width: 120,
      render: (_: unknown, r: EffectivePrompt) => (
        <Button size="small" onClick={() => openEdit(r)}>
          编辑
        </Button>
      ),
    },
  ];

  const verColumns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 140,
      render: (id: string) => (
        <a onClick={() => navigate(`/prompts/${id}/edit`)}>
          <Text style={{ fontSize: 12 }}>{truncate(id, 12)}</Text>
        </a>
      ),
    },
    {
      title: '类别',
      dataIndex: 'prompt_category',
      key: 'prompt_category',
      width: 110,
      render: (c: string) => PROMPT_CATEGORY_LABELS[c] || c,
    },
    {
      title: '标识',
      key: 'identity',
      width: 160,
      render: (
        _: unknown,
        row: { task_type: string | null; prompt_key: string | null; prompt_category: string },
      ) => {
        const key = row.task_type || row.prompt_key || '';
        return row.prompt_category === 'task' ? TASK_TYPE_LABELS[key] || key : key;
      },
    },
    { title: '版本', dataIndex: 'version', key: 'version', width: 160 },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (s: string) => (
        <StatusTag
          status={s}
          label={s === 'active' ? '活跃' : s === 'draft' ? '草稿' : '已归档'}
        />
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (desc: string) => truncate(desc, 60),
    },
    {
      title: '时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 140,
      render: (iso: string) => <RelativeTime iso={iso} />,
    },
    {
      title: '操作',
      key: 'actions',
      width: 240,
      render: (_: unknown, row: { id: string; status: string }) => (
        <Space size={4}>
          <Button size="small" onClick={() => navigate(`/prompts/${row.id}/edit`)}>
            编辑
          </Button>
          {row.status === 'draft' && (
            <Button
              size="small"
              type="primary"
              onClick={() => {
                Modal.confirm({
                  title: '确认激活',
                  content: '激活后该版本将用于生产环境，确定激活？',
                  onOk: () => activateMutation.mutate(row.id),
                });
              }}
            >
              激活
            </Button>
          )}
          {(row.status === 'draft' || row.status === 'active') && (
            <Button
              size="small"
              danger
              onClick={() => {
                Modal.confirm({
                  title: '确认归档',
                  content: '归档后该版本将不再可用，确定归档？',
                  onOk: () => archiveMutation.mutate(row.id),
                });
              }}
            >
              归档
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="提示词管理"
        description="所有层 prompt（task/system/router/risk/coach）的当前生效内容可直接编辑，保存即时生效"
        actions={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => navigate('/prompts/new')}
          >
            新建 Prompt
          </Button>
        }
      />

      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          {
            key: 'effective',
            label: '当前生效',
            children:
              effQuery.isLoading ? (
                <LoadingState tip="加载生效 prompt..." />
              ) : effQuery.isError ? (
                <ErrorState message="加载失败" onRetry={() => effQuery.refetch()} />
              ) : (
                <>
                  <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
                    下方为各 prompt <b>运行时实际使用</b> 的内容。「内置默认」= 代码出厂值；
                    「已自定义」= 你改过并已激活。点「编辑」可直接修改，<b>保存即时生效</b>。
                  </Typography.Paragraph>
                  <Collapse
                    defaultActiveKey={CATEGORY_ORDER}
                    items={CATEGORY_ORDER.map((cat) => ({
                      key: cat,
                      label: `${PROMPT_CATEGORY_LABELS[cat]}（${effByCategory[cat]?.length ?? 0}）`,
                      children: (
                        <Table
                          size="small"
                          rowKey="prompt_key"
                          dataSource={effByCategory[cat] ?? []}
                          columns={effColumns}
                          pagination={false}
                        />
                      ),
                    }))}
                  />
                </>
              ),
          },
          {
            key: 'versions',
            label: '版本历史',
            children: (
              <>
                <Space style={{ marginBottom: 16 }} size={12}>
                  <Select
                    allowClear
                    placeholder="Prompt 类别"
                    style={{ width: 140 }}
                    value={category}
                    onChange={setCategory}
                    options={categoryOptions}
                  />
                  {category === 'task' && (
                    <Select
                      allowClear
                      placeholder="任务类型"
                      style={{ width: 180 }}
                      value={taskType}
                      onChange={setTaskType}
                      options={taskTypeOptions}
                    />
                  )}
                  <Select
                    allowClear
                    placeholder="状态"
                    style={{ width: 140 }}
                    value={status}
                    onChange={setStatus}
                    options={statusOptions}
                  />
                </Space>
                {verQuery.isLoading ? (
                  <LoadingState tip="加载版本列表..." />
                ) : verQuery.isError ? (
                  <ErrorState message="加载失败" onRetry={() => verQuery.refetch()} />
                ) : (
                  <Table
                    dataSource={verQuery.data?.items ?? []}
                    columns={verColumns}
                    rowKey="id"
                    size="small"
                    pagination={{ pageSize: 20, showSizeChanger: true }}
                  />
                )}
              </>
            ),
          },
        ]}
      />

      <Modal
        open={!!editing}
        title={
          editing
            ? `编辑 ${PROMPT_CATEGORY_LABELS[editing.prompt_category]} / ${editing.prompt_key}`
            : ''
        }
        onCancel={() => setEditing(null)}
        onOk={handleSave}
        okText="保存并生效"
        confirmLoading={saveMutation.isPending}
        width={820}
        okButtonProps={{ disabled: !editText.trim() }}
        destroyOnClose
      >
        {editing && (
          <>
            {editing.required_placeholders.length > 0 && (
              <div style={{ marginBottom: 8 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  占位符：
                </Text>{' '}
                {editing.required_placeholders.map((p) => (
                  <Tag key={p}>{`{${p}}`}</Tag>
                ))}
                {editing.prompt_category === 'task' && (
                  <Tag>context_block / retrieval_*（可选）</Tag>
                )}
              </div>
            )}
            <TextArea
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              rows={18}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
            />
            <Typography.Paragraph type="secondary" style={{ marginTop: 8, fontSize: 12 }}>
              保存即创建新版本并激活，运行时即时使用新内容（覆盖当前）。原版本可在「版本历史」归档/回退。
            </Typography.Paragraph>
          </>
        )}
      </Modal>
    </>
  );
}
