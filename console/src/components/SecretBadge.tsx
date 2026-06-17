/** Secret badge — never reveals actual values. */

import { Tooltip, Tag } from 'antd';

interface Props {
  label?: string;
}

export default function SecretBadge({ label = '已配置' }: Props) {
  return (
    <Tooltip title="敏感信息已隐藏">
      <Tag color="default">**** ({label})</Tag>
    </Tooltip>
  );
}
