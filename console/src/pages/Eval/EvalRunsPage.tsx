import { useState } from 'react';
import { Table, Tag, Button, Modal, Descriptions, message, Select, Space } from 'antd';
import { PlayCircleOutlined, SwapOutlined } from '@ant-design/icons';
import { useTenant } from '@/context/TenantContext';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import * as pilotApi from '@/api/pilot';
import { queryKeys } from '@/utils/queryKeys';
import type { EvalRun, EvalRunResult } from '@/api/types';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

export default function EvalRunsPage() {
  const { tenantId } = useTenant();
  const qc = useQueryClient();
  const [selectedRun, setSelectedRun] = useState<EvalRun | null>(null);

  const suitesQuery = useQuery({
    queryKey: queryKeys.evalSuites(tenantId!),
    queryFn: () => pilotApi.listEvalSuites(tenantId!),
    enabled: !!tenantId,
  });

  const runsQuery = useQuery({
    queryKey: queryKeys.evalRuns(tenantId!),
    queryFn: () => pilotApi.listEvalRuns(tenantId!),
    enabled: !!tenantId,
  });

  const resultsQuery = useQuery({
    queryKey: queryKeys.evalRunResults(tenantId!, selectedRun?.id ?? ''),
    queryFn: () => pilotApi.getEvalRunResults(tenantId!, selectedRun!.id),
    enabled: !!tenantId && !!selectedRun,
  });

  const runMutation = useMutation({
    mutationFn: (suiteId: string) => pilotApi.runEvalSuite(tenantId!, suiteId),
    onSuccess: () => {
      message.success('评估运行已启动');
      qc.invalidateQueries({ queryKey: queryKeys.evalRuns(tenantId!) });
    },
    onError: () => message.error('评估运行失败'),
  });

  if (suitesQuery.isLoading || runsQuery.isLoading) {
    return (
      <>
        <PageHeader title="Eval 回归" description="评估套件管理与回归测试" />
        <LoadingState tip="加载评估数据..." />
      </>
    );
  }

  if (suitesQuery.isError || runsQuery.isError) {
    return (
      <>
        <PageHeader title="Eval 回归" description="评估套件管理与回归测试" />
        <ErrorState title="加载失败" message="评估数据加载失败" onRetry={() => { suitesQuery.refetch(); runsQuery.refetch(); }} />
      </>
    );
  }

  const suiteColumns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '用例数', dataIndex: 'case_count', key: 'case_count' },
    { title: '状态', dataIndex: 'status', key: 'status', render: (s: string) => <Tag color={s === 'active' ? 'green' : 'default'}>{s}</Tag> },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: { id: string }) => (
        <Button size="small" type="primary" icon={<PlayCircleOutlined />}
          onClick={() => runMutation.mutate(record.id)} loading={runMutation.isPending}>
          运行
        </Button>
      ),
    },
  ];

  const runColumns = [
    { title: 'ID', dataIndex: 'id', key: 'id', render: (id: string) => id.slice(0, 8) + '...' },
    { title: '状态', dataIndex: 'status', key: 'status', render: (s: string) => <Tag color={s === 'completed' ? 'green' : s === 'failed' ? 'red' : 'blue'}>{s}</Tag> },
    { title: '总数', dataIndex: 'total_cases', key: 'total_cases' },
    { title: '通过', dataIndex: 'passed', key: 'passed', render: (v: number) => <Tag color="green">{v}</Tag> },
    { title: '失败', dataIndex: 'failed', key: 'failed', render: (v: number) => v > 0 ? <Tag color="red">{v}</Tag> : <Tag>{v}</Tag> },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: EvalRun) => (
        <Button size="small" onClick={() => setSelectedRun(record)}>查看结果</Button>
      ),
    },
  ];

  const resultColumns = [
    { title: 'Case ID', dataIndex: 'eval_case_id', key: 'eval_case_id', render: (id: string) => id.slice(0, 8) + '...' },
    {
      title: '结果',
      dataIndex: 'passed',
      key: 'passed',
      render: (p: boolean) => <Tag color={p ? 'green' : 'red'}>{p ? '通过' : '失败'}</Tag>,
    },
    { title: '实际任务类型', dataIndex: 'actual_task_type', key: 'actual_task_type' },
    { title: '延迟', dataIndex: 'latency_ms', key: 'latency_ms', render: (ms: number | null) => ms ? `${ms}ms` : '-' },
    {
      title: '失败原因',
      dataIndex: 'failure_reasons',
      key: 'failure_reasons',
      render: (reasons: string[]) => reasons?.length > 0 ? reasons.join('; ') : '-',
    },
  ];

  return (
    <>
      <PageHeader title="Eval 回归" description="评估套件管理与回归测试" />

      <h3>评估套件</h3>
      <Table
        dataSource={suitesQuery.data?.items ?? []}
        columns={suiteColumns}
        rowKey="id"
        size="small"
        pagination={false}
        style={{ marginBottom: 24 }}
      />

      <h3>运行记录</h3>
      <Table
        dataSource={runsQuery.data?.items ?? []}
        columns={runColumns}
        rowKey="id"
        size="small"
        pagination={{ pageSize: 10 }}
      />

      {/* Run Results Modal */}
      <Modal
        title={`运行结果 - ${selectedRun?.id?.slice(0, 8) ?? ''}...`}
        open={!!selectedRun}
        onCancel={() => setSelectedRun(null)}
        footer={null}
        width={800}
      >
        {selectedRun && (
          <Descriptions size="small" column={3} style={{ marginBottom: 16 }}>
            <Descriptions.Item label="总数">{selectedRun.total_cases}</Descriptions.Item>
            <Descriptions.Item label="通过"><Tag color="green">{selectedRun.passed}</Tag></Descriptions.Item>
            <Descriptions.Item label="失败"><Tag color="red">{selectedRun.failed}</Tag></Descriptions.Item>
          </Descriptions>
        )}
        <Table
          dataSource={resultsQuery.data?.items ?? []}
          columns={resultColumns}
          rowKey="id"
          size="small"
          pagination={false}
          loading={resultsQuery.isLoading}
        />
      </Modal>
    </>
  );
}
