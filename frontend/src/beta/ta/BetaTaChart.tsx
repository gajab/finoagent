/**
 * BetaTaChart — the centre of the Beta Technical cockpit: ONE candlestick chart (with volume) that every layer
 * draws onto. Levels are drawn as thin lines/bands inside the plot and LABELLED in a right-hand gutter, where
 * layoutGutter() fans crowded labels apart (with a leader line back to the true price) instead of letting them
 * overprint each other the way the classic in-plot labels do.
 *
 * Data: the same /candles endpoint the classic TAChart uses (15m · 1h · 1d · 1wk), cached per interval.
 * What is drawn and where its price comes from is decided upstream (betaTaChartModel); this file only renders.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { Loader2, RefreshCw } from 'lucide-react';
import { fetchCandles } from '../../api';
import type { Candle, CandleInterval } from '../../types';
import { COLOR, yRange, inRange, labelVisible, type ChartLevel } from './betaTaChartModel';
import { layoutGutter, type GutterPlaced } from './betaTaModel';

export const GUTTER_W = 126;
const LABEL_GAP = 15;
const DEFAULT_BARS: Record<CandleInterval, number> = { '15m': 130, '1h': 150, '1d': 120, '1wk': 104 };
const IV_LABEL: Record<CandleInterval, string> = { '15m': '15-minute', '1h': 'hourly', '1d': 'daily', '1wk': 'weekly' };

const hexA = (hex: string, a: number) => {
  const m = hex.match(/^#([0-9a-f]{6})$/i);
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
};

interface Props {
  ticker: string;
  interval: CandleInterval;
  levels: ChartLevel[];
  height?: number;
  onSpot?: (spot: number) => void;
}
interface Win { start: number; end: number }

export default function BetaTaChart({ ticker, interval, levels, height = 480, onSpot }: Props) {
  const [candles, setCandles] = useState<Candle[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [win, setWin] = useState<Win>({ start: 0, end: 100 });
  const [placed, setPlaced] = useState<GutterPlaced[]>([]);
  const cache = useRef<Map<string, Candle[]>>(new Map());
  const chartRef = useRef<ReactECharts>(null);
  const sigRef = useRef('');

  useEffect(() => { cache.current.clear(); setCandles(null); }, [ticker]);

  useEffect(() => {
    let cancelled = false;
    const key = `${ticker}|${interval}`;
    const adopt = (cs: Candle[]) => {
      setCandles(cs);
      const n = cs.length;
      setWin({ start: Math.max(0, 100 - (DEFAULT_BARS[interval] / Math.max(1, n)) * 100), end: 100 });
      const last = cs[n - 1]?.c;
      if (last != null && onSpot) onSpot(last);
    };
    const cached = cache.current.get(key);
    if (cached) { adopt(cached); return; }
    setLoading(true); setErr(null);
    fetchCandles(ticker, interval)
      .then(r => { if (cancelled) return; const cs = r.candles?.candles || []; cache.current.set(key, cs); adopt(cs); })
      .catch((e: unknown) => { if (!cancelled) setErr(e instanceof Error ? e.message : 'Failed to load candles'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, interval, reloadKey]);

  const spotNow = candles?.length ? candles[candles.length - 1].c : null;

  // visible slice → y-range (so a zoomed-in view re-fits, and a far-off level can't squash the candles)
  const view = useMemo(() => {
    if (!candles?.length) return null;
    const n = candles.length;
    const s = Math.max(0, Math.floor((win.start / 100) * (n - 1))), e = Math.min(n - 1, Math.ceil((win.end / 100) * (n - 1)));
    const slice = candles.slice(s, e + 1);
    const range = yRange(slice, levels);
    return range ? { range, s, e } : null;
  }, [candles, win, levels]);

  const drawn = useMemo(() => (view ? levels.filter(l => inRange(l, view.range)) : []), [levels, view]);

  const option = useMemo(() => {
    if (!candles?.length || !view) return null;
    const ts = candles.map(c => c.t);
    const k = candles.map(c => (c.o == null || c.c == null || c.l == null || c.h == null ? ['-', '-', '-', '-'] : [c.o, c.c, c.l, c.h]));
    const vol = candles.map(c => ({ value: c.v, itemStyle: { color: hexA((c.c ?? 0) >= (c.o ?? 0) ? COLOR.up : COLOR.down, 0.4) } }));
    const lines: { yAxis: number; label: { show: boolean }; lineStyle: { color: string; type: 'dashed' | 'solid' | 'dotted'; width: number; opacity: number } }[] = drawn.filter(l => l.kind === 'line').map(l => ({
      yAxis: l.price, label: { show: false },
      lineStyle: { color: l.color, type: l.dashed ? 'dashed' : 'solid', width: l.priority >= 9 ? 1.4 : 1, opacity: 0.9 },
    }));
    const bands = drawn.filter(l => l.kind === 'band').map(l => ([
      { yAxis: l.bottom, itemStyle: { color: hexA(l.color, 0.13) }, label: { show: false } },
      { yAxis: l.top },
    ]));
    if (spotNow != null) lines.push({ yAxis: spotNow, label: { show: false }, lineStyle: { color: COLOR.now, type: 'dotted', width: 1, opacity: 0.7 } });
    const gridL = 48, gridR = GUTTER_W;
    return {
      animation: false,
      grid: [{ left: gridL, right: gridR, top: 8, height: '68%' }, { left: gridL, right: gridR, top: '78%', height: '12%' }],
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'cross', lineStyle: { color: '#64748b' } }, confine: true, textStyle: { fontSize: 12 },
        formatter: (ps: any) => {
          const p = Array.isArray(ps) ? ps.find((x: any) => x.seriesType === 'candlestick') : ps;
          if (!p?.data) return '';
          const [, o, c, l, h] = p.data;
          const v = Array.isArray(ps) ? ps.find((x: any) => x.seriesType === 'bar') : null;
          return `${p.axisValue}<br/>O ${o}  H ${h}<br/>L ${l}  C <b>${c}</b>${v ? `<br/>Vol ${Number(v.data?.value ?? v.data).toLocaleString()}` : ''}`;
        },
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      xAxis: [
        { type: 'category', data: ts, gridIndex: 0, boundaryGap: true, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#475569' } } },
        { type: 'category', data: ts, gridIndex: 1, boundaryGap: true, axisLabel: { color: '#94a3b8', fontSize: 11, hideOverlap: true }, axisLine: { lineStyle: { color: '#475569' } } },
      ],
      yAxis: [
        { scale: true, gridIndex: 0, min: view.range.min, max: view.range.max, position: 'left', axisLabel: { color: '#94a3b8', fontSize: 11, formatter: (v: number) => v.toFixed(v >= 100 ? 0 : 2) }, splitLine: { lineStyle: { color: 'rgba(100,116,139,0.14)' } } },
        { scale: true, gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: win.start, end: win.end },
        { type: 'slider', xAxisIndex: [0, 1], start: win.start, end: win.end, height: 14, bottom: 4, brushSelect: false, textStyle: { color: '#94a3b8', fontSize: 11 } },
      ],
      series: [
        { type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0, data: k,
          itemStyle: { color: COLOR.up, color0: COLOR.down, borderColor: COLOR.up, borderColor0: COLOR.down },
          markLine: { symbol: ['none', 'none'], silent: true, animation: false, data: lines },
          markArea: { silent: true, data: bands } },
        { type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: vol },
      ],
    };
  }, [candles, view, drawn, win, spotNow]);

  // measure true pixels → place the labels (only commit when something actually moved, so 'finished' can't loop).
  // ECharts throws if asked to convert before its first frame is drawn, so every call is guarded and the real
  // trigger is its own 'finished' event.
  const place = useCallback(() => {
    const inst = chartRef.current?.getEchartsInstance?.();
    if (!inst || !view) return;
    const px = (price: number): number | null => {
      try { const y = inst.convertToPixel({ yAxisIndex: 0 }, price) as unknown as number; return Number.isFinite(y) ? y : null; } catch { return null; }
    };
    const items: { id: string; y: number; priority: number }[] = [];
    for (const l of drawn) {
      if (!labelVisible(l, view.range)) continue;
      const y = px(l.price);
      if (y != null) items.push({ id: l.id, y, priority: l.priority });
    }
    if (spotNow != null) { const y = px(spotNow); if (y != null) items.push({ id: '__now', y, priority: 10 }); }
    if (!items.length) return;
    const h = inst.getHeight();
    const { placed: p } = layoutGutter(items, 10, Math.max(40, h * 0.68), LABEL_GAP);
    const sig = p.map(x => `${x.id}:${Math.round(x.y)}:${Math.round(x.at)}`).join('|');
    if (sig !== sigRef.current) { sigRef.current = sig; setPlaced(p); }
  }, [drawn, view, spotNow]);

  const onZoom = useCallback((params: any) => {
    const b = params?.batch?.[0] ?? params;
    if (typeof b?.start === 'number' && typeof b?.end === 'number') setWin(w => (Math.abs(w.start - b.start) < 0.01 && Math.abs(w.end - b.end) < 0.01 ? w : { start: b.start, end: b.end }));
  }, []);

  const byId = useMemo(() => new Map(drawn.map(l => [l.id, l])), [drawn]);

  return (
    <div className="relative" style={{ height }}>
      {loading && !candles && (
        <div className="absolute inset-0 flex items-center justify-center gap-2 text-sm text-base-content/55"><Loader2 className="w-4 h-4 animate-spin" /> Loading {IV_LABEL[interval]} candles…</div>
      )}
      {err && !loading && (
        <div className="absolute inset-0 flex items-center justify-center gap-2 text-sm text-base-content/55">
          {err} <button className="btn btn-ghost btn-xs" onClick={() => { cache.current.delete(`${ticker}|${interval}`); setReloadKey(k => k + 1); }}><RefreshCw className="w-3 h-3" /> Retry</button>
        </div>
      )}
      {!err && !loading && candles && !candles.length && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-base-content/55">No {IV_LABEL[interval]} candles for {ticker}.</div>
      )}
      {option && <ReactECharts ref={chartRef} option={option} style={{ height: '100%', width: '100%' }} notMerge={false} lazyUpdate
        onEvents={{ datazoom: onZoom, finished: place }} />}
      {/* gutter: collision-free labels with a leader back to the true price */}
      {option && (
        <div className="absolute top-0 bottom-0 right-0 pointer-events-none" style={{ width: GUTTER_W }} data-testid="ta-gutter">
          <svg className="absolute inset-0 overflow-visible" width={GUTTER_W} height="100%" aria-hidden="true">
            {placed.filter(p => p.displaced).map(p => {
              const col = p.id === '__now' ? COLOR.now : byId.get(p.id)?.color || COLOR.slate;
              return <polyline key={p.id} points={`0,${p.y} 10,${p.at}`} fill="none" stroke={col} strokeWidth={0.8} opacity={0.7} />;
            })}
          </svg>
          {placed.map(p => {
            const isNow = p.id === '__now';
            const lv = byId.get(p.id);
            if (!isNow && !lv) return null;
            const col = isNow ? COLOR.now : lv!.color;
            return (
              <div key={p.id} className="absolute left-2.5 right-0 flex items-center leading-none" style={{ top: p.at - 7, height: 14 }} data-level={p.id}>
                {isNow
                  ? <span className="rounded px-1.5 py-[3px] text-[11px] font-semibold tabular-nums bg-base-content text-base-100">{spotNow?.toFixed(2)} now</span>
                  : <span className="text-[11px] whitespace-nowrap overflow-hidden text-ellipsis tabular-nums" style={{ color: col }} title={lv!.title || lv!.label}>{lv!.label}</span>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
