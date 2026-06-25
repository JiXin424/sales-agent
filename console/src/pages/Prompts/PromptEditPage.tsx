import { useState, useEffect, useMemo } from 'react';
import {
  Form,
  Input,
  Select,
  Button,
  Card,
  Space,
  Modal,
  message,
  Typography,
  Divider,
  Tag,
} from 'antd';
import { useNavigate, useParams } from 'react-router-dom';
import { useTenant } from '@/context/TenantContext';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  getPromptVersion,
  createPromptVersion,
  updatePromptVersion,
  activatePromptVersion,
  previewPrompt,
  listBuiltinPrompts,
} from '@/api/prompts';
import type { PromptCategory, PromptPreviewRequest } from '@/api/types';
import { queryKeys } from '@/utils/queryKeys';
import {
  TASK_TYPE_LABELS,
  PROMPT_CATEGORY_LABELS,
  PROMPT_KEYS_BY_CATEGORY,
} from '@/utils/constants';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

const { TextArea } = Input;
const { Text } = Typography;

const CATEGORY_OPTIONS = Object.entries(PROMPT_CATEGORY_LABELS).map(([value, label]) => ({
  value,
  label,
}));
const TASK_OPTIONS = Object.entries(TASK_TYPE_LABELS).map(([value, label]) => ({ value, label }));

export default function PromptEditPage() {
  const { tenantId } = useTenant();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { id } = useParams<{ id: string }>();
  const isEditing = !!id;

  const [form] = Form.useForm();
  const [savedId, setSavedId] = useState<string | null>(id ?? null);
  const [previewMessage, setPreviewMessage] = useState('');
  const [previewResult, setPreviewResult] = useState<string | null>(null);

  const promptCategory: string = Form.useWatch('prompt_category', form) ?? 'task';

  const promptQuery = useQuery({
    queryKey: queryKeys.prompt(tenantId!, id!),
    queryFn: () => getPromptVersion(tenantId!, id!),
    enabled: isEditing && !!tenantId,
  });

  // 内置 prompt（供占位符提示）
  const builtinQuery = useQuery({
    queryKey: ['builtin-prompts', tenantId],
    queryFn: () => listBuiltinPrompts(tenantId!),
    enabled: !!tenantId,
  });

  useEffect(() => {
    if (promptQuery.data) {
      form.setFieldsValue({
        prompt_category: promptQuery.data.prompt_category ?? 'task',
        task_type: promptQuery.data.task_type ?? undefined,
        prompt_key: promptQuery.data.prompt_key ?? undefined,
        version: promptQuery.data.version,
        description: promptQuery.data.description,
        template_text: promptQuery.data.template_text,
      });
    }
  }, [promptQuery.data, form]);

  // 当前 (category, key) 的必须占位符
  const placeholders = useMemo(() => {
    const key = form.getFieldValue('task_type') || form.getFieldValue('prompt_key');
    const found = builtinQuery.data?.find(
      (b) => b.prompt_category === promptCategory && b.prompt_key === key,
    );
    return found?.required_placeholders ?? [];
  }, [promptCategory, builtinQuery.data, form]);

  const createMutation = useMutation({
    mutationFn: (data: {
      task_type?: string;
      template_text: string;
      description?: string;
      version?: string;
      prompt_category?: string;
      prompt_key?: string | null;
    }) => createPromptVersion(tenantId!, data),
    onSuccess: (data) => {
      message.success('草稿已保存');
      setSavedId(data.id);
      queryClient.invalidateQueries({ queryKey: queryKeys.prompts(tenantId!) });
    },
    onError: () => message.error('保存失败'),
  });

  const updateMutation = useMutation({
    mutationFn: (data: { template_text?: string; description?: string }) =>
      updatePromptVersion(tenantId!, savedId!, data),
    onSuccess: () => {
      message.success('草稿已更新');
      queryClient.invalidateQueries({ queryKey: queryKeys.prompts(tenantId!) });
      queryClient.invalidateQueries({ queryKey: queryKeys.prompt(tenantId!, savedId!) });
    },
    onError: () => message.error('更新失败'),
  });

  const activateMutation = useMutation({
    mutationFn: () => activatePromptVersion(tenantId!, savedId!),
    onSuccess: () => {
      message.success('已激活');
      queryClient.invalidateQueries({ queryKey: queryKeys.prompts(tenantId!) });
      navigate('/prompts');
    },
    onError: () => message.error('激活失败'),
  });

  const previewMutation = useMutation({
    mutationFn: (req: PromptPreviewRequest) => previewPrompt(tenantId!, req),
    onSuccess: (data) => setPreviewResult(data.rendered_prompt),
    onError: () => message.error('预览失败'),
  });

  const handleSaveDraft = async () => {
    try {
      const values = await form.validateFields();
      if (savedId && isEditing) {
        updateMutation.mutate({
          template_text: values.template_text,
          description: values.description,
        });
      } else {
        const category = values.prompt_category ?? 'task';
        createMutation.mutate({
          template_text: values.template_text,
          description: values.description,
          version: values.version,
          prompt_category: category,
          task_type: category === 'task' ? values.task_type : '',
          prompt_key: category === 'task' ? values.task_type : values.prompt_key,
        });
      }
    } catch {
      // validation failed
    }
  };

  const handlePreview = async () => {
    if (!previewMessage.trim()) {
      message.warning('请输入示例消息');
      return;
    }
    const category = ((form.getFieldValue('prompt_category') as string) ?? 'task') as PromptCategory;
    const key =
      category === 'task'
        ? form.getFieldValue('task_type')
        : form.getFieldValue('prompt_key');
    previewMutation.mutate({
      prompt_category: category,
      prompt_key: key,
      task_type: category === 'task' ? (key as string) : null,
      version_id: savedId,
      sample_message: previewMessage,
    });
  };

  const handleActivate = () => {
    Modal.confirm({
      title: '确认激活',
      content: '激活后该版本将用于生产环境，确定激活？',
      onOk: () => activateMutation.mutate(),
    });
  };

  const currentStatus = promptQuery.data?.status;

  if (isEditing && promptQuery.isLoading) {
    return (
      <>
        <PageHeader title="编辑提示词" />
        <LoadingState tip="加载提示词详情..." />
      </>
    );
  }
  if (isEditing && promptQuery.isError) {
    return (
      <>
        <PageHeader title="编辑提示词" />
        <ErrorState message="加载提示词详情失败" onRetry={() => promptQuery.refetch()} />
      </>
    );
  }

  const isSaving = createMutation.isPending || updateMutation.isPending;
  const nonTaskKeyOptions = PROMPT_KEYS_BY_CATEGORY[promptCategory] ?? [];

  return (
    <>
      <PageHeader
        title={isEditing ? '编辑提示词' : '新建提示词'}
        actions={<Button onClick={() => navigate('/prompts')}>返回列表</Button>}
      />

      <div style={{ display: 'flex', gap: 24 }}>
        {/* Left: form */}
        <Card style={{ flex: 1 }}>
          <Form
            form={form}
            layout="vertical"
            initialValues={{
              prompt_category: 'task',
              version: '',
              description: '',
              template_text: '',
            }}
          >
            <Form.Item
              name="prompt_category"
              label="Prompt 类别"
              rules={[{ required: true }]}
            >
              <Select options={CATEGORY_OPTIONS} disabled={isEditing} />
            </Form.Item>

            {promptCategory === 'task' ? (
              <Form.Item
                name="task_type"
                label="任务类型"
                rules={[{ required: true, message: '请选择任务类型' }]}
              >
                <Select options={TASK_OPTIONS} disabled={isEditing} />
              </Form.Item>
            ) : (
              <Form.Item
                name="prompt_key"
                label="Prompt 标识"
                rules={[{ required: true, message: '请选择标识' }]}
              >
                <Select
                  options={nonTaskKeyOptions.map((k) => ({ value: k.key, label: k.label }))}
                  disabled={isEditing}
                />
              </Form.Item>
            )}

            <Form.Item name="version" label="版本">
              <Input placeholder="如 v1.0" disabled={isEditing} />
            </Form.Item>

            <Form.Item name="description" label="描述">
              <TextArea rows={2} placeholder="描述该版本的变更" />
            </Form.Item>

            {placeholders.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  必须占位符：
                </Text>{' '}
                {placeholders.map((p) => (
                  <Tag key={p}>{`{${p}}`}</Tag>
                ))}
                {promptCategory === 'task' && (
                  <Tag>context_block / retrieval_block / retrieval_content（可选）</Tag>
                )}
              </div>
            )}

            <Form.Item
              name="template_text"
              label="模板文本"
              rules={[{ required: true, message: '请输入模板文本' }]}
            >
              <TextArea
                rows={16}
                placeholder="输入提示词模板..."
                style={{ fontFamily: 'monospace', fontSize: 13 }}
              />
            </Form.Item>
          </Form>

          <Space>
            <Button type="primary" onClick={handleSaveDraft} loading={isSaving}>
              保存草稿
            </Button>
            {savedId && currentStatus === 'draft' && (
              <Button onClick={handleActivate} loading={activateMutation.isPending}>
                激活
              </Button>
            )}
          </Space>
        </Card>

        {/* Right: preview */}
        <Card title="预览" style={{ width: 400 }}>
          <Form.Item label="示例消息" style={{ marginBottom: 12 }}>
            <Input
              value={previewMessage}
              onChange={(e) => setPreviewMessage(e.target.value)}
              placeholder="输入示例消息进行预览"
            />
          </Form.Item>
          <Button
            block
            onClick={handlePreview}
            loading={previewMutation.isPending}
            style={{ marginBottom: 16 }}
          >
            预览
          </Button>
          <Divider style={{ margin: '12px 0' }} />
          {previewResult !== null ? (
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                渲染结果:
              </Text>
              <pre
                style={{
                  background: '#f5f5f5',
                  padding: 12,
                  borderRadius: 6,
                  fontSize: 12,
                  fontFamily: 'monospace',
                  whiteSpace: 'pre-wrap',
                  maxHeight: 480,
                  overflow: 'auto',
                  marginTop: 8,
                }}
              >
                {previewResult}
              </pre>
            </div>
          ) : (
            <Text type="secondary">点击预览按钮查看渲染结果</Text>
          )}
        </Card>
      </div>
    </>
  );
}
