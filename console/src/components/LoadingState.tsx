/** Reusable loading spinner. */

import { Spin } from 'antd';

interface Props {
  tip?: string;
}

export default function LoadingState({ tip = '加载中...' }: Props) {
  return (
    <div style={{ textAlign: 'center', padding: '48px 0' }}>
      <Spin size="large" tip={tip}>
        <div style={{ minHeight: 100 }} />
      </Spin>
    </div>
  );
}
