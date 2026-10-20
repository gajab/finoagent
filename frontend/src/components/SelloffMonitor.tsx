import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import {
  ShieldAlert, Loader2, RefreshCw, AlertCircle, CheckCircle2, Circle, Eye, CalendarClock,
  ChevronDown, ChevronUp, Info, History, Activity, Crosshair, Users, Layers,
} from 'lucide-react';
import { fetchSelloffMonitor, fetchSelloffInternals } from '../api';
import { AnatomyPanel, BreadthPanel } from './SelloffInternals';
import SelloffBook from './SelloffBook';
import SelloffEvidencePanel from './SelloffEvidence';
import type {
  SelloffMonitorData, SelloffInternalsData, SelloffStressRow, SelloffStressCell, SelloffTell, SelloffTone, SelloffHorizonStats, SelloffLevel,
} from '../types';

// ---------------------------------------------------------------------------
// formatting
// ---------------------------------------------------------------------------

const sgn = (v: number) => (v > 0 ? '+' : v < 0 ? '−' : '');
const pct = (v: number | null | undefined, d = 2) => (v == null ? '—' : `${sgn(v)}${Math.abs(v).toFixed(d)}%`);
const sig = (v: number | null | undefined) => (v == null ? '—' : `${sgn(v)}${Math.abs(v).toFixed(1)}σ`);
const usd = (v: number | null | undefined) => (v == null ? '—' : `$${v.toFixed(2)}`);
const dayLabel = (iso: string) => {
  const d = new Date(`${iso}T12:00:00`);
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
};
const shortDay = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short', day: 'numeric' });

const TONE_TEXT: Record<SelloffTone, string> = {
  stress: 'text-error', watch: 'text-warning', calm: 'text-success',
};
const TONE_BOX: Record<SelloffTone, string> = {
  stress: 'border-error/30 bg-error/10', watch: 'border-warning/30 bg-warning/10', calm: 'border-success/30 bg-success/10',
};
const TONE_DOT: Record<SelloffTone, string> = { stress: 'bg-error', watch: 'bg-warning', calm: 'bg-success' };

// chart palette (dark UI)
const C = {
  price: '#e5e7eb', hist: '#818cf8', opt: '#f59e0b', recovery: '#34d399', continuation: '#f87171',
  trend: '#94a3b8', dealer: '#c084fc', futures: '#38bdf8',
};

// ---------------------------------------------------------------------------
// small pieces
// ---------------------------------------------------------------------------

function Chip({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: string }) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-base-200/30 px-3 py-2 min-w-0">
      <div className="text-[9px] uppercase tracking-wide text-base-content/40 truncate">{label}</div>
      <div className={`text-sm font-bold tabular-nums ${tone || 'text-base-content/85'}`}>{value}</div>
      {sub && <div className="text-[10px] text-base-content/45 truncate">{sub}</div>}
    </div>
  );
}

function SectionTitle({ icon, title, sub }: { icon: React.ReactNode; title: string; sub?: string }) {
  return (
    <div className="flex items-start gap-2 mb-3">
      <span className="text-primary mt-0.5">{icon}</span>
      <div>
        <h3 className="text-sm font-semibold">{title}</h3>
        {sub && <p className="text-[11px] text-base-content/45 leading-snug">{sub}</p>}
      </div>
    </div>
  );
}

function stressClass(row: SelloffStressRow, cell: Pick<SelloffStressCell, 'z' | 'stress' | 'value'> | null): string {
  if (!cell) return '';
  if (row.kind === 'ratio') {
    const v = cell.value;
    if (v == null) return '';
    return v >= 1 ? 'bg-red-500/40' : v >= 0.9 ? 'bg-amber-500/25' : 'bg-emerald-500/15';
  }
  if (row.stress_sign !== 0) {
    const s = cell.stress;
    if (s == null) return '';
    if (s >= 2) return 'bg-red-500/40';
    if (s >= 1) return 'bg-red-500/20';
    if (s <= -2) return 'bg-emerald-500/30';
    if (s <= -1) return 'bg-emerald-500/15';
    return '';
  }
  const z = Math.abs(cell.z ?? 0);
  return z >= 2 ? 'bg-sky-500/30' : z >= 1 ? 'bg-sky-500/15' : '';
}

function fmtChange(row: SelloffStressRow, change: number | null, value: number | null): string {
  if (row.kind === 'ratio') return value == null ? '—' : value.toFixed(2);
  if (change == null) return '—';
  if (row.kind === 'pct') return pct(change, 2);
  if (row.kind === 'bp') return `${sgn(change)}${Math.abs(change).toFixed(0)}bp`;
  return `${sgn(change)}${Math.abs(change).toFixed(1)}`;
}

function cellTitle(row: SelloffStressRow, c: SelloffStressCell | null, when: string): string {
  if (!c) return '';
  const parts = [`${row.label} · ${when}`];
  if (c.value != null) parts.push(`level ${c.value}`);
  if (c.z != null) parts.push(`z ${c.z.toFixed(2)} vs. own volatility`);
  if (c.change == null && row.kind !== 'ratio') parts.push('no new print for this session');
  return parts.join(' · ');
}

// ---------------------------------------------------------------------------
// fan chart
// ---------------------------------------------------------------------------

type LevelGroup = 'key' | 'dealer' | 'trend';
const LEVEL_GROUP: Record<SelloffLevel['kind'], LevelGroup> = {
  recovery: 'key', continuation: 'key', dealer: 'dealer', trend: 'trend',
};

function useFanOption(d: SelloffMonitorData | null, show: Record<LevelGroup, boolean>) {
  return useMemo(() => {
    if (!d?.chart?.history?.length) return null;
    const ch = d.chart;
    const histDates = ch.history.map(p => p.date);
    const x = [...histDates, ...ch.forward_dates];
    const a = histDates.length - 1;                          // anchor index (last close)
    const spot = ch.anchor.close;
    const blank = () => new Array<number | null>(x.length).fill(null);

    const price = blank();
    ch.history.forEach((p, i) => { price[i] = p.close; });

    const series: any[] = [];
    const band = (fan: NonNullable<typeof ch.history_fan>, lo: 'p10' | 'p25', hi: 'p90' | 'p75', stack: string, color: string, opacity: number, name: string) => {
      const base = blank(); const width = blank();
      base[a] = spot; width[a] = 0;
      fan.forEach(f => { base[a + f.h] = f[lo]; width[a + f.h] = +(f[hi] - f[lo]).toFixed(2); });
      series.push({ name: `${name} base`, type: 'line', data: base, stack, symbol: 'none', lineStyle: { opacity: 0 }, silent: true, tooltip: { show: false } });
      series.push({ name, type: 'line', data: width, stack, symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color, opacity }, silent: true, tooltip: { show: false } });
    };
    const line = (fan: NonNullable<typeof ch.history_fan>, q: 'p10' | 'p25' | 'p50' | 'p75' | 'p90', name: string, color: string, type: 'solid' | 'dashed' | 'dotted', width = 1.2) => {
      const v = blank(); v[a] = spot;
      fan.forEach(f => { v[a + f.h] = f[q]; });
      series.push({ name, type: 'line', data: v, symbol: 'none', lineStyle: { color, type, width }, itemStyle: { color } });
    };

    if (ch.history_fan) {
      band(ch.history_fan, 'p10', 'p90', 'h1090', C.hist, 0.14, 'History P10–P90');
      band(ch.history_fan, 'p25', 'p75', 'h2575', C.hist, 0.22, 'History P25–P75');
      line(ch.history_fan, 'p50', 'History median', C.hist, 'solid', 2);
    }
    if (ch.options_fan) {
      line(ch.options_fan, 'p90', 'Options P90', C.opt, 'dotted');
      line(ch.options_fan, 'p75', 'Options P75', C.opt, 'dashed');
      line(ch.options_fan, 'p50', 'Options median', C.opt, 'solid', 1);
      line(ch.options_fan, 'p25', 'Options P25', C.opt, 'dashed');
      line(ch.options_fan, 'p10', 'Options P10', C.opt, 'dotted');
    }

    // y range: everything visible, padded
    const shown = ch.levels.filter(l => show[LEVEL_GROUP[l.kind]]).sort((p, q) => p.price - q.price);
    const vals: number[] = [...ch.history.map(p => p.close)];
    for (const f of [...(ch.history_fan || []), ...(ch.options_fan || [])]) vals.push(f.p10, f.p90);
    shown.forEach(l => vals.push(l.price));
    const lo = Math.min(...vals), hi = Math.max(...vals), pad = (hi - lo) * 0.04;

    // levels closer than ~4% of the axis would overprint: one label per cluster, every line still drawn
    const levelColor = (l: SelloffLevel) => (l.kind === 'recovery' ? C.recovery : l.kind === 'continuation' ? C.continuation
      : l.kind === 'dealer' ? C.dealer : C.trend);
    const clusters: SelloffLevel[][] = [];
    for (const l of shown) {
      const last = clusters[clusters.length - 1];
      if (last && l.price - last[last.length - 1].price < (hi - lo) * 0.04) last.push(l); else clusters.push([l]);
    }
    const marks = clusters.flatMap(cl => cl.map((l, i) => ({
      yAxis: l.price, name: l.label,
      lineStyle: { color: levelColor(l), type: l.kind === 'trend' ? 'dotted' : 'dashed', width: 1 },
      label: i === cl.length - 1
        ? { formatter: cl.map(m => `${m.label} ${m.price.toFixed(2)}`).reverse().join('  ·  '), color: levelColor(l),
            fontSize: 9, position: 'insideStartTop' }
        : { show: false },
    })));
    series.unshift({
      name: 'SPY', type: 'line', data: price, symbol: 'none', lineStyle: { color: C.price, width: 1.6 }, itemStyle: { color: C.price },
      markLine: { symbol: 'none', silent: true, data: marks },
      markArea: { silent: true, itemStyle: { color: 'rgba(255,255,255,0.025)' }, data: [[{ xAxis: x[a] }, { xAxis: x[x.length - 1] }]] },
    });
    if (ch.spy_implied_open != null && ch.forward_dates.length) {
      const v = blank(); v[a + 1] = ch.spy_implied_open;
      series.push({ name: 'Futures-implied', type: 'scatter', data: v, symbolSize: 9, itemStyle: { color: C.futures },
        label: { show: true, position: 'right', formatter: 'futures', color: C.futures, fontSize: 9 } });
    }

    return {
      backgroundColor: 'transparent',
      animation: false,
      grid: { left: 52, right: 16, top: 16, bottom: 36 },
      tooltip: {
        trigger: 'axis', backgroundColor: 'rgba(17,24,39,0.95)', borderColor: 'rgba(255,255,255,0.1)',
        textStyle: { color: '#e5e7eb', fontSize: 11 },
        formatter: (ps: any[]) => {
          const i = ps[0]?.dataIndex ?? 0;
          const rows: string[] = [`<b>${dayLabel(x[i])}</b>${i > a ? ` · session +${i - a}` : ''}`];
          if (price[i] != null) rows.push(`SPY ${usd(price[i])}`);
          const h = i - a;
          const hf = ch.history_fan?.find(f => f.h === h);
          const of = ch.options_fan?.find(f => f.h === h);
          if (hf) rows.push(`<span style="color:${C.hist}">History</span> P10 ${usd(hf.p10)} · median ${usd(hf.p50)} · P90 ${usd(hf.p90)}`);
          if (of) rows.push(`<span style="color:${C.opt}">Options</span> P10 ${usd(of.p10)} · median ${usd(of.p50)} · P90 ${usd(of.p90)}`);
          if (h === 1 && ch.spy_implied_open != null) rows.push(`<span style="color:${C.futures}">Futures-implied</span> ${usd(ch.spy_implied_open)}`);
          return rows.join('<br/>');
        },
      },
      xAxis: {
        type: 'category', data: x, boundaryGap: false,
        axisLabel: { color: '#9ca3af', fontSize: 10, formatter: (v: string) => v.slice(5) },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.15)' } },
      },
      yAxis: {
        type: 'value', scale: true, min: +(lo - pad).toFixed(0), max: +(hi + pad).toFixed(0),
        axisLabel: { color: '#9ca3af', fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
      },
      series,
    };
  }, [d, show]);
}

// ---------------------------------------------------------------------------
// sub-panels
// ---------------------------------------------------------------------------

function OddsRow({ label, st, filt, trackSkill }: { label: string; st: SelloffHorizonStats; filt?: number | null; trackSkill: boolean }) {
  const tag = st.significant && !trackSkill
    ? <span className="badge badge-xs badge-ghost border-warning/40 text-warning/80"
        title={`Holm-adjusted p = ${st.p_holm} in these analogs, but the engine's out-of-sample record shows no skill`}>in-sample only</span>
    : st.significant
    ? <span className={`badge badge-xs ${st.edge_pts > 0 ? 'badge-success' : 'badge-error'}`}
        title={`Holm-adjusted p = ${st.p_holm}, z = ${st.z}`}>{st.strength === 'strong' ? 'strong' : 'suggestive'}</span>
    : <span className="badge badge-xs badge-ghost" title={st.p_holm != null ? `Holm-adjusted p = ${st.p_holm}, z = ${st.z}` : ''}>within noise</span>;
  const ci = st.p_up_ci_episode ?? st.p_up_ci;
  return (
    <tr className="border-t border-white/[0.04]">
      <td className="py-1.5 pr-2 text-base-content/60">{label}</td>
      <td className="py-1.5 pr-2 tabular-nums font-semibold">{st.p_up.toFixed(0)}%</td>
      <td className="py-1.5 pr-2 tabular-nums text-base-content/50" title={st.p_up_ci_episode ? 'Resampling whole episodes (analogs cluster in a few crises)' : 'Wilson interval'}>
        {ci[0].toFixed(0)}–{ci[1].toFixed(0)}%</td>
      <td className="py-1.5 pr-2 tabular-nums text-base-content/60">{st.baseline_p_up.toFixed(0)}%</td>
      <td className="py-1.5 pr-2 tabular-nums text-base-content/60">{filt != null ? `${filt.toFixed(0)}%` : '—'}</td>
      <td className="py-1.5 pr-2 tabular-nums text-primary/90">{st.p_up_shrunk != null ? `${st.p_up_shrunk.toFixed(0)}%` : '—'}</td>
      <td className="py-1.5 pr-2 tabular-nums">{pct(st.median_pct)}</td>
      <td className="py-1.5 pr-2 tabular-nums text-error/80">{pct(st.p10_pct)}</td>
      <td className="py-1.5">{tag}</td>
    </tr>
  );
}

function Compare({ label, now, base, ci, unit = '%' }: { label: string; now: number; base: number | null; ci?: [number, number]; unit?: string }) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-base-200/30 px-3 py-2">
      <div className="text-[10px] text-base-content/50 leading-snug">{label}</div>
      <div className="flex items-baseline gap-2 mt-0.5">
        <span className="text-base font-bold tabular-nums">{now.toFixed(0)}{unit}</span>
        {ci && <span className="text-[10px] text-base-content/40 tabular-nums">({ci[0].toFixed(0)}–{ci[1].toFixed(0)})</span>}
        {base != null && <span className="text-[10px] text-base-content/50">vs {base.toFixed(0)}{unit} base</span>}
      </div>
    </div>
  );
}

function HistoryPanel({ d }: { d: SelloffMonitorData }) {
  const [showMatches, setShowMatches] = useState(false);
  const an = d.analogs;
  if (!an.available || !an.stats || !an.path) {
    return <div className="text-xs text-base-content/50">{an.reason || 'Historical analogs unavailable.'}</div>;
  }
  const H = an.horizon ?? 20;
  const p = an.path;
  const fp = p.first_passage;
  const cal = an.calibration;
  const badge = cal?.skill?.h5?.badge;
  const sk = cal?.skill?.h5?.stress;
  const BADGE: Record<string, [string, string]> = {
    skill: ['badge-success', 'Has beaten the base rate'], no_skill: ['badge-ghost', 'No demonstrated skill vs the base rate'],
    worse: ['badge-error', 'Has done worse than the base rate'], insufficient: ['badge-ghost', 'Not enough history to score'],
  };
  return (
    <div className="space-y-3">
      {cal?.available && badge && sk && (
        <div className="rounded-lg border border-white/[0.08] bg-base-200/40 px-3 py-2 text-[11px] space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[9px] uppercase tracking-wide text-base-content/40">Track record</span>
            <span className={`badge badge-sm ${BADGE[badge][0]}`}>{BADGE[badge][1]}</span>
            <span className="text-base-content/60 tabular-nums">
              5-session odds after selloffs, {cal.evaluated_on}: Brier skill {sk.bss_raw != null ? `${(sk.bss_raw * 100).toFixed(1)}%` : '—'}
              {sk.lo != null && sk.hi != null && ` (95% CI ${(sk.lo * 100).toFixed(1)}% to ${(sk.hi * 100).toFixed(1)}%)`} · {sk.n} test origins
            </span>
          </div>
          <div className="text-base-content/50">
            Replayed with no hindsight. {cal.m_is_inf
              ? 'The best-tuned prior puts all its weight on the base rate, so the best estimate below equals it — the analog odds are context, not a forecast.'
              : `Best estimate = analog odds shrunk toward the base rate (prior weight ${cal.m}, tuned on ${cal.tuned_on}).`}
          </div>
        </div>
      )}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-base-content/50">
        {an.episodes && <span>{an.n} analogs from <b className="text-base-content/70">{an.episodes.n} distinct episodes</b> ({Object.entries(an.episodes.decades).map(([d, c]) => `${d} ${c}`).join(' · ')})</span>}
        {an.era && <span title="Daily index autocorrelation turned negative around 1999 (Baltussen, van Bekkum & Da 2019)">
          Higher after 5 sessions: before 2000 {an.era.pre_2000.p_up_h5 ?? '—'}% (n={an.era.pre_2000.n}) · since 2000 {an.era.from_2000.p_up_h5 ?? '—'}% (n={an.era.from_2000.n})</span>}
        {an.quality?.percentile != null && an.quality.percentile >= 90 && (
          <span className="text-warning/90">Few close precedents: today's match quality is worse than {an.quality.percentile.toFixed(0)}% of past days.</span>)}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-[9px] uppercase tracking-wide text-base-content/40 text-left">
              <th className="pb-1 pr-2 font-medium">Horizon</th>
              <th className="pb-1 pr-2 font-medium">Higher</th>
              <th className="pb-1 pr-2 font-medium">95% range</th>
              <th className="pb-1 pr-2 font-medium">Any day</th>
              <th className="pb-1 pr-2 font-medium" title="Every day whose 3-session move was at least as extreme as today's">Same-size drop</th>
              <th className="pb-1 pr-2 font-medium" title="Shrunk toward the base rate by the walk-forward-tuned prior">Best estimate</th>
              <th className="pb-1 pr-2 font-medium">Median</th>
              <th className="pb-1 pr-2 font-medium">Bad case (P10)</th>
              <th className="pb-1 font-medium" />
            </tr>
          </thead>
          <tbody>
            <OddsRow label="Next session" st={an.stats.h1} filt={an.filter_baseline?.h1?.p_up} trackSkill={!cal?.available || badge === 'skill'} />
            <OddsRow label="5 sessions" st={an.stats.h5} filt={an.filter_baseline?.h5?.p_up} trackSkill={!cal?.available || badge === 'skill'} />
            <OddsRow label={`${H} sessions`} st={an.stats[`h${H}`]} filt={an.filter_baseline?.[`h${H}`]?.p_up} trackSkill={!cal?.available || badge === 'skill'} />
          </tbody>
        </table>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        <Compare label="Traded below the last session's low within 5 sessions" now={p.p_new_low_5d} ci={p.p_new_low_5d_ci} base={p.baseline_p_new_low_5d} />
        <Compare label={`Fully recovered the ${d.window_sessions}-session move within ${H} sessions`} now={p.p_reclaim_pre_move_20d} ci={p.p_reclaim_pre_move_20d_ci} base={p.baseline_p_reclaim_pre_move_20d} />
        <div className="rounded-lg border border-white/[0.06] bg-base-200/30 px-3 py-2">
          <div className="text-[10px] text-base-content/50 leading-snug">Next ±{fp.barrier_pct.toFixed(2)}% move (closing basis): which came first</div>
          <div className="flex h-2 rounded overflow-hidden mt-1.5">
            <div className="bg-success/70" style={{ width: `${fp.up_first}%` }} title={`Up first ${fp.up_first}%`} />
            <div className="bg-error/70" style={{ width: `${fp.down_first}%` }} title={`Down first ${fp.down_first}%`} />
            <div className="bg-base-content/20" style={{ width: `${fp.neither}%` }} title={`Neither ${fp.neither}%`} />
          </div>
          <div className="text-[10px] mt-1 tabular-nums">
            <span className="text-success">Up {fp.up_first.toFixed(0)}%</span> · <span className="text-error">Down {fp.down_first.toFixed(0)}%</span>
            <span className="text-base-content/40"> · base {fp.baseline_up_first.toFixed(0)}/{fp.baseline_down_first.toFixed(0)}</span>
          </div>
        </div>
      </div>
      <div className="text-[10px] text-base-content/45">
        Worst close in the next {H} sessions: median {pct(p.worst_close_20d_median_pct, 1)}, bad case {pct(p.worst_close_20d_p10_pct, 1)}
        {p.baseline_worst_close_20d_median_pct != null && <> (any day: {pct(p.baseline_worst_close_20d_median_pct, 1)} / {pct(p.baseline_worst_close_20d_p10_pct, 1)})</>}.
      </div>
      <button className="btn btn-xs btn-ghost gap-1 px-1" onClick={() => setShowMatches(v => !v)}>
        {showMatches ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        Closest historical matches
      </button>
      {showMatches && an.matches && (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] tabular-nums">
            <thead>
              <tr className="text-[9px] uppercase tracking-wide text-base-content/40 text-left">
                <th className="pb-1 pr-2 font-medium">Date</th><th className="pb-1 pr-2 font-medium">3-session</th>
                <th className="pb-1 pr-2 font-medium">vs 200d</th><th className="pb-1 pr-2 font-medium">Off high</th>
                <th className="pb-1 pr-2 font-medium">VIX</th><th className="pb-1 pr-2 font-medium">+5</th>
                <th className="pb-1 pr-2 font-medium">+{H}</th><th className="pb-1 font-medium">Worst</th>
              </tr>
            </thead>
            <tbody>
              {an.matches.map(m => (
                <tr key={m.date} className="border-t border-white/[0.04]">
                  <td className="py-1 pr-2 text-base-content/70">{m.date}</td>
                  <td className="py-1 pr-2">{pct(m.ret3_pct)} <span className="text-base-content/40">({sig(m.move3_sigma)})</span></td>
                  <td className="py-1 pr-2">{pct(m.dist_200d, 1)}</td>
                  <td className="py-1 pr-2">{pct(m.drawdown_52w, 1)}</td>
                  <td className="py-1 pr-2">{m.vix.toFixed(1)}</td>
                  <td className={`py-1 pr-2 ${m.fwd5_pct >= 0 ? 'text-success' : 'text-error'}`}>{pct(m.fwd5_pct)}</td>
                  <td className={`py-1 pr-2 ${m.fwd20_pct >= 0 ? 'text-success' : 'text-error'}`}>{pct(m.fwd20_pct)}</td>
                  <td className="py-1 text-error/80">{pct(m.worst20_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-[10px] text-base-content/40 leading-snug">
        {an.method} {an.n} matches, {an.since} → {an.through}. {p.baseline_note}
      </p>
    </div>
  );
}

function StressGrid({ d }: { d: SelloffMonitorData }) {
  const st = d.stress;
  const dg = st.diagnosis;
  const hasNow = st.groups.some(g => g.rows.some(r => r.now));
  const winStart = st.sessions.length - st.window_sessions;
  return (
    <div className="space-y-3">
      <div className={`rounded-lg border px-3 py-2 ${TONE_BOX[dg.tone]}`}>
        <div className={`text-sm font-semibold ${TONE_TEXT[dg.tone]}`}>{dg.label}</div>
        <div className="text-[11px] text-base-content/70">{dg.summary}</div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
          {dg.points.map(p => (
            <span key={p.lens} className="text-[11px] text-base-content/70 inline-flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${TONE_DOT[p.tone]}`} />
              <span className="text-base-content/45">{p.lens}:</span> {p.value}
            </span>
          ))}
        </div>
        <div className="text-[10px] text-base-content/40 mt-1.5 flex gap-1"><Info className="w-3 h-3 shrink-0 mt-px" />{dg.rules}</div>
      </div>
      {st.groups.length === 0 ? (
        <div className="text-xs text-base-content/50">Cross-asset data unavailable.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] tabular-nums">
            <thead>
              <tr className="text-[9px] uppercase tracking-wide text-base-content/40">
                <th className="text-left font-medium pb-1 pr-2">Asset</th>
                {st.sessions.map((s, i) => (
                  <th key={s} className={`font-medium pb-1 px-1 text-right ${i >= winStart ? 'text-base-content/60' : ''}`}>{shortDay(s)}</th>
                ))}
                <th className="font-medium pb-1 px-1 text-right text-base-content/60">{st.window_sessions}-session</th>
                {hasNow && <th className="font-medium pb-1 pl-1 text-right text-sky-400/80">Since close</th>}
              </tr>
            </thead>
            <tbody>
              {st.groups.map(g => (
                <React.Fragment key={g.group}>
                  <tr><td colSpan={st.sessions.length + 3} className="pt-2 pb-0.5 text-[9px] uppercase tracking-widest text-base-content/30">{g.group}</td></tr>
                  {g.rows.map(r => (
                    <tr key={r.symbol} className="border-t border-white/[0.03]">
                      <td className="py-0.5 pr-2 text-base-content/75 whitespace-nowrap" title={r.note || r.symbol}>
                        {r.label}{r.stress_sign === 0 && r.kind !== 'ratio' && <span className="text-base-content/30"> ·ctx</span>}
                      </td>
                      {r.cells.map((c, i) => (
                        <td key={c.date} title={cellTitle(r, c, dayLabel(c.date))}
                          className={`py-0.5 px-1 text-right rounded-sm ${stressClass(r, c)} ${i >= winStart ? '' : 'opacity-70'}`}>
                          {fmtChange(r, c.change, c.value)}
                        </td>
                      ))}
                      <td title={r.window.z != null ? `z ${r.window.z.toFixed(2)}` : ''}
                        className={`py-0.5 px-1 text-right font-semibold rounded-sm ${r.kind === 'ratio' ? '' : stressClass(r, { ...r.window, value: null })}`}>
                        {r.kind === 'ratio' ? '' : fmtChange(r, r.window.change, null)}
                      </td>
                      {hasNow && (
                        <td title={r.now ? cellTitle(r, r.now, r.now.date) : ''}
                          className={`py-0.5 pl-1 text-right rounded-sm ${stressClass(r, r.now)}`}>
                          {r.now ? fmtChange(r, r.now.change, r.now.value) : ''}
                        </td>
                      )}
                    </tr>
                  ))}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-[10px] text-base-content/40 leading-snug">
        Cells show each session's move; shading is the move in σ of that asset's own prior {st.z_lookback}-session volatility
        (red = stress direction, green = relief, blue = large move in a context asset such as Treasuries, gold or the dollar).
        "Since close" covers futures, FX, crypto and commodities that trade after the equity close.
      </p>
    </div>
  );
}

const EVIDENCE_CLS: Record<string, string> = {
  strong: 'border-success/40 text-success/80', moderate: 'border-info/40 text-info/80', weak: 'border-warning/40 text-warning/80',
  folklore: 'border-white/15 text-base-content/45', descriptive: 'border-white/10 text-base-content/35',
};

function TellRow({ t }: { t: SelloffTell }) {
  const icon = t.status === 'met'
    ? <CheckCircle2 className={`w-4 h-4 ${t.side === 'continuation' ? 'text-error' : 'text-success'}`} />
    : t.status === 'watch' ? <Eye className="w-4 h-4 text-base-content/40" /> : <Circle className="w-4 h-4 text-base-content/25" />;
  const metric = t.level != null
    ? <>{usd(t.level)}{t.distance_pct != null && <span className="text-base-content/40"> ({pct(t.distance_pct, 1)} away)</span>}</>
    : t.value != null ? <>now {t.value}{t.unit ?? ''}{t.threshold != null && <span className="text-base-content/40"> · trigger {t.threshold}{t.unit ?? ''}</span>}</> : null;
  return (
    <li className="flex gap-2 py-1.5 border-t border-white/[0.04] first:border-t-0">
      <span className="mt-0.5 shrink-0" title={t.status === 'met' ? 'Active now' : t.status === 'watch' ? 'Watch at the open' : 'Not active'}>{icon}</span>
      <div className="min-w-0">
        <div className="text-[12px] font-medium text-base-content/85">{t.label}</div>
        {metric && <div className="text-[11px] tabular-nums text-base-content/65">{metric}</div>}
        <div className="text-[10px] text-base-content/45 leading-snug">{t.detail} <span className="text-base-content/30">· {t.basis}</span></div>
        {t.evidence && (
          <span title={t.evidence.note}
            className={`inline-block mt-0.5 text-[9px] uppercase tracking-wide px-1.5 py-px rounded border ${EVIDENCE_CLS[t.evidence.grade] ?? 'border-white/10 text-base-content/40'}`}>
            evidence: {t.evidence.grade}
          </span>
        )}
      </div>
    </li>
  );
}

// ONE counter for tells: the panel headers and the read's summary line both use it.
function tellCount(ts: SelloffTell[]): { met: number; checkable: number } {
  return { met: ts.filter(t => t.status === 'met').length, checkable: ts.filter(t => t.status !== 'watch').length };
}

function tellsLine(all: SelloffTell[]): string {
  const c = tellCount(all.filter(t => t.side === 'continuation'));
  const r = tellCount(all.filter(t => t.side === 'recovery'));
  const fut = all.some(t => t.basis === 'futures-implied SPY');
  return `${c.met} of ${c.checkable} checkable continuation tells and ${r.met} of ${r.checkable} checkable recovery tells `
    + `are active now${fut ? ' (price tells use the futures-implied SPY level)' : ''}.`;
}

function Tells({ d, extra }: { d: SelloffMonitorData; extra: SelloffTell[] }) {
  const all = [...d.tells, ...extra];
  const cont = all.filter(t => t.side === 'continuation');
  const rec = all.filter(t => t.side === 'recovery');
  const count = (ts: SelloffTell[]) => { const c = tellCount(ts); return `${c.met}/${c.checkable} active`; };
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      <div className="rounded-lg border border-error/20 bg-error/[0.04] p-3">
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs font-semibold text-error">Selloff continues if…</span>
          <span className="text-[10px] text-base-content/45">{count(cont)}</span>
        </div>
        <ul>{cont.map(t => <TellRow key={t.id} t={t} />)}</ul>
      </div>
      <div className="rounded-lg border border-success/20 bg-success/[0.04] p-3">
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs font-semibold text-success">Recovery confirms if…</span>
          <span className="text-[10px] text-base-content/45">{count(rec)}</span>
        </div>
        <ul>{rec.map(t => <TellRow key={t.id} t={t} />)}</ul>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

export default function SelloffMonitor() {
  const [data, setData] = useState<SelloffMonitorData | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [show, setShow] = useState<Record<LevelGroup, boolean>>({ key: true, dealer: false, trend: false });
  const [internals, setInternals] = useState<SelloffInternalsData | null>(null);
  const [iLoading, setILoading] = useState(false);
  const [iErr, setIErr] = useState<string | null>(null);
  const started = useRef(false);

  // Phase 2 scans ~500 stocks (~20 s cold), so it loads after the main read is on screen.
  const loadInternals = useCallback((refresh = false) => {
    setILoading(true); setIErr(null);
    fetchSelloffInternals(refresh)
      .then(r => {
        if (!r.available) { setIErr(r.reason || 'Market internals unavailable right now.'); return; }
        setInternals(r);
      })
      .catch((e: unknown) => setIErr(e instanceof Error ? e.message : 'Failed to load market internals'))
      .finally(() => setILoading(false));
  }, []);

  const load = useCallback((refresh = false) => {
    setLoading(true); setErr(null);
    fetchSelloffMonitor(refresh)
      .then(r => {
        if (!r.available) { setErr(r.reason || 'Selloff monitor unavailable right now.'); return; }
        setData(r);
        loadInternals(refresh);
      })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : 'Failed to load the selloff monitor'))
      .finally(() => setLoading(false));
  }, [loadInternals]);

  useEffect(() => {
    if (started.current) return;                     // StrictMode double-mount guard
    started.current = true;
    load(false);
  }, [load]);

  const option = useFanOption(data, show);

  const header = (
    <div className="flex items-start justify-between gap-3 mb-4">
      <div className="flex items-start gap-2">
        <ShieldAlert className="w-5 h-5 text-primary mt-0.5" />
        <div>
          <h2 className="text-lg font-semibold">Selloff Monitor</h2>
          <p className="text-[11px] text-base-content/50">
            Continuation or recovery? Base rates, market-implied ranges and the levels to watch at the next open.
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {data && <span className="text-[10px] text-base-content/40 hidden sm:inline">as of {data.as_of}</span>}
        <button className="btn btn-xs btn-ghost gap-1.5" onClick={() => load(true)} disabled={loading || iLoading}>
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>
    </div>
  );

  if (!data) {
    return (
      <div>
        {header}
        {loading && <div className="flex items-center gap-2 text-sm text-base-content/60 py-10 justify-center"><Loader2 className="w-4 h-4 animate-spin" /> Reading the tape — index history, cross-asset moves, option chains…</div>}
        {err && <div className="alert alert-error text-sm"><AlertCircle className="w-4 h-4" />{err}</div>}
      </div>
    );
  }

  const s = data.state;
  const dealer = s.dealer;
  const es = data.overnight.es;
  const vixTone = s.vix_ratio == null ? undefined : s.vix_ratio >= 1 ? 'text-error' : s.vix_ratio >= 0.9 ? 'text-warning' : 'text-success';
  const conePts = data.options_cone.points || [];

  return (
    <div className="space-y-6">
      {header}
      {err && <div className="alert alert-warning text-xs py-2"><AlertCircle className="w-4 h-4" />{err} — showing the last good read.</div>}

      {/* ---- the read ---- */}
      <div className="rounded-xl border border-white/[0.08] bg-base-200/40 p-4">
        <div className="text-[10px] uppercase tracking-widest text-base-content/40 mb-2">The read · {dayLabel(data.last_session)} close</div>
        <ul className="space-y-1.5">
          {[...data.read, ...(internals?.read ?? []), tellsLine([...data.tells, ...(internals?.tells ?? [])])].map((line, i) => (
            <li key={i} className="text-[12.5px] leading-relaxed text-base-content/80 flex gap-2">
              <span className="text-primary/60 mt-[3px]">•</span><span>{line}</span>
            </li>
          ))}
          {iLoading && !internals && (
            <li className="text-[12px] text-base-content/45 flex gap-2 items-center">
              <Loader2 className="w-3 h-3 animate-spin" /> Scanning S&P 500 members for breadth and what got sold…
            </li>
          )}
          {iErr && !internals && <li className="text-[11px] text-warning/80">Market internals: {iErr}</li>}
        </ul>
      </div>

      {/* ---- state strip ---- */}
      <div>
        <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-2">
          <Chip label={`S&P 500 · ${data.window_sessions} sessions`} value={pct(s.ret3_pct)} sub={`${sig(s.move3_sigma)} vs normal`}
            tone={(s.ret3_pct ?? 0) < 0 ? 'text-error' : 'text-success'} />
          <Chip label="Last session" value={pct(s.ret1_pct)} sub={`${sig(s.move1_sigma)} · ${dayLabel(s.index_date)}`}
            tone={(s.ret1_pct ?? 0) < 0 ? 'text-error' : 'text-success'} />
          <Chip label="From 52-week high" value={pct(s.drawdown_52w, 1)} sub={`5 sessions ${pct(s.ret5_pct, 1)}`} />
          <Chip label="Trend" value={`${pct(s.dist_200d, 1)} vs 200d`} sub={`${pct(s.dist_50d, 1)} vs 50d`}
            tone={(s.dist_200d ?? 0) >= 0 ? 'text-success' : 'text-error'} />
          <Chip label="VIX · curve" value={s.vix != null ? s.vix.toFixed(2) : '—'}
            sub={s.vix_ratio != null ? <span className={vixTone}>VIX/VIX3M {s.vix_ratio.toFixed(2)}</span> : undefined} />
          <Chip label="High-yield spread" value={s.hy_oas != null ? `${s.hy_oas.toFixed(2)}%` : '—'}
            sub={s.hy_oas_window_bp != null ? `${sgn(s.hy_oas_window_bp)}${Math.abs(s.hy_oas_window_bp).toFixed(0)}bp · thru ${s.hy_oas_as_of ?? ''}` : undefined}
            tone={(s.hy_oas_window_bp ?? 0) >= 15 ? 'text-error' : undefined} />
          <Chip label="Dealer gamma (est.)" value={dealer?.sign ? (dealer.sign === 'short' ? 'Short' : 'Long') : '—'}
            sub={dealer?.flip != null ? `vol regime · sign uncertain` : 'unavailable'}
            tone={dealer?.sign === 'short' ? 'text-warning' : dealer?.sign === 'long' ? 'text-success' : undefined} />
          <Chip label="Futures since close" value={es?.trading ? pct(es.change_pct) : 'not trading'}
            sub={es?.trading ? `SPY ≈ ${usd(data.overnight.spy_implied)} · ${es.as_of}` : `SPY close ${usd(s.spy_close)}`}
            tone={es?.trading ? ((es.change_pct ?? 0) >= 0 ? 'text-success' : 'text-error') : 'text-base-content/50'} />
        </div>
        {s.momentum_panic_state && (
          <div className="text-[10px] text-base-content/50 mt-2" title={s.momentum_panic_state.note}>
            Momentum panic state (Daniel & Moskowitz):{' '}
            <b className={s.momentum_panic_state.on ? 'text-warning' : 'text-base-content/70'}>{s.momentum_panic_state.on ? 'ON' : 'off'}</b>
            {' '}— 24-month S&P return {pct(s.momentum_panic_state.ret_24m_pct, 1)}, 126-day variance at the {s.momentum_panic_state.var126_pct_rank ?? '—'}th percentile.
            {s.momentum_panic_state.on && ' Momentum crashes have come on sharp rebounds, not selloffs.'}
          </div>
        )}
        <div className="flex flex-wrap gap-1.5 mt-2">
          {s.recent.map(r => (
            <span key={r.date} className={`text-[10px] tabular-nums px-2 py-0.5 rounded border border-white/[0.06] ${
              (r.sigma ?? 0) <= -1.5 ? 'bg-red-500/25' : (r.sigma ?? 0) <= -0.75 ? 'bg-red-500/10' : (r.sigma ?? 0) >= 1.5 ? 'bg-emerald-500/25' : (r.sigma ?? 0) >= 0.75 ? 'bg-emerald-500/10' : 'bg-base-200/30'}`}>
              {shortDay(r.date)} {pct(r.ret_pct)} <span className="text-base-content/40">{sig(r.sigma)}</span>
            </span>
          ))}
        </div>
      </div>

      {/* ---- what the evidence says (graded research; same registry as the tell chips) ---- */}
      {data.evidence && <SelloffEvidencePanel ev={data.evidence} />}

      {/* ---- fan chart ---- */}
      <div className="rounded-xl border border-white/[0.06] bg-base-100/30 p-4">
        <SectionTitle icon={<Activity className="w-4 h-4" />} title="Next 20 sessions: what history says vs. what options price"
          sub="Shaded = range of outcomes after the closest historical setups. Amber lines = the same percentiles implied by SPY option prices. Both start from the last close." />
        <div className="flex flex-wrap items-center gap-3 text-[10px] text-base-content/60 mb-2">
          <span className="inline-flex items-center gap-1"><span className="w-3 h-2 rounded-sm" style={{ background: C.hist, opacity: 0.5 }} /> History P10–P90 · P25–P75 · median
            {data.analogs.fan_calibrated && data.analogs.fan_factor && (
              <span className="text-base-content/40" title="The raw analog fan historically held too few outcomes; it is widened by its walk-forward coverage factor (tuned 2000–2012, checked 2013+)">
                &nbsp;(calibrated ×{data.analogs.fan_factor.h5.toFixed(2)})</span>)}</span>
          <span className="inline-flex items-center gap-1"><span className="w-3 h-0.5" style={{ background: C.opt }} /> Options P10/P25/median/P75/P90</span>
          {data.chart.spy_implied_open != null && <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded-full" style={{ background: C.futures }} /> Futures-implied open</span>}
          <span className="ml-auto inline-flex gap-1">
            {(['key', 'dealer', 'trend'] as LevelGroup[]).map(g => (
              <button key={g} onClick={() => setShow(v => ({ ...v, [g]: !v[g] }))}
                className={`btn btn-xs ${show[g] ? 'btn-primary btn-outline' : 'btn-ghost text-base-content/40'}`}>
                {g === 'key' ? 'Selloff levels' : g === 'dealer' ? 'Dealer levels (est.)' : '50/200-day'}
              </button>
            ))}
          </span>
        </div>
        {option ? <ReactECharts option={option} style={{ height: 420, width: '100%' }} notMerge lazyUpdate /> : null}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3">
          <div className="text-[11px]">
            <div className="text-[9px] uppercase tracking-wide text-base-content/40 mb-1">Options-implied 1σ (SPY at-the-money straddle)</div>
            {data.options_cone.available ? (
              <table className="w-full tabular-nums">
                <tbody>
                  {conePts.map(p => (
                    <tr key={p.expiration} className="border-t border-white/[0.04]">
                      <td className="py-1 pr-2 text-base-content/60">{dayLabel(p.expiration)}</td>
                      <td className="py-1 pr-2">±{p.sigma_total_pct.toFixed(2)}%</td>
                      <td className="py-1 pr-2 text-base-content/50">straddle {usd(p.straddle)}</td>
                      <td className="py-1 text-base-content/40">{p.sigma_annual_pct.toFixed(1)}% ann. · {p.price_source}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="text-base-content/50">{data.options_cone.reason}</div>}
          </div>
          <p className="text-[10px] text-base-content/40 leading-snug self-end">
            {data.options_cone.method} History and options answer different questions: history = what followed similar setups
            (small sample, real-world odds); options = what the market charges to insure the move (risk-neutral, usually wider).
          </p>
        </div>
      </div>

      {/* ---- history odds ---- */}
      <div className="rounded-xl border border-white/[0.06] bg-base-100/30 p-4">
        <SectionTitle icon={<History className="w-4 h-4" />} title="What happened after similar setups"
          sub="Every number is shown next to the same statistic for any day, so you can see whether this setup is actually different." />
        <HistoryPanel d={data} />
      </div>

      {/* ---- phase 3: the user's own book under these scenarios ---- */}
      <div className="rounded-xl border border-primary/20 bg-base-100/30 p-4">
        <SelloffBook marketSession={data.last_session} />
      </div>

      {/* ---- phase 2: what got sold + participation ---- */}
      <div className="rounded-xl border border-white/[0.06] bg-base-100/30 p-4">
        <SectionTitle icon={<Layers className="w-4 h-4" />} title="What got sold"
          sub="Sectors, factor spreads and the biggest holdings over the same sessions — tells a crowded-trade unwind apart from a broad liquidation." />
        {internals ? <AnatomyPanel a={internals.anatomy} />
          : iLoading ? <div className="flex items-center gap-2 text-xs text-base-content/50 py-6 justify-center"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Scanning ~500 stocks — about 20 seconds on a cold cache…</div>
            : <div className="text-xs text-base-content/50">{iErr || 'Not loaded.'}</div>}
      </div>
      <div className="rounded-xl border border-white/[0.06] bg-base-100/30 p-4">
        <SectionTitle icon={<Users className="w-4 h-4" />} title="Who is participating"
          sub="Breadth across all S&P 500 members: an index can fall while most stocks rise, and a durable low usually needs broad buying." />
        {internals ? <BreadthPanel b={internals.breadth} />
          : iLoading ? <div className="flex items-center gap-2 text-xs text-base-content/50 py-6 justify-center"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Counting highs, lows and participation…</div>
            : <div className="text-xs text-base-content/50">{iErr || 'Not loaded.'}</div>}
      </div>

      {/* ---- stress grid ---- */}
      <div className="rounded-xl border border-white/[0.06] bg-base-100/30 p-4">
        <SectionTitle icon={<ShieldAlert className="w-4 h-4" />} title="Is the stress spreading?"
          sub="Cross-asset moves over the last 5 sessions. A selloff that stays in stocks behaves differently from one that credit, volatility and funding markets confirm." />
        <StressGrid d={data} />
      </div>

      {/* ---- tells ---- */}
      <div className="rounded-xl border border-white/[0.06] bg-base-100/30 p-4">
        <SectionTitle icon={<Crosshair className="w-4 h-4" />} title="What to watch at the open"
          sub={data.overnight.available
            ? 'Price tells are checked against the futures-implied SPY level right now; recheck after the open.'
            : 'Price tells are levels to watch; market-data tells show their current reading.'} />
        <Tells d={data} extra={internals?.tells ?? []} />
        {(data.context_notes ?? []).map(n => (
          <div key={n.id} className="mt-3 rounded-lg border border-white/[0.08] bg-base-200/30 px-3 py-2">
            <div className="text-[9px] uppercase tracking-wide text-base-content/40">Volatility regime — not a direction call</div>
            <div className="text-[12px] font-medium text-base-content/80">{n.label}
              {n.flip != null && <span className="text-base-content/45 font-normal"> · estimated flip {usd(n.flip)} ({pct(n.flip_distance_pct, 1)} away)</span>}</div>
            <div className="text-[10px] text-base-content/50 leading-snug">{n.detail}</div>
            {n.evidence && <div className="text-[9px] text-base-content/35 mt-0.5">evidence: {n.evidence.grade} — {n.evidence.note}</div>}
          </div>
        ))}
        {data.events.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {data.events.map(e => (
              <span key={e.date + e.label} title={e.note}
                className="inline-flex items-center gap-1.5 text-[10px] rounded border border-white/[0.06] bg-base-200/30 px-2 py-1 text-base-content/65">
                <CalendarClock className="w-3 h-3 text-primary/70" /> {dayLabel(e.date)} — {e.label}
              </span>
            ))}
          </div>
        )}
      </div>

      <p className="text-[10px] text-base-content/35 text-center">
        Base rates and market-implied ranges, not a forecast. Past setups are a small sample; nothing here is investment advice.
      </p>
    </div>
  );
}
