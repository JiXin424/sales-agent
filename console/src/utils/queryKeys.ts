/** TanStack Query key factory. */

export const queryKeys = {
  // Health
  health: ['health'] as const,
  ready: ['ready'] as const,
  diagnostics: ['diagnostics'] as const,

  // Tenant
  tenant: (id: string) => ['tenant', id] as const,

  // Admin
  conversations: (tid: string, filters?: Record<string, unknown>) => ['conversations', tid, filters] as const,
  conversation: (tid: string, id: string) => ['conversation', tid, id] as const,
  runDetail: (tid: string, runId: string) => ['runDetail', tid, runId] as const,
  runSteps: (tid: string, runId: string) => ['runSteps', tid, runId] as const,
  documents: (tid: string, filters?: Record<string, unknown>) => ['documents', tid, filters] as const,
  sourceFiles: (tid: string, filters?: Record<string, unknown>) => ['sourceFiles', tid, filters] as const,
  feedbackSummary: (tid: string) => ['feedbackSummary', tid] as const,
  latencyStats: (tid: string) => ['latencyStats', tid] as const,
  modelCalls: (tid: string, filters?: Record<string, unknown>) => ['modelCalls', tid, filters] as const,
  modelCallsSummary: (tid: string) => ['modelCallsSummary', tid] as const,
  workflowMetrics: (tid: string, dateRange?: Record<string, string>) => ['workflowMetrics', tid, dateRange] as const,

  // Prompts
  prompts: (tid: string, filters?: Record<string, unknown>) => ['prompts', tid, filters] as const,
  prompt: (tid: string, id: string) => ['prompt', tid, id] as const,

  // Knowledge
  ingestionJobs: (tid: string, filters?: Record<string, unknown>) => ['ingestionJobs', tid, filters] as const,
  ingestionJob: (tid: string, jobId: string) => ['ingestionJob', tid, jobId] as const,

  // Ontology Explorer（本体探索器）
  ontologyStatus: (agentId: string) => ['ontology-status', agentId] as const,
  ontologyStats: (agentId: string) => ['ontology-stats', agentId] as const,

  // Feedback
  feedback: (tid: string, filters?: Record<string, unknown>) => ['feedback', tid, filters] as const,
  feedbackDetail: (tid: string, id: string) => ['feedbackDetail', tid, id] as const,

  // Readiness
  readiness: (tid: string) => ['readiness', tid] as const,

  // Phase D: Pilot
  pilotMetrics: (tid: string, startDate: string, endDate: string) => ['pilotMetrics', tid, startDate, endDate] as const,
  reviewQueue: (tid: string, filters?: Record<string, unknown>) => ['reviewQueue', tid, filters] as const,
  reviewItem: (tid: string, id: string) => ['reviewItem', tid, id] as const,
  feedbackCategorySummary: (tid: string) => ['feedbackCategorySummary', tid] as const,
  knowledgeGaps: (tid: string, filters?: Record<string, unknown>) => ['knowledgeGaps', tid, filters] as const,
  knowledgeGap: (tid: string, id: string) => ['knowledgeGap', tid, id] as const,
  knowledgeGapsSummary: (tid: string) => ['knowledgeGapsSummary', tid] as const,
  evalSuites: (tid: string, filters?: Record<string, unknown>) => ['evalSuites', tid, filters] as const,
  evalRuns: (tid: string, filters?: Record<string, unknown>) => ['evalRuns', tid, filters] as const,
  evalRun: (tid: string, id: string) => ['evalRun', tid, id] as const,
  evalRunResults: (tid: string, runId: string) => ['evalRunResults', tid, runId] as const,
  alertRules: (tid: string, filters?: Record<string, unknown>) => ['alertRules', tid, filters] as const,
  alerts: (tid: string, filters?: Record<string, unknown>) => ['alerts', tid, filters] as const,
  pilotReports: (tid: string, filters?: Record<string, unknown>) => ['pilotReports', tid, filters] as const,
  pilotReport: (tid: string, id: string) => ['pilotReport', tid, id] as const,
  pilotStatus: (tid: string) => ['pilotStatus', tid] as const,
} as const;
