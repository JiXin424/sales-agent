import { useRef, type ReactNode } from 'react';

interface MagnetProps {
  children: ReactNode;
  padding?: number;
  strength?: number;
  className?: string;
}

export default function Magnet({
  children,
  padding = 20,
  strength = 3,
  className = '',
}: MagnetProps) {
  const ref = useRef<HTMLDivElement>(null);

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const x = e.clientX - rect.left - rect.width / 2;
    const y = e.clientY - rect.top - rect.height / 2;

    const isInBounds =
      Math.abs(x) < rect.width / 2 + padding &&
      Math.abs(y) < rect.height / 2 + padding;

    if (isInBounds) {
      ref.current.style.transform = `translate(${x * strength * 0.1}px, ${y * strength * 0.1}px)`;
    }
  };

  const handleMouseLeave = () => {
    if (!ref.current) return;
    ref.current.style.transform = 'translate(0px, 0px)';
    ref.current.style.transition = 'transform 0.4s ease-out';
  };

  const handleMouseEnter = () => {
    if (!ref.current) return;
    ref.current.style.transition = 'transform 0.15s ease-out';
  };

  return (
    <div
      ref={ref}
      className={`magnet-wrapper ${className}`}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      onMouseEnter={handleMouseEnter}
    >
      {children}
    </div>
  );
}
