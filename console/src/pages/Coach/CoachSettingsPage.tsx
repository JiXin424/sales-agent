/** Coach settings — agent-level coach configuration. */

import { Button, Card, Form, InputNumber, Switch, message } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getCoachSettings, patchCoachSettings, type CoachSettings } from '@/api/coach';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

export default function CoachSettingsPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const qc = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: ['coach-settings', agentId],
    queryFn: () => getCoachSettings(agentId!),
    enabled: !!agentId,
  });

  const [form] = Form.useForm<CoachSettings>();

  const saveMutation = useMutation({
    mutationFn: (vals: Partial<CoachSettings>) => patchCoachSettings(agentId!, vals),
    onSuccess: () => {
      message.success('已保存');
      qc.invalidateQueries({ queryKey: ['coach-settings', agentId] });
    },
    onError: (e: Error) => message.error(`保存失败：${e.message}`),
  });

  if (isLoading) return <LoadingState />;
  if (isError) return <ErrorState />;

  return (
    <div>
      <PageHeader title="教练设置" description="实时教练、每日评估、奖励等开关与阈值" />
      <Card>
        <Form
          form={form}
          layout="vertical"
          initialValues={data}
          onFinish={(vals) => saveMutation.mutate(vals)}
        >
          <Form.Item name="daily_evaluation_enabled" label="启用每日评估" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="realtime_enabled" label="启用实时教练引导" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="minimum_user_messages" label="每日评估最少用户消息数">
            <InputNumber min={1} max={50} />
          </Form.Item>
          <Form.Item name="daily_realtime_guidance_limit" label="每日实时引导上限">
            <InputNumber min={0} max={50} />
          </Form.Item>
          <Form.Item name="daily_reward_notification_limit" label="每日奖励通知上限">
            <InputNumber min={0} max={50} />
          </Form.Item>
          <Form.Item name="initial_score" label="初始分数">
            <InputNumber min={0} max={100} />
          </Form.Item>
          <Form.Item name="evidence_quote_max_chars" label="证据片段最大字符数">
            <InputNumber min={20} max={1000} />
          </Form.Item>
          <Form.Item name="voice_rewards_enabled" label="启用语音奖励（建议）" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="red_packet_reminders_enabled" label="启用红包提醒（建议）" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="allow_negative_delta" label="允许负向 delta" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={saveMutation.isPending}>
            保存
          </Button>
        </Form>
      </Card>
    </div>
  );
}
