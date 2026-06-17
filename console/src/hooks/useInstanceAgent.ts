/** Resolve the current (dedicated) instance's single Agent. */

import { useQuery } from '@tanstack/react-query';
import { getInstanceAgent } from '@/api/agents';
import type { AgentInstance } from '@/api/types';

export function useInstanceAgent() {
  return useQuery<AgentInstance>({
    queryKey: ['instance-agent'],
    queryFn: () => getInstanceAgent(),
    staleTime: 60_000,
  });
}
