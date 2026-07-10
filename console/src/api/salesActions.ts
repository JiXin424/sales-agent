/** Sales actions ops API - read-only typed wrappers for the operations page. */

import { apiGet } from './client';

export interface SalesAction {
  id: string;
  tenant_id: string;
  agent_id: string;
  user_id: string;
  channel: string;
  dingtalk_user_id: string | null;
  conversation_id: string;
  title: string;
  customer_name: string | null;
  action_type: string;
  scheduled_at: string;
  timezone: string;
  status: string;
  priority: string;
  source_kind: string;
  created_at: string;
}

export interface SalesActionReminder {
  id: string;
  action_id: string | null;
  user_id: string;
  remind_at: string;
  reminder_type: string;
  status: string;
  attempts: number;
  next_attempt_at: string | null;
  last_error: string | null;
  created_at: string;
}

export interface SalesActionDelivery {
  id: string;
  action_id: string | null;
  reminder_id: string | null;
  user_id: string;
  channel: string;
  delivery_type: string;
  dingtalk_message_id: string | null;
  card_instance_id: string | null;
  rendered_text: string;
  status: string;
  error: string | null;
  created_at: string;
}

export interface SalesActionEvent {
  id: string;
  action_id: string | null;
  user_id: string;
  event_type: string;
  created_at: string;
}

export interface SalesActionDetail {
  card: SalesAction;
  reminders: SalesActionReminder[];
  deliveries: SalesActionDelivery[];
  events: SalesActionEvent[];
}

export interface SalesActionListResponse {
  items: SalesAction[];
  total: number;
}

export interface ReminderListResponse {
  items: SalesActionReminder[];
  total: number;
}

export interface DeliveryListResponse {
  items: SalesActionDelivery[];
  total: number;
}

export interface SalesActionFilters {
  status?: string;
  action_type?: string;
  user_id?: string;
  scheduled_from?: string;
  scheduled_to?: string;
  [key: string]: string | undefined;
}

/** List sales actions for an agent (cross-user, filterable; read-only ops view). */
export function listSalesActions(agentId: string, filters: SalesActionFilters = {}) {
  return apiGet<SalesActionListResponse>(`/agents/${agentId}/sales-actions`, filters);
}

/** Action detail: card + its reminders, deliveries, and events. */
export function getSalesAction(agentId: string, actionId: string) {
  return apiGet<SalesActionDetail>(`/agents/${agentId}/sales-actions/${actionId}`);
}

/** List reminders for an agent (includes failed/sent). */
export function listSalesActionReminders(
  agentId: string,
  filters: { status?: string; user_id?: string } = {},
) {
  return apiGet<ReminderListResponse>(`/agents/${agentId}/sales-actions/reminders`, filters);
}

/** List delivery records for an agent (includes failed/sent). */
export function listSalesActionDeliveries(
  agentId: string,
  filters: { status?: string; user_id?: string } = {},
) {
  return apiGet<DeliveryListResponse>(`/agents/${agentId}/sales-actions/deliveries`, filters);
}
