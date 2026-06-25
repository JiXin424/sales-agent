/** Instance-level API wrappers. */

import { apiGet } from './client';
import type { InstanceConfigResponse } from './types';

export function getInstanceConfig() {
  return apiGet<InstanceConfigResponse>('/instance/config');
}
