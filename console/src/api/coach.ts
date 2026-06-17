/** Coach Growth System API wrappers. */

import { apiGet, apiPost, apiPatch } from './client';

export interface CoachScore {
  dimension: string;
  dimension_label: string;
  score: number;
  last_delta: number;
  last_evaluated_at?: string | null;
}

export interface CoachUserProfileSummary {
  user_id: string;
  has_data: boolean;
  rank?: string;
  level?: number;
  total_points?: number;
  enabled?: boolean;
  last_evaluated_date?: string | null;
  scores?: CoachScore[];
  message?: string;
}

export interface CoachReport {
  summary: string;
  sections: { title: string; content: string }[];
}

export interface CoachEvaluation {
  id: string;
  user_id: string;
  evaluation_date: string;
  status: string;
  conversation_count: number;
  user_message_count: number;
  points_delta: number;
  latency_ms: number;
  created_at: string;
}

export interface CoachRewardRow {
  id: string;
  user_id?: string;
  reward_type: string;
  status: string;
  message: string;
  delivered_at?: string | null;
  created_at?: string;
}

export interface CoachSettings {
  configured: boolean;
  realtime_enabled: boolean;
  daily_evaluation_enabled: boolean;
  daily_evaluation_time: string;
  timezone: string;
  minimum_user_messages: number;
  daily_realtime_guidance_limit: number;
  daily_reward_notification_limit: number;
  initial_score: number;
  allow_negative_delta: boolean;
  voice_rewards_enabled: boolean;
  red_packet_reminders_enabled: boolean;
  evidence_quote_max_chars: number;
}

export interface CoachDashboard {
  evaluated_today: number;
  skipped_or_failed_total: number;
  avg_scores: { dimension: string; dimension_label: string; avg_score: number }[];
  note: string;
}

const base = (agentId: string) => `/agents/${agentId}/coach`;

export function getCoachDashboard(agentId: string) {
  return apiGet<CoachDashboard>(`${base(agentId)}/dashboard`);
}

export function getCoachUserProfile(agentId: string, userId: string) {
  return apiGet<CoachUserProfileSummary>(`${base(agentId)}/users/${userId}`);
}

export function getCoachScores(agentId: string, userId: string) {
  return apiGet<{ user_id: string; scores: CoachScore[] }>(`${base(agentId)}/users/${userId}/scores`);
}

export function getCoachReport(agentId: string, userId: string, type: string) {
  return apiGet<CoachReport>(`${base(agentId)}/users/${userId}/report`, { type });
}

export function listCoachUsers(agentId: string, limit = 100, offset = 0) {
  return apiGet<{ items: { user_id: string; rank: string; level: number; total_points: number; last_evaluated_date: string | null }[]; total: number }>(
    `${base(agentId)}/users`,
    { limit, offset },
  );
}

export function listCoachEvaluations(agentId: string, limit = 50, offset = 0, status?: string) {
  return apiGet<{ items: CoachEvaluation[]; total: number }>(
    `${base(agentId)}/evaluations`,
    { limit, offset, status },
  );
}

export function runCoachDaily(
  agentId: string,
  body: { user_id?: string; date?: string; dry_run?: boolean; force_recompute?: boolean },
) {
  return apiPost<{ summary: Record<string, unknown>; results: Record<string, unknown>[] }>(
    `${base(agentId)}/admin/run_daily`,
    body,
  );
}

export function rerunCoachEvaluation(agentId: string, evaluationId: string) {
  return apiPost(`${base(agentId)}/evaluations/${evaluationId}/rerun`);
}

export function listCoachRewards(agentId: string, limit = 50, offset = 0, userId?: string) {
  return apiGet<{ items: CoachRewardRow[]; total: number }>(`${base(agentId)}/rewards`, { limit, offset, user_id: userId });
}

export function patchCoachReward(agentId: string, rewardId: string, body: { status?: string; admin_note?: string }) {
  return apiPatch(`${base(agentId)}/rewards/${rewardId}`, body);
}

export function getCoachSettings(agentId: string) {
  return apiGet<CoachSettings>(`${base(agentId)}/settings`);
}

export function patchCoachSettings(agentId: string, body: Partial<CoachSettings>) {
  return apiPatch<CoachSettings>(`${base(agentId)}/settings`, body);
}
