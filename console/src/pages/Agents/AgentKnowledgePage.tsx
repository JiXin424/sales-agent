/** Agent-scoped knowledge — documents visible to this Agent + scope mode + Neo4j ontology ingestion. */

import { useState } from 'react';
import { Table, Tag, Typography, Card, Descriptions, Alert, Button, Space, Upload } from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getAgent, listAgentDocuments } from '@/api/agents';
import { getOntologyStatus, startOntologyIngest, listOntologyJobs } from '@/api/knowledge';
import type { OntologyJob } from '@/api/types';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import RelativeTime from '@/components/RelativeTime';

interface DocRow {
  id: string;
  title: string;
  status: string;
  created_at: string;
}

const SCOPE_LABEL: Record<string, string> = {
  tenant_all: '租户全量知识',
  document_subset: '指定文档子集',
  source_file_subset: '指定源文件子集',
};

export default function AgentKnowledgePage() {
  const { agentId } = useParams<{ agentId: string }>();
  const queryClient = useQueryClient();
  const [ingestFiles, setIngestFiles] = useState<File[]>([]);

  const { data: agent, isLoading: agentLoading } = useQuery({
    queryKey: ['agent', agentId], queryFn: () => getAgent(agentId!), enabled: !!agentId,
  });
  const { data, isLoading, isError } = useQuery({
    queryKey: ['agent-documents', agentId],
    queryFn: () => listAgentDocuments(agentId!, 100, 0),
    enabled: !!agentId,
  });

  const statusQuery = useQuery({
    queryKey: ['ontology-status', agentId],
    queryFn: () => getOntologyStatus(agentId!),
    enabled: !!agentId,
  });
  const jobsQuery = useQuery({
    queryKey: ['ontology-jobs', agentId],
    queryFn: () => listOntologyJobs(agentId!, 20, 0),
    enabled: !!agentId,
  });
  const ingestMutation = useMutation({
    mutationFn: () => startOntologyIngest(agentId!, ingestFiles),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ontology-jobs', agentId] });
      setIngestFiles([]);
    },
  });

  if (agentLoading || isLoading) return <LoadingState />;
  if (isError || !agent) return <ErrorState />;

  const ontologyStatus = statusQuery.data;
  const ontologyReady = ontologyStatus?.neo4j_ready;

  const docColumns = [
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true,
      render: (t: string) => <Typography.Text ellipsis style={{ maxWidth: 360 }}>{t}</Typography.Text> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 110,
      render: (s: string) => <Tag color={s === 'active' ? 'green' : 'default'}>{s}</Tag> },
    { title: '文档 ID', dataIndex: 'id', key: 'id', width: 150, ellipsis: true },
    { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 160,
      render: (v: string) => (v ? <RelativeTime iso={v} /> : '-') },
  ];

  const jobColumns = [
    { title: '阶段', dataIndex: 'stage', key: 'stage', width: 120,
      render: (s: string) => <Tag>{s || '-'}</Tag> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (s: string) => <Tag color={s === 'completed' ? 'green' : s === 'failed' ? 'red' : 'processing'}>{s}</Tag> },
    { title: '实体', dataIndex: 'entities_created', key: 'entities_created', width: 80 },
    { title: 'Fact', dataIndex: 'facts_created', key: 'facts_created', width: 80 },
    { title: '待复核', dataIndex: 'facts_pending_review', key: 'facts_pending_review', width: 80 },
    { title: '冲突', dataIndex: 'conflicts_created', key: 'conflicts_created', width: 80 },
    { title: '时间', dataIndex: 'updated_at', key: 'updated_at', width: 160,
      render: (v: string) => (v ? <RelativeTime iso={v} /> : '-') },
  ];

  return (
    <div>
      <PageHeader title="知识库" description={`作用域：Agent「${agent.name}」`} />
      <Card size="small" style={{ marginBottom: 16 }}>
        <Descriptions column={2} size="small">
          <Descriptions.Item label="知识作用域">
            <Tag color="blue">{SCOPE_LABEL['tenant_all']}</Tag>
            {agent.knowledge_scope_id ? '（已配置）' : '（未配置，回退租户全量）'}
          </Descriptions.Item>
          <Descriptions.Item label="作用域 ID">{agent.knowledge_scope_id ?? '—'}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card
        size="small"
        title="Neo4j 本体入库"
        style={{ marginBottom: 16 }}
        extra={
          ontologyStatus ? (
            <Space>
              <Tag color={ontologyReady ? 'green' : 'orange'}>{ontologyStatus.ontology_status}</Tag>
              {ontologyStatus.visual_url ? (
                <Button size="small" href={ontologyStatus.visual_url} target="_blank" rel="noreferrer">
                  打开可视化
                </Button>
              ) : null}
            </Space>
          ) : null
        }
      >
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Upload
            multiple
            beforeUpload={(file) => {
              setIngestFiles((prev) => [...prev, file]);
              return false; // prevent auto-upload
            }}
            onRemove={(file) => {
              setIngestFiles((prev) => prev.filter((f) => f.name !== file.name));
            }}
            fileList={ingestFiles.map((f) => ({ uid: f.name, name: f.name, status: 'done' as const }))}
          >
            <Button icon={<UploadOutlined />}>选择入库文档</Button>
          </Upload>
          <Button
            type="primary"
            loading={ingestMutation.isPending}
            disabled={ingestFiles.length === 0}
            onClick={() => ingestMutation.mutate()}
          >
            启动 Neo4j 入库 ({ingestFiles.length} 个文件)
          </Button>
        </Space>
        {ingestMutation.isError ? (
          <Alert
            type="error" showIcon style={{ marginBottom: 12 }}
            message="入库启动失败"
            description={String((ingestMutation.error as Error)?.message ?? ingestMutation.error)}
          />
        ) : null}
        {statusQuery.isLoading ? (
          <Alert type="info" showIcon message="正在读取本体状态…" />
        ) : null}
        <Table<OntologyJob>
          rowKey="id"
          size="small"
          dataSource={jobsQuery.data?.items ?? []}
          columns={jobColumns}
          pagination={{ pageSize: 10, total: jobsQuery.data?.total ?? 0 }}
        />
      </Card>

      {(data?.total ?? 0) === 0 && (
        <Alert
          type="info" showIcon style={{ marginBottom: 16 }}
          message="该 Agent 作用域内暂无文档"
          description="租户全量模式下需先为租户导入知识；子集模式需在设置中配置文档 ID。"
        />
      )}
      <Table
        rowKey="id"
        dataSource={(data?.items ?? []) as DocRow[]}
        columns={docColumns}
        pagination={{ pageSize: 20, total: data?.total ?? 0 }}
      />
    </div>
  );
}
