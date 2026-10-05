import React, { useMemo, useState } from 'react';
import type { BondKind, BondRow } from '../../types';
import { AXIS, Chart, KIND_META, LEGEND, Note, Seg, TOOLTIP, pct, usd } from './bondUi';

type View = 'map' | 'treemap' | 'maturity' | 'credit' | 'pnl';
const VIEWS: { value: View; label: string; help: string }[] = [
  { value: 'map', label: 'Risk vs return', help: 'Each bubble is a holding: further right = more rate risk (duration), higher = more yield, bigger = more money. The line is today\'s Treasury curve — bubbles above it are paid for credit or inflation risk; below it, you could earn more in a Treasury of the same duration.' },
  { value: 'treemap', label: 'Treemap', help: 'Every holding sized by value, grouped by type, colored by its after-tax yield (green = higher).' },
  { value: 'maturity', label: 'Maturity wall', help: 'Value coming due each year, by type — gaps and spikes show reinvestment risk. Open-ended funds never mature.' },
  { value: 'credit', label: 'Credit × maturity', help: 'Where the money sits by credit quality and maturity (funds split by their credit mix). Bottom-right = most credit and rate risk.' },
  { value: 'pnl', label: 'Gains & losses', help: 'Unrealized gain/loss vs your cost — losses can be harvested to offset gains (taxable accounts).' },
];
const RATINGS = ['GOVT', 'AAA', 'AA', 'A', 'BBB', 'BB', 'B', 'CCC', 'NR'];
const BUCKETS = ['0–1y', '1–3y', '3–5y', '5–10y', '10–20y', '20y+', 'n/a'];   // same buckets as the Overview
const FUND_RATING: Record<string, string> = { us_government: 'GOVT', aaa: 'AAA', aa: 'AA', a: 'A', bbb: 'BBB', bb: 'BB', b: 'B', below_b: 'CCC', other: 'NR' };
const isFund = (r: BondRow) => r.kind === 'etf' || r.kind === 'mutual_fund';
const yrs = (r: BondRow) => r.years_to_maturity ?? null;
const bucketOf = (y: number | null) => y == null ? 'n/a' : y < 1 ? '0–1y' : y < 3 ? '1–3y' : y < 5 ? '3–5y' : y < 10 ? '5–10y' : y < 20 ? '10–20y' : '20y+';

// Credit split of one holding: individual bonds by their rating group; funds by their credit mix (government
// carved out first, like the Overview's credit allocation).
function creditSplit(r: BondRow): [string, number][] {
  const mv = r.market_value || 0;
  const mix = r.fund?.credit_mix ?? {};
  if (isFund(r) && r.fund?.cash_like) return [['GOVT', mv]];          // box-spread / T-bill funds
  if (isFund(r) && Object.keys(mix).length) {
    const gov = Math.min(mix.us_government ?? 0, 1);
    const rest = Object.entries(mix).filter(([k, v]) => k !== 'us_government' && v > 0);
    const tot = rest.reduce((a, [, v]) => a + v, 0);
    const out: [string, number][] = [['GOVT', mv * gov]];
    rest.forEach(([k, v]) => out.push([FUND_RATING[k] ?? 'NR', tot ? mv * (1 - gov) * v / tot : 0]));
    if (!tot) out.push(['NR', mv * (1 - gov)]);
    return out;
  }
  return [[RATINGS.includes(r.rating_group) ? r.rating_group : 'NR', mv]];
}

export default function BondHoldingsVisuals({ rows, curve }: { rows: BondRow[]; curve?: [number, number][] }) {
  const [view, setView] = useState<View>('map');
  const [yBasis, setYBasis] = useState<'total' | 'after_tax'>('total');
  const live = useMemo(() => rows.filter(r => r.status === 'held' && !r.matured && (r.market_value || 0) > 0), [rows]);
  const kinds = useMemo(() => Array.from(new Set(live.map(r => r.kind))) as BondKind[], [live]);

  const option = useMemo(() => {
    const money = (v: number) => usd(v, { compact: true });
    if (view === 'map') {
      const maxMv = Math.max(1, ...live.map(r => r.market_value));
      const yv = (r: BondRow) => (yBasis === 'total' ? (r.total_yield_pct ?? r.ytw_pct) : r.tax?.after_tax_yield_pct) ?? null;
      const maxDur = Math.max(2, ...live.map(r => r.eff_duration ?? 0));
      return {
        tooltip: {
          ...TOOLTIP, trigger: 'item',
          formatter: (p: { seriesName: string; data: [number, number, number, string, number | null, number | null] }) => {
            if (p.seriesName === 'Treasury curve') return `Treasury ${p.data[0]}y: ${p.data[1]?.toFixed(2)}%`;
            const [d, y, mv, label, at, tot] = p.data;
            return `<b>${label}</b><br/>Value ${usd(mv)}<br/>Duration ${d.toFixed(2)}y<br/>Total yield ${pct(tot)} · after tax ${pct(at)}<br/>${yBasis === 'total' ? 'Shown: total' : 'Shown: after-tax'} ${pct(y)}`;
          },
        },
        legend: LEGEND, grid: { left: 8, right: 16, top: 34, bottom: 8, containLabel: true },
        xAxis: { type: 'value', name: 'duration (years) → more rate risk', nameLocation: 'middle', nameGap: 22, min: 0, max: Math.ceil(maxDur + 1), ...AXIS,
          nameTextStyle: { color: '#94a3b8', fontSize: 10 } },
        yAxis: { type: 'value', name: yBasis === 'total' ? 'total yield %' : 'after-tax yield %', ...AXIS, scale: true,
          nameTextStyle: { color: '#94a3b8', fontSize: 10 }, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => `${v.toFixed(1)}%` } },
        series: [
          ...kinds.map(k => ({
            name: KIND_META[k]?.label ?? k, type: 'scatter',
            data: live.filter(r => r.kind === k && r.eff_duration != null && yv(r) != null)
              .map(r => [r.eff_duration, yv(r), r.market_value, r.label, r.tax?.after_tax_yield_pct ?? null, r.total_yield_pct ?? r.ytw_pct ?? null]),
            symbolSize: (d: number[]) => 8 + 38 * Math.sqrt(d[2] / maxMv),
            itemStyle: { color: KIND_META[k]?.color, opacity: 0.8, borderColor: '#0f172a', borderWidth: 1 },
          })),
          ...(curve?.length && yBasis === 'total' ? [{
            name: 'Treasury curve', type: 'line', smooth: true, symbol: 'none', z: 1,
            data: curve.filter(([t]) => t <= maxDur + 1.5).map(([t, y]) => [t, y]),
            lineStyle: { color: '#94a3b8', type: 'dashed', width: 1.5 }, itemStyle: { color: '#94a3b8' },
          }] : []),
        ],
      };
    }
    if (view === 'treemap') {
      const ats = live.map(r => r.tax?.after_tax_yield_pct ?? 0);
      const lo = Math.min(...ats), hi = Math.max(...ats);
      return {
        tooltip: { ...TOOLTIP, formatter: (p: { name: string; value: number; data: { at?: number; tot?: number; dur?: number } }) =>
          p.data?.at != null ? `<b>${p.name}</b><br/>${usd(p.value)}<br/>after-tax ${pct(p.data.at)} · total ${pct(p.data.tot)}<br/>duration ${p.data.dur?.toFixed(1) ?? '—'}y`
            : `<b>${p.name}</b><br/>${usd(p.value)}` },
        visualMap: { type: 'continuous', min: lo, max: hi === lo ? lo + 0.01 : hi, dimension: 1, show: true, orient: 'horizontal', left: 'center', bottom: 0,
          text: ['higher after-tax yield', 'lower'], textStyle: { color: '#94a3b8', fontSize: 10 }, itemHeight: 120, inRange: { color: ['#7f1d1d', '#a16207', '#15803d'] }, seriesIndex: 0 },
        series: [{
          type: 'treemap', roam: false, nodeClick: false, breadcrumb: { show: false }, top: 4, bottom: 36, left: 4, right: 4,
          label: { show: true, formatter: (p: { name: string; data: { at?: number } }) => p.data?.at != null ? `${p.name}\n${pct(p.data.at)}` : p.name, fontSize: 10, color: '#f8fafc', overflow: 'truncate' },
          upperLabel: { show: true, height: 16, color: '#e2e8f0', fontSize: 10 },
          levels: [{ itemStyle: { borderColor: '#0f172a', borderWidth: 2, gapWidth: 2 } }, { itemStyle: { borderColor: '#1e293b', borderWidth: 1, gapWidth: 1 } }],
          data: kinds.map(k => ({
            name: KIND_META[k]?.label ?? k,
            children: live.filter(r => r.kind === k).map(r => ({
              name: r.label, value: [r.market_value, r.tax?.after_tax_yield_pct ?? 0],
              at: r.tax?.after_tax_yield_pct ?? null, tot: r.total_yield_pct ?? r.ytw_pct ?? null, dur: r.eff_duration ?? null,
            })),
          })),
        }],
      };
    }
    if (view === 'maturity') {
      const nowY = new Date().getFullYear();
      const yearOf = (r: BondRow) => (isFund(r) ? (r.defined_maturity_year ?? null) : (r.maturity_year ?? (r.maturity ? Number(r.maturity.slice(0, 4)) : null)));
      const ys = live.map(yearOf).filter((y): y is number => y != null);
      const last = Math.max(nowY, ...ys);
      const cats = [...Array.from({ length: last - nowY + 1 }, (_, i) => String(nowY + i)), 'Funds (no maturity)'];
      return {
        tooltip: { ...TOOLTIP, trigger: 'axis', valueFormatter: (v: number) => usd(v) },
        legend: LEGEND, grid: { left: 8, right: 8, top: 34, bottom: 4, containLabel: true },
        xAxis: { type: 'category', data: cats, ...AXIS }, yAxis: { type: 'value', ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: money } },
        series: kinds.map(k => ({
          name: KIND_META[k]?.label ?? k, type: 'bar', stack: 'm', itemStyle: { color: KIND_META[k]?.color },
          data: cats.map(c => live.filter(r => r.kind === k && (c === 'Funds (no maturity)' ? yearOf(r) == null : String(yearOf(r)) === c))
            .reduce((a, r) => a + r.market_value, 0)),
        })),
      };
    }
    if (view === 'credit') {
      const cells: Record<string, number> = {};
      live.forEach(r => creditSplit(r).forEach(([g, v]) => { const key = `${g}|${bucketOf(yrs(r))}`; cells[key] = (cells[key] ?? 0) + v; }));
      const usedR = RATINGS.filter(g => BUCKETS.some(b => (cells[`${g}|${b}`] ?? 0) > 0.5));
      const usedB = BUCKETS.filter(b => usedR.some(g => (cells[`${g}|${b}`] ?? 0) > 0.5));
      const data = usedR.flatMap((g, yi) => usedB.map((b, xi) => [xi, yi, Math.round(cells[`${g}|${b}`] ?? 0)]));
      const max = Math.max(1, ...data.map(d => d[2]));
      return {
        tooltip: { ...TOOLTIP, formatter: (p: { data: number[] }) => `${usedR[p.data[1]]} · ${usedB[p.data[0]]}<br/><b>${usd(p.data[2])}</b>` },
        grid: { left: 8, right: 8, top: 10, bottom: 40, containLabel: true },
        xAxis: { type: 'category', data: usedB, ...AXIS, name: 'years to maturity →', nameLocation: 'middle', nameGap: 26, nameTextStyle: { color: '#94a3b8', fontSize: 10 } },
        yAxis: { type: 'category', data: usedR, ...AXIS, inverse: true, axisLabel: { ...AXIS.axisLabel, color: '#cbd5e1' } },
        visualMap: { min: 0, max, show: false, inRange: { color: ['#0f172a', '#1e3a8a', '#7c3aed', '#f59e0b'] } },
        series: [{ type: 'heatmap', data, label: { show: true, color: '#f8fafc', fontSize: 10, formatter: (p: { data: number[] }) => (p.data[2] ? money(p.data[2]) : '') },
          itemStyle: { borderColor: '#0f172a', borderWidth: 2, borderRadius: 3 } }],
      };
    }
    // gains & losses
    const withPnl = live.filter(r => r.unrealized_pnl != null).sort((a, b) => (a.unrealized_pnl ?? 0) - (b.unrealized_pnl ?? 0));
    return {
      tooltip: { ...TOOLTIP, trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: (ps: { name: string; value: number; dataIndex: number }[]) => {
          const r = withPnl[ps[0].dataIndex];
          return `<b>${r.label}</b><br/>${usd(r.unrealized_pnl ?? 0, { sign: true })} (${pct(r.unrealized_pnl_pct, 1, true)})<br/>cost ${usd(r.cost_basis ?? null)} · value ${usd(r.market_value)}<br/>${r.account_type.toUpperCase()}`;
        } },
      grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
      xAxis: { type: 'value', ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => usd(v, { compact: true, sign: true }) } },
      yAxis: { type: 'category', data: withPnl.map(r => r.label.length > 26 ? `${r.label.slice(0, 25)}…` : r.label), ...AXIS,
        axisLabel: { ...AXIS.axisLabel, fontSize: 9.5 } },
      series: [{ type: 'bar', data: withPnl.map(r => ({ value: r.unrealized_pnl, itemStyle: { color: (r.unrealized_pnl ?? 0) >= 0 ? '#34d399' : '#f87171' } })), barMaxWidth: 14 }],
    };
  }, [view, live, kinds, curve, yBasis]);

  const totals = useMemo(() => {
    const mv = live.reduce((a, r) => a + r.market_value, 0);
    const gains = live.reduce((a, r) => a + Math.max(0, r.unrealized_pnl ?? 0), 0);
    const losses = live.reduce((a, r) => a + Math.min(0, r.unrealized_pnl ?? 0), 0);
    const belowCurve = live.filter(r => {
      if (!curve?.length || r.eff_duration == null || (r.total_yield_pct ?? r.ytw_pct) == null) return false;
      const d = r.eff_duration;
      const pts = curve.slice().sort((a, b) => a[0] - b[0]);
      const i = pts.findIndex(p => p[0] >= d);
      const tsy = i <= 0 ? pts[0][1] : pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * (d - pts[i - 1][0]) / (pts[i][0] - pts[i - 1][0]);
      return (r.total_yield_pct ?? r.ytw_pct)! < tsy - 0.25 && r.kind !== 'muni' && r.fund?.tax_class !== 'muni';
    });
    return { mv, gains, losses, belowCurve };
  }, [live, curve]);

  if (!live.length) return <p className="px-4 py-6 text-center text-xs text-base-content/50">No held positions to chart{rows.length ? ' with these filters' : ''}.</p>;
  const meta = VIEWS.find(v => v.value === view)!;
  const height = view === 'pnl' ? Math.max(260, 22 * live.filter(r => r.unrealized_pnl != null).length + 30) : view === 'credit' ? 300 : 340;
  return (
    <div className="px-4 py-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <Seg size="xs" value={view} onChange={v => setView(v as View)} options={VIEWS.map(v => ({ value: v.value, label: v.label }))} />
        {view === 'map' && <Seg size="xs" value={yBasis} onChange={v => setYBasis(v as typeof yBasis)} options={[{ value: 'total', label: 'Total yield' }, { value: 'after_tax', label: 'After tax' }]} />}
      </div>
      <Chart option={option} height={height} />
      <div className="mt-2 space-y-0.5">
        <Note>{meta.help}</Note>
        {view === 'map' && totals.belowCurve.length > 0 && (
          <Note>Yielding noticeably less than a Treasury of the same duration: {totals.belowCurve.map(r => r.label).join(', ')} (excluding tax-free munis).</Note>
        )}
        {view === 'pnl' && <Note>Gains {usd(totals.gains, { sign: true })} · losses {usd(totals.losses, { sign: true })} across {live.length} positions ({usd(totals.mv, { compact: true })}).</Note>}
        {view === 'credit' && <Note>Ratings: GOVT = Treasuries, TIPS, FDIC CDs, box-spread / T-bill funds and funds' government share; NR = unrated / unknown.</Note>}
      </div>
    </div>
  );
}
