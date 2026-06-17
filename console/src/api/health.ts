/** Health / Ready / Diagnostics API wrappers. */

import { apiGet } from './client';
import type { HealthStatus, ReadyStatus, DiagnosticsResult, LatencyStatsGlobal } from './types';

export function getHealth() {
  return apiGet<HealthStatus>('/health');
}

export function getReady() {
  return apiGet<ReadyStatus>('/ready');
}

export function getDiagnostics() {
  return apiGet<DiagnosticsResult>('/diagnostics/model');
}

export function getGlobalLatencyStats() {
  return apiGet<LatencyStatsGlobal>('/health/latency-stats');
}
