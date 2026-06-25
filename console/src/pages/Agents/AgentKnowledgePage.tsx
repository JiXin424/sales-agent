/** Agent-scoped ontology knowledge ingestion page — upload + SSE progress. */
import { useState, useEffect, useCallback, useRef } from 'react';
import { Upload, Steps, Tag, Button, Alert, Typography, Space } from 'antd';
import { InboxOutlined } from '@ant-design/icons';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { message } from 'antd';
import { getOntologyStatus, startOntologyIngest, subscribeJobEvents, listOntologyJobs } from '@/api/knowledge';
import type { IngestStartResponse, JobProgressEvent, OntologyJob } from '@/api/types';
import PageHeader from '@/components/PageHeader';
import LoadingState from '@/components/LoadingState';

const { Dragger } = Upload;
const { Text } = Typography;

const STAGES = ['上传', '解析', '抽实体', '抽事实', '写图谱', '完成'];
const STAGE_IDX: Record<string, number> = {
  uploaded: 0, parsed: 1, extracting_entities: 2,
  extracting_facts: 3, writing_neo4j: 4, completed: 5,
};
const DONE_STATES = new Set(['completed', 'completed_with_errors', 'failed']);

interface FileJob {
  filename: string;
  jobId: string;
  stage: string;
  status: string;
  stats?: Record<string, number>;
  errorSummary?: string;
}

export default function AgentKnowledgePage() {
  const { agentId } = useParams<{ agentId: string }>();

  const statusQuery = useQuery({
    queryKey: ['ontology-status', agentId],
    queryFn: () => getOntologyStatus(agentId!),
    enabled: !!agentId,
  });
  const engineReady = statusQuery.data?.knowledge_engine === 'ontology_neo4j' && statusQuery.data?.neo4j_ready;
  const visualUrl = statusQuery.data?.visual_url || '';

  const [fileJobs, setFileJobs] = useState<FileJob[]>([]);
  const [uploading, setUploading] = useState(false);
  const eventSourcesRef = useRef<Map<string, EventSource>>(new Map());

  // (re)subscribe SSE for one job; idempotent（已订阅则跳过）
  const subscribeJob = useCallback((jobId: string) => {
    if (!agentId || eventSourcesRef.current.has(jobId)) return;
    const es = subscribeJobEvents(agentId, jobId);
    eventSourcesRef.current.set(jobId, es);
    es.onmessage = (evt) => {
      try {
        const data: JobProgressEvent = JSON.parse(evt.data);
        setFileJobs(prev => prev.map(fj =>
          fj.jobId === jobId
            ? { ...fj, stage: data.stage, status: data.status, stats: data.stats || fj.stats, errorSummary: data.error_summary }
            : fj
        ));
        if (DONE_STATES.has(data.status)) { es.close(); eventSourcesRef.current.delete(jobId); }
      } catch { /* ignore */ }
    };
    es.onerror = () => { es.close(); eventSourcesRef.current.delete(jobId); };
  }, [agentId]);

  // 进页面 / 切 tab 回来：从后端拉最近 job 重建列表（state 不再丢失），仍在 running 的重新订阅
  useEffect(() => {
    if (!agentId) return;
    let cancelled = false;
    (async () => {
      try {
        const resp = await listOntologyJobs(agentId, 20, 0);
        if (cancelled) return;
        const jobs: FileJob[] = resp.items.map((j: OntologyJob) => ({
          filename: (j.metadata as Record<string, unknown>)?.filename as string || j.id,
          jobId: j.id,
          stage: j.stage || 'uploaded',
          status: j.status,
          stats: {
            entities_created: j.entities_created,
            facts_created: j.facts_created,
            facts_pending_review: j.facts_pending_review,
            conflicts_created: j.conflicts_created,
          },
          errorSummary: j.error_summary || undefined,
        }));
        setFileJobs(jobs);
        jobs.filter(j => !DONE_STATES.has(j.status)).forEach(j => subscribeJob(j.jobId));
      } catch { /* 引擎未就绪等，忽略 */ }
    })();
    return () => { cancelled = true; };
  }, [agentId, subscribeJob]);

  // process raw files from Upload component
  const handleFiles = useCallback(async (rawFiles: File[]) => {
    if (!agentId || !engineReady) return;
    setUploading(true);
    try {
      const result: IngestStartResponse[] = await startOntologyIngest(agentId, rawFiles);
      const newJobs: FileJob[] = result.map(r => ({
        filename: r.filename, jobId: r.job_id, stage: 'uploaded', status: 'running',
      }));
      setFileJobs(prev => [...prev, ...newJobs]);
      newJobs.forEach(job => subscribeJob(job.jobId));
    } catch (e: any) {
      message.error(`上传失败：${e?.message || e}`);
    } finally {
      setUploading(false);
    }
  }, [agentId, engineReady, subscribeJob]);

  // all done?
  useEffect(() => {
    if (fileJobs.length > 0 && fileJobs.every(fj => DONE_STATES.has(fj.status))) {
      const ok = fileJobs.filter(fj => fj.status === 'completed').length;
      const ents = fileJobs.reduce((s, fj) => s + (fj.stats?.entities_created || 0), 0);
      const fcts = fileJobs.reduce((s, fj) => s + (fj.stats?.facts_created || 0), 0);
      message.success(`${ok}/${fileJobs.length} 个文件入库完成 · ${ents} 实体 / ${fcts} 事实`);
    }
  }, [fileJobs]);

  // cleanup
  useEffect(() => () => { eventSourcesRef.current.forEach(es => es.close()); }, []);

  if (statusQuery.isLoading) return <LoadingState />;

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: 24 }}>
      <PageHeader title="本体知识入库" />

      {!engineReady && (
        <Alert type="warning" showIcon message="本体引擎未就绪"
          description="请先配置 KNOWLEDGE_ENGINE=ontology_neo4j 并确保 Neo4j 可连接。"
          style={{ marginBottom: 16 }} />
      )}
      <Space style={{ marginBottom: 16 }}>
        <Tag color={engineReady ? 'green' : 'orange'}>{statusQuery.data?.ontology_status || 'unknown'}</Tag>
        {visualUrl && <Button size="small" href={visualUrl} target="_blank" rel="noreferrer">Neo4j Browser ↗</Button>}
      </Space>

      <Dragger
        accept=".md,.txt,.docx,.pdf,.pptx" multiple disabled={!engineReady || uploading}
        showUploadList={false}
        beforeUpload={(file, fileList) => {
          // 等所有文件收集完后一起处理
          if (fileList.indexOf(file) === fileList.length - 1 && fileList.length > 0) {
            handleFiles(fileList as unknown as File[]);
          }
          return false;
        }}
      >
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p className="ant-upload-text">拖拽文件到此处，或点击选择</p>
        <p className="ant-upload-hint">支持 .md / .txt / .docx / .pdf / .pptx，可多选（每文件独立入库）</p>
      </Dragger>

      {fileJobs.length > 0 && (
        <div style={{ marginTop: 20 }}>
          {fileJobs.map(fj => {
            const isDone = fj.status === 'completed';
            const isFailed = fj.status === 'failed';
            const stepIdx = STAGE_IDX[fj.stage] ?? 0;
            return (
              <div key={fj.jobId} style={{
                border: `1px solid ${isFailed ? '#ff4d4f' : isDone ? '#b7eb8f' : '#d9d9d9'}`,
                borderRadius: 8, padding: '12px 16px', marginBottom: 10,
                background: isFailed ? '#fff2f0' : isDone ? '#f6ffed' : '#fafafa',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Text strong delete={isFailed}>{fj.filename}</Text>
                  <Space size={8}>
                    {isFailed && <Tag color="red">失败</Tag>}
                    {isDone && <Tag color="green">完成</Tag>}
                    {!isFailed && !isDone && <Tag color="processing">入库中</Tag>}
                  </Space>
                </div>

                {!isDone && !isFailed && (
                  <Steps size="small" current={stepIdx} style={{ marginTop: 8 }}
                    items={STAGES.map((s, i) => ({ title: i === stepIdx ? s : '' }))} />
                )}

                {isDone && fj.stats && (
                  <div style={{ marginTop: 6 }}>
                    <Text>{fj.stats.entities_created || 0} 实体 · {fj.stats.facts_created || 0} 事实
                      {fj.stats.facts_pending_review ? ` · ${fj.stats.facts_pending_review} 待复核` : ''}
                      {fj.stats.conflicts_created ? ` · ${fj.stats.conflicts_created} 冲突` : ''}
                    </Text>
                    {visualUrl && <Button type="link" size="small" href={visualUrl} target="_blank" style={{ marginLeft: 8 }}>查看图谱 →</Button>}
                  </div>
                )}

                {isFailed && (
                  <div style={{ marginTop: 6 }}>
                    <Text type="danger">{fj.errorSummary || '入库过程出错'}</Text>
                    <Button size="small" onClick={() => message.info('请重新选择该文件上传')} style={{ marginLeft: 8 }}>重试</Button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
