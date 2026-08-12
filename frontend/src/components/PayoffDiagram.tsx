import React from 'react';
import type { OptionsPlan } from '../types';

// Option P&L-at-expiry vs underlying price: green profit region above the zero line, red loss
// below, with breakevens, spot, and strikes marked. Pure SVG, theme-neutral.
export default function PayoffDiagram({ plan, spot }: { plan: OptionsPlan; spot: number }) {
  const pts = (plan.payoff || []).filter(p => p.price != null && p.pnl != null);
  if (pts.length < 3) return null;

  const W = 340, H = 170, mL = 40, mR = 10, mT = 16, mB = 22;
  const x0 = mL, x1 = W - mR, y0 = mT, y1 = H - mB;
  const xs = pts.map(p => p.price), ys = pts.map(p => p.pnl);
  const xmin = Math.min(...xs), xmax = Math.max(...xs);
  let ymin = Math.min(0, ...ys), ymax = Math.max(0, ...ys);
  const pad = (ymax - ymin) * 0.08 || 1;
  ymin -= pad; ymax += pad;
  const xOf = (p: number) => x0 + (p - xmin) / (xmax - xmin || 1) * (x1 - x0);
  const yOf = (v: number) => y1 - (v - ymin) / (ymax - ymin || 1) * (y1 - y0);
  const zeroY = yOf(0);

  const areaUp = pts.map(p => `${xOf(p.price).toFixed(1)},${yOf(Math.max(p.pnl, 0)).toFixed(1)}`).join(' ')
    + ` ${xOf(xmax).toFixed(1)},${zeroY.toFixed(1)} ${xOf(xmin).toFixed(1)},${zeroY.toFixed(1)}`;
  const areaDn = pts.map(p => `${xOf(p.price).toFixed(1)},${yOf(Math.min(p.pnl, 0)).toFixed(1)}`).join(' ')
    + ` ${xOf(xmax).toFixed(1)},${zeroY.toFixed(1)} ${xOf(xmin).toFixed(1)},${zeroY.toFixed(1)}`;
  const line = pts.map(p => `${xOf(p.price).toFixed(1)},${yOf(p.pnl).toFixed(1)}`).join(' ');
  const strikes = Array.from(new Set((plan.legs || []).map(l => l.strike))).filter(s => s >= xmin && s <= xmax);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ height: 'auto', maxHeight: 200 }} role="img" aria-label="Option payoff at expiry">
      {/* profit / loss fills + zero line */}
      <polygon points={areaUp} fill="rgba(34,197,94,0.18)" />
      <polygon points={areaDn} fill="rgba(239,68,68,0.18)" />
      <line x1={x0} y1={zeroY} x2={x1} y2={zeroY} stroke="#64748b" strokeWidth={1} opacity={0.6} />
      <polyline points={line} fill="none" stroke="#e2e8f0" strokeWidth={1.6} strokeLinejoin="round" />

      {/* max profit / loss labels */}
      <text x={x0} y={y0 + 2} fontSize={8.5} fill="rgb(34,197,94)" fontWeight={700}>
        max +${plan.max_profit != null ? Math.round(plan.max_profit) : '—'}
      </text>
      <text x={x0} y={y1 - 1} fontSize={8.5} fill="rgb(239,68,68)" fontWeight={700}>
        max ${plan.max_loss != null ? Math.round(plan.max_loss) : '—'}
      </text>

      {/* strikes */}
      {strikes.map((s, i) => (
        <g key={i}>
          <line x1={xOf(s)} y1={y0} x2={xOf(s)} y2={y1} stroke="#475569" strokeWidth={0.6} strokeDasharray="1 3" opacity={0.6} />
          <text x={xOf(s)} y={y1 + 9} fontSize={7.5} fill="#94a3b8" textAnchor="middle">{s}</text>
        </g>
      ))}

      {/* breakevens */}
      {(plan.breakevens || []).filter(b => b >= xmin && b <= xmax).map((b, i) => (
        <g key={i}>
          <circle cx={xOf(b)} cy={zeroY} r={2.4} fill="rgb(251,191,36)" />
          <text x={xOf(b)} y={zeroY - 3} fontSize={7.5} fill="rgb(251,191,36)" textAnchor="middle">BE {b}</text>
        </g>
      ))}

      {/* spot */}
      {spot >= xmin && spot <= xmax && (
        <g>
          <line x1={xOf(spot)} y1={y0} x2={xOf(spot)} y2={y1} stroke="rgb(56,189,248)" strokeWidth={1} strokeDasharray="3 2" />
          <text x={xOf(spot)} y={y0 - 4} fontSize={8} fill="rgb(56,189,248)" textAnchor="middle" fontWeight={700}>spot {spot}</text>
        </g>
      )}
    </svg>
  );
}
