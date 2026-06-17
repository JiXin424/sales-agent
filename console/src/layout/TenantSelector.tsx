/** Tenant selector — dropdown loaded from backend API. */

import { Select, Space, Typography } from 'antd';
import { useTenant } from '@/context/TenantContext';
import { listTenants } from '@/api/tenants';
import { useQuery } from '@tanstack/react-query';

export default function TenantSelector() {
  const { tenantId, tenantName, setTenant, clearTenant, isTenantSelected } = useTenant();

  const { data } = useQuery({
    queryKey: ['tenantList'],
    queryFn: () => listTenants({ status: 'active', limit: 100 }),
    staleTime: 60_000,
  });

  const options = (data?.items || []).map((t) => ({
    value: t.tenant_id,
    label: `${t.name} (${t.tenant_id})`,
  }));

  return (
    <Space>
      <Typography.Text type="secondary">租户:</Typography.Text>
      <Select
        showSearch
        value={isTenantSelected ? tenantId! : undefined}
        placeholder="选择租户"
        style={{ minWidth: 220 }}
        optionFilterProp="label"
        onChange={(value: string) => {
          if (!value) {
            clearTenant();
            return;
          }
          const item = data?.items?.find((t) => t.tenant_id === value);
          setTenant(value, item?.name || value);
        }}
        options={options}
        allowClear
        onClear={clearTenant}
      />
      {isTenantSelected && (
        <Typography.Text strong>{tenantName}</Typography.Text>
      )}
    </Space>
  );
}
