import React, { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { Info } from 'lucide-react';
import type { AnatomyData, BreadthData, AnatomyFactor } from '../types';

// Phase 2 panels of the Selloff Monitor: who is participating (breadth) and what got sold (anatomy).

const sgn = (v: number) => (v > 0 ? '+' : v < 0 ? '−' : '');
const pct = (v: number | null | undefined, d = 2) => (v == null ? '—' : `${sgn(v)}${Math.abs(v).toFixed(d)}%`);
const pts = (v: number | null | undefined, d = 2) => (v == null ? '—' : `${sgn(v)}${Math.abs(v).toFixed(d)}`);
const sig = (v: number | null | undefined) => (v == null ? '—' : `${sgn(v)}${Math.abs(v).toFixed(1)}σ`);
const shortDay = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

function zClass(z: number | null | undefined): string {
  if (z == null) return '';
  if (z <= -2) return 'bg-red-500/35';
  if (z <= -1) return 'bg-red-500/15';
  if (z >= 2) return 'bg-emerald-500/30';
  if (z >= 1) return 'bg-emerald-500/15';
  return '';
}

function Stat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: string }) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-base-200/30 px-3 py-2 min-w-0">
      <div className="text-[9px] uppercase tracking-wide text-base-content/40 truncate">{label}</div>
      <div className={`text-sm font-bold tabular-nums ${tone || 'text-base-content/85'}`}>{value}</div>
      {sub && <div className="text-[10px] text-base-content/45 truncate">{sub}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Breadth
// ---------------------------------------------------------------------------

export function BreadthPanel({ b }: { b: BreadthData }) {
  const option = useMemo(() => {
    if (!b.series?.length || !b.thresholds) return null;
    const s = b.series;
    const x = s.map(p => p.date);
    const th = b.thresholds;
    return {
      backgroundColor: 'transparent', animation: false,
      grid: [{ left: 40, right: 12, top: 18, height: 150 }, { left: 40, right: 12, top: 200, height: 70 }],
      legend: { top: 0, right: 8, textStyle: { color: '#9ca3af', fontSize: 10 }, itemWidth: 14, itemHeight: 6,
        data: ['% above 20-day', '% above 50-day', '% above 200-day'] },
      tooltip: {
        trigger: 'axis', backgroundColor: 'rgba(17,24,39,0.95)', borderColor: 'rgba(255,255,255,0.1)',
        textStyle: { color: '#e5e7eb', fontSize: 11 },
        formatter: (ps: any[]) => {
          const p = s[ps[0]?.dataIndex ?? 0];
          return [`<b>${shortDay(p.date)}</b>`,
            `Above 20d ${p.pct20 ?? '—'}% · 50d ${p.pct50 ?? '—'}% · 200d ${p.pct200 ?? '—'}%`,
            `New highs ${p.nh} · new lows ${p.nl}`,
            `Advancers ${p.adv} · decliners ${p.dec} · up-volume ${p.up_vol_pct ?? '—'}%`].join('<br/>');
        },
      },
      xAxis: [
        { type: 'category', data: x, gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: 'rgba(255,255,255,0.15)' } } },
        { type: 'category', data: x, gridIndex: 1, axisLabel: { color: '#9ca3af', fontSize: 10, formatter: (v: string) => v.slice(5) },
          axisLine: { lineStyle: { color: 'rgba(255,255,255,0.15)' } } },
      ],
      yAxis: [
        { type: 'value', min: 0, max: 100, gridIndex: 0, axisLabel: { color: '#9ca3af', fontSize: 10, formatter: '{value}%' },
          splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } } },
        { type: 'value', gridIndex: 1, axisLabel: { color: '#9ca3af', fontSize: 9 }, splitLine: { show: false } },
      ],
      series: [
        { name: '% above 20-day', type: 'line', data: s.map(p => p.pct20), symbol: 'none', lineStyle: { color: '#38bdf8', width: 1.4 }, itemStyle: { color: '#38bdf8' },
          markArea: { silent: true, data: [
            [{ yAxis: 0, itemStyle: { color: 'rgba(16,185,129,0.06)' } }, { yAxis: th.washout_20d }],
            [{ yAxis: th.overbought_20d, itemStyle: { color: 'rgba(239,68,68,0.06)' } }, { yAxis: 100 }],
          ] } },
        { name: '% above 50-day', type: 'line', data: s.map(p => p.pct50), symbol: 'none', lineStyle: { color: '#a78bfa', width: 1.6 }, itemStyle: { color: '#a78bfa' } },
        { name: '% above 200-day', type: 'line', data: s.map(p => p.pct200), symbol: 'none', lineStyle: { color: '#e5e7eb', width: 1.2, type: 'dashed' }, itemStyle: { color: '#e5e7eb' } },
        { name: 'New highs', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, stack: 'hl', data: s.map(p => p.nh), itemStyle: { color: 'rgba(52,211,153,0.75)' } },
        { name: 'New lows', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, stack: 'hl', data: s.map(p => -p.nl), itemStyle: { color: 'rgba(248,113,113,0.75)' } },
      ],
    };
  }, [b]);

  if (!b.available || !b.last || !b.window) return <div className="text-xs text-base-content/50">{b.reason || 'Breadth unavailable.'}</div>;
  const L = b.last, W = b.window;
  const move = (now: number | null, before: number | null) => (now != null && before != null
    ? <span className={now >= before ? 'text-success' : 'text-error'}>{now.toFixed(0)}% <span className="text-base-content/40">from {before.toFixed(0)}%</span></span>
    : '—');
  const zw = b.zweig_thrust_on;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Stat label={`Stocks down over ${W.sessions} sessions`} value={W.pct_down != null ? `${W.pct_down.toFixed(0)}%` : '—'}
          sub={`median stock ${pct(W.median_pct)} · ${W.n} members`} tone={(W.pct_down ?? 0) >= 60 ? 'text-error' : (W.pct_down ?? 0) <= 40 ? 'text-success' : undefined} />
        <Stat label="Above 20-day avg" value={move(L.pct20, L.pct20_before)} sub={b.state === 'washout' ? 'washed out' : b.state === 'overbought' ? 'stretched' : 'before → now'} />
        <Stat label="Above 50-day avg" value={move(L.pct50, L.pct50_before)} />
        <Stat label="Above 200-day avg" value={move(L.pct200, L.pct200_before)} />
        <Stat label="New highs / lows" value={<><span className="text-success">{L.nh}</span> / <span className="text-error">{L.nl}</span></>}
          sub={`window: ${W.nh_sum} / ${W.nl_sum}`} />
        <Stat label="Advancers / decliners" value={`${L.adv} / ${L.dec}`} sub={shortDay(L.date)} />
        <Stat label="Up-volume share" value={L.up_vol_pct != null ? `${L.up_vol_pct.toFixed(0)}%` : '—'}
          sub={b.ninety_pct_days?.length ? `90% days: ${b.ninety_pct_days.map(d => `${shortDay(d.date)} ${d.kind}`).join(', ')}` : 'no 90% days in 10 sessions'}
          tone={(L.up_vol_pct ?? 50) <= 10 ? 'text-error' : (L.up_vol_pct ?? 50) >= 90 ? 'text-success' : undefined} />
        <Stat label="Zweig breadth (10-day)" value={L.zweig != null ? L.zweig.toFixed(3) : '—'}
          sub={zw ? `thrust fired ${shortDay(zw)}` : `thrust = <${b.thresholds?.zweig_low} → >${b.thresholds?.zweig_high}`}
          tone={zw ? 'text-success' : undefined} />
      </div>
      {option && <ReactECharts option={option} style={{ height: 290, width: '100%' }} notMerge lazyUpdate />}
      <p className="text-[10px] text-base-content/40 leading-snug flex gap-1">
        <Info className="w-3 h-3 shrink-0 mt-px" />
        Equal-weighted counts over today's {b.n_stocks} S&P 500 members, closing basis. Shaded bands mark the conventional
        washout (≤{b.thresholds?.washout_20d}% above the 20-day) and stretched (≥{b.thresholds?.overbought_20d}%) zones. {b.thresholds?.note}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Anatomy
// ---------------------------------------------------------------------------

function Bar({ v, max }: { v: number | null; max: number }) {
  if (v == null || !max) return null;
  const w = Math.min(50, (Math.abs(v) / max) * 50);
  return (
    <div className="relative h-2 w-full bg-base-content/[0.04] rounded">
      <div className="absolute top-0 bottom-0 w-px bg-base-content/20" style={{ left: '50%' }} />
      <div className={`absolute top-0 bottom-0 rounded ${v < 0 ? 'bg-error/70' : 'bg-success/70'}`}
        style={v < 0 ? { right: '50%', width: `${w}%` } : { left: '50%', width: `${w}%` }} />
    </div>
  );
}

function FactorRow({ f }: { f: AnatomyFactor }) {
  return (
    <tr className="border-t border-white/[0.04]" title={`${f.long.join('+')} minus ${f.short.join('+')}. Negative = ${f.negative_means}.`}>
      <td className="py-1 pr-2 text-base-content/75">{f.label}</td>
      <td className="py-1 pr-2 text-right tabular-nums">{pts(f.spread_pts)} pts</td>
      <td className={`py-1 px-1 text-right tabular-nums rounded-sm ${zClass(f.z)}`}>{sig(f.z)}</td>
      <td className="py-1 pl-2 text-[10px] text-base-content/40">{(f.z ?? 0) <= -1.5 ? f.negative_means : ''}</td>
    </tr>
  );
}

export function AnatomyPanel({ a }: { a: AnatomyData }) {
  if (!a.available || !a.sectors || !a.factors || !a.classification) {
    return <div className="text-xs text-base-content/50">{a.reason || 'Sector / factor anatomy unavailable.'}</div>;
  }
  const cl = a.classification;
  const maxSec = Math.max(...a.sectors.map(s => Math.abs(s.ret_pct ?? 0)), 0.5);
  const conc = a.concentration;
  const W = a.window_sessions;
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-primary/25 bg-primary/[0.06] px-3 py-2">
        <div className="text-sm font-semibold text-primary">{cl.primary.label}</div>
        <div className="text-[11px] text-base-content/70">{cl.primary.why}</div>
        {cl.also.length > 0 && (
          <div className="mt-1.5 space-y-0.5">
            {cl.also.map(h => (
              <div key={h.id} className="text-[11px] text-base-content/60"><span className="text-base-content/80 font-medium">Also — {h.label}:</span> {h.why}</div>
            ))}
          </div>
        )}
        <div className="text-[10px] text-base-content/40 mt-1.5 flex gap-1"><Info className="w-3 h-3 shrink-0 mt-px" />{cl.rules}</div>
        {cl.evidence && <div className="text-[10px] text-base-content/45 mt-1">Evidence ({cl.evidence.grade}): {cl.evidence.note}</div>}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="overflow-x-auto">
          <div className="text-[9px] uppercase tracking-wide text-base-content/40 mb-1">Sectors · {W}-session move (ETF) and how many members fell</div>
          <table className="w-full text-[11px] tabular-nums">
            <thead>
              <tr className="text-[9px] uppercase tracking-wide text-base-content/35 text-left">
                <th className="pb-1 pr-2 font-medium">Sector</th><th className="pb-1 pr-2 font-medium text-right">Move</th>
                <th className="pb-1 pr-2 font-medium w-24" /><th className="pb-1 pr-2 font-medium text-right">σ</th>
                <th className="pb-1 pr-2 font-medium text-right">vs S&P</th><th className="pb-1 font-medium text-right">Members down</th>
              </tr>
            </thead>
            <tbody>
              {a.sectors.map(s => (
                <tr key={s.symbol} className="border-t border-white/[0.04]">
                  <td className="py-1 pr-2 text-base-content/75 whitespace-nowrap">{s.name} <span className="text-base-content/30">{s.symbol}</span></td>
                  <td className={`py-1 pr-2 text-right ${(s.ret_pct ?? 0) < 0 ? 'text-error' : 'text-success'}`}>{pct(s.ret_pct)}</td>
                  <td className="py-1 pr-2"><Bar v={s.ret_pct} max={maxSec} /></td>
                  <td className={`py-1 px-1 text-right rounded-sm ${zClass(s.z)}`}>{sig(s.z)}</td>
                  <td className="py-1 pr-2 text-right text-base-content/60">{pts(s.vs_spy_pts)}</td>
                  <td className="py-1 text-right text-base-content/60" title={`median member ${pct(s.median_member_pct)}`}>
                    {s.pct_down != null ? `${s.pct_down.toFixed(0)}% of ${s.members}` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="space-y-4">
          <div className="overflow-x-auto">
            <div className="text-[9px] uppercase tracking-wide text-base-content/40 mb-1">Factor spreads · long minus short, in σ of the spread's own volatility</div>
            <table className="w-full text-[11px]"><tbody>{a.factors.map(f => <FactorRow key={f.id} f={f} />)}</tbody></table>
          </div>

          {conc && (
            <div>
              <div className="text-[9px] uppercase tracking-wide text-base-content/40 mb-1">
                Concentration · SPY's top {conc.holdings.length} holdings ({conc.weight_pct.toFixed(0)}% of the index)
              </div>
              <div className="flex items-center gap-2 text-[11px] tabular-nums">
                <span className="text-base-content/60">S&P {pct(conc.index_ret_pct)}</span>
                <span className="text-base-content/30">=</span>
                <span className="text-error/90">top holdings {pts(conc.contribution_pts)}</span>
                <span className="text-base-content/30">+</span>
                <span className={conc.rest_pts < 0 ? 'text-error/70' : 'text-success/80'}>everything else {pts(conc.rest_pts)}</span>
              </div>
              <div className="flex flex-wrap gap-1 mt-1.5">
                {conc.holdings.map(h => (
                  <span key={h.symbol} title={`${h.name}: ${pct(h.ret_pct)} × ${h.weight_pct.toFixed(2)}% weight`}
                    className={`text-[10px] tabular-nums px-1.5 py-0.5 rounded border border-white/[0.06] ${h.contribution_pts < 0 ? 'bg-red-500/10' : 'bg-emerald-500/10'}`}>
                    {h.symbol} {pts(h.contribution_pts, 2)}
                  </span>
                ))}
              </div>
              <p className="text-[10px] text-base-content/35 mt-1">{conc.note}</p>
            </div>
          )}
        </div>
      </div>

      {a.movers && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {(['losers', 'gainers'] as const).map(k => (
            <div key={k}>
              <div className="text-[9px] uppercase tracking-wide text-base-content/40 mb-1">{k === 'losers' ? 'Hardest hit' : 'Best performers'} · S&P 500 members</div>
              <div className="flex flex-wrap gap-1">
                {a.movers![k].map(m => (
                  <span key={m.symbol} title={`${m.name} · ${m.sector ?? ''}`}
                    className={`text-[10px] tabular-nums px-1.5 py-0.5 rounded border border-white/[0.06] ${k === 'losers' ? 'bg-red-500/10' : 'bg-emerald-500/10'}`}>
                    {m.symbol} <span className={k === 'losers' ? 'text-error' : 'text-success'}>{pct(m.ret_pct, 1)}</span>
                    {m.sector && <span className="text-base-content/35"> · {m.sector.replace('Information Technology', 'Tech').replace('Communication Services', 'Comm')}</span>}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
