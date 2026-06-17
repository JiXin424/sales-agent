/** Agent Setup / Readiness page — checklist + activate gate.

Activate is blocked with actionable reasons until readiness passes or a waiver
is recorded.
*/

import { Card, List, Tag, Button, Alert, message, Modal, Input, Typography, Space } from 'antd';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';
import { useState } from 'react';
import { getReadiness, activateAgent, getAgent } from '@/api/agents';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

const { TextArea } = Input;

export default function AgentSetupPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [waiverModal, setWaiverModal] = useState(false);
  const [waiverReason, setWaiverReason] = useState('');

  const { data: agent } = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => getAgent(agentId!),
    enabled: !!agentId,
  });

  const { data: readiness, isLoading, isError } = useQuery({
    queryKey: ['agent-readiness', agentId],
    queryFn: () => getReadiness(agentId!),
    enabled: !!agentId,
  });

  const activateMutation = useMutation({
    mutationFn: (waivers?: Record<string, string>) => activateAgent(agentId!, waivers),
    onSuccess: () => {
      message.success('Agent 已激活');
      queryClient.invalidateQueries();
    },
    onError: (e: unknown) => {
      const err = e as { detail?: { blockers?: string[]; message?: string } };
      const blockers = err?.detail?.blockers;
      message.error(
        blockers?.length ? `无法激活：${blockers.join('；')}` : `激活失败：${(e as Error).message}`,
      );
    },
  });

  if (isLoading) return <LoadingState />;
  if (isError || !readiness) return <ErrorState />;

  const canActivate = readiness.ready && agent?.status === 'draft';

  return (
    <div>
      <PageHeader
        title="就绪与激活"
        description="完成必填检查后方可激活；缺项可申请豁免。"
        actions={
          <Space>
            <Button onClick={() => setWaiverModal(true)}>申请豁免</Button>
            <Button
              type="primary"
              disabled={!canActivate && readiness.blockers.length > 0}
              onClick={() => activateMutation.mutate(undefined)}
              loading={activateMutation.isPending}
            >
              激活 Agent
            </Button>
          </Space>
        }
      />

      {readiness.blockers.length > 0 && (
        <Alert
          type="warning" showIcon style={{ marginBottom: 16 }}
          message="存在阻塞项，暂不可激活"
          description={readiness.blockers.map((b, i) => <div key={i}>• {b}</div>)}
        />
      )}
      {readiness.ready && agent?.status === 'draft' && (
        <Alert type="success" showIcon style={{ marginBottom: 16 }} message="所有必填检查通过，可以激活。" />
      )}

      <Card>
        <List
          dataSource={readiness.checks}
          renderItem={(c) => (
            <List.Item>
              <List.Item.Meta
                title={<span>{c.label}{c.required ? <Tag color="red" style={{ marginLeft: 8 }}>必填</Tag> : <Tag>可选</Tag>}</span>}
                description={c.reason || (c.passed ? '通过' : '未通过')}
              />
              <Tag color={c.passed ? 'green' : 'default'}>{c.passed ? '✓ 通过' : '✗ 未通过'}</Tag>
            </List.Item>
          )}
        />
      </Card>

      <Modal
        title="申请激活豁免"
        open={waiverModal}
        onCancel={() => setWaiverModal(false)}
        onOk={() => {
          if (waiverReason.trim()) {
            activateMutation.mutate({ eval: waiverReason.trim() });
            setWaiverModal(false);
            setWaiverReason('');
          }
        }}
      >
        <Typography.Paragraph type="secondary">
          为 Eval 检查申请豁免理由（将记录到 Agent 配置，用于审计）。
        </Typography.Paragraph>
        <TextArea
          rows={3}
          value={waiverReason}
          onChange={(e) => setWaiverReason(e.target.value)}
          placeholder="例如：已人工确认通过，等待正式 Eval 运行"
        />
      </Modal>
    </div>
  );
}
