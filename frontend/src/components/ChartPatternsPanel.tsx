import { useEffect, useMemo, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import {
  Shapes, RefreshCw, Loader2, Sparkles, Copy, ClipboardCheck, Download,
  TrendingUp, TrendingDown, Check, Clock, GraduationCap,
} from 'lucide-react';
import { fetchChartPatterns, chartPatternImageUrl, analyzeTa } from '../api';
import type { ChartPatternsData, ChartPattern } from '../types';
import IndicatorAIConsole from './IndicatorAIConsole';
import { InfoTip } from './taUi';

const PURPLE = '#8b5cf6';
const money = (n?: number | null) => (n == null ? '—' : `$${Number(n).toFixed(2)}`);

function dirTone(d: string) {
  return d === 'bullish' ? 'text-success' : d === 'bearish' ? 'text-error' : 'text-warning';
}
function DirChip({ p }: { p: ChartPattern }) {
  const bull = p.direction === 'bullish';
  return (
    <span className={`inline-flex items-center gap-0.5 text-[10px] font-semibold ${dirTone(p.direction)}`}>
      {bull ? <TrendingUp className="w-3 h-3" /> : p.direction === 'bearish' ? <TrendingDown className="w-3 h-3" /> : null}
      {p.direction}
    </span>
  );
}

export default function ChartPatternsPanel({ ticker }: { ticker: string }) {
  const [data, setData] = useState<ChartPatternsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [selType, setSelType] = useState<string | null>(null);
  const [showFib, setShowFib] = useState(false);
  const [showAI, setShowAI] = useState(false);
  const [copied, setCopied] = useState(false);

  const load = () => {
    setLoading(true); setErr(null);
    fetchChartPatterns(ticker)
      .then(r => { setData(r.chart_patterns); setSelType(r.chart_patterns.patterns[0]?.type ?? null); })
      .catch(e => setErr(e?.message || 'Failed to load chart patterns'))
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [ticker]);

  const selected = data?.patterns.find(p => p.type === selType) || data?.patterns[0] || null;
  const fib = data?.fibonacci;

  const option = useMemo(() => {
    if (!data) return {};
    const s = data.series; const ts = s.timestamps; const n = ts.length; const off = s.offset || 0;
    const xAt = (idx: number) => ts[Math.max(0, Math.min(idx - off, n - 1))];
    const candles = ts.map((_, i) => [s.open[i], s.close[i], s.low[i], s.high[i]]);
    const markLineData: any[] = [];
    const markPointData: any[] = [];
    const markAreaData: any[] = [];
    const p = selected;
    if (p) {
      const tone = p.direction === 'bullish' ? '#16a34a' : p.direction === 'bearish' ? '#dc2626' : '#f59e0b';
      p.lines.forEach((ln, i) => markLineData.push([
        { coord: [xAt(ln.from.idx), ln.from.price], name: ln.label,
          lineStyle: { color: ln.kind === 'pole' ? '#22d3ee' : PURPLE, width: 2, type: ln.kind === 'neckline' ? 'solid' : 'solid' },
          label: { show: i === 0, formatter: ln.label, color: PURPLE, fontSize: 10 } },
        { coord: [xAt(ln.to.idx), ln.to.price] },
      ]));
      if (p.breakout) markLineData.push({
        yAxis: p.breakout.level, lineStyle: { color: '#f59e0b', type: 'dashed', width: 1.4 },
        label: { formatter: `Breakout ${money(p.breakout.level)}`, position: 'insideEndTop', color: '#f59e0b', fontSize: 10 },
      });
      if (p.target) markLineData.push({
        yAxis: p.target.price, lineStyle: { color: tone, type: 'dashed', width: 1.4 },
        label: { formatter: `Target ${money(p.target.price)}`, position: 'insideEndBottom', color: tone, fontSize: 10 },
      });
      if (p.stop != null) markLineData.push({
        yAxis: p.stop, lineStyle: { color: '#94a3b8', type: 'dotted', width: 1 },
        label: { formatter: `Stop ${money(p.stop)}`, position: 'insideEndTop', color: '#94a3b8', fontSize: 9 },
      });
      p.points.forEach(pt => markPointData.push({
        coord: [xAt(pt.idx), pt.price], value: pt.label,
        symbol: 'circle', symbolSize: 9,
        itemStyle: { color: PURPLE, borderColor: '#fff', borderWidth: 1 },
        label: { show: true, formatter: pt.label, position: 'top', color: PURPLE, fontSize: 9, fontWeight: 'bold' },
      }));
      if (p.points.length) {
        const xs = p.points.map(pt => pt.idx);
        markAreaData.push([
          { xAxis: xAt(Math.min(...xs)), itemStyle: { color: p.direction === 'bullish' ? 'rgba(16,185,129,0.06)' : 'rgba(239,68,68,0.06)' } },
          { xAxis: xAt(Math.max(...xs)) },
        ]);
      }
    }
    if (showFib && data.fibonacci) {
      data.fibonacci.levels.forEach(l => markLineData.push({
        yAxis: l.price, lineStyle: { color: '#a78bfa', type: 'dashed', width: 1 },
        label: { formatter: `${(l.ratio * 100).toFixed(1)}% ${money(l.price)}`, position: 'insideStartBottom', color: '#a78bfa', fontSize: 9 },
      }));
      // extension targets (price after breakout) — green, projected beyond the swing
      (data.fibonacci.extensions || []).forEach(l => markLineData.push({
        yAxis: l.price, lineStyle: { color: '#22c55e', type: 'dashed', width: 1 },
        label: { formatter: `${(l.ratio * 100).toFixed(1)}% tgt ${money(l.price)}`, position: 'insideEndTop', color: '#22c55e', fontSize: 9 },
      }));
    }
    return {
      animation: false,
      grid: { left: 8, right: 62, top: 12, bottom: 24 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      xAxis: { type: 'category', data: ts, boundaryGap: true, axisLabel: { color: '#94a3b8', fontSize: 10 }, axisLine: { lineStyle: { color: '#475569' } } },
      yAxis: { scale: true, position: 'right', axisLabel: { color: '#94a3b8', fontSize: 10, formatter: '${value}' }, splitLine: { lineStyle: { color: 'rgba(100,116,139,0.15)' } } },
      dataZoom: [{ type: 'inside', start: 55, end: 100 }, { type: 'slider', height: 14, bottom: 4, start: 55, end: 100 }],
      series: [{
        type: 'candlestick', data: candles,
        itemStyle: { color: '#10b981', color0: '#ef4444', borderColor: '#10b981', borderColor0: '#ef4444' },
        markLine: { symbol: ['none', 'none'], silent: true, data: markLineData },
        markPoint: { silent: true, data: markPointData },
        markArea: { silent: true, data: markAreaData },
      }],
    };
  }, [data, selected, showFib]);

  const copyJson = () => {
    if (!data) return;
    const payload = { ticker, price: data.price, as_of: data.as_of, patterns: data.patterns, fibonacci: data.fibonacci };
    navigator.clipboard.writeText(JSON.stringify(payload, null, 2)).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Shapes className="w-4 h-4 text-primary" />
          <h4 className="text-sm font-bold text-base-content/80">Chart Patterns</h4>
          <InfoTip text="Classical price patterns detected from swing pivots — the shapes traders draw by hand. Each shows the geometry, the breakout trigger and a measured-move target." />
        </div>
        <button onClick={load} className="btn btn-ghost btn-xs gap-1"><RefreshCw className="w-3.5 h-3.5" /> Refresh</button>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 justify-center py-14 text-sm text-base-content/50"><Loader2 className="w-5 h-5 animate-spin" /> Scanning for chart patterns…</div>
      ) : err ? (
        <div className="alert alert-error text-sm">{err}</div>
      ) : !data ? null : (
        <>
          {/* pattern selector chips */}
          <div className="flex flex-wrap gap-1.5">
            {data.patterns.length === 0 && (
              <div className="text-xs text-base-content/50 py-2">No high-confidence chart pattern on the daily chart right now. Toggle Fibonacci below, or check back after more price action.</div>
            )}
            {data.patterns.map(p => (
              <button key={p.type} onClick={() => setSelType(p.type)}
                className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs transition ${selected?.type === p.type ? 'border-primary bg-primary/10' : 'border-base-300 bg-base-100 hover:bg-base-200'}`}>
                <span className="font-semibold">{p.name}</span>
                <DirChip p={p} />
                <span className={`text-[9px] ${p.status === 'broken_out' ? 'text-success' : 'text-warning'}`}>{p.status === 'broken_out' ? 'broke out' : 'forming'}</span>
                <span className="text-[9px] text-base-content/40">{Math.round(p.confidence * 100)}%</span>
              </button>
            ))}
            {data.fibonacci && (
              <button onClick={() => setShowFib(f => !f)}
                className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1 text-xs transition ${showFib ? 'border-secondary bg-secondary/10 text-secondary' : 'border-base-300 bg-base-100 hover:bg-base-200'}`}>
                Fibonacci {showFib ? '✓' : ''}
              </button>
            )}
          </div>

          {/* chart */}
          <div className="rounded-xl border border-base-300 bg-base-100 p-1">
            <ReactECharts option={option} style={{ height: 420, width: '100%' }} notMerge />
          </div>

          {/* selected pattern detail + education */}
          {selected && (
            <div className="rounded-xl border border-base-300 bg-base-200/40 p-3 space-y-2">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-bold">{selected.name}</span>
                  <span className={`badge badge-xs ${selected.direction === 'bullish' ? 'badge-success' : selected.direction === 'bearish' ? 'badge-error' : 'badge-warning'}`}>{selected.direction}</span>
                  <span className="badge badge-xs badge-outline capitalize">{selected.category}</span>
                  <span className={`inline-flex items-center gap-1 text-[10px] ${selected.status === 'broken_out' ? 'text-success' : 'text-warning'}`}>
                    {selected.status === 'broken_out' ? <Check className="w-3 h-3" /> : <Clock className="w-3 h-3" />}{selected.status === 'broken_out' ? 'Broken out' : 'Still forming'}
                  </span>
                </div>
                <div className="flex items-center gap-3 text-xs">
                  {selected.breakout && <span>Trigger <b>{money(selected.breakout.level)}</b></span>}
                  {selected.target && <span className={dirTone(selected.direction)}>Target <b>{money(selected.target.price)}</b>{selected.target.pct != null ? ` (${selected.target.pct > 0 ? '+' : ''}${selected.target.pct}%)` : ''}</span>}
                </div>
              </div>

              <div className="rounded-lg bg-base-100 border border-base-300 p-2.5 text-[11px] space-y-1.5">
                <div className="flex items-center gap-1 font-semibold text-primary"><GraduationCap className="w-3.5 h-3.5" /> Learn to spot it yourself</div>
                <p><span className="font-semibold text-base-content/70">What it is:</span> <span className="text-base-content/70">{selected.education.what}</span></p>
                <p><span className="font-semibold text-base-content/70">On this chart:</span> <span className="text-base-content/70">{selected.education.where}</span></p>
                <p><span className="font-semibold text-base-content/70">How to spot it:</span> <span className="text-base-content/70">{selected.education.how_to_spot}</span></p>
                <p><span className="font-semibold text-success">Confirms when:</span> <span className="text-base-content/70">{selected.education.confirms}</span></p>
                <p><span className="font-semibold text-error">Invalidated if:</span> <span className="text-base-content/70">{selected.education.invalidates}</span></p>
              </div>
            </div>
          )}

          {/* Fibonacci detail — retracements (support) + extensions (breakout targets) */}
          {showFib && fib && (
            <div className="rounded-xl border border-secondary/30 bg-secondary/5 p-3 space-y-2">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold text-secondary">Fibonacci</span>
                  <span className="text-[11px] text-base-content/60">{fib.direction === 'up' ? '↑' : '↓'} swing {money(fib.swing.from.price)} → {money(fib.swing.to.price)}</span>
                  <span className={`badge badge-xs ${fib.position === 'broken_out' ? 'badge-success' : 'badge-ghost'}`}>
                    {fib.position === 'broken_out' ? 'broken out — targets live' : 'inside swing'}
                  </span>
                </div>
                {fib.next_target && (
                  <span className="text-xs">Next target <b className="text-success">{money(fib.next_target.price)}</b> ({(fib.next_target.ratio * 100).toFixed(1)}%)</span>
                )}
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-base-content/50 mb-1">Retracements (pullback support)</div>
                  <div className="flex flex-wrap gap-1">
                    {fib.levels.map(l => (
                      <span key={l.ratio} className="inline-flex items-center gap-1 rounded bg-base-100 border border-base-300 px-1.5 py-0.5 text-[10px]">
                        <span className="text-[#a78bfa] font-semibold">{(l.ratio * 100).toFixed(1)}%</span> {money(l.price)}
                      </span>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-base-content/50 mb-1">Extensions (targets after breakout)</div>
                  <div className="flex flex-wrap gap-1">
                    {(fib.extensions || []).map(l => (
                      <span key={l.ratio} className="inline-flex items-center gap-1 rounded bg-base-100 border border-success/30 px-1.5 py-0.5 text-[10px]">
                        <span className="text-success font-semibold">{(l.ratio * 100).toFixed(1)}%</span> {money(l.price)}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
              <p className="text-[11px] text-base-content/60 leading-snug">{fib.education.where} {fib.education.confirms}</p>
            </div>
          )}

          {/* actions */}
          <div className="flex flex-wrap gap-1.5">
            {selected && (
              <a href={chartPatternImageUrl(ticker, selected.type)} target="_blank" rel="noreferrer"
                className="btn btn-ghost btn-xs gap-1"><Download className="w-3.5 h-3.5" /> Download PNG</a>
            )}
            <button className="btn btn-ghost btn-xs gap-1" onClick={() => setShowAI(s => !s)}><Sparkles className="w-3.5 h-3.5" /> Ask AI</button>
            <button className="btn btn-ghost btn-xs gap-1" onClick={copyJson}>{copied ? <ClipboardCheck className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />} {copied ? 'Copied' : 'Copy JSON'}</button>
          </div>

          {showAI && (
            <IndicatorAIConsole
              selectionJson={{ ticker, spot: data.price, pattern: selected, fibonacci: data.fibonacci }}
              chips={selected ? [{ key: 'pattern', label: `${selected.name} (${selected.direction})`, tone: 'text-base-content/70' }] : []}
              analyzeFn={(sel, msgs) => analyzeTa(ticker, sel, msgs)}
            />
          )}
        </>
      )}
    </div>
  );
}
