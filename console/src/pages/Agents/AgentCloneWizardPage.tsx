/** Clone Wizard — copy / reference / reset rules per resource.

Explicit choices per resource (metadata / prompt / risk / knowledge / eval /
channel / model), a secret-exclusion warning, and a manifest preview before
creating the draft clone.
*/

import { useState } from 'react';
import { Card, Form, Input, Select, Steps, Button, Alert, Descriptions, message, Space, Typography } from 'antd';
import { useQuery, useMutation } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';
import { getAgent, cloneAgent } from '@/api/agents';
import type { CloneOptions } from '@/api/types';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

const RESOURCE_DOCS: Record<string, string> = {
  prompt_set: 'copy=复制为独立版本（推荐，未来可独立编辑）；reference=引用源版本。',
  risk_policy: 'copy=复制规则副本；reference=引用源策略（源改动会影响克隆）。',
  knowledge_scope: 'reference=引用源作用域；copy_subset=独立副本；empty=空作用域（跨租户克隆强制此项）。',
  eval_suite: 'copy=复制套件元数据；reference=引用源套件；empty=不复制。',
  channel_config: 'shell_only=仅创建空壳（绝不复制密钥/凭证）；skip=跳过。',
  model_config_choice: 'reference=引用源模型配置；new=新建（密钥绝不复制）。',
};

export default function AgentCloneWizardPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [form] = Form.useForm<CloneOptions>();

  const { data: source, isLoading, isError } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => getAgent(agentId!),
    enabled: !!agentId,
  });

  const mutation = useMutation({
    mutationFn: (opts: CloneOptions) => cloneAgent(agentId!, opts),
    onSuccess: (result) => {
      message.success('克隆完成（草稿）');
      navigate(`/agents/${result.agent.id}/settings`);
    },
    onError: (e: unknown) => message.error(`克隆失败：${(e as Error).message}`),
  });

  if (isLoading) return <LoadingState />;
  if (isError || !source) return <ErrorState />;

  const options = form.getFieldsValue() as CloneOptions;

  return (
    <div>
      <PageHeader title="克隆 Agent" description={`源 Agent：${source.name}`} />
      <Steps
        current={step}
        items={[{ title: '目标信息' }, { title: '资源选项' }, { title: '预览与确认' }]}
        style={{ marginBottom: 24 }}
      />

      {step === 0 && (
        <Card style={{ maxWidth: 640 }}>
          <Form
            layout="vertical" form={form}
            initialValues={{
              name: `${source.name} (克隆)`,
              tenant_id: source.tenant_id,
              prompt_set: 'copy',
              risk_policy: 'copy',
              knowledge_scope: 'reference',
              eval_suite: 'copy',
              channel_config: 'shell_only',
              model_config_choice: 'reference',
              description: source.description,
            }}
          >
            <Form.Item label="新 Agent 名称" name="name" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item label="目标租户" name="tenant_id" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item label="描述" name="description"><Input.TextArea rows={2} /></Form.Item>
            <Button type="primary" onClick={() => setStep(1)}>下一步</Button>
          </Form>
        </Card>
      )}

      {step === 1 && (
        <Card style={{ maxWidth: 720 }}>
          <Alert
            type="warning" showIcon style={{ marginBottom: 16 }}
            message="安全约束"
            description="克隆绝不复制明文密钥、webhook 凭证或任何运行历史（会话/反馈/告警/报告）。"
          />
          <Form layout="vertical" form={form}>
            <Form.Item label="Prompt 集合" name="prompt_set" extra={RESOURCE_DOCS.prompt_set}>
              <Select options={[{ value: 'copy', label: '复制（独立）' }, { value: 'reference', label: '引用' }]} />
            </Form.Item>
            <Form.Item label="风险策略" name="risk_policy" extra={RESOURCE_DOCS.risk_policy}>
              <Select options={[{ value: 'copy', label: '复制' }, { value: 'reference', label: '引用' }]} />
            </Form.Item>
            <Form.Item label="知识作用域" name="knowledge_scope" extra={RESOURCE_DOCS.knowledge_scope}>
              <Select options={[
                { value: 'reference', label: '引用源作用域' },
                { value: 'copy_subset', label: '复制子集（独立）' },
                { value: 'empty', label: '空作用域' },
              ]} />
            </Form.Item>
            <Form.Item label="Eval 套件" name="eval_suite" extra={RESOURCE_DOCS.eval_suite}>
              <Select options={[
                { value: 'copy', label: '复制' }, { value: 'reference', label: '引用' }, { value: 'empty', label: '不复制' },
              ]} />
            </Form.Item>
            <Form.Item label="渠道配置" name="channel_config" extra={RESOURCE_DOCS.channel_config}>
              <Select options={[{ value: 'shell_only', label: '仅空壳' }, { value: 'skip', label: '跳过' }]} />
            </Form.Item>
            <Form.Item label="模型配置" name="model_config_choice" extra={RESOURCE_DOCS.model_config_choice}>
              <Select options={[{ value: 'reference', label: '引用' }, { value: 'new', label: '新建' }]} />
            </Form.Item>
            <Space>
              <Button onClick={() => setStep(0)}>上一步</Button>
              <Button type="primary" onClick={() => { form.validateFields(); setStep(2); }}>下一步</Button>
            </Space>
          </Form>
        </Card>
      )}

      {step === 2 && (
        <Card style={{ maxWidth: 640 }}>
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="新名称">{options.name}</Descriptions.Item>
            <Descriptions.Item label="目标租户">{options.tenant_id}</Descriptions.Item>
            <Descriptions.Item label="Prompt">{options.prompt_set}</Descriptions.Item>
            <Descriptions.Item label="风险策略">{options.risk_policy}</Descriptions.Item>
            <Descriptions.Item label="知识作用域">{options.knowledge_scope}</Descriptions.Item>
            <Descriptions.Item label="Eval">{options.eval_suite}</Descriptions.Item>
            <Descriptions.Item label="渠道">{options.channel_config}</Descriptions.Item>
            <Descriptions.Item label="模型配置">{options.model_config_choice}</Descriptions.Item>
            <Descriptions.Item label="运行历史"><Typography.Text type="danger">重置（不复制）</Typography.Text></Descriptions.Item>
          </Descriptions>
          <Alert
            style={{ marginTop: 16 }} type="info" showIcon
            message="将创建一个 draft Agent；激活前请完成就绪检查。"
          />
          <Space style={{ marginTop: 16 }}>
            <Button onClick={() => setStep(1)}>上一步</Button>
            <Button type="primary" loading={mutation.isPending} onClick={() => form.validateFields().then(() => mutation.mutate(options))}>
              确认克隆
            </Button>
          </Space>
        </Card>
      )}
    </div>
  );
}
