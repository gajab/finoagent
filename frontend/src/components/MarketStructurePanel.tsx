import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  GitBranch, ChevronDown, ChevronUp, Loader2, RefreshCw,
  TrendingUp, TrendingDown, Minus, GitMerge,
} from 'lucide-react';
import { fetchMarketStructure, analyzeTa } from '../api';
import type { MarketStructureData, TimeframeBlock, ConfluenceZone, CandleInterval } from '../types';
import IndicatorAIConsole from './IndicatorAIConsole';
import TAChart from './TAChart';
import type { OverlayLine, OverlayBand } from './taOverlay';
import { useLocalState, usePersistentSet, nearPrice, LevelProximity, LayerChips, confluenceBands, useTARegister, type ProxItem } from './taShared';

// ── selectable layers (draw on chart + serialize to the AI JSON) ──
interface MSLine { price: number; label: string; dash?: number[] }
interface MSBand { top: number; bottom: number; label: string; color: string }
interface MSLayer {
  id: string; group: string; label: string; sub?: string;
  tone: string; color: string;
  lines?: MSLine[]; bands?: MSBand[];
  json: Record<string, unknown>;
}

const GREEN = 'rgb(34,197,94)';
const RED = 'rgb(239,68,68)';

const TF_DEFS = [
  ['daily', 'rgb(139,92,246)', 'text-violet-400', 'D'],
  ['h4', 'rgb(56,189,248)', 'text-sky-400', '4H'],
  ['h1', 'rgb(251,191,36)', 'text-amber-400', '1H'],
] as const;

function buildLayers(d: MarketStructureData): MSLayer[] {
  const out: MSLayer[] = [];
  for (const [key, color, tone, tag] of TF_DEFS) {
    const tf = d.timeframes[key] as TimeframeBlock | null;
    if (!tf) continue;
    const g = `${tf.label} structure`;
    const st = tf.structure;

    const lines: MSLine[] = [];
    if (st.last_event) lines.push({ price: st.last_event.level, label: `${tag} ${st.last_event.type} ${st.last_event.direction === 'bullish' ? '▲' : '▼'}` });
    if (st.recent_swing_high != null) lines.push({ price: st.recent_swing_high, label: `${tag} swing H`, dash: [2, 2] });
    if (st.recent_swing_low != null) lines.push({ price: st.recent_swing_low, label: `${tag} swing L`, dash: [2, 2] });
    if (lines.length) out.push({
      id: `${key}_struct`, group: g, tone, color,
      label: `Structure — ${st.last_event ? `${st.last_event.type} ${st.last_event.direction}` : tf.trend}`,
      sub: st.last_event ? `last ${st.last_event.type} @ $${st.last_event.level}` : `trend ${tf.trend}`,
      lines, json: { indicator: 'market_structure', timeframe: key, trend: tf.trend, last_event: st.last_event, recent_swing_high: st.recent_swing_high, recent_swing_low: st.recent_swing_low, events: st.events },
    });

    const obs = (tf.order_blocks || []).filter(o => !o.mitigated);
    if (obs.length) out.push({
      id: `${key}_ob`, group: g, tone, color, label: `Order Blocks (${obs.length})`, sub: 'unmitigated demand/supply',
      bands: obs.map(o => ({ top: o.top, bottom: o.bottom, label: `${tag} ${o.type === 'bullish' ? 'Demand' : 'Supply'} OB`, color: o.type === 'bullish' ? GREEN : RED })),
      json: { indicator: 'order_blocks', timeframe: key, blocks: obs },
    });

    const fvgs = (tf.fair_value_gaps || []).filter(f => !f.filled);
    if (fvgs.length) out.push({
      id: `${key}_fvg`, group: g, tone, color, label: `Fair-Value Gaps (${fvgs.length})`, sub: 'unmitigated imbalances',
      bands: fvgs.map(f => ({ top: f.top, bottom: f.bottom, label: `${tag} ${f.type === 'bullish' ? 'Bull' : 'Bear'} FVG`, color: f.type === 'bullish' ? GREEN : RED })),
      json: { indicator: 'fair_value_gaps', timeframe: key, gaps: fvgs },
    });

    const pools = tf.liquidity_pools || [];
    if (pools.length) out.push({
      id: `${key}_pools`, group: g, tone, color, label: `Liquidity Pools (${pools.length})`, sub: 'unswept BSL / SSL',
      lines: pools.map(p => ({ price: p.price, label: `${p.type} $${p.price}${p.strength === 'strong' ? ' ★' : ''}`, dash: [6, 3] })),
      json: { indicator: 'liquidity_pools', timeframe: key, pools },
    });
  }

  const cf = d.confluence || [];
  if (cf.length) out.push({
    id: 'confluence', group: 'Confluence', tone: 'text-emerald-400', color: 'rgb(16,185,129)',
    label: `MTF Confluence (${cf.length})`, sub: 'stacked unmitigated zones',
    bands: cf.map(c => ({ top: c.zone[1], bottom: c.zone[0], label: `${c.bias === 'bullish' ? 'Bull' : 'Bear'} confluence`, color: c.bias === 'bullish' ? 'rgb(16,185,129)' : 'rgb(244,63,94)' })),
    json: { indicator: 'mtf_confluence', zones: cf },
  });
  return out;
}

function toOverlays(sel: MSLayer[]): { lines: OverlayLine[]; bands: OverlayBand[] } {
  return {
    lines: sel.flatMap(l => (l.lines || []).map(ln => ({ price: ln.price, label: ln.label, color: l.color, dash: ln.dash }))),
    bands: sel.flatMap(l => (l.bands || []).map(b => ({ top: b.top, bottom: b.bottom, label: b.label, color: b.color }))),
  };
}
function proxItems(layers: MSLayer[]): ProxItem[] {
  return layers.flatMap(l => [
    ...(l.lines || []).map(ln => ({ id: l.id, price: ln.price, label: ln.label, color: l.color, json: l.json })),
    ...(l.bands || []).map(b => ({ id: l.id, price: (b.top + b.bottom) / 2, label: b.label, color: b.color, json: l.json })),
  ]);
}

function BiasBanner({ d }: { d: MarketStructureData }) {
  const b = d.bias;
  const Icon = b.overall === 'bullish' ? TrendingUp : b.overall === 'bearish' ? TrendingDown : Minus;
  const tone = b.overall === 'bullish' ? 'text-success border-success/30 bg-success/10'
    : b.overall === 'bearish' ? 'text-error border-error/30 bg-error/10'
      : 'text-warning border-warning/30 bg-warning/10';
  const trendIco = (t?: string) => t === 'up' ? '▲' : t === 'down' ? '▼' : '◦';
  return (
    <div className={`rounded-lg border p-2.5 ${tone}`}>
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Icon className="w-4 h-4 shrink-0" />
          <div>
            <p className="text-sm font-bold leading-tight capitalize">{b.overall} structure {b.aligned && <span className="badge badge-xs badge-success ml-1 align-middle">aligned</span>}</p>
            <p className="text-[11px] opacity-70">{b.note}</p>
          </div>
        </div>
        <div className="flex gap-2 text-[11px]">
          {(['daily', 'h4', 'h1'] as const).map(k => {
            const tf = d.timeframes[k];
            const lbl = k === 'daily' ? 'D' : k === 'h4' ? '4H' : '1H';
            return <span key={k} className="badge badge-sm bg-base-100/50 border-base-content/10 tabular-nums">{lbl} {tf ? trendIco(tf.trend) : '—'}</span>;
          })}
        </div>
      </div>
    </div>
  );
}

export default function MarketStructurePanel({ ticker, price, confluenceZones }: { ticker: string; price?: number; confluenceZones?: ConfluenceZone[] }) {
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<MarketStructureData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = usePersistentSet('ta:sel:ms');
  const [interval, setInterval] = useLocalState<CandleInterval>('ta:iv:ms', '1d');
  const [showConfluence, setShowConfluence] = useLocalState('ta:conf:ms', false);
  const [chartSpot, setChartSpot] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetchMarketStructure(ticker);
      const md = r.market_structure;
      setData(md);
      setSelected(prev => {
        if (prev.size) return prev;
        const def = new Set<string>();
        if (md.timeframes.daily) { def.add('daily_struct'); if (md.timeframes.daily.liquidity_pools?.length) def.add('daily_pools'); }
        if (md.confluence?.length) def.add('confluence');
        return def;
      });
    } catch (e: any) {
      setError(e?.message || 'Failed to load market structure');
    } finally {
      setLoading(false);
    }
  }, [ticker, setSelected]);

  useEffect(() => { setData(null); setError(null); }, [ticker]);
  useEffect(() => { if (expanded && !data && !loading && !error) load(); }, [expanded, data, loading, error, load]);

  const layers = useMemo(() => (data ? buildLayers(data) : []), [data]);
  const selectedLayers = useMemo(() => layers.filter(l => selected.has(l.id)), [layers, selected]);
  const groups = useMemo(() => {
    const m = new Map<string, MSLayer[]>();
    for (const l of layers) { if (!m.has(l.group)) m.set(l.group, []); m.get(l.group)!.push(l); }
    return Array.from(m.entries());
  }, [layers]);

  const spot = chartSpot ?? data?.price ?? price ?? null;
  const overlays = useMemo(() => toOverlays(selectedLayers), [selectedLayers]);
  const bands = useMemo(() => [...overlays.bands, ...(showConfluence ? confluenceBands(confluenceZones, spot) : [])], [overlays.bands, showConfluence, confluenceZones, spot]);
  const near = useMemo(() => nearPrice(proxItems(layers), spot), [layers, spot]);

  const selectionJson = useMemo(() => ({
    ticker, spot: data?.price ?? price ?? null, as_of: data?.as_of, mtf_bias: data?.bias,
    selected_indicators: selectedLayers.map(l => l.json),
  }), [ticker, data, price, selectedLayers]);
  useTARegister('ms', 'Market Structure', selectionJson, selectedLayers.length);

  const toggle = (id: string) => setSelected(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const setGroup = (gLayers: { id: string }[], on: boolean) => setSelected(prev => { const n = new Set(prev); gLayers.forEach(l => on ? n.add(l.id) : n.delete(l.id)); return n; });

  return (
    <div className="bg-base-300 rounded-xl overflow-hidden shadow-xl border border-white/[0.05]">
      <button type="button" onClick={() => setExpanded(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-gradient-to-r from-base-200/60 to-transparent">
        <div className="flex items-center gap-2 text-left">
          <GitBranch className="w-4 h-4 text-secondary" />
          <div>
            <h4 className="text-sm font-semibold text-base-content/90">Market Structure &amp; Liquidity</h4>
            <p className="text-[10px] text-base-content/50">BOS / CHOCH across Daily · 4H · 1H, liquidity pools (BSL/SSL) &amp; confluence → candlestick chart + AI read</p>
          </div>
        </div>
        {expanded ? <ChevronUp className="w-4 h-4 shrink-0" /> : <ChevronDown className="w-4 h-4 shrink-0" />}
      </button>

      {expanded && (
        <div className="p-3 border-t border-white/[0.05] space-y-3">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-base-content/60 py-6 justify-center">
              <Loader2 className="w-4 h-4 animate-spin" /> Mapping structure across timeframes…
            </div>
          )}
          {error && !loading && (
            <div className="alert alert-error text-xs flex items-center justify-between">
              <span>{error}</span>
              <button className="btn btn-ghost btn-xs" onClick={load}><RefreshCw className="w-3.5 h-3.5" /> Retry</button>
            </div>
          )}

          {data && !loading && (
            <>
              <BiasBanner d={data} />
              <LayerChips groups={groups.map(([name, ls]) => ({ name, layers: ls }))} selected={selected} onToggle={toggle} onGroupAll={setGroup} />
              <TAChart
                ticker={ticker} interval={interval} onInterval={setInterval}
                lines={overlays.lines} bands={bands} height={400}
                title={<>Structure &amp; liquidity <span className="text-base-content/40 font-normal">· {selectedLayers.length} shown</span></>}
                rightExtra={confluenceZones?.length ? (
                  <button onClick={() => setShowConfluence(v => !v)}
                    className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold transition ${showConfluence ? 'bg-secondary/20 text-secondary' : 'text-base-content/45 hover:text-base-content'}`}
                    title="Overlay high-conviction confluence zones (where all TA methods agree)">
                    <GitMerge className="w-3 h-3" /> Confluence
                  </button>
                ) : undefined}
                onSpot={setChartSpot}
              />
              <LevelProximity levels={near} onAdd={(l) => toggle(l.id)} />
              <IndicatorAIConsole
                selectionJson={selectionJson}
                chips={selectedLayers.map(l => ({ key: l.id, label: l.label, tone: l.tone, onRemove: () => toggle(l.id) }))}
                analyzeFn={(sel, msgs) => analyzeTa(ticker, sel, msgs)}
                emptyHint="Toggle structure/liquidity layers below to include them in the AI read."
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}
