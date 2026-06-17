/** Create Agent flow — produces a draft Agent, redirects to its setup page.

dedicated mode auto-resolves the instance tenant on the backend, so no tenant
selection is required here.
*/

import { Card, Form, Input, Select, Button, message } from 'antd';
import { useMutation } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { createAgent } from '@/api/agents';
import PageHeader from '@/components/PageHeader';

export default function CreateAgentPage() {
  const navigate = useNavigate();
  const [form] = Form.useForm();

  const mutation = useMutation({
    mutationFn: (values: { name: string; agent_type: string; description?: string; knowledge_scope_mode: string }) =>
      createAgent({
        // tenant_id 由后端 dedicated 模式自动填充；这里给占位仅为满足 schema
        tenant_id: '__auto__',
        name: values.name,
        agent_type: values.agent_type,
        description: values.description,
        knowledge_scope_mode: values.knowledge_scope_mode as 'tenant_all' | 'document_subset' | 'source_file_subset',
      }),
    onSuccess: (agent) => {
      message.success('已创建草稿 Agent');
      navigate(`/agents/${agent.id}/setup`);
    },
    onError: (e: unknown) => message.error(`创建失败：${(e as Error).message}`),
  });

  return (
    <div>
      <PageHeader title="新建 Agent" description="创建一个草稿 Agent，随后在就绪页完成配置并激活。" />
      <Card style={{ maxWidth: 640 }}>
        <Form
          layout="vertical"
          form={form}
          initialValues={{ agent_type: 'sales_assistant', knowledge_scope_mode: 'tenant_all' }}
          onFinish={(v) => mutation.mutate(v)}
        >
          <Form.Item label="Agent 名称" name="name" rules={[{ required: true }]}>
            <Input placeholder="例如：泰山销售陪跑 Agent" />
          </Form.Item>
          <Form.Item label="模板类型" name="agent_type">
            <Select options={[{ value: 'sales_assistant', label: '销售陪跑 (sales_assistant)' }]} />
          </Form.Item>
          <Form.Item label="知识作用域" name="knowledge_scope_mode">
            <Select
              options={[
                { value: 'tenant_all', label: '租户全量知识 (默认)' },
                { value: 'document_subset', label: '指定文档子集' },
                { value: 'source_file_subset', label: '指定源文件子集' },
              ]}
            />
          </Form.Item>
          <Form.Item label="描述" name="description">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={mutation.isPending}>
            创建草稿 Agent
          </Button>
        </Form>
      </Card>
    </div>
  );
}
