/** Page header with animated title and shiny description. */

import type { ReactNode } from 'react';
import BlurText from '@/components/react-bits/BlurText';
import ShinyText from '@/components/react-bits/ShinyText';

interface Props {
  title: string;
  description?: string;
  actions?: ReactNode;
}

export default function PageHeader({ title, description, actions }: Props) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
      <div>
        <BlurText
          text={title}
          delay={30}
          animateBy="characters"
          direction="top"
          className="page-header-title"
        />
        {description && (
          <div style={{ marginTop: 4 }}>
            <ShinyText
              text={description}
              speed={4}
              color="#8c8c8c"
              shineColor="#1890ff"
            />
          </div>
        )}
      </div>
      {actions && <div>{actions}</div>}
    </div>
  );
}
