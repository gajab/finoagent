import React from 'react';
import type { DeskRankedTrade } from '../types';
import { ladderSpec, ladderX } from './betaLadder';

/** A tiny price ladder: red = where it loses, green = where it keeps its premium, ticks = short strikes, bold = spot. */
export default function PriceLadder({ t, spot, width = 96, height = 20 }: { t: DeskRankedTrade; spot: number; width?: number; height?: number }) {
  const spec = ladderSpec(t, spot);
  if (!spec) return <span className="text-base-content/25">—</span>;
  const x = (p: number) => ladderX(spec, p, width);
  const zl = spec.safeLow != null ? x(spec.safeLow) : 0;
  const zr = spec.safeHigh != null ? x(spec.safeHigh) : width;
  const mid = height / 2;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img"
      aria-label={`Spot ${spot.toFixed(2)}${spec.hasZone ? `, profitable ${spec.safeLow != null ? `above ${spec.safeLow.toFixed(2)}` : ''}${spec.safeLow != null && spec.safeHigh != null ? ' and ' : ''}${spec.safeHigh != null ? `below ${spec.safeHigh.toFixed(2)}` : ''}` : ''}`}>
      <rect x={0} y={mid - 2} width={width} height={4} rx={2} className="fill-error/30" />
      {spec.hasZone && <rect x={zl} y={mid - 2} width={Math.max(2, zr - zl)} height={4} rx={2} className="fill-success/60" />}
      {spec.shorts.map((k, i) => <line key={i} x1={x(k)} x2={x(k)} y1={mid - 5} y2={mid + 5} className="stroke-base-content/55" strokeWidth={1} />)}
      <line x1={x(spec.spot)} x2={x(spec.spot)} y1={1} y2={height - 1} className="stroke-base-content" strokeWidth={2} />
    </svg>
  );
}
