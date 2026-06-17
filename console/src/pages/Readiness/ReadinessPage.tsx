import { useMemo } from 'react';
import { Card, List, Typography, Tag, Progress } from 'antd';
import { CheckCircleFilled, CloseCircleFilled } from '@ant-design/icons';
import { useTenant } from '@/context/TenantContext';
import { useQuery } from '@tanstack/react-query';
import * as api from '@/api';
import { queryKeys } from '@/utils/queryKeys';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

const { Text } = Typography;

interface CheckItem {
  key: string;
  title: string;
  description: string;
  passed: boolean;
  detail?: string;
}

export default function ReadinessPage() {
  const { tenantId } = useTenant();

  // 6 parallel readiness checks
  const tenantQuery = useQuery({
    queryKey: queryKeys.tenant(tenantId!),
    queryFn: () => api.getTenant(tenantId!),
    enabled: !!tenantId,
  });

  const readyQuery = useQuery({
    queryKey: queryKeys.ready,
    queryFn: () => api.getReady(),
  });

  const documentsQuery = useQuery({
    queryKey: queryKeys.documents(tenantId!, { limit: 1 }),
    queryFn: () => api.listDocuments(tenantId!, { limit: 1 }),
    enabled: !!tenantId,
  });

  const promptsQuery = useQuery({
    queryKey: queryKeys.prompts(tenantId!, { status: 'active', limit: 1 }),
    queryFn: () => api.listPromptVersions(tenantId!, { status: 'active', limit: 1 }),
    enabled: !!tenantId,
  });

  const conversationsQuery = useQuery({
    queryKey: queryKeys.conversations(tenantId!, { limit: 1 }),
    queryFn: () => api.listConversations(tenantId!, { limit: 1 }),
    enabled: !!tenantId,
  });

  const healthQuery = useQuery({
    queryKey: queryKeys.health,
    queryFn: () => api.getHealth(),
  });

  const isLoading =
    tenantQuery.isLoading ||
    readyQuery.isLoading ||
    documentsQuery.isLoading ||
    promptsQuery.isLoading ||
    conversationsQuery.isLoading ||
    healthQuery.isLoading;

  const hasError =
    tenantQuery.isError ||
    readyQuery.isError ||
    documentsQuery.isError ||
    promptsQuery.isError ||
    conversationsQuery.isError ||
    healthQuery.isError;

  const retryAll = () => {
    tenantQuery.refetch();
    readyQuery.refetch();
    documentsQuery.refetch();
    promptsQuery.refetch();
    conversationsQuery.refetch();
    healthQuery.refetch();
  };

  // Build checklist items from query results
  const checks = useMemo<CheckItem[]>(() => {
    return [
      {
        key: 'tenant',
        title: '租户已配置',
        description: '租户存在且状态为活跃',
        passed: tenantQuery.data?.status === 'active',
        detail: tenantQuery.data ? `状态: ${tenantQuery.data.status}` : undefined,
      },
      {
        key: 'ready',
        title: '模型连接正常',
        description: '模型服务连通性检查通过',
        passed: readyQuery.data?.status === 'ready',
        detail: readyQuery.data?.errors?.length
          ? `错误: ${readyQuery.data.errors.join('; ')}`
          : undefined,
      },
      {
        key: 'documents',
        title: '知识库已上传',
        description: '至少有一份知识文档',
        passed: (documentsQuery.data?.total ?? 0) > 0,
        detail: `文档数: ${documentsQuery.data?.total ?? 0}`,
      },
      {
        key: 'prompts',
        title: '提示词已激活',
        description: '至少有一个活跃的提示词版本',
        passed: (promptsQuery.data?.total ?? 0) > 0,
        detail: `活跃版本数: ${promptsQuery.data?.total ?? 0}`,
      },
      {
        key: 'conversations',
        title: '对话已产生',
        description: '至少有一轮成功的对话记录',
        passed: (conversationsQuery.data?.total ?? 0) > 0,
        detail: `对话数: ${conversationsQuery.data?.total ?? 0}`,
      },
      {
        key: 'health',
        title: '服务运行正常',
        description: '后端服务健康检查通过',
        passed: healthQuery.data?.status === 'ok',
        detail: healthQuery.data ? `状态: ${healthQuery.data.status}` : undefined,
      },
    ];
  }, [
    tenantQuery.data,
    readyQuery.data,
    documentsQuery.data,
    promptsQuery.data,
    conversationsQuery.data,
    healthQuery.data,
  ]);

  const passedCount = checks.filter((c) => c.passed).length;
  const totalCount = checks.length;

  // --- Loading / Error states ---

  if (isLoading) {
    return (
      <>
        <PageHeader title="就绪检查" description="验证系统是否已准备就绪，可投入使用" />
        <LoadingState tip="正在检查系统就绪状态..." />
      </>
    );
  }

  if (hasError) {
    return (
      <>
        <PageHeader title="就绪检查" description="验证系统是否已准备就绪，可投入使用" />
        <ErrorState
          title="就绪检查失败"
          message="部分检查项加载失败，请重试"
          onRetry={retryAll}
        />
      </>
    );
  }

  return (
    <>
      <PageHeader title="就绪检查" description="验证系统是否已准备就绪，可投入使用" />

      {/* Overall score card */}
      <Card style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
          <Progress
            type="circle"
            percent={Math.round((passedCount / totalCount) * 100)}
            format={() => (
              <span>
                {passedCount}/{totalCount}
              </span>
            )}
            strokeColor={passedCount === totalCount ? '#52c41a' : passedCount >= 4 ? '#faad14' : '#ff4d4f'}
            size={80}
          />
          <div>
            <Typography.Title level={4} style={{ margin: 0 }}>
              {passedCount}/{totalCount} 项通过
            </Typography.Title>
            <Text type="secondary">
              {passedCount === totalCount
                ? '系统已完全就绪，可以开始使用'
                : passedCount >= 4
                  ? '大部分检查已通过，请关注未通过项'
                  : '多项检查未通过，请先完成基础配置'}
            </Text>
          </div>
          {passedCount === totalCount && (
            <Tag color="success" style={{ marginLeft: 'auto', fontSize: 14, padding: '4px 12px' }}>
              全部通过
            </Tag>
          )}
        </div>
      </Card>

      {/* Checklist */}
      <Card>
        <List
          dataSource={checks}
          renderItem={(item) => (
            <List.Item>
              <List.Item.Meta
                avatar={
                  item.passed ? (
                    <CheckCircleFilled style={{ color: '#52c41a', fontSize: 22 }} />
                  ) : (
                    <CloseCircleFilled style={{ color: '#ff4d4f', fontSize: 22 }} />
                  )
                }
                title={
                  <span>
                    {item.title}{' '}
                    <Tag color={item.passed ? 'success' : 'error'}>
                      {item.passed ? '通过' : '未通过'}
                    </Tag>
                  </span>
                }
                description={
                  <span>
                    {item.description}
                    {item.detail && (
                      <Text type="secondary" style={{ marginLeft: 8 }}>
                        ({item.detail})
                      </Text>
                    )}
                  </span>
                }
              />
            </List.Item>
          )}
        />
      </Card>
    </>
  );
}
