import React, { useMemo } from 'react';
import type { GexProfilePoint, DealerGammaLevels } from '../types';

// Net Gamma-Exposure by strike — a diverging horizontal bar chart on a true price axis:
// +GEX (green, dealers suppress vol) extends right, −GEX (red, dealers amplify) extends left,
// with the per-strike profile line and the named dealer levels (Call Resistance / Put Support /
// HVL / γ-flip / Spot) drawn as horizontal references. Pure SVG, theme-neutral.
export default function GexChart({ profile, levels, spot, netGexMM }: {
  profile: GexProfilePoint[];
  levels?: DealerGammaLevels;
  spot: number;
  netGexMM?: number | null;
}) {
  const view = useMemo(() => {
    const pts = (profile || [])
      .filter(p => p.strike != null && p.gex_millions != null)
      .map(p => ({ strike: p.strike as number, gex_millions: p.gex_millions as number }));
    if (pts.length < 3 || !spot) return null;

    const cr = levels?.call_resistance?.strike ?? null;
    const ps = levels?.put_support?.strike ?? null;
    const hvl = levels?.hvl?.strike ?? null;
    const flip = levels?.gamma_flip?.level ?? null;
    const marks = [spot, cr, ps, hvl, flip].filter((x): x is number => x != null);

    // focus window: ±5% of spot, widened to include every named level
    let lo = Math.min(spot * 0.95, ...marks) * 0.997;
    let hi = Math.max(spot * 1.05, ...marks) * 1.003;
    let win = pts.filter(p => p.strike >= lo && p.strike <= hi);
    if (win.length < 3) { win = pts; lo = pts[0].strike; hi = pts[pts.length - 1].strike; }

    const maxAbs = Math.max(...win.map(p => Math.abs(p.gex_millions)), 1);
    return { win, lo, hi, maxAbs, cr, ps, hvl, flip };
  }, [profile, levels, spot]);

  if (!view) return <div className="text-center text-xs text-base-content/40 py-10">No option-chain gamma data.</div>;
  const { win, lo, hi, maxAbs, cr, ps, hvl, flip } = view;

  // viewBox layout
  const W = 360, H = 380;
  const mL = 40, mR = 70, mT = 24, mB = 26;
  const x0 = mL, x1 = W - mR, y0 = mT, y1 = H - mB;
  const cx = (x0 + x1) / 2;
  const yOf = (s: number) => y0 + (hi - s) / (hi - lo) * (y1 - y0);
  const xOf = (g: number) => cx + (g / maxAbs) * (x1 - cx) * 0.96;
  const barH = Math.max(2, Math.min(9, ((y1 - y0) / win.length) * 0.72));

  const GREEN = 'rgb(34,197,94)', RED = 'rgb(239,68,68)';
  const linePts = win.map(p => `${xOf(p.gex_millions).toFixed(1)},${yOf(p.strike).toFixed(1)}`).join(' ');

  const refLine = (s: number | null, color: string, dash: string, label: string) => {
    if (s == null || s < lo || s > hi) return null;
    const y = yOf(s);
    return (
      <g key={label}>
        <line x1={x0} y1={y} x2={x1} y2={y} stroke={color} strokeWidth={1} strokeDasharray={dash} opacity={0.9} />
        <text x={x1 + 3} y={y + 3} fontSize={8.5} fill={color} fontWeight={600}>{label}</text>
      </g>
    );
  };

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ height: 'auto', maxHeight: 420 }} role="img"
      aria-label="Net gamma exposure by strike">
      {/* title + net gex */}
      <text x={x0} y={14} fontSize={10} fill="#cbd5e1" fontWeight={700}>Net GEX by strike</text>
      {netGexMM != null && (
        <text x={x1 + 3} y={14} fontSize={9} fill={netGexMM >= 0 ? GREEN : RED} fontWeight={700} textAnchor="start">
          {netGexMM >= 0 ? '+' : ''}{netGexMM}M net
        </text>
      )}

      {/* zero axis */}
      <line x1={cx} y1={y0} x2={cx} y2={y1} stroke="#64748b" strokeWidth={1} opacity={0.5} />
      <text x={cx} y={y1 + 16} fontSize={8} fill="#94a3b8" textAnchor="middle">0</text>
      <text x={x1} y={y1 + 16} fontSize={8} fill="#94a3b8" textAnchor="end">+{Math.round(maxAbs)}M</text>
      <text x={x0} y={y1 + 16} fontSize={8} fill="#94a3b8" textAnchor="start">−{Math.round(maxAbs)}M</text>

      {/* bars */}
      {win.map((p, i) => {
        const y = yOf(p.strike), x = xOf(p.gex_millions);
        const pos = p.gex_millions >= 0;
        const isCR = cr != null && Math.abs(p.strike - cr) < 1e-6;
        const isPS = ps != null && Math.abs(p.strike - ps) < 1e-6;
        const color = pos ? GREEN : RED;
        const op = isCR || isPS ? 0.95 : 0.5;
        const left = Math.min(cx, x), w = Math.abs(x - cx);
        return (
          <rect key={i} x={left} y={y - barH / 2} width={Math.max(0.5, w)} height={barH} rx={1.2}
            fill={color} opacity={op} stroke={isCR || isPS ? color : 'none'} strokeWidth={isCR || isPS ? 0.6 : 0}>
            <title>{`$${p.strike}  ·  ${pos ? '+' : ''}${p.gex_millions}M GEX`}</title>
          </rect>
        );
      })}

      {/* per-strike profile line */}
      <polyline points={linePts} fill="none" stroke="rgb(234,179,8)" strokeWidth={1.4} opacity={0.85}
        strokeLinejoin="round" strokeLinecap="round" />

      {/* strike ticks (a few) */}
      {win.filter((_, i) => i % Math.max(1, Math.round(win.length / 8)) === 0).map((p, i) => (
        <text key={i} x={x0 - 3} y={yOf(p.strike) + 3} fontSize={8} fill="#94a3b8" textAnchor="end">{p.strike}</text>
      ))}

      {/* named level references */}
      {refLine(spot, '#e2e8f0', '', `Spot ${spot}`)}
      {refLine(cr, RED, '4 2', `Call Res ${cr}`)}
      {refLine(ps, GREEN, '4 2', `Put Supp ${ps}`)}
      {refLine(hvl, 'rgb(139,92,246)', '2 2', `HVL ${hvl}`)}
      {refLine(flip, 'rgb(251,191,36)', '1 2', `γ-flip ${flip}`)}
    </svg>
  );
}
