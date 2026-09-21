import React, { useEffect, useMemo, useRef, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { Loader2, RefreshCw, CandlestickChart } from 'lucide-react';
import { fetchCandles } from '../api';
import type { Candle, CandleInterval } from '../types';
import { toFill, type OverlayLine, type OverlayBand } from './taOverlay';

const INTERVALS: CandleInterval[] = ['15m', '1h', '1d', '1wk'];
const IV_LABEL: Record<CandleInterval, string> = { '15m': '15m', '1h': '1H', '1d': '1D', '1wk': '1W' };

interface TAChartProps {
  ticker: string;
  interval: CandleInterval;
  onInterval: (iv: CandleInterval) => void;
  lines?: OverlayLine[];
  bands?: OverlayBand[];
  height?: number;
  title?: React.ReactNode;
  rightExtra?: React.ReactNode;
  onSpot?: (spot: number) => void;
}

/**
 * Reusable candlestick chart for the Institutional-TA panels: real OHLC candles at a switchable
 * interval (15m · 1H · 1D · 1W) from /candles, with horizontal level (markLine) and price-zone
 * (markArea) overlays. Candles are cached per interval so switching is instant after first load.
 */
export default function TAChart({ ticker, interval, onInterval, lines = [], bands = [], height = 300, title, rightExtra, onSpot }: TAChartProps) {
  const [candles, setCandles] = useState<Candle[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const cache = useRef<Map<string, Candle[]>>(new Map());

  useEffect(() => { cache.current.clear(); setCandles(null); }, [ticker]);

  useEffect(() => {
    let cancelled = false;
    const key = interval;
    const cached = cache.current.get(key);
    if (cached) { setCandles(cached); return; }
    setLoading(true); setErr(null);
    fetchCandles(ticker, interval)
      .then(r => {
        if (cancelled) return;
        const cs = r.candles?.candles || [];
        cache.current.set(key, cs);
        setCandles(cs);
        const last = cs[cs.length - 1]?.c;
        if (last != null && onSpot) onSpot(last);
      })
      .catch((e: unknown) => { if (!cancelled) setErr(e instanceof Error ? e.message : 'Failed to load candles'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [ticker, interval, onSpot, reloadKey]);
  const retry = () => { cache.current.delete(interval); setCandles(null); setReloadKey(k => k + 1); };

  const option = useMemo(() => {
    if (!candles?.length) return null;
    const ts = candles.map(c => c.t);
    const data = candles.map(c => [c.o, c.c, c.l, c.h]);
    const lows = candles.map(c => c.l).filter((n): n is number => n != null);
    const highs = candles.map(c => c.h).filter((n): n is number => n != null);
    let lo = Math.min(...lows), hi = Math.max(...highs);
    const rng = hi - lo || hi * 0.02;
    const near = (p: number) => p >= lo - 0.35 * rng && p <= hi + 0.35 * rng;
    const extra = [...lines.map(l => l.price), ...bands.flatMap(b => [b.top, b.bottom])].filter(p => p != null && near(p));
    if (extra.length) { lo = Math.min(lo, ...extra); hi = Math.max(hi, ...extra); }
    const pad = (hi - lo) * 0.04 || hi * 0.01;

    const markLineData = lines.map(ln => ({
      yAxis: ln.price,
      lineStyle: { color: ln.color || '#94a3b8', type: (ln.dash && ln.dash.length ? 'dashed' : 'solid') as any, width: 1.2, opacity: 0.9 },
      label: { formatter: ln.label, position: 'insideEndTop' as const, color: ln.color || '#cbd5e1', fontSize: 9, backgroundColor: 'rgba(0,0,0,0.4)', padding: [1, 3] },
    }));
    const markAreaData = bands.map(b => ([
      { yAxis: Math.min(b.top, b.bottom), itemStyle: { color: toFill(b.color, 0.10) }, label: { show: true, formatter: b.label, position: 'insideTopLeft' as const, color: b.color, fontSize: 9 } },
      { yAxis: Math.max(b.top, b.bottom) },
    ]));

    return {
      animation: false,
      grid: { left: 8, right: 64, top: 10, bottom: 20 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, confine: true,
        textStyle: { fontSize: 11 },
        formatter: (ps: any) => {
          const p = Array.isArray(ps) ? ps.find((x: any) => x.seriesType === 'candlestick') : ps;
          if (!p?.data) return '';
          const [, o, c, l, h] = p.data;
          return `${p.axisValue}<br/>O ${o}  H ${h}<br/>L ${l}  C <b>${c}</b>`;
        } },
      xAxis: { type: 'category', data: ts, boundaryGap: true, axisLabel: { color: '#94a3b8', fontSize: 9, hideOverlap: true }, axisLine: { lineStyle: { color: '#475569' } } },
      yAxis: { scale: true, min: lo - pad, max: hi + pad, position: 'right', axisLabel: { color: '#94a3b8', fontSize: 9, formatter: '${value}' }, splitLine: { lineStyle: { color: 'rgba(100,116,139,0.12)' } } },
      dataZoom: [{ type: 'inside', start: 45, end: 100 }, { type: 'slider', height: 12, bottom: 2, start: 45, end: 100, brushSelect: false }],
      series: [{
        type: 'candlestick', data,
        itemStyle: { color: '#10b981', color0: '#ef4444', borderColor: '#10b981', borderColor0: '#ef4444' },
        markLine: { symbol: ['none', 'none'], silent: true, animation: false, data: markLineData },
        markArea: { silent: true, data: markAreaData },
      }],
    };
  }, [candles, lines, bands]);

  return (
    <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2">
      <div className="flex items-center justify-between gap-2 mb-1 px-1 flex-wrap">
        <span className="text-[11px] font-bold text-base-content/70 flex items-center gap-1">
          <CandlestickChart className="w-3.5 h-3.5 text-secondary" /> {title || 'Price'}
        </span>
        <div className="flex items-center gap-2">
          {rightExtra}
          <div className="flex items-center gap-0.5 bg-base-300/60 rounded-lg p-0.5">
            {INTERVALS.map(iv => (
              <button key={iv} onClick={() => onInterval(iv)}
                className={`px-2 py-0.5 rounded-md text-[10px] font-semibold transition ${interval === iv ? 'bg-primary/20 text-primary' : 'text-base-content/45 hover:text-base-content'}`}>
                {IV_LABEL[iv]}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div style={{ height }} className="relative">
        {loading && !candles && (
          <div className="absolute inset-0 flex items-center justify-center gap-2 text-xs text-base-content/50"><Loader2 className="w-4 h-4 animate-spin" /> Loading {IV_LABEL[interval]} candles…</div>
        )}
        {err && !loading && (
          <div className="absolute inset-0 flex items-center justify-center gap-2 text-xs text-base-content/50">
            {err} <button className="btn btn-ghost btn-xs" onClick={retry}><RefreshCw className="w-3 h-3" /></button>
          </div>
        )}
        {option && <ReactECharts option={option} style={{ height: '100%', width: '100%' }} notMerge lazyUpdate />}
      </div>
    </div>
  );
}
