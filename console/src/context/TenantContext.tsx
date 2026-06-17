/** Tenant context — stores active tenant, persists to localStorage. */

import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';

interface TenantContextValue {
  tenantId: string | null;
  tenantName: string | null;
  isTenantSelected: boolean;
  setTenant: (id: string, name: string) => void;
  clearTenant: () => void;
}

const STORAGE_KEY = 'sales-agent-console-tenant';

const TenantContext = createContext<TenantContextValue | null>(null);

export function TenantProvider({ children }: { children: ReactNode }) {
  const [tenantId, setTenantId] = useState<string | null>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored).id : null;
    } catch {
      return null;
    }
  });
  const [tenantName, setTenantName] = useState<string | null>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored).name : null;
    } catch {
      return null;
    }
  });

  const queryClient = useQueryClient();

  const setTenant = useCallback((id: string, name: string) => {
    setTenantId(id);
    setTenantName(name);
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ id, name }));
    // Invalidate all queries when tenant changes
    queryClient.invalidateQueries();
  }, [queryClient]);

  const clearTenant = useCallback(() => {
    setTenantId(null);
    setTenantName(null);
    localStorage.removeItem(STORAGE_KEY);
    queryClient.clear();
  }, [queryClient]);

  return (
    <TenantContext.Provider
      value={{
        tenantId,
        tenantName,
        isTenantSelected: tenantId !== null,
        setTenant,
        clearTenant,
      }}
    >
      {children}
    </TenantContext.Provider>
  );
}

export function useTenant(): TenantContextValue {
  const ctx = useContext(TenantContext);
  if (!ctx) throw new Error('useTenant must be used within TenantProvider');
  return ctx;
}
