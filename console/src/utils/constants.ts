/** Constants: task type labels, stage labels, status colors. */

export const TASK_TYPE_LABELS: Record<string, string> = {
  emotional_support: '情绪支持',
  knowledge_qa: '知识问答',
  script_generation: '话术生成',
  objection_handling: '异议处理',
  conversation_review: '对话复盘',
  general_sales_coaching: '通用辅导',
  visit_preparation: '访前准备 / 访前作战卡',
  post_visit_review: '访后复盘 / 访后机会推进卡',
  follow_up_planning: '跟进计划',
  customer_context_summary: '客户整理',
  deal_advancement: '成交推进',
  conversation_scoring: '对话评分',
};

export const STAGE_LABELS: Record<string, string> = {
  lead_discovery: '线索发现',
  first_contact: '首次触达',
  needs_discovery: '需求挖掘',
  visit_preparation: '拜访准备',
  proposal: '方案报价',
  objection: '异议处理',
  follow_up: '跟进维护',
  deal_closing: '成交推进',
  post_mortem: '复盘总结',
};

export const STATUS_COLORS: Record<string, string> = {
  active: 'green',
  draft: 'blue',
  archived: 'default',
  completed: 'green',
  failed: 'red',
  running: 'processing',
  queued: 'orange',
  processing: 'processing',
  uploaded: 'blue',
  ingested: 'green',
  pending: 'orange',
  success: 'green',
  error: 'red',
};

export const RISK_LEVEL_COLORS: Record<string, string> = {
  none: 'default',
  low: 'blue',
  medium: 'orange',
  high: 'red',
};

export const REVIEW_STATUS_OPTIONS = [
  { value: 'open', label: '待处理', color: 'orange' },
  { value: 'reviewed', label: '已处理', color: 'green' },
  { value: 'ignored', label: '忽略', color: 'default' },
] as const;
