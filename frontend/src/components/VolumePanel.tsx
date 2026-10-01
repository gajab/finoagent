import React, { useCallback, useEffect, useMemo, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { BarChart3, ChevronDown, ChevronUp, Loader2, RefreshCw, Eye } from 'lucide-react';
import { fetchVolumeAnalysis, analyzeTa } from '../api';
import type { VolumeAnalysisData, CandleInterval } from '../types';
import IndicatorAIConsole from './IndicatorAIConsole';
import { useLocalState } from './taShared';

const INTERVALS: CandleInterval[] = ['15m', '1h', '1d', '1wk'];
const IV_LABEL: Record<CandleInterval, string> = { '15m': '15m', '1h': '1H', '1d': '1D', '1wk': '1W' };
const GREEN = 'rgba(16,185,129,0.65)', RED = 'rgba(239,68,68,0.65)';
const abbrev = (n: number) => n >= 1e9 ? `${(n / 1e9).toFixed(1)}B` : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(0)}K` : `${Math.round(n)}`;
const segCls = (a: boolean) => `px-2 py-0.5 rounded-md text-[10px] font-semibold transition ${a ? 'bg-primary/20 text-primary' : 'text-base-content/45 hover:text-base-content'}`;

const TREND_TONE: Record<string, string> = { rising: 'text-success', falling: 'text-error', flat: 'text-base-content/60' };
const DRY_TONE: Record<string, string> = { 'drying up': 'text-info', expanding: 'text-success', steady: 'text-base-content/60' };
const DIV_TONE: Record<string, string> = { bullish: 'text-success', bearish: 'text-error', aligned: 'text-base-content/60' };

function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-base-200/30 px-2 py-1">
      <div className="text-[9px] uppercase tracking-wide text-base-content/40">{label}</div>
      <div className={`text-[12px] font-bold tabular-nums ${tone || 'text-base-content/80'}`}>{value}</div>
    </div>
  );
}

export default function VolumePanel({ ticker, defaultOpen = false }: { ticker: string; defaultOpen?: boolean }) {
  const [expanded, setExpanded] = useState(defaultOpen);
  const [interval, setInterval] = useLocalState<CandleInterval>('ta:vol:iv', '1d');
  const [lookback, setLookback] = useLocalState<number>('ta:vol:lb', 20);
  const [lower, setLower] = useLocalState<'obv' | 'cvd' | 'off'>('ta:vol:lower', 'obv');
  const [data, setData] = useState<VolumeAnalysisData | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true); setErr(null);
    fetchVolumeAnalysis(ticker, interval, lookback)
      .then(r => setData(r.volume_analysis))
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : 'Failed to load volume'))
      .finally(() => setLoading(false));
  }, [ticker, interval, lookback]);

  useEffect(() => { setData(null); setErr(null); }, [ticker]);
  useEffect(() => { if (expanded) load(); }, [expanded, interval, lookback, load]);

  const option = useMemo(() => {
    if (!data?.bars?.length) return null;
    const bars = data.bars;
    const ts = bars.map(b => b.t);
    const candles = bars.map(b => [b.o, b.c, b.l, b.h]);
    const volData = bars.map(b => ({ value: b.v, itemStyle: { color: (b.c ?? 0) >= (b.o ?? 0) ? GREEN : RED } }));
    const climaxMarks = (data.climax_bars || []).map(cb => ({
      coord: [cb.t, cb.price], symbol: 'pin', symbolSize: 26,
      itemStyle: { color: cb.direction === 'up' ? '#10b981' : '#ef4444' },
      label: { show: true, formatter: `${cb.ratio}×`, fontSize: 8, color: '#fff' },
    }));
    const showLower = lower !== 'off';
    const nGrids = showLower ? 3 : 2;
    const xIdx = Array.from({ length: nGrids }, (_, i) => i);
    return {
      animation: false,
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'cross' }, confine: true, textStyle: { fontSize: 11 },
        formatter: (ps: any) => {
          const cp = ps.find((x: any) => x.seriesType === 'candlestick');
          const vp = ps.find((x: any) => x.seriesType === 'bar');
          let out = ps[0]?.axisValue ? `${ps[0].axisValue}<br/>` : '';
          if (cp?.data) { const [, o, c, l, h] = cp.data; out += `O ${o} H ${h} L ${l} C <b>${c}</b><br/>`; }
          if (vp?.data) out += `Vol <b>${abbrev(Number(vp.data.value ?? vp.data))}</b>`;
          return out;
        },
      },
      grid: showLower
        ? [{ left: 8, right: 58, top: 8, height: '42%' }, { left: 8, right: 58, top: '54%', height: '27%' }, { left: 8, right: 58, top: '86%', height: '11%' }]
        : [{ left: 8, right: 58, top: 8, height: '50%' }, { left: 8, right: 58, top: '62%', height: '34%' }],
      xAxis: Array.from({ length: nGrids }, (_, gi) => ({
        type: 'category', data: ts, gridIndex: gi, boundaryGap: true,
        axisLabel: gi === nGrids - 1 ? { color: '#94a3b8', fontSize: 9, hideOverlap: true } : { show: false },
        axisLine: { lineStyle: { color: '#475569' } }, axisTick: { show: gi === nGrids - 1 },
      })),
      yAxis: [
        { scale: true, gridIndex: 0, position: 'right', axisLabel: { color: '#94a3b8', fontSize: 9, formatter: '${value}' }, splitLine: { lineStyle: { color: 'rgba(100,116,139,0.12)' } } },
        { gridIndex: 1, position: 'right', axisLabel: { color: '#94a3b8', fontSize: 8, formatter: (v: number) => abbrev(v) }, splitLine: { show: false } },
        ...(showLower ? [{ scale: true, gridIndex: 2, position: 'right' as const, axisLabel: { color: '#94a3b8', fontSize: 8, formatter: (v: number) => abbrev(v) }, splitLine: { show: false } }] : []),
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: xIdx, start: 50, end: 100 },
        { type: 'slider', xAxisIndex: xIdx, height: 12, bottom: 2, start: 50, end: 100, brushSelect: false },
      ],
      series: [
        { type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0, data: candles, itemStyle: { color: '#10b981', color0: '#ef4444', borderColor: '#10b981', borderColor0: '#ef4444' }, markPoint: { silent: true, data: climaxMarks } },
        { type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volData, barWidth: '60%' },
        { type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: data.volume_ma, smooth: true, showSymbol: false, lineStyle: { color: '#f59e0b', width: 1 }, name: 'Vol avg' },
        ...(showLower ? [{
          type: 'line' as const, xAxisIndex: 2, yAxisIndex: 2, data: lower === 'cvd' ? data.cvd : data.obv,
          smooth: true, showSymbol: false, areaStyle: { opacity: 0.07 },
          lineStyle: { color: lower === 'cvd' ? '#38bdf8' : '#a78bfa', width: 1.2 }, name: lower.toUpperCase(),
        }] : []),
      ],
    };
  }, [data, lower]);

  const m = data?.metrics;
  const selectionJson = useMemo(() => data ? { ticker, interval, price: data.price, volume: { metrics: data.metrics, climax_bars: data.climax_bars, read: data.read } } : {}, [ticker, interval, data]);

  return (
    <div className="bg-base-300 rounded-xl overflow-hidden shadow-xl border border-white/[0.05]">
      <button type="button" onClick={() => setExpanded(o => !o)} className="w-full flex items-center justify-between px-4 py-3 bg-gradient-to-r from-base-200/60 to-transparent">
        <div className="flex items-center gap-2 text-left">
          <BarChart3 className="w-4 h-4 text-secondary" />
          <div>
            <h4 className="text-sm font-semibold text-base-content/90">Volume — spot dry-up, spikes &amp; delta</h4>
            <p className="text-[10px] text-base-content/50">Price + volume with RVOL, rising/falling trend, dry-up ⇄ expansion, climax bars, up/down split, CVD &amp; OBV divergence · any timeframe</p>
          </div>
        </div>
        {expanded ? <ChevronUp className="w-4 h-4 shrink-0" /> : <ChevronDown className="w-4 h-4 shrink-0" />}
      </button>

      {expanded && (
        <div className="p-3 border-t border-white/[0.05] space-y-2">
          <div className="flex items-center justify-end gap-2 flex-wrap">
            <div className="flex items-center gap-1">
              <span className="text-[9px] text-base-content/40 uppercase tracking-wide">RVOL</span>
              <div className="flex gap-0.5 bg-base-300/60 rounded-lg p-0.5">
                {[10, 20, 50].map(lb => <button key={lb} onClick={() => setLookback(lb)} className={segCls(lookback === lb)}>{lb}</button>)}
              </div>
            </div>
            <div className="flex items-center gap-1">
              <span className="text-[9px] text-base-content/40 uppercase tracking-wide">Lower</span>
              <div className="flex gap-0.5 bg-base-300/60 rounded-lg p-0.5">
                {(['obv', 'cvd', 'off'] as const).map(o => <button key={o} onClick={() => setLower(o)} className={segCls(lower === o)}>{o === 'off' ? 'Off' : o.toUpperCase()}</button>)}
              </div>
            </div>
            <div className="flex gap-0.5 bg-base-300/60 rounded-lg p-0.5">
              {INTERVALS.map(iv => <button key={iv} onClick={() => setInterval(iv)} className={segCls(interval === iv)}>{IV_LABEL[iv]}</button>)}
            </div>
          </div>

          {loading && !data && <div className="flex items-center gap-2 text-sm text-base-content/60 py-10 justify-center"><Loader2 className="w-4 h-4 animate-spin" /> Reading volume…</div>}
          {err && !loading && <div className="alert alert-error text-xs flex items-center justify-between"><span>{err}</span><button className="btn btn-ghost btn-xs" onClick={load}><RefreshCw className="w-3.5 h-3.5" /> Retry</button></div>}

          {data && m && (
            <>
              <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-1">
                {option && <ReactECharts option={option} style={{ height: 460, width: '100%' }} notMerge lazyUpdate />}
              </div>

              <div className="grid grid-cols-3 sm:grid-cols-6 gap-1.5">
                <Stat label="RVOL 10/20/50" value={m.rvol_multi ? `${m.rvol_multi['10'] ?? '—'}/${m.rvol_multi['20'] ?? '—'}/${m.rvol_multi['50'] ?? '—'}×` : (m.rvol != null ? `${m.rvol}×` : '—')} tone={m.rvol != null ? (m.rvol >= 2 ? 'text-success' : m.rvol <= 0.6 ? 'text-base-content/45' : 'text-base-content/80') : ''} />
                <Stat label="Vol trend" value={m.volume_trend} tone={TREND_TONE[m.volume_trend]} />
                <Stat label={m.dryup_state === 'expanding' ? 'Expansion' : 'Dry-up'} value={m.dryup_ratio != null ? `${m.dryup_ratio}×` : '—'} tone={DRY_TONE[m.dryup_state]} />
                <Stat label="Up/Down · 20 bars" value={m.up_volume_pct != null ? `${m.up_volume_pct}/${m.down_volume_pct}%` : '—'} tone={(m.up_volume_pct ?? 50) >= 58 ? 'text-success' : (m.down_volume_pct ?? 50) >= 58 ? 'text-error' : 'text-base-content/70'} />
                <Stat label="CVD / OBV" value={`${m.cvd_trend} / ${m.obv_trend}`} tone={TREND_TONE[m.cvd_trend]} />
                <Stat label="Divergence" value={m.divergence} tone={DIV_TONE[m.divergence]} />
              </div>
              {m.rvol_basis && (
                <p className="text-[9px] text-base-content/40">
                  RVOL basis: {m.rvol_method === 'time-of-day' ? `time-of-day — ${m.rvol_basis} (pro method for intraday: compares each bar to the same time slot, not a flat average)` : m.rvol_basis}
                </p>
              )}

              {!!data.read?.length && (
                <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2.5">
                  <div className="text-[10px] uppercase tracking-wider text-base-content/45 mb-1 flex items-center gap-1"><Eye className="w-3 h-3" /> What the volume is saying</div>
                  <ul className="list-disc pl-4 text-[11px] text-base-content/70 space-y-0.5 leading-snug">
                    {data.read.map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                </div>
              )}

              <IndicatorAIConsole
                selectionJson={selectionJson}
                chips={[{ key: 'vol', label: `Volume ${IV_LABEL[interval]} · RVOL ${m.rvol ?? '—'}× · ${m.dryup_state}`, tone: DRY_TONE[m.dryup_state] }]}
                analyzeFn={(sel, msgs) => analyzeTa(ticker, sel, msgs)}
                emptyHint="Ask the AI about this volume read."
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}
