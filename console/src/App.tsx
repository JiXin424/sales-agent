import { Routes, Route, Navigate, useParams } from 'react-router-dom';
import AppLayout from './layout/AppLayout';
import AgentLayout from './layout/AgentLayout';
import DashboardPage from './pages/Dashboard/DashboardPage';
import ConversationListPage from './pages/Conversations/ConversationListPage';
import ConversationDetailPage from './pages/Conversations/ConversationDetailPage';
import TracePage from './pages/Traces/TracePage';
import KnowledgePage from './pages/Knowledge/KnowledgePage';
import PromptListPage from './pages/Prompts/PromptListPage';
import PromptEditPage from './pages/Prompts/PromptEditPage';
import WorkflowPage from './pages/Workflow/WorkflowPage';
import FeedbackPage from './pages/Feedback/FeedbackPage';
import FeedbackDetailPage from './pages/Feedback/FeedbackDetailPage';
import ReadinessPage from './pages/Readiness/ReadinessPage';
import PilotDashboardPage from './pages/Pilot/PilotDashboardPage';
import ReviewQueuePage from './pages/Review/ReviewQueuePage';
import KnowledgeGapsPage from './pages/Gaps/KnowledgeGapsPage';
import EvalRunsPage from './pages/Eval/EvalRunsPage';
import AlertsPage from './pages/Alerts/AlertsPage';
import PilotReportPage from './pages/Reports/PilotReportPage';
import AgentOverviewPage from './pages/Agents/AgentOverviewPage';
import AgentSetupPage from './pages/Agents/AgentSetupPage';
import AgentSettingsPage from './pages/Agents/AgentSettingsPage';
import AgentCloneWizardPage from './pages/Agents/AgentCloneWizardPage';
import AgentConversationsPage from './pages/Agents/AgentConversationsPage';
import AgentKnowledgePage from './pages/Agents/AgentKnowledgePage';
import OntologyExplorerPage from './pages/Agents/OntologyExplorerPage';
import AgentFeedbackPage from './pages/Agents/AgentFeedbackPage';
import CoachDashboardPage from './pages/Coach/CoachDashboardPage';
import CoachUsersPage from './pages/Coach/CoachUsersPage';
import CoachUserProfilePage from './pages/Coach/CoachUserProfilePage';
import CoachEvaluationsPage from './pages/Coach/CoachEvaluationsPage';
import CoachRewardsPage from './pages/Coach/CoachRewardsPage';
import CoachSettingsPage from './pages/Coach/CoachSettingsPage';
import AgentPromptsPage from './pages/Agents/AgentPromptsPage';
import AgentChannelsPage from './pages/Agents/AgentChannelsPage';
import AgentAlertsPage from './pages/Agents/AgentAlertsPage';
import AgentReportsPage from './pages/Agents/AgentReportsPage';
import AgentEvalRunsPage from './pages/Agents/AgentEvalRunsPage';
import AgentReviewQueuePage from './pages/Agents/AgentReviewQueuePage';
import GraphDebugPage from './pages/Agents/GraphDebugPage';
import ConversationHistoryPage from './pages/Agents/ConversationHistoryPage';
import KnowledgeIterationPage from './pages/Agents/KnowledgeIterationPage';
import { useInstanceAgent } from './hooks/useInstanceAgent';

/** 根路径 boot：解析当前实例的唯一 Agent，重定向到其指定子页（单 Agent 模式）。 */
function InstanceEntry({ to = 'overview' }: { to?: string }) {
  const { data, isLoading, isError } = useInstanceAgent();
  if (isLoading) return <div style={{ padding: 48, textAlign: 'center' }}>加载实例 Agent…</div>;
  if (isError || !data) {
    return (
      <div style={{ padding: 48, textAlign: 'center', color: '#888' }}>
        当前实例尚未配置 Agent。请在后端为该租户创建默认 Agent 后刷新。
      </div>
    );
  }
  return <Navigate to={`/agents/${data.id}/${to}`} replace />;
}

export default function App() {
  return (
    <Routes>
      {/* 根 → 当前实例 Agent 概览（带 AgentLayout 侧边栏）。
          /dashboard → 同实例的运营面板（同样在 AgentLayout 内，带侧边栏）。 */}
      <Route path="/" element={<InstanceEntry />} />
      <Route path="/dashboard" element={<InstanceEntry to="dashboard" />} />

      {/* One Agent, one management shell. No list, no switcher. */}
      <Route path="/agents/:agentId" element={<AgentLayout />}>
        <Route index element={<Navigate to="overview" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="overview" element={<AgentOverviewPage />} />
        <Route path="setup" element={<AgentSetupPage />} />
        <Route path="clone" element={<AgentCloneWizardPage />} />
        <Route path="knowledge" element={<AgentKnowledgePage />} />
        <Route path="ontology" element={<OntologyExplorerPage />} />
        <Route path="prompts" element={<AgentPromptsPage />} />
        <Route path="channels" element={<AgentChannelsPage />} />
        <Route path="conversations" element={<AgentConversationsPage />} />
        <Route path="feedback" element={<AgentFeedbackPage />} />
        <Route path="review" element={<AgentReviewQueuePage />} />
        <Route path="eval" element={<AgentEvalRunsPage />} />
        <Route path="alerts" element={<AgentAlertsPage />} />
        <Route path="reports" element={<AgentReportsPage />} />
        <Route path="traces/:runId" element={<TracePage />} />
        <Route path="graph-debug" element={<GraphDebugPage />} />
        <Route path="history" element={<ConversationHistoryPage />} />
        <Route path="optimization" element={<KnowledgeIterationPage />} />
        <Route path="coach" element={<CoachDashboardPage />} />
        <Route path="coach/users" element={<CoachUsersPage />} />
        <Route path="coach/users/:userId" element={<CoachUserProfilePage />} />
        <Route path="coach/evaluations" element={<CoachEvaluationsPage />} />
        <Route path="coach/rewards" element={<CoachRewardsPage />} />
        <Route path="coach/settings" element={<CoachSettingsPage />} />
        <Route path="settings" element={<AgentSettingsPage />} />
      </Route>

      {/* Legacy tenant-aggregate routes (kept for backward compatibility / admin). */}
      <Route element={<AppLayout />}>
        <Route path="legacy/pilot" element={<PilotDashboardPage />} />
        <Route path="legacy/conversations" element={<ConversationListPage />} />
        <Route path="legacy/conversations/:id" element={<ConversationDetailPage />} />
        <Route path="legacy/traces/:runId" element={<TracePage />} />
        <Route path="legacy/knowledge" element={<KnowledgePage />} />
        <Route path="legacy/prompts" element={<PromptListPage />} />
        <Route path="legacy/prompts/new" element={<PromptEditPage />} />
        <Route path="legacy/prompts/:id/edit" element={<PromptEditPage />} />
        <Route path="legacy/workflow" element={<WorkflowPage />} />
        <Route path="legacy/feedback" element={<FeedbackPage />} />
        <Route path="legacy/feedback/:id" element={<FeedbackDetailPage />} />
        <Route path="legacy/review" element={<ReviewQueuePage />} />
        <Route path="legacy/gaps" element={<KnowledgeGapsPage />} />
        <Route path="legacy/eval" element={<EvalRunsPage />} />
        <Route path="legacy/alerts" element={<AlertsPage />} />
        <Route path="legacy/reports" element={<PilotReportPage />} />
        <Route path="readiness" element={<ReadinessPage />} />
        <Route path="*" element={<InstanceEntry />} />
      </Route>
    </Routes>
  );
}
