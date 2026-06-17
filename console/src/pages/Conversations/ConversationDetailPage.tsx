import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Descriptions, Card, Collapse, Timeline, Tag, Button, Space } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useTenant } from '@/context/TenantContext';
import { getConversationDetail } from '@/api/admin';
import { queryKeys } from '@/utils/queryKeys';
import { TASK_TYPE_LABELS } from '@/utils/constants';
import { formatDate } from '@/utils/formatters';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';
import StatusTag from '@/components/StatusTag';

export default function ConversationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { tenantId } = useTenant();
  const navigate = useNavigate();

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: queryKeys.conversation(tenantId!, id!),
    queryFn: () => getConversationDetail(tenantId!, id!),
    enabled: !!tenantId && !!id,
  });

  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState message={(error as Error).message} onRetry={() => refetch()} />;
  if (!data) return <ErrorState message="对话不存在" />;

  const answer = data.answer as { summary?: string; sections?: { title: string; content: string }[] } | null;
  const risk = data.risk as { level?: string; flags?: string[]; action?: string; notice?: string } | null;
  const sources = (data.sources || []) as { title: string; section_title?: string; score?: number }[];

  return (
    <>
      <PageHeader
        title="对话详情"
        actions={
          <Space>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/conversations')}>返回列表</Button>
          </Space>
        }
      />
      <Card size="small" style={{ marginBottom: 16 }}>
        <Descriptions column={3} size="small">
          <Descriptions.Item label="ID">{data.id}</Descriptions.Item>
          <Descriptions.Item label="用户">{data.user_id}</Descriptions.Item>
          <Descriptions.Item label="渠道">{data.channel}</Descriptions.Item>
          <Descriptions.Item label="任务类型">{TASK_TYPE_LABELS[data.task_type || ''] || data.task_type || '-'}</Descriptions.Item>
          <Descriptions.Item label="置信度">{data.task_confidence?.toFixed(2) || '-'}</Descriptions.Item>
          <Descriptions.Item label="状态"><StatusTag status={data.status} /></Descriptions.Item>
          <Descriptions.Item label="创建时间">{formatDate(data.created_at)}</Descriptions.Item>
        </Descriptions>
      </Card>

      {/* User message */}
      <Card title="用户消息" size="small" style={{ marginBottom: 16 }}>
        <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{data.message}</pre>
      </Card>

      {/* Answer */}
      {answer && (
        <Card title="回答" size="small" style={{ marginBottom: 16 }}>
          {answer.summary && <p><strong>摘要：</strong>{answer.summary}</p>}
          {answer.sections && (
            <Collapse
              items={answer.sections.map((s, i) => ({
                key: String(i),
                label: s.title,
                children: <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{s.content}</pre>,
              }))}
            />
          )}
        </Card>
      )}

      {/* Sources */}
      {sources.length > 0 && (
        <Card title="引用来源" size="small" style={{ marginBottom: 16 }}>
          <table style={{ width: '100%', fontSize: 13 }}>
            <thead><tr><th>标题</th><th>章节</th><th>相关度</th></tr></thead>
            <tbody>
              {sources.map((s, i) => (
                <tr key={i}><td>{s.title}</td><td>{s.section_title || '-'}</td><td>{s.score?.toFixed(2) || '-'}</td></tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {/* Risk */}
      {risk && risk.level !== 'none' && (
        <Card title="风险结果" size="small" style={{ marginBottom: 16 }}>
          <Descriptions column={2} size="small">
            <Descriptions.Item label="级别"><Tag color={risk.level === 'high' ? 'red' : risk.level === 'medium' ? 'orange' : 'blue'}>{risk.level}</Tag></Descriptions.Item>
            <Descriptions.Item label="动作">{risk.action}</Descriptions.Item>
            <Descriptions.Item label="标记" span={2}>{(risk.flags || []).join(', ') || '-'}</Descriptions.Item>
            {risk.notice && <Descriptions.Item label="提示" span={2}>{risk.notice}</Descriptions.Item>}
          </Descriptions>
        </Card>
      )}

      {/* Messages */}
      {data.messages && data.messages.length > 0 && (
        <Card title="消息历史" size="small">
          <Timeline
            items={data.messages.map((m) => ({
              color: m.role === 'user' ? 'blue' : 'green',
              children: (
                <div>
                  <Tag>{m.role}</Tag>
                  <span style={{ color: '#999', fontSize: 12 }}>{formatDate(m.created_at)}</span>
                  <pre style={{ whiteSpace: 'pre-wrap', margin: '4px 0 0', fontSize: 13 }}>{m.content?.slice(0, 500)}</pre>
                </div>
              ),
            }))}
          />
        </Card>
      )}
    </>
  );
}
