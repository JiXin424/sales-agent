/** Agent List page — entry point of the one-to-one console.

Shows all Agents with status/type/tenant and actions: open / clone / pause / archive / create.
Replaces the tenant selector as the product home.
*/

import { useState } from 'react';
import { Table, Typography, Button, Space, Tag, Input, message, Popconfirm } from 'antd';
import { PlusOutlined, CopyOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { listAgents, pauseAgent, archiveAgent } from '@/api/agents';
import type { AgentInstance, AgentStatus } from '@/api/types';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

const { Search } = Input;

const STATUS_COLOR: Record<AgentStatus, string> = {
  draft: 'default',
  active: 'green',
  paused: 'orange',
  archived: 'red',
};

export default function AgentListPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [q, setQ] = useState('');

  const { data, isLoading, isError } = useQuery({
    queryKey: ['agents', { q }],
    queryFn: () => listAgents({ q: q || undefined, limit: 100 }),
  });

  const handlePause = async (a: AgentInstance) => {
    try {
      await pauseAgent(a.id);
      message.success(`已暂停 ${a.name}`);
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    } catch (e) {
      message.error(`暂停失败：${(e as Error).message}`);
    }
  };

  const handleArchive = async (a: AgentInstance) => {
    try {
      await archiveAgent(a.id);
      message.success(`已归档 ${a.name}`);
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    } catch (e) {
      message.error(`归档失败：${(e as Error).message}`);
    }
  };

  const columns = [
    {
      title: '名称', dataIndex: 'name', key: 'name',
      render: (name: string, a: AgentInstance) => (
        <Typography.Link onClick={() => navigate(`/agents/${a.id}/overview`)}>{name}</Typography.Link>
      ),
    },
    { title: '类型', dataIndex: 'agent_type', key: 'agent_type', width: 120 },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (s: AgentStatus) => <Tag color={STATUS_COLOR[s]}>{s}</Tag>,
    },
    {
      title: '租户', dataIndex: 'tenant_id', key: 'tenant_id', width: 160,
      render: (t: string, a: AgentInstance) =>
        a.is_tenant_default ? <span>{t} <Tag>默认</Tag></span> : t,
    },
    {
      title: 'Prompt', key: 'prompt', width: 100,
      render: (_: unknown, a: AgentInstance) => (a.prompt_set_id ? <Tag color="blue">已配置</Tag> : <Tag>未配置</Tag>),
    },
    {
      title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 160,
      render: (v: string) => (v ? <RelativeTime iso={v} /> : '-'),
    },
    {
      title: '操作', key: 'actions', width: 240,
      render: (_: unknown, a: AgentInstance) => (
        <Space>
          <Button size="small" onClick={() => navigate(`/agents/${a.id}/overview`)}>打开</Button>
          <Button size="small" icon={<CopyOutlined />} onClick={() => navigate(`/agents/${a.id}/clone`)}>
            克隆
          </Button>
          {a.status === 'active' && (
            <Button size="small" onClick={() => handlePause(a)}>暂停</Button>
          )}
          {a.status !== 'archived' && (
            <Popconfirm title={`归档 ${a.name}？`} onConfirm={() => handleArchive(a)}>
              <Button size="small" danger>归档</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title="Agent 列表"
        description="一个 Agent 一个管理页。打开任意 Agent 进入其专属控制台。"
        actions={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/agents/new')}>
            新建 Agent
          </Button>
        }
      />
      <Space style={{ marginBottom: 16 }}>
        <Search
          placeholder="搜索 Agent 名称"
          allowClear
          style={{ width: 300 }}
          onSearch={setQ}
        />
      </Space>
      {isLoading ? <LoadingState /> : isError ? <ErrorState /> : (
        <Table
          rowKey="id"
          dataSource={data?.items ?? []}
          columns={columns}
          pagination={{ pageSize: 20, total: data?.total ?? 0 }}
        />
      )}
    </div>
  );
}
