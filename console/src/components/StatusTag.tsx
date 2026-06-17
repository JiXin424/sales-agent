/** Colored status tag. */

import { Tag } from 'antd';
import { STATUS_COLORS } from '@/utils/constants';

interface Props {
  status: string;
  label?: string;
}

export default function StatusTag({ status, label }: Props) {
  const color = STATUS_COLORS[status] || 'default';
  return <Tag color={color}>{label || status}</Tag>;
}
