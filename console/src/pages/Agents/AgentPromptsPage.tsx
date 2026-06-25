/** Agent-scoped prompts — 绑定/切换/解绑各层 prompt 版本。 */

import { useState } from 'react';
import {
  Table, Tag, Typography, Card, Descriptions, Empty, Button,
  Modal, Select, message, Space, Tabs,
} from 'antd';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getAgent, listAgentPrompts, setAgentPromptBinding } from '@/api/agents';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import {
  PROMPT_CATEGORY_LABELS,
  PROMPT_KEYS_BY_CATEGORY,
  TASK_TYPE_LABELS,
} from '@/utils/constants';

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

const CATEGORIES = ['task', 'system', 'router', 'risk', 'coach'];

export default function AgentPromptsPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const qc = useQueryClient();
  const [modal, setModal] = useState<{ category: string; key: string } | null>(null);

  const { data: agent } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => getAgent(agentId!),
    enabled: !!agentId,
  });
  const { data, isLoading, isError } = useQuery({
    queryKey: ['agent-prompts', agentId],
    queryFn: () => listAgentPrompts(agentId!, 200, 0),
    enabled: !!agentId,
  });

  const bindMut = useMutation({
    mutationFn: (p: { category: string; key: string; versionId: string | null }) =>
      setAgentPromptBinding(agentId!, p.category, p.key, p.versionId),
    onSuccess: () => {
      message.success('已更新');
      qc.invalidateQueries({ queryKey: ['agent-prompts', agentId] });
      setModal(null);
    },
    onError: () => message.error('操作失败'),
  });

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
      width: 200,
      render: (_: unknown, r: { category: string; prompt_key: string; bound: string | null }) => (
        <Space>
          <Button size="small" onClick={() => setModal({ category: r.category, key: r.prompt_key })}>
            切换
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

  return (
    <div>
      <PageHeader
        title="Prompt 管理"
        description={`作用域：Agent「${agent.name}」— 绑定/切换各层 prompt 版本（task / system / router / risk / coach）`}
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
          const boundCount = subset.filter((d) => d.bound).length;
          return {
            key: cat,
            label: `${PROMPT_CATEGORY_LABELS[cat]}（${boundCount}/${subset.length}）`,
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

      <Modal
        open={!!modal}
        title={
          modal
            ? `切换 ${PROMPT_CATEGORY_LABELS[modal.category]} / ${modal.key}`
            : ''
        }
        onCancel={() => setModal(null)}
        footer={null}
      >
        {modal && (
          <>
            <Select
              style={{ width: '100%' }}
              placeholder="选择要绑定的版本"
              allowClear
              defaultValue={boundId(modal.category, modal.key) ?? undefined}
              loading={bindMut.isPending}
              onChange={(val: string | undefined) =>
                bindMut.mutate({
                  category: modal.category,
                  key: modal.key,
                  versionId: val ?? null,
                })
              }
              options={versionsFor(modal.category, modal.key).map((v) => ({
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
    </div>
  );
}
