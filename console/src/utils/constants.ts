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

export const PROMPT_CATEGORY_LABELS: Record<string, string> = {
  task: '任务执行',
  system: '系统约束',
  router: '任务路由',
  risk: '风险检查',
  coach: '教练辅导',
};

// 非 task 类的 prompt_key（task 类用 TASK_TYPE_LABELS 的 12 项）
export const PROMPT_KEYS_BY_CATEGORY: Record<string, { key: string; label: string }[]> = {
  system: [{ key: 'system_constraint', label: '系统约束（Agent 人设）' }],
  router: [{ key: 'task_router', label: '任务路由分类器' }],
  risk: [{ key: 'risk_check', label: '风险合规检查' }],
  coach: [
    { key: 'coach_daily_eval', label: '每日能力评估' },
    { key: 'coach_daily_eval_system', label: '评估 system 消息' },
    { key: 'coach_sw_system', label: '小赢欣赏人设' },
    { key: 'coach_sw_card', label: '小赢卡模板' },
    { key: 'coach_sb_system', label: '卡点破框人设' },
    { key: 'coach_sb_split', label: '事实/解释拆分' },
    { key: 'coach_sb_card', label: '破框卡模板' },
  ],
};
