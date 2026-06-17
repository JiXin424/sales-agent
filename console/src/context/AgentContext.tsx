/** Agent context — the active open Agent (one-to-one model).

Replaces the top TenantSelector for Agent pages. The active agent_id resolves
the tenant, status, feature flags, and scope for all Agent-page API calls.
TenantContext is still used on /agents list filter and aggregate admin pages.
*/

import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { AgentInstance } from '@/api/types';

interface AgentContextValue {
  agentId: string | null;
  agent: AgentInstance | null;
  tenantId: string | null;
  isAgentOpen: boolean;
  setAgent: (agent: AgentInstance) => void;
  clearAgent: () => void;
}

const STORAGE_KEY = 'sales-agent-console-agent';

const AgentContext = createContext<AgentContextValue | null>(null);

export function AgentProvider({ children }: { children: ReactNode }) {
  const [agentId, setAgentId] = useState<string | null>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored).id : null;
    } catch {
      return null;
    }
  });
  const [tenantId, setTenantId] = useState<string | null>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored).tenant_id : null;
    } catch {
      return null;
    }
  });
  // Full agent object (lazy; populated when a page loads it). We only persist
  // id + tenant_id to localStorage so stale config is never reused.
  const [agent, setAgentState] = useState<AgentInstance | null>(null);

  const queryClient = useQueryClient();

  const setAgent = useCallback((a: AgentInstance) => {
    setAgentId(a.id);
    setTenantId(a.tenant_id);
    setAgentState(a);
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ id: a.id, tenant_id: a.tenant_id }),
    );
    // Changing Agent must clear stale tenant-scoped queries.
    queryClient.invalidateQueries();
  }, [queryClient]);

  const clearAgent = useCallback(() => {
    setAgentId(null);
    setTenantId(null);
    setAgentState(null);
    localStorage.removeItem(STORAGE_KEY);
    queryClient.clear();
  }, [queryClient]);

  // If an agentId was restored from storage but the full object isn't loaded,
  // pages will fetch it; here we keep state consistent.
  useEffect(() => {
    if (!agentId) setAgentState(null);
  }, [agentId]);

  return (
    <AgentContext.Provider
      value={{
        agentId,
        agent,
        tenantId,
        isAgentOpen: agentId !== null,
        setAgent,
        clearAgent,
      }}
    >
      {children}
    </AgentContext.Provider>
  );
}

export function useAgent(): AgentContextValue {
  const ctx = useContext(AgentContext);
  if (!ctx) throw new Error('useAgent must be used within AgentProvider');
  return ctx;
}
