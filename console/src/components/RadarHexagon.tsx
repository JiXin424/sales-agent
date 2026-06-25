/** 依赖无关的 SVG 多边形雷达图（六维时为正六边形）。
 *
 * 支持多条 series 叠加对比：每条 series 按各维度得分 0..max 绘制一个多边形，
 * 半透明填充 + 描边 + 顶点圆点；外圈同心多边形网格 + 轴线 + 维度标签。
 */

export interface RadarSeries {
  name: string;
  color: string;
  /** 每个维度得分 0..max，长度需与 labels 一致 */
  values: number[];
}

interface RadarHexagonProps {
  labels: string[];
  series: RadarSeries[];
  max?: number;
  size?: number;
}

const DEG = Math.PI / 180;

export default function RadarHexagon({
  labels,
  series,
  max = 100,
  size = 400,
}: RadarHexagonProps) {
  const n = labels.length;
  const cx = size / 2;
  const cy = size / 2;
  const R = size * 0.34; // 图表半径（留出标签空间）
  const labelR = R + 26;

  // 第 i 个顶点的角度（顶部起、顺时针）。n=6 时即正六边形。
  const angle = (i: number) => -90 + (360 / n) * i;

  const point = (i: number, ratio: number): readonly [number, number] => {
    const a = angle(i) * DEG;
    return [cx + ratio * R * Math.cos(a), cy + ratio * R * Math.sin(a)];
  };

  const labelPos = (i: number): readonly [number, number] => {
    const a = angle(i) * DEG;
    return [cx + labelR * Math.cos(a), cy + labelR * Math.sin(a)];
  };

  const clamp = (v: number) => Math.max(0, Math.min(max, v));

  // 同心网格环（20/40/60/80/100）
  const rings = [0.2, 0.4, 0.6, 0.8, 1];
  const ringPoints = (ratio: number) =>
    Array.from({ length: n }, (_, i) => point(i, ratio).join(',')).join(' ');

  const seriesPoints = (s: RadarSeries) =>
    s.values.map((v, i) => point(i, clamp(v) / max).join(',')).join(' ');

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="六维雷达图">
      {/* 网格环 */}
      {rings.map((r) => (
        <polygon key={r} points={ringPoints(r)} fill="none" stroke="#eaeaec" strokeWidth={1} />
      ))}
      {/* 轴线 */}
      {labels.map((_, i) => {
        const [x, y] = point(i, 1);
        return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="#eeeeee" strokeWidth={1} />;
      })}
      {/* 中心点 */}
      <circle cx={cx} cy={cy} r={1.5} fill="#d9d9d9" />

      {/* 各 series 多边形 */}
      {series.map((s) => (
        <g key={s.name}>
          <polygon
            points={seriesPoints(s)}
            fill={s.color}
            fillOpacity={0.16}
            stroke={s.color}
            strokeWidth={2}
            strokeLinejoin="round"
          />
          {s.values.map((v, i) => {
            const [x, y] = point(i, clamp(v) / max);
            return <circle key={i} cx={x} cy={y} r={3.5} fill="#fff" stroke={s.color} strokeWidth={2} />;
          })}
        </g>
      ))}

      {/* 维度标签 */}
      {labels.map((label, i) => {
        const [x, y] = labelPos(i);
        const cos = Math.cos(angle(i) * DEG);
        const anchor = Math.abs(cos) < 0.3 ? 'middle' : cos > 0 ? 'start' : 'end';
        return (
          <text
            key={label}
            x={x}
            y={y}
            textAnchor={anchor}
            dominantBaseline="middle"
            fontSize={13}
            fill="#595959"
          >
            {label}
          </text>
        );
      })}
    </svg>
  );
}
