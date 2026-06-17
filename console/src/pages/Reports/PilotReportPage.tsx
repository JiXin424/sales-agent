import { useState } from 'react';
import { Table, Button, DatePicker, Modal, Typography, Tag, Space, message } from 'antd';
import { FileTextOutlined, DownloadOutlined } from '@ant-design/icons';
import { useTenant } from '@/context/TenantContext';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import * as pilotApi from '@/api/pilot';
import { queryKeys } from '@/utils/queryKeys';
import type { PilotReport } from '@/api/types';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';
import ErrorState from '@/components/ErrorState';

const { Paragraph, Title } = Typography;
const { RangePicker } = DatePicker;

export default function PilotReportPage() {
  const { tenantId } = useTenant();
  const qc = useQueryClient();
  const [selectedReport, setSelectedReport] = useState<PilotReport | null>(null);

  const reportsQuery = useQuery({
    queryKey: queryKeys.pilotReports(tenantId!),
    queryFn: () => pilotApi.listReports(tenantId!),
    enabled: !!tenantId,
  });

  const generateMutation = useMutation({
    mutationFn: (values: { start_date: string; end_date: string }) =>
      pilotApi.generateReport(tenantId!, values),
    onSuccess: () => {
      message.success('报告生成成功');
      qc.invalidateQueries({ queryKey: queryKeys.pilotReports(tenantId!) });
    },
    onError: () => message.error('报告生成失败'),
  });

  const handleGenerate = (dates: [string, string]) => {
    generateMutation.mutate({ start_date: dates[0], end_date: dates[1] });
  };

  if (reportsQuery.isLoading) {
    return (
      <>
        <PageHeader title="Pilot 报告" description="生成和查看 Pilot 阶段报告" />
        <LoadingState tip="加载报告列表..." />
      </>
    );
  }

  if (reportsQuery.isError) {
    return (
      <>
        <PageHeader title="Pilot 报告" description="生成和查看 Pilot 阶段报告" />
        <ErrorState title="加载失败" message="报告列表加载失败" onRetry={() => reportsQuery.refetch()} />
      </>
    );
  }

  const columns = [
    {
      title: '类型',
      dataIndex: 'report_type',
      key: 'report_type',
      render: (t: string) => <Tag>{t}</Tag>,
    },
    {
      title: '时间范围',
      key: 'range',
      render: (_: unknown, record: PilotReport) => `${record.start_date} ~ ${record.end_date}`,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (s: string) => <Tag color={s === 'completed' ? 'green' : s === 'failed' ? 'red' : 'blue'}>{s}</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (t: string) => t ? t.slice(0, 19).replace('T', ' ') : '-',
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: PilotReport) => (
        <Space>
          <Button size="small" onClick={() => setSelectedReport(record)}>查看</Button>
          {record.markdown_content && (
            <Button size="small" icon={<DownloadOutlined />}
              onClick={() => {
                const blob = new Blob([record.markdown_content!], { type: 'text/markdown' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `pilot-report-${record.start_date}-${record.end_date}.md`;
                a.click();
                URL.revokeObjectURL(url);
              }}>
              下载 MD
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <>
      <PageHeader title="Pilot 报告" description="生成和查看 Pilot 阶段报告" />

      {/* Generate Report */}
      <div style={{ marginBottom: 16 }}>
        <Space>
          <RangePicker
            onChange={(_, dateStrings) => {
              if (dateStrings[0] && dateStrings[1]) {
                handleGenerate([dateStrings[0], dateStrings[1]]);
              }
            }}
          />
          <Button type="primary" icon={<FileTextOutlined />} loading={generateMutation.isPending}>
            生成周报
          </Button>
        </Space>
      </div>

      <Table
        dataSource={reportsQuery.data?.items ?? []}
        columns={columns}
        rowKey="id"
        size="small"
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 条` }}
      />

      {/* Report Detail Modal */}
      <Modal
        title={`Pilot 报告 - ${selectedReport?.start_date ?? ''} ~ ${selectedReport?.end_date ?? ''}`}
        open={!!selectedReport}
        onCancel={() => setSelectedReport(null)}
        footer={null}
        width={800}
        style={{ maxHeight: '80vh' }}
      >
        {selectedReport?.markdown_content && (
          <div style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: 13, maxHeight: '60vh', overflow: 'auto' }}>
            {selectedReport.markdown_content}
          </div>
        )}
        {selectedReport?.status === 'failed' && (
          <Paragraph type="danger">报告生成失败</Paragraph>
        )}
      </Modal>
    </>
  );
}
