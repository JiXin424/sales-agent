/** Agent Settings — metadata, status, source Agent, clone manifest, feature flags. */

import { Card, Descriptions, Form, Input, Button, Tag, message, Tabs, Typography } from 'antd';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';
import { getAgent, updateAgent, getCloneManifest } from '@/api/agents';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

export default function AgentSettingsPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [form] = Form.useForm();

  const { data: agent, isLoading, isError } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => getAgent(agentId!),
    enabled: !!agentId,
  });

  const { data: manifest } = useQuery({
    queryKey: ['agent-clone-manifest', agentId],
    queryFn: () => getCloneManifest(agentId!),
    enabled: !!agentId,
    retry: false,
  });

  const updateMutation = useMutation({
    mutationFn: (body: { name?: string; description?: string }) =>
      updateAgent(agentId!, body),
    onSuccess: () => {
      message.success('已保存');
      queryClient.invalidateQueries({ queryKey: ['agent', agentId] });
    },
    onError: (e: unknown) => message.error(`保存失败：${(e as Error).message}`),
  });

  if (isLoading) return <LoadingState />;
  if (isError || !agent) return <ErrorState />;

  return (
    <div>
      <PageHeader title="设置" description={agent.name} />
      <Tabs
        items={[
          {
            key: 'meta',
            label: '元数据',
            children: (
              <Card>
                <Form
                  layout="vertical"
                  initialValues={{ name: agent.name, description: agent.description }}
                  onFinish={(v) => updateMutation.mutate(v)}
                >
                  <Form.Item label="名称" name="name" rules={[{ required: true }]}>
                    <Input />
                  </Form.Item>
                  <Form.Item label="描述" name="description">
                    <Input.TextArea rows={2} />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" loading={updateMutation.isPending}>
                    保存
                  </Button>
                </Form>
              </Card>
            ),
          },
          {
            key: 'status',
            label: '状态与来源',
            children: (
              <Card>
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="状态"><Tag>{agent.status}</Tag></Descriptions.Item>
                  <Descriptions.Item label="类型">{agent.agent_type}</Descriptions.Item>
                  <Descriptions.Item label="租户">{agent.tenant_id}</Descriptions.Item>
                  <Descriptions.Item label="默认 Agent">{agent.is_tenant_default ? '是' : '否'}</Descriptions.Item>
                  <Descriptions.Item label="来源 Agent">
                    {agent.source_agent_id ? (
                      <Typography.Link onClick={() => navigate(`/agents/${agent.source_agent_id}/overview`)}>
                        {agent.source_agent_id}
                      </Typography.Link>
                    ) : '原生创建'}
                  </Descriptions.Item>
                  <Descriptions.Item label="激活时间">
                    {agent.activated_at ? <RelativeTime iso={agent.activated_at} /> : '—'}
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            ),
          },
          {
            key: 'manifest',
            label: '克隆清单',
            children: manifest ? (
              <Card>
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="来源 Agent">{manifest.source_agent_id}</Descriptions.Item>
                  <Descriptions.Item label="克隆时间"><RelativeTime iso={manifest.created_at} /></Descriptions.Item>
                  <Descriptions.Item label="复制的资源">
                    <pre style={{ margin: 0, fontSize: 12 }}>{JSON.stringify(manifest.copied_resources, null, 2)}</pre>
                  </Descriptions.Item>
                  <Descriptions.Item label="引用的资源">
                    <pre style={{ margin: 0, fontSize: 12 }}>{JSON.stringify(manifest.referenced_resources, null, 2)}</pre>
                  </Descriptions.Item>
                  <Descriptions.Item label="重置的资源（运行历史）">
                    {JSON.stringify(manifest.reset_resources)}
                  </Descriptions.Item>
                  <Descriptions.Item label="跳过的资源">
                    {JSON.stringify(manifest.skipped_resources)}
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            ) : (
              <Card><Typography.Text type="secondary">该 Agent 不是克隆产生的（无清单）。</Typography.Text></Card>
            ),
          },
        ]}
      />
    </div>
  );
}
