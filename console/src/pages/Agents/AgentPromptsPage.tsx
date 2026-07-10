/** Agent-scoped prompts — 绑定/切换/编辑各层 prompt 版本。 */

import { useState } from 'react';
import {
  Table, Tag, Typography, Card, Descriptions, Empty, Button,
  Modal, Select, message, Space, Tabs, Input, Spin,
} from 'antd';
import { EditOutlined, ReloadOutlined } from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getAgent, listAgentPrompts, setAgentPromptBinding } from '@/api/agents';
import {
  getPromptVersion,
  createPromptVersion,
  activatePromptVersion,
  listBuiltinPrompts,
} from '@/api/prompts';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import {
  PROMPT_CATEGORY_LABELS,
  PROMPT_KEYS_BY_CATEGORY,
  TASK_TYPE_LABELS,
} from '@/utils/constants';

const { TextArea } = Input;
const { Text } = Typography;

interface PromptRow {
  id: string;
  task_type: string | null;
  prompt_category: string;
  prompt_key: string | null;
  version: string;
  status: string;
  description: string;
  agent_scoped: boolean;
  created_at: string;
  updated_at: string;
}

// task 类 12 个 key
const TASK_KEYS = Object.keys(TASK_TYPE_LABELS).map((k) => ({ key: k, label: TASK_TYPE_LABELS[k] }));

// 所有 (category, key, label) 清单
const ALL_PROMPT_KEYS: { category: string; key: string; label: string }[] = [
  ...TASK_KEYS.map((t) => ({ category: 'task', key: t.key, label: t.label })),
  ...Object.entries(PROMPT_KEYS_BY_CATEGORY).flatMap(([cat, keys]) =>
    keys.map((k) => ({ category: cat, key: k.key, label: k.label })),
  ),
];

const CATEGORIES = ['task', 'system', 'router', 'risk', 'coach', 'web', 'knowledge'];

export default function AgentPromptsPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const qc = useQueryClient();
  const [bindModal, setBindModal] = useState<{ category: string; key: string } | null>(null);
  const [editModal, setEditModal] = useState<{
    category: string; key: string; label: string;
  } | null>(null);
  const [editTemplate, setEditTemplate] = useState('');
  const [editVersion, setEditVersion] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [loadingTemplate, setLoadingTemplate] = useState(false);

  const { data: agent } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => getAgent(agentId!),
    enabled: !!agentId,
  });

  const tenantId = agent?.tenant_id;

  const { data, isLoading, isError } = useQuery({
    queryKey: ['agent-prompts', agentId],
    queryFn: () => listAgentPrompts(agentId!, 200, 0),
    enabled: !!agentId,
  });

  // 预加载内置 prompt（用于编辑时获取默认模板文本 + 占位符）
  const builtinQuery = useQuery({
    queryKey: ['builtin-prompts', tenantId],
    queryFn: () => listBuiltinPrompts(tenantId!),
    enabled: !!tenantId,
  });

  // 刷新所有数据
  const handleRefresh = () => {
    qc.invalidateQueries({ queryKey: ['agent-prompts', agentId] });
    qc.invalidateQueries({ queryKey: ['builtin-prompts', tenantId] });
    message.success('已刷新');
  };

  const bindMut = useMutation({
    mutationFn: (p: { category: string; key: string; versionId: string | null }) =>
      setAgentPromptBinding(agentId!, p.category, p.key, p.versionId),
    onSuccess: () => {
      message.success('已更新');
      qc.invalidateQueries({ queryKey: ['agent-prompts', agentId] });
      setBindModal(null);
    },
    onError: () => message.error('操作失败'),
  });

  // 编辑保存：创建 → 激活 → 绑定（三步链式调用）
  const editMut = useMutation({
    mutationFn: async (p: {
      category: string; key: string;
      template_text: string; description: string; version: string;
    }) => {
      if (!tenantId) throw new Error('missing tenant');
      const isTask = p.category === 'task';
      // 1. 创建 draft
      const created = await createPromptVersion(tenantId, {
        prompt_category: p.category,
        task_type: isTask ? p.key : '',
        prompt_key: p.key,
        template_text: p.template_text,
        description: p.description,
        version: p.version,
      });
      // 2. 激活
      await activatePromptVersion(tenantId, created.id);
      // 3. 绑定到 Agent
      await setAgentPromptBinding(agentId!, p.category, p.key, created.id);
      return created;
    },
    onSuccess: () => {
      message.success('已保存并激活');
      qc.invalidateQueries({ queryKey: ['agent-prompts', agentId] });
      qc.invalidateQueries({ queryKey: ['builtin-prompts', tenantId] });
      setEditModal(null);
    },
    onError: () => message.error('保存失败'),
  });

  // 打开编辑弹窗，异步加载当前生效的模板文本
  const handleOpenEdit = async (category: string, key: string, label: string) => {
    if (!tenantId) return;
    const boundVersionId = boundId(category, key);
    setEditModal({ category, key, label });
    setEditVersion('');
    setEditDescription('');
    setEditTemplate('');
    setLoadingTemplate(true);

    try {
      if (boundVersionId) {
        // 有绑定的版本：获取其 template_text
        const pv = await getPromptVersion(tenantId, boundVersionId);
        setEditTemplate(pv.template_text);
        setEditDescription(pv.description || '');
      } else {
        // 无绑定：使用内置默认模板
        const builtin = builtinQuery.data?.find(
          (b) => b.prompt_category === category && b.prompt_key === key,
        );
        setEditTemplate(builtin?.template || '');
      }
    } catch {
      message.error('加载模板失败');
      setEditModal(null);
    } finally {
      setLoadingTemplate(false);
    }
  };

  if (isLoading) return <LoadingState />;
  if (isError || !agent) return <ErrorState />;

  const mapping = (data?.prompt_set_mapping ?? {}) as Record<string, unknown>;
  const rows = (data?.items ?? []) as PromptRow[];

  // 取某 (category, key) 当前绑定的 version_id（兼容新旧两种 mapping schema）
  const boundId = (category: string, key: string): string | null => {
    const cat = mapping[category];
    if (cat && typeof cat === 'object') {
      const v = (cat as Record<string, string>)[key];
      if (typeof v === 'string') return v;
    }
    if (category === 'task') {
      const v = (mapping as Record<string, string>)[key];
      if (typeof v === 'string') return v;
    }
    return null;
  };

  // 该 (category, key) 下可选的版本
  const versionsFor = (category: string, key: string): PromptRow[] =>
    rows.filter(
      (r) => r.prompt_category === category && (r.prompt_key === key || r.task_type === key),
    );

  // 获取 (category, key) 的必须占位符
  const placeholdersFor = (category: string, key: string): string[] => {
    const builtin = builtinQuery.data?.find(
      (b) => b.prompt_category === category && b.prompt_key === key,
    );
    return builtin?.required_placeholders ?? [];
  };

  const tableData = ALL_PROMPT_KEYS.map((p) => ({
    key: `${p.category}/${p.key}`,
    category: p.category,
    prompt_key: p.key,
    label: p.label,
    bound: boundId(p.category, p.key),
  }));

  const columns = [
    {
      title: 'Prompt',
      dataIndex: 'label',
      key: 'label',
    },
    {
      title: '当前绑定',
      dataIndex: 'bound',
      key: 'bound',
      render: (id: string | null) => {
        if (!id) return <Typography.Text type="secondary">租户默认 / 内置</Typography.Text>;
        const v = rows.find((x) => x.id === id);
        return (
          <Space>
            <Tag color={v?.agent_scoped ? 'blue' : 'green'}>
              {v?.agent_scoped ? '本 Agent' : '租户级'}
            </Tag>
            <span>{v?.version || id.slice(0, 8)}</span>
            <Tag>{v?.status}</Tag>
          </Space>
        );
      },
    },
    {
      title: '操作',
      key: 'op',
      width: 280,
      render: (_: unknown, r: { category: string; prompt_key: string; label: string; bound: string | null }) => (
        <Space>
          <Button size="small" onClick={() => setBindModal({ category: r.category, key: r.prompt_key })}>
            切换
          </Button>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => handleOpenEdit(r.category, r.prompt_key, r.label)}
          >
            编辑
          </Button>
          {r.bound && (
            <Button
              size="small"
              danger
              loading={bindMut.isPending}
              onClick={() =>
                bindMut.mutate({ category: r.category, key: r.prompt_key, versionId: null })
              }
            >
              解绑
            </Button>
          )}
        </Space>
      ),
    },
  ];

  const currentEditPlaceholders = editModal
    ? placeholdersFor(editModal.category, editModal.key)
    : [];

  return (
    <div>
      <PageHeader
        title="Prompt 管理"
        description={`作用域：Agent「${agent.name}」— 绑定/切换/编辑各层 prompt 版本（task / system / router / risk / coach / web / knowledge）`}
        actions={
          <Button icon={<ReloadOutlined />} onClick={handleRefresh}>
            刷新
          </Button>
        }
      />
      <Card size="small" style={{ marginBottom: 16 }}>
        <Descriptions column={1} size="small">
          <Descriptions.Item label="Prompt 集合">
            {agent.prompt_set_id ?? '—（首次绑定时自动创建）'}
          </Descriptions.Item>
        </Descriptions>
      </Card>
      <Tabs
        items={CATEGORIES.map((cat) => {
          const subset = tableData.filter((d) => d.category === cat);
          return {
            key: cat,
            label: PROMPT_CATEGORY_LABELS[cat],
            children: (
              <Table
                rowKey="key"
                size="small"
                dataSource={subset}
                columns={columns}
                locale={{ emptyText: <Empty description="无" /> }}
                pagination={false}
              />
            ),
          };
        })}
      />

      {/* 绑定/切换 Modal */}
      <Modal
        open={!!bindModal}
        title={
          bindModal
            ? `切换 ${PROMPT_CATEGORY_LABELS[bindModal.category]} / ${bindModal.key}`
            : ''
        }
        onCancel={() => setBindModal(null)}
        footer={null}
      >
        {bindModal && (
          <>
            <Select
              style={{ width: '100%' }}
              placeholder="选择要绑定的版本"
              allowClear
              defaultValue={boundId(bindModal.category, bindModal.key) ?? undefined}
              loading={bindMut.isPending}
              onChange={(val: string | undefined) =>
                bindMut.mutate({
                  category: bindModal.category,
                  key: bindModal.key,
                  versionId: val ?? null,
                })
              }
              options={versionsFor(bindModal.category, bindModal.key).map((v) => ({
                value: v.id,
                label: `${v.version || v.id.slice(0, 8)} [${v.status}]${
                  v.agent_scoped ? ' · 本Agent' : ' · 租户级'
                }`,
              }))}
            />
            <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
              选择版本即绑定到该 Agent；清空选择即解绑（回退到租户 active 版本或内置默认）。
            </Typography.Paragraph>
          </>
        )}
      </Modal>

      {/* 编辑 Modal */}
      <Modal
        open={!!editModal}
        title={editModal ? `编辑：${editModal.label}` : ''}
        onCancel={() => setEditModal(null)}
        width={720}
        footer={null}
        destroyOnClose
      >
        {editModal && (
          <Spin spinning={loadingTemplate}>
            <div style={{ marginBottom: 12 }}>
              {boundId(editModal.category, editModal.key) ? (
                <Tag color="blue">当前已绑定 Agent 版本</Tag>
              ) : (
                <Tag>来源：内置默认</Tag>
              )}
              <Text type="secondary" style={{ fontSize: 12 }}>
                {PROMPT_CATEGORY_LABELS[editModal.category]} / {editModal.key}
              </Text>
            </div>

            {currentEditPlaceholders.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>必须占位符：</Text>
                {currentEditPlaceholders.map((p) => (
                  <Tag key={p} style={{ fontSize: 11 }}>{`{${p}}`}</Tag>
                ))}
                {editModal.category === 'task' && (
                  <Tag style={{ fontSize: 11 }}>context_block / retrieval_block / retrieval_content（可选）</Tag>
                )}
              </div>
            )}

            <div style={{ marginBottom: 12 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>模板文本</Text>
              <TextArea
                rows={16}
                value={editTemplate}
                onChange={(e) => setEditTemplate(e.target.value)}
                style={{ fontFamily: 'monospace', fontSize: 13, marginTop: 4 }}
              />
            </div>

            <div style={{ marginBottom: 12 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>版本（可选）</Text>
              <Input
                value={editVersion}
                onChange={(e) => setEditVersion(e.target.value)}
                placeholder="如 v1.1，留空自动生成"
                style={{ marginTop: 4 }}
              />
            </div>

            <div style={{ marginBottom: 16 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>变更说明（可选）</Text>
              <TextArea
                rows={2}
                value={editDescription}
                onChange={(e) => setEditDescription(e.target.value)}
                placeholder="描述本次变更..."
                style={{ marginTop: 4 }}
              />
            </div>

            <Space>
              <Button
                type="primary"
                loading={editMut.isPending}
                onClick={() => {
                  if (!editTemplate.trim()) {
                    message.warning('请输入模板文本');
                    return;
                  }
                  editMut.mutate({
                    category: editModal.category,
                    key: editModal.key,
                    template_text: editTemplate,
                    description: editDescription,
                    version: editVersion,
                  });
                }}
              >
                保存并激活
              </Button>
              <Button onClick={() => setEditModal(null)}>取消</Button>
            </Space>
          </Spin>
        )}
      </Modal>
    </div>
  );
}
