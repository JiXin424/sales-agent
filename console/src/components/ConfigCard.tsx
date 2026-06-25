import { useMemo } from 'react';
import { Card, Collapse, Descriptions, Tag, Typography } from 'antd';
import {
  SettingOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  RobotOutlined,
  SoundOutlined,
  TrophyOutlined,
  ApiOutlined,
} from '@ant-design/icons';
import SensitiveField from './SensitiveField';
import type { InstanceConfigResponse, ConfigValue } from '@/api/types';

const { Text } = Typography;

const GROUP_LABELS: Record<string, string> = {
  deployment: '部署信息',
  model: '模型配置',
  storage: '存储配置',
  dingtalk: '钉钉集成',
  media: '媒体理解',
  coach: '教练快捷',
  neo4j: 'Neo4j',
  ontology: '本体引擎',
  other: '其他配置',
};

const GROUP_ICONS: Record<string, React.ReactNode> = {
  deployment: <CloudServerOutlined />,
  model: <RobotOutlined />,
  storage: <DatabaseOutlined />,
  dingtalk: <ApiOutlined />,
  media: <SoundOutlined />,
  coach: <TrophyOutlined />,
  neo4j: <DatabaseOutlined />,
  ontology: <SettingOutlined />,
  other: <SettingOutlined />,
};

/** Pick 2-3 key summary fields for the collapsed header. */
function summarizeGroup(fields: Record<string, ConfigValue>): string {
  const entries = Object.entries(fields);
  // Priority: show non-sensitive first, then sensitive masked values
  const nonSensitive = entries.filter(([, v]) => typeof v === 'string');
  const preview = nonSensitive.slice(0, 3).map(([, v]) => String(v));
  if (preview.length < 2) {
    // fill with sensitive masked values if needed
    const sens = entries.filter(([, v]) => typeof v === 'object');
    for (const [, v] of sens) {
      if (preview.length >= 3) break;
      const sv = v as { masked: string };
      preview.push(sv.masked);
    }
  }
  return preview.join(' · ') || '—';
}

function isSensitive(val: ConfigValue): val is { value: string; sensitive: true; masked: string } {
  return typeof val === 'object' && val !== null && 'sensitive' in val;
}

interface ConfigCardProps {
  data: InstanceConfigResponse | undefined;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
}

export default function ConfigCard({ data, loading, error, onRetry }: ConfigCardProps) {
  const collapseItems = useMemo(() => {
    if (!data?.groups) return [];
    return Object.entries(data.groups).map(([key, fields]) => {
      const label = GROUP_LABELS[key] || key;
      const icon = GROUP_ICONS[key] || <SettingOutlined />;
      const summary = summarizeGroup(fields);
      const fieldEntries = Object.entries(fields);

      return {
        key,
        label: (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            {icon}
            <Text strong>{label}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {fieldEntries.length} 项 · {summary}
            </Text>
          </span>
        ),
        children: (
          <Descriptions column={1} size="small" colon={false}>
            {fieldEntries.map(([fieldName, fieldValue]) => (
              <Descriptions.Item
                key={fieldName}
                label={
                  <Text code style={{ fontSize: 12 }}>
                    {fieldName}
                  </Text>
                }
              >
                {isSensitive(fieldValue) ? (
                  <SensitiveField
                    value={fieldValue.value}
                    maskedValue={fieldValue.masked}
                  />
                ) : (
                  <Text>{String(fieldValue)}</Text>
                )}
              </Descriptions.Item>
            ))}
          </Descriptions>
        ),
      };
    });
  }, [data]);

  return (
    <Card
      title={
        <span>
          <SettingOutlined style={{ marginRight: 8 }} />
          环境配置
        </span>
      }
      style={{ marginBottom: 24 }}
      loading={loading}
    >
      {error ? (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Text type="danger">加载环境配置失败</Text>
          <br />
          <a onClick={onRetry} style={{ marginTop: 8, display: 'inline-block' }}>
            点击重试
          </a>
        </div>
      ) : collapseItems.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Text type="secondary">暂无环境配置数据</Text>
        </div>
      ) : (
        <Collapse
          items={collapseItems}
          defaultActiveKey={['deployment']}
          size="small"
        />
      )}
    </Card>
  );
}
