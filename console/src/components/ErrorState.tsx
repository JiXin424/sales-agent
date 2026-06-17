/** Reusable error display with retry — enhanced with ClickSpark. */

import { Result, Button } from 'antd';
import ClickSpark from '@/components/react-bits/ClickSpark';

interface Props {
  title?: string;
  message?: string;
  onRetry?: () => void;
}

export default function ErrorState({ title = '加载失败', message, onRetry }: Props) {
  return (
    <Result
      status="error"
      title={title}
      subTitle={message}
      extra={
        onRetry ? (
          <ClickSpark sparkColor="#1890ff" sparkSize={8} sparkCount={6} duration={400}>
            <Button onClick={onRetry}>重试</Button>
          </ClickSpark>
        ) : undefined
      }
    />
  );
}
