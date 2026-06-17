import { useState } from 'react';
import {
  Tabs,
  Upload,
  Button,
  Table,
  Typography,
  Modal,
  Space,
  message,
  Alert,
} from 'antd';
import { UploadOutlined, RedoOutlined } from '@ant-design/icons';
import type { UploadFile } from 'antd/es/upload/interface';
import { useTenant } from '@/context/TenantContext';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  uploadKnowledgeFile,
  listIngestionJobs,
  retryIngestionJob,
  reindexDocument,
} from '@/api/knowledge';
import { listDocuments } from '@/api/admin';
import { queryKeys } from '@/utils/queryKeys';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import StatusTag from '@/components/StatusTag';
import RelativeTime from '@/components/RelativeTime';

const { Text } = Typography;

export default function KnowledgePage() {
  const { tenantId } = useTenant();
  const queryClient = useQueryClient();

  // ---- Upload state ----
  const [uploadResult, setUploadResult] = useState<{ job_id: string } | null>(null);

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadKnowledgeFile(tenantId!, file),
    onSuccess: (data) => {
      message.success('上传成功');
      setUploadResult(data);
      queryClient.invalidateQueries({ queryKey: queryKeys.ingestionJobs(tenantId!) });
    },
    onError: () => {
      message.error('上传失败');
    },
  });

  // ---- Jobs query ----
  const jobsQuery = useQuery({
    queryKey: queryKeys.ingestionJobs(tenantId!),
    queryFn: () => listIngestionJobs(tenantId!),
    enabled: !!tenantId,
    refetchInterval: (query) => {
      const running = query.state.data?.items?.some(
        (job) => job.status === 'running' || job.status === 'queued' || job.status === 'processing',
      );
      return running ? 5000 : false;
    },
  });

  // ---- Documents query ----
  const docsQuery = useQuery({
    queryKey: queryKeys.documents(tenantId!),
    queryFn: () => listDocuments(tenantId!),
    enabled: !!tenantId,
  });

  // ---- Retry mutation ----
  const retryMutation = useMutation({
    mutationFn: (jobId: string) => retryIngestionJob(tenantId!, jobId),
    onSuccess: () => {
      message.success('重试已提交');
      queryClient.invalidateQueries({ queryKey: queryKeys.ingestionJobs(tenantId!) });
    },
    onError: () => {
      message.error('重试失败');
    },
  });

  // ---- Reindex mutation ----
  const reindexMutation = useMutation({
    mutationFn: (documentId: string) => reindexDocument(tenantId!, documentId),
    onSuccess: () => {
      message.success('重建索引已提交');
      queryClient.invalidateQueries({ queryKey: queryKeys.documents(tenantId!) });
    },
    onError: () => {
      message.error('重建索引失败');
    },
  });

  // ---- Upload tab ----
  const uploadTab = (
    <div style={{ maxWidth: 600 }}>
      <Upload.Dragger
        accept=".md"
        multiple={false}
        showUploadList={false}
        customRequest={({ file, onSuccess: onUploadSuccess }) => {
          uploadMutation.mutate(file as File, {
            onSettled: () => {
              onUploadSuccess?.(null);
            },
          });
        }}
        disabled={uploadMutation.isPending}
      >
        <p className="ant-upload-drag-icon">
          <UploadOutlined style={{ fontSize: 48, color: '#1677ff' }} />
        </p>
        <p className="ant-upload-text">点击或拖拽 .md 文件到此处上传</p>
        <p className="ant-upload-hint">仅支持 Markdown 文件</p>
      </Upload.Dragger>

      {uploadMutation.isPending && (
        <Alert
          style={{ marginTop: 16 }}
          type="info"
          message="正在上传..."
          showIcon
        />
      )}

      {uploadResult && (
        <Alert
          style={{ marginTop: 16 }}
          type="success"
          message="上传成功"
          description={
            <span>
              任务 ID: <Text copyable>{uploadResult.job_id}</Text>
            </span>
          }
          showIcon
          closable
          onClose={() => setUploadResult(null)}
        />
      )}
    </div>
  );

  // ---- Jobs tab ----
  const jobColumns = [
    {
      title: 'Job ID',
      dataIndex: 'id',
      key: 'id',
      width: 200,
      render: (id: string) => (
        <Text copyable style={{ fontSize: 12 }}>
          {id.length > 12 ? `${id.slice(0, 12)}...` : id}
        </Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => <StatusTag status={status} />,
    },
    {
      title: '文档数',
      dataIndex: 'documents_ingested',
      key: 'documents_ingested',
      width: 90,
      align: 'right' as const,
      render: (val: number, row: { documents_seen: number }) =>
        `${row.documents_seen ?? 0}`,
    },
    {
      title: '分块数',
      dataIndex: 'chunks_created',
      key: 'chunks_created',
      width: 90,
      align: 'right' as const,
    },
    {
      title: '警告/错误',
      key: 'issues',
      width: 120,
      render: (_: unknown, row: { warnings: string[]; errors: Record<string, unknown>[] }) => (
        <Space size={4}>
          {row.warnings?.length > 0 && (
            <Text type="warning">{row.warnings.length} 警告</Text>
          )}
          {row.errors?.length > 0 && (
            <Text type="danger">{row.errors.length} 错误</Text>
          )}
          {(!row.warnings?.length && !row.errors?.length) && <Text type="secondary">-</Text>}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: unknown, row: { id: string; status: string }) => {
        if (row.status === 'failed' || row.status === 'error') {
          return (
            <Button
              size="small"
              icon={<RedoOutlined />}
              onClick={() => {
                Modal.confirm({
                  title: '确认重试',
                  content: `确定要重试任务 ${row.id.slice(0, 12)}... 吗？`,
                  onOk: () => retryMutation.mutate(row.id),
                });
              }}
            >
              重试
            </Button>
          );
        }
        return null;
      },
    },
  ];

  const jobsTab = (
    <>
      {jobsQuery.isLoading ? (
        <LoadingState tip="加载任务列表..." />
      ) : jobsQuery.isError ? (
        <ErrorState
          message="加载任务列表失败"
          onRetry={() => jobsQuery.refetch()}
        />
      ) : (
        <Table
          dataSource={jobsQuery.data?.items ?? []}
          columns={jobColumns}
          rowKey="id"
          size="small"
          pagination={{ pageSize: 20, showSizeChanger: true }}
        />
      )}
    </>
  );

  // ---- Documents tab ----
  const docColumns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 180,
      render: (id: string) => (
        <Text copyable style={{ fontSize: 12 }}>
          {id.length > 12 ? `${id.slice(0, 12)}...` : id}
        </Text>
      ),
    },
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
    },
    {
      title: '来源',
      dataIndex: 'source_path',
      key: 'source_path',
      ellipsis: true,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => <StatusTag status={status} />,
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
      key: 'action',
      width: 120,
      render: (_: unknown, row: { id: string }) => (
        <Button
          size="small"
          icon={<RedoOutlined />}
          onClick={() => {
            Modal.confirm({
              title: '确认重建索引',
              content: `确定要重建文档 ${row.id.slice(0, 12)}... 的索引吗？`,
              onOk: () => reindexMutation.mutate(row.id),
            });
          }}
        >
          重建索引
        </Button>
      ),
    },
  ];

  const docsTab = (
    <>
      {docsQuery.isLoading ? (
        <LoadingState tip="加载文档列表..." />
      ) : docsQuery.isError ? (
        <ErrorState
          message="加载文档列表失败"
          onRetry={() => docsQuery.refetch()}
        />
      ) : (
        <Table
          dataSource={docsQuery.data?.items ?? []}
          columns={docColumns}
          rowKey="id"
          size="small"
          pagination={{ pageSize: 20, showSizeChanger: true }}
        />
      )}
    </>
  );

  return (
    <>
      <PageHeader title="知识库" description="管理知识文档上传与索引" />
      <Tabs
        defaultActiveKey="upload"
        items={[
          { key: 'upload', label: '上传', children: uploadTab },
          { key: 'jobs', label: '导入任务', children: jobsTab },
          { key: 'documents', label: '文档', children: docsTab },
        ]}
      />
    </>
  );
}
