/** Reusable empty state display — enhanced with ClickSpark on action button. */

import { Empty, Button } from 'antd';
import type { ReactNode } from 'react';
import ClickSpark from '@/components/react-bits/ClickSpark';

interface Props {
  description?: string;
  action?: string;
  onAction?: () => void;
  icon?: ReactNode;
}

export default function EmptyState({ description = '暂无数据', action, onAction, icon }: Props) {
  return (
    <Empty
      image={icon || Empty.PRESENTED_IMAGE_SIMPLE}
      description={description}
    >
      {action && onAction && (
        <ClickSpark sparkColor="#1890ff" sparkSize={8} sparkCount={6} duration={400}>
          <Button type="primary" onClick={onAction}>{action}</Button>
        </ClickSpark>
      )}
    </Empty>
  );
}
