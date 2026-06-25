import { useState, useEffect, useCallback } from 'react';
import { Typography, Button, Tooltip, message } from 'antd';
import { EyeOutlined, EyeInvisibleOutlined, CopyOutlined } from '@ant-design/icons';

const { Text } = Typography;

interface SensitiveFieldProps {
  value: string;        // full plaintext value (for copy)
  maskedValue: string;  // truncated display text from API (e.g. "sk-2e2a...2242a")
}

export default function SensitiveField({ value, maskedValue }: SensitiveFieldProps) {
  const [visible, setVisible] = useState(false);

  // Auto-hide after 5 seconds
  useEffect(() => {
    if (!visible) return;
    const timer = setTimeout(() => setVisible(false), 5000);
    return () => clearTimeout(timer);
  }, [visible]);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(value).then(
      () => message.success('已复制到剪贴板'),
      () => message.error('复制失败'),
    );
  }, [value]);

  if (!visible) {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        <Text code style={{ letterSpacing: 1 }}>●●●●●●●●●●</Text>
        <Tooltip title="点击查看">
          <Button
            type="text"
            size="small"
            icon={<EyeOutlined />}
            onClick={() => setVisible(true)}
            style={{ color: '#999' }}
          />
        </Tooltip>
      </span>
    );
  }

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <Text code>{maskedValue}</Text>
      <Tooltip title="复制完整值">
        <Button
          type="text"
          size="small"
          icon={<CopyOutlined />}
          onClick={handleCopy}
        />
      </Tooltip>
      <Tooltip title="隐藏">
        <Button
          type="text"
          size="small"
          icon={<EyeInvisibleOutlined />}
          onClick={() => setVisible(false)}
        />
      </Tooltip>
    </span>
  );
}
