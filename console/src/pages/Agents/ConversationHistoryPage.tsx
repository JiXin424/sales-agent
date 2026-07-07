/** Conversation History Page — read-only checkpoint time-travel for REAL
 *  production conversations (DingTalk stream).
 *
 * Left: recent conversation list (from `conversations` table) + manual
 *   conversation_id input (fallback).
 * Right: CheckpointDAG timeline of the selected conversation + per-step state
 *   (JsonNode), both reused from GraphDebugPage.
 *
 * READ-ONLY: no edit / fork / replay. Production execution is untouched.
 */

import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Col,
  Empty,
  Input,
  Row,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { TableColumnsType } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import {
  listConversationHistory,
  getConversationCheckpoints,
  getConversationCheckpointState,
  type ConversationHistoryItem,
} from '@/api/conversationHistory';
import type { CheckpointMeta } from '@/api/types';
import CheckpointDAG from './CheckpointDAG';
import JsonNode from './JsonNode';
// Reuse GraphDebugPage.css so CheckpointDAG (gd-dag-wrap) + JsonNode (json-node)
// render with their intended styles.
import './GraphDebugPage.css';

const PAGE_SIZE = 50;

/** Truncate a long id/message for a table cell, full text in the title tooltip. */
function truncate(s: string | null, n = 28): string {
  if (!s) return '-';
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

function timeOf(iso: string | null): string {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString('zh-CN', { hour12: false });
  } catch {
    return iso;
  }
}

export default function ConversationHistoryPage() {
  const { agentId } = useParams<{ agentId: string }>();

  const [manualConvId, setManualConvId] = useState('');
  const [selectedConvId, setSelectedConvId] = useState<string | null>(null);
  const [selectedCp, setSelectedCp] = useState<CheckpointMeta | null>(null);

  // ── Conversation list ──
  const convs = useQuery({
    queryKey: ['conv-history', agentId, PAGE_SIZE, 0],
    queryFn: () => listConversationHistory(agentId!, PAGE_SIZE, 0),
    enabled: !!agentId,
  });

  // ── Checkpoint timeline for the selected conversation ──
  const checkpoints = useQuery({
    queryKey: ['conv-checkpoints', agentId, selectedConvId],
    queryFn: () => getConversationCheckpoints(agentId!, selectedConvId!),
    enabled: !!agentId && !!selectedConvId,
  });

  // ── State values for the selected checkpoint ──
  const cpState = useQuery({
    queryKey: ['conv-checkpoint-state', agentId, selectedConvId, selectedCp?.checkpoint_id],
    queryFn: () =>
      getConversationCheckpointState(agentId!, selectedConvId!, selectedCp!.checkpoint_id),
    enabled: !!agentId && !!selectedConvId && !!selectedCp?.checkpoint_id,
  });

  const columns: TableColumnsType<ConversationHistoryItem> = [
    {
      title: '会话',
      dataIndex: 'conversation_id',
      key: 'conversation_id',
      width: 200,
      render: (id: string) => (
        <Typography.Text code style={{ fontSize: 11 }} title={id}>
          {truncate(id, 22)}
        </Typography.Text>
      ),
    },
    {
      title: '最新消息',
      dataIndex: 'message',
      key: 'message',
      ellipsis: true,
      render: (m: string) => <Typography.Text type="secondary">{m || '-'}</Typography.Text>,
    },
    {
      title: '渠道',
      dataIndex: 'channel',
      key: 'channel',
      width: 90,
      render: (c: string) => (c ? <Tag>{c}</Tag> : '-'),
    },
    {
      title: '类型',
      dataIndex: 'task_type',
      key: 'task_type',
      width: 100,
      render: (t: string) => (t ? <Tag color="blue">{t}</Tag> : '-'),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (s: string) => (s ? <Tag color={s === 'completed' ? 'green' : 'default'}>{s}</Tag> : '-'),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 160,
      render: (iso: string) => <span style={{ fontSize: 11, color: '#8c8c8c' }}>{timeOf(iso)}</span>,
    },
  ];

  return (
    <div>
      <style>{`.conv-history-row-selected > td { background: #e6f4ff !important; }`}</style>
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 12 }}
        message="只读回看：查看历史会话的 checkpoint 快照"
        description="仅查看，不会 fork / replay / 改写状态，不影响生产。数据来自钉钉流式路径写入的 Postgres checkpoint。state 含完整对话与检索内容，属敏感数据。"
      />

      <Row gutter={16} style={{ height: 'calc(100vh - 220px)', minHeight: 480 }}>
        {/* Left: conversation picker */}
        <Col flex="0 0 46%">
          <Typography.Title level={5} style={{ marginTop: 0 }}>选择会话</Typography.Title>

          {/* Manual conversation_id fallback */}
          <div style={{ marginBottom: 12, display: 'flex', gap: 8 }}>
            <Input
              style={{ flex: 1 }}
              placeholder="或手动输入 conversation_id（兜底）"
              value={manualConvId}
              onChange={e => setManualConvId(e.target.value)}
              onPressEnter={() => manualConvId.trim() && setSelectedConvId(manualConvId.trim())}
              allowClear
            />
            <Button
              type="primary"
              icon={<SearchOutlined />}
              disabled={!manualConvId.trim()}
              onClick={() => {
                setSelectedCp(null);
                setSelectedConvId(manualConvId.trim());
              }}
            >
              查看
            </Button>
          </div>

          <Spin spinning={convs.isLoading}>
            {convs.data && convs.data.conversations.length > 0 ? (
              <Table<ConversationHistoryItem>
                size="small"
                rowKey="conversation_id"
                columns={columns}
                dataSource={convs.data.conversations}
                pagination={{ pageSize: 10, size: 'small', showSizeChanger: false }}
                onRow={(row) => ({
                  onClick: () => {
                    setSelectedCp(null);
                    setSelectedConvId(row.conversation_id);
                    setManualConvId(row.conversation_id);
                  },
                  style: { cursor: 'pointer' },
                })}
                rowClassName={(row) =>
                  row.conversation_id === selectedConvId ? 'conv-history-row-selected' : ''
                }
              />
            ) : (
              <Empty description={convs.isLoading ? '加载中…' : '该 Agent 暂无会话记录'} />
            )}
          </Spin>
          {convs.isError ? (
            <Typography.Text type="danger" style={{ fontSize: 12 }}>
              加载会话列表失败
            </Typography.Text>
          ) : null}
        </Col>

        {/* Right: timeline + state viewer */}
        <Col flex="1 1 54%">
          {!selectedConvId ? (
            <Empty description="从左侧选择一个会话，或手动输入 conversation_id" style={{ marginTop: 80 }} />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
              <Typography.Title level={5} style={{ marginTop: 0 }}>
                Checkpoint 时间轴
              </Typography.Title>
              <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: -4 }}>
                thread_id = <Typography.Text code>{selectedConvId}</Typography.Text> · 粒度：Online Graph 节点边界
              </Typography.Paragraph>

              <Spin spinning={checkpoints.isLoading}>
                {checkpoints.data && checkpoints.data.checkpoints.length > 0 ? (
                  <div style={{ height: 280, border: '1px solid #f0f0f0', borderRadius: 6, marginBottom: 12 }}>
                    <CheckpointDAG
                      checkpoints={checkpoints.data.checkpoints}
                      selectedCheckpointId={selectedCp?.checkpoint_id ?? null}
                      onSelect={(meta) => setSelectedCp(meta)}
                    />
                  </div>
                ) : (
                  <Empty description={checkpoints.isLoading ? '加载中…' : '该会话无 checkpoint（可能非钉钉路径或已被清理）'} />
                )}
              </Spin>
              {checkpoints.isError ? (
                <Typography.Text type="danger" style={{ fontSize: 12 }}>
                  加载 checkpoint 失败：{(checkpoints.error as Error)?.message}
                </Typography.Text>
              ) : null}

              {/* Selected checkpoint state */}
              {selectedCp ? (
                <div style={{ flex: 1, minHeight: 200, overflow: 'auto' }}>
                  <Typography.Text strong>
                    step {selectedCp.step ?? '-'} · {selectedCp.node ?? '-'}
                  </Typography.Text>
                  {selectedCp.next && selectedCp.next.length > 0 ? (
                    <Tag color="blue" style={{ marginLeft: 8 }}>next: {selectedCp.next.join(',')}</Tag>
                  ) : null}
                  <div style={{ marginTop: 8, padding: 12, background: '#fafafa', borderRadius: 6, fontSize: 12 }}>
                    <Spin spinning={cpState.isLoading}>
                      {cpState.data ? (
                        <JsonNode value={cpState.data.values} defaultCollapsed={false} />
                      ) : cpState.isError ? (
                        <Typography.Text type="danger">
                          加载 state 失败：{(cpState.error as Error)?.message}
                        </Typography.Text>
                      ) : null}
                    </Spin>
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </Col>
      </Row>
    </div>
  );
}
