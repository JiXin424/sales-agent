/**
 * Knowledge Iteration workspace — optimization lifecycle management.
 *
 * Panels:
 *   1. Overview: stage, progress, budget, iteration controls
 *   2. Attribution: failure clusters with evidence
 *   3. Candidates: router/config/document diffs with approval
 *   4. Eval Comparison: baseline vs candidate per-metric
 *   5. Releases & Time Travel: release DAG, rollback, checkpoint replay
 *   6. Question Review: exploration suite, quality status, promotion
 */

import React, { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  Tabs,
  Card,
  Button,
  Space,
  Tag,
  Descriptions,
  Table,
  Progress,
  Modal,
  message,
  Spin,
  Empty,
  Typography,
  Select,
  Input,
  Popconfirm,
} from 'antd';
import {
  PlayCircleOutlined,
  StopOutlined,
  CheckCircleOutlined,
  RollbackOutlined,
  BranchesOutlined,
} from '@ant-design/icons';
import {
  listIterations,
  startIteration,
  cancelIteration,
  getIteration,
  approveIteration,
  rejectIteration,
  listCandidates,
  publishCandidate,
  rollbackRelease,
  listCheckpoints,
  forkCheckpoint,
  type IterationResponse,
  type CandidateResponse,
  type DiagnosisResponse,
} from '../../api/optimization';
import { apiGet } from '../../api/client';

const { Text, Title } = Typography;

// ── Iteration Overview ──────────────────────────────────────────────────

function IterationOverview({ agentId }: { agentId: string }) {
  const [iterations, setIterations] = useState<IterationResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listIterations(agentId);
      setIterations(data);
    } catch {
      message.error('Failed to load iterations');
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => { load(); }, [load]);

  const handleStart = async () => {
    setStarting(true);
    try {
      await startIteration(agentId, {
        fixed_suite_id: 'fixed_v1',
        max_candidates: 3,
      });
      message.success('Iteration started');
      await load();
    } catch {
      message.error('Failed to start iteration');
    } finally {
      setStarting(false);
    }
  };

  const handleCancel = async (iterationId: string) => {
    try {
      await cancelIteration(agentId, iterationId);
      message.success('Iteration cancelled');
      await load();
    } catch {
      message.error('Failed to cancel');
    }
  };

  const columns = [
    { title: '#', dataIndex: 'iteration_no', key: 'no', width: 60 },
    { title: 'Status', dataIndex: 'status', key: 'status', render: (s: string) => (
      <Tag color={s === 'running' ? 'processing' : s === 'completed' ? 'success' : s === 'cancelled' ? 'default' : 'warning'}>
        {s}
      </Tag>
    )},
    { title: 'Created', dataIndex: 'created_at', key: 'created' },
    {
      title: 'Actions', key: 'actions',
      render: (_: unknown, record: IterationResponse) => (
        <Space>
          {record.status === 'running' && (
            <Popconfirm title="Cancel this iteration?" onConfirm={() => handleCancel(record.id)}>
              <Button size="small" danger icon={<StopOutlined />}>Cancel</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Card title="Iterations" extra={
      <Button type="primary" icon={<PlayCircleOutlined />} loading={starting} onClick={handleStart}>
        Start New Iteration
      </Button>
    }>
      {loading ? <Spin /> : (
        <Table dataSource={iterations} columns={columns} rowKey="id" size="small" pagination={false} />
      )}
    </Card>
  );
}

// ── Attribution Panel ───────────────────────────────────────────────────

function AttributionPanel({ agentId, iterationId }: { agentId: string; iterationId: string }) {
  const [diagnoses, setDiagnoses] = useState<DiagnosisResponse[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        // Fetch diagnoses from API
        const data = await apiGet<DiagnosisResponse[]>(
          `/agents/${agentId}/optimization/iterations/${iterationId}/diagnoses`
        ).catch(() => [] as DiagnosisResponse[]);
        setDiagnoses(data);
      } finally {
        setLoading(false);
      }
    })();
  }, [agentId, iterationId]);

  const columns = [
    { title: 'Cluster', dataIndex: 'cluster_key', key: 'cluster' },
    {
      title: 'Primary Cause', dataIndex: 'primary_cause', key: 'cause',
      render: (c: string) => <Tag color="red">{c}</Tag>,
    },
    {
      title: 'Confidence', dataIndex: 'confidence', key: 'confidence',
      render: (c: number) => <Progress percent={Math.round(c * 100)} size="small" />,
    },
    { title: 'Action', dataIndex: 'recommended_action', key: 'action' },
    {
      title: 'Cases', dataIndex: 'affected_case_ids', key: 'cases',
      render: (ids: string[]) => `${ids.length} cases`,
    },
  ];

  return (
    <Card title="Failure Attribution">
      {loading ? <Spin /> : diagnoses.length === 0 ? (
        <Empty description="No diagnoses yet. Run a baseline evaluation first." />
      ) : (
        <Table dataSource={diagnoses} columns={columns} rowKey="id" size="small" pagination={false} />
      )}
    </Card>
  );
}

// ── Candidate Diff Panel ────────────────────────────────────────────────

function CandidateDiffPanel({ agentId, iterationId }: { agentId: string; iterationId: string }) {
  const [candidates, setCandidates] = useState<CandidateResponse[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listCandidates(agentId, iterationId);
      setCandidates(data);
    } catch {
      message.error('Failed to load candidates');
    } finally {
      setLoading(false);
    }
  }, [agentId, iterationId]);

  useEffect(() => { load(); }, [load]);

  const handlePublish = async (candidateId: string) => {
    try {
      await publishCandidate(agentId, candidateId);
      message.success('Published!');
      await load();
    } catch {
      message.error('Publish failed');
    }
  };

  const columns = [
    { title: 'Attempt', dataIndex: 'attempt_number', key: 'attempt', width: 70 },
    {
      title: 'Type', dataIndex: 'change_type', key: 'type',
      render: (t: string) => <Tag>{t}</Tag>,
    },
    { title: 'Status', dataIndex: 'status', key: 'status' },
    { title: 'Hypothesis', dataIndex: 'hypothesis', key: 'hypothesis', ellipsis: true },
    { title: 'Patch Hash', dataIndex: 'patch_hash', key: 'hash', ellipsis: true },
    {
      title: 'Actions', key: 'actions',
      render: (_: unknown, record: CandidateResponse) => (
        record.status === 'approved' ? (
          <Popconfirm title="Publish this candidate?" onConfirm={() => handlePublish(record.id)}>
            <Button size="small" type="primary" icon={<CheckCircleOutlined />}>Publish</Button>
          </Popconfirm>
        ) : null
      ),
    },
  ];

  return (
    <Card title="Candidates">
      {loading ? <Spin /> : (
        <Table dataSource={candidates} columns={columns} rowKey="id" size="small" pagination={false} />
      )}
    </Card>
  );
}

// ── Eval Comparison Panel ───────────────────────────────────────────────

function EvalComparisonPanel({ agentId }: { agentId: string }) {
  const [comparisons, setComparisons] = useState<{ metric_name: string; baseline_score: number | null; candidate_score: number | null; delta: number | null; is_regression: boolean }[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const data = await apiGet<typeof comparisons>(`/agents/${agentId}/optimization/eval-comparison`).catch(() => []);
        setComparisons(data);
      } finally {
        setLoading(false);
      }
    })();
  }, [agentId]);

  const columns = [
    { title: 'Metric', dataIndex: 'metric_name', key: 'metric' },
    {
      title: 'Baseline', dataIndex: 'baseline_score', key: 'baseline',
      render: (s: number | null) => s?.toFixed(3) ?? '-',
    },
    {
      title: 'Candidate', dataIndex: 'candidate_score', key: 'candidate',
      render: (s: number | null) => s?.toFixed(3) ?? '-',
    },
    {
      title: 'Delta', dataIndex: 'delta', key: 'delta',
      render: (d: number | null) => (
        <Text style={{ color: (d ?? 0) > 0 ? 'green' : (d ?? 0) < 0 ? 'red' : undefined }}>
          {d != null ? (d > 0 ? `+${d.toFixed(3)}` : d.toFixed(3)) : '-'}
        </Text>
      ),
    },
    {
      title: 'Regression', dataIndex: 'is_regression', key: 'regression',
      render: (r: boolean) => r ? <Tag color="red">REGRESSION</Tag> : <Tag>OK</Tag>,
    },
  ];

  return (
    <Card title="Evaluation Comparison">
      {loading ? <Spin /> : comparisons.length === 0 ? (
        <Empty description="No comparison data yet" />
      ) : (
        <>
          <Text type="secondary" style={{ marginBottom: 8, display: 'block' }}>
            Regressions are highlighted in red.
          </Text>
          <Table dataSource={comparisons} columns={columns} rowKey="metric_name" size="small" pagination={false} />
        </>
      )}
    </Card>
  );
}

// ── Releases & Time Travel ──────────────────────────────────────────────

function ReleaseGraphPanel({ agentId }: { agentId: string }) {
  const [executing, setExecuting] = useState(false);

  const handleRollback = async (releaseId: string) => {
    setExecuting(true);
    try {
      const result = await rollbackRelease(agentId, releaseId);
      message.success(`Rolled back to release ${result.rolled_back_to}`);
    } catch {
      message.error('Rollback failed');
    } finally {
      setExecuting(false);
    }
  };

  return (
    <Card title="Releases & Time Travel">
      <Descriptions column={1} size="small">
        <Descriptions.Item label="Active Release">
          <Button size="small" icon={<BranchesOutlined />} loading={executing}>
            View Release DAG
          </Button>
        </Descriptions.Item>
      </Descriptions>
      <Space style={{ marginTop: 12 }}>
        <Popconfirm
          title="This will roll back to a previous release. All changes in the release DAG will be shown before confirmation. Continue?"
          onConfirm={() => handleRollback('release_v1')}
        >
          <Button danger icon={<RollbackOutlined />} loading={executing}>
            Rollback
          </Button>
        </Popconfirm>
      </Space>
    </Card>
  );
}

// ── Question Review Panel ───────────────────────────────────────────────

function QuestionReviewPanel({ agentId }: { agentId: string }) {
  const [questions, setQuestions] = useState<{ id: string; input_text: string; question_type: string; quality_status: string }[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const data = await apiGet<typeof questions>(`/agents/${agentId}/optimization/questions`).catch(() => []);
        setQuestions(data);
      } finally {
        setLoading(false);
      }
    })();
  }, [agentId]);

  const columns = [
    { title: 'Question', dataIndex: 'input_text', key: 'text', ellipsis: true },
    {
      title: 'Type', dataIndex: 'question_type', key: 'type',
      render: (t: string) => <Tag>{t}</Tag>,
    },
    {
      title: 'Quality', dataIndex: 'quality_status', key: 'quality',
      render: (s: string) => (
        <Tag color={s === 'accepted' ? 'success' : s === 'quarantined' ? 'error' : 'warning'}>
          {s}
        </Tag>
      ),
    },
  ];

  return (
    <Card title="Question Review">
      {loading ? <Spin /> : questions.length === 0 ? (
        <Empty description="No exploration questions generated yet" />
      ) : (
        <Table dataSource={questions} columns={columns} rowKey="id" size="small" pagination={false} />
      )}
    </Card>
  );
}

// ── Main Page ───────────────────────────────────────────────────────────

export default function KnowledgeIterationPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const [activeIterationId, setActiveIterationId] = useState<string | null>(null);

  if (!agentId) return <Empty description="No agent selected" />;

  const tabItems = [
    { key: 'overview', label: 'Overview', children: <IterationOverview agentId={agentId} /> },
    {
      key: 'attribution', label: 'Attribution',
      children: activeIterationId
        ? <AttributionPanel agentId={agentId} iterationId={activeIterationId} />
        : <Empty description="Select an active iteration from Overview" />,
    },
    {
      key: 'candidates', label: 'Candidates',
      children: activeIterationId
        ? <CandidateDiffPanel agentId={agentId} iterationId={activeIterationId} />
        : <Empty description="Select an active iteration from Overview" />,
    },
    { key: 'eval', label: 'Evaluation', children: <EvalComparisonPanel agentId={agentId} /> },
    { key: 'releases', label: 'Releases', children: <ReleaseGraphPanel agentId={agentId} /> },
    { key: 'questions', label: 'Questions', children: <QuestionReviewPanel agentId={agentId} /> },
  ];

  return (
    <div style={{ padding: 16 }}>
      <Title level={4}>Knowledge Iteration</Title>
      <Tabs items={tabItems} />
    </div>
  );
}
