/** Relative time display. */

import { Tooltip } from 'antd';
import { formatRelativeTime, formatDate } from '@/utils/formatters';

interface Props {
  iso: string | null | undefined;
}

export default function RelativeTime({ iso }: Props) {
  if (!iso) return <span>-</span>;
  return <Tooltip title={formatDate(iso)}>{formatRelativeTime(iso)}</Tooltip>;
}
