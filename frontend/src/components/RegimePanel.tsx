import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Gauge, ChevronDown, ChevronUp, Loader2, RefreshCw, TrendingUp, TrendingDown, Minus, GitMerge,
} from 'lucide-react';
import { fetchRegime, analyzeTa } from '../api';
import type { RegimeData, ConfluenceZone, CandleInterval } from '../types';
import IndicatorAIConsole from './IndicatorAIConsole';
import TAChart from './TAChart';
import type { OverlayLine } from './taOverlay';
import { useLocalState, usePersistentSet, nearPrice, LevelProximity, LayerChips, confluenceBands, useTARegister, type ProxItem } from './taShared';

interface Layer { id: string; group: string; label: string; sub?: string; tone: string; color: string; lines?: OverlayLine[]; json: Record<string, unknown> }

const regimeTone = (r?: string) => r === 'trending' ? 'text-success border-success/30 bg-success/10'
  : r === 'mean_reverting' ? 'text-info border-info/30 bg-info/10'
    : 'text-warning border-warning/30 bg-warning/10';
const regimeLabel = (r?: string) => r === 'trending' ? 'Trending' : r === 'mean_reverting' ? 'Mean-Reverting' : 'Transitional';

function buildLayers(d: RegimeData): Layer[] {
  const out: Layer[] = [];
  const tfDefs = [['daily', d.timeframes.daily, 'text-violet-400', 'rgb(139,92,246)'],
    ['h4', d.timeframes.h4, 'text-sky-400', 'rgb(56,189,248)']] as const;
  for (const [key, tf, tone, color] of tfDefs) {
    if (!tf) continue;
    out.push({
      id: `regime_${key}`, group: 'Regime', tone, color,
      label: `${tf.label} — ${regimeLabel(tf.regime)}`,
      sub: `H ${tf.hurst ?? '—'} · ER ${tf.efficiency_ratio ?? '—'} · ${tf.confidence}`,
      json: { indicator: 'regime', timeframe: key, regime: tf.regime, hurst: tf.hurst, efficiency_ratio: tf.efficiency_ratio, confidence: tf.confidence },
    });
  }
  if (d.zscore) {
    const z = d.zscore;
    out.push({
      id: 'zscore', group: 'Statistical extremes', tone: 'text-amber-400', color: 'rgb(251,191,36)',
      label: `50-day VWAP z-score ${z.z}`,
      sub: `${z.state}${z.vwap != null ? ` · VWAP $${z.vwap}` : ''}`,
      lines: z.vwap != null ? [{ price: z.vwap, label: `50d VWAP $${z.vwap} (z ${z.z})`, color: 'rgb(251,191,36)', dash: [6, 4] }] : undefined,
      json: { indicator: 'vwap_zscore', window: z.window, vwap: z.vwap, z: z.z, state: z.state, distance_pct: z.distance_pct },
    });
  }
  return out;
}

function Banner({ d }: { d: RegimeData }) {
  const r = d.regime;
  const Icon = r.overall === 'trending' ? TrendingUp : r.overall === 'mean_reverting' ? Minus : TrendingDown;
  return (
    <div className={`rounded-lg border p-2.5 ${regimeTone(r.overall)}`}>
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Icon className="w-4 h-4 shrink-0" />
          <div>
            <p className="text-sm font-bold leading-tight">{regimeLabel(r.overall)} regime
              <span className="text-[10px] uppercase tracking-wider opacity-70 ml-1">· {r.confidence} confidence{r.aligned && ' · TF-aligned'}</span>
            </p>
            <p className="text-[11px] opacity-70 mt-0.5">{r.playbook}</p>
          </div>
        </div>
        <div className="flex gap-2 text-[11px] shrink-0">
          {(['daily', 'h4'] as const).map(k => {
            const tf = d.timeframes[k];
            return <span key={k} className="badge badge-sm bg-base-100/50 border-base-content/10 tabular-nums">{k === 'daily' ? 'D' : '4H'} H{tf?.hurst ?? '—'}</span>;
          })}
        </div>
      </div>
    </div>
  );
}

function toLines(sel: Layer[]): OverlayLine[] {
  return sel.flatMap(l => (l.lines || []).map(ln => ({ price: ln.price, label: ln.label, color: ln.color || l.color, dash: ln.dash })));
}
function proxItems(layers: Layer[]): ProxItem[] {
  return layers.flatMap(l => (l.lines || []).map(ln => ({ id: l.id, price: ln.price, label: ln.label, color: ln.color || l.color, json: l.json })));
}

export default function RegimePanel({ ticker, price, confluenceZones }: { ticker: string; price?: number; confluenceZones?: ConfluenceZone[] }) {
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<RegimeData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = usePersistentSet('ta:sel:regime');
  const [interval, setInterval] = useLocalState<CandleInterval>('ta:iv:regime', '1d');
  const [showConfluence, setShowConfluence] = useLocalState('ta:conf:regime', false);
  const [chartSpot, setChartSpot] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const r = await fetchRegime(ticker);
      setData(r.regime);
      setSelected(prev => {
        if (prev.size) return prev;
        const def = new Set<string>();
        if (r.regime.timeframes.daily) def.add('regime_daily');
        if (r.regime.timeframes.h4) def.add('regime_h4');
        if (r.regime.zscore) def.add('zscore');
        return def;
      });
    } catch (e: any) {
      setError(e?.message || 'Failed to load regime');
    } finally { setLoading(false); }
  }, [ticker, setSelected]);

  useEffect(() => { setData(null); setError(null); }, [ticker]);
  useEffect(() => { if (expanded && !data && !loading && !error) load(); }, [expanded, data, loading, error, load]);

  const layers = useMemo(() => (data ? buildLayers(data) : []), [data]);
  const selectedLayers = useMemo(() => layers.filter(l => selected.has(l.id)), [layers, selected]);
  const groups = useMemo(() => {
    const m = new Map<string, Layer[]>();
    for (const l of layers) { if (!m.has(l.group)) m.set(l.group, []); m.get(l.group)!.push(l); }
    return Array.from(m.entries());
  }, [layers]);

  const spot = chartSpot ?? data?.price ?? price ?? null;
  const lines = useMemo(() => toLines(selectedLayers), [selectedLayers]);
  const bands = useMemo(() => (showConfluence ? confluenceBands(confluenceZones, spot) : []), [showConfluence, confluenceZones, spot]);
  const near = useMemo(() => nearPrice(proxItems(layers), spot), [layers, spot]);

  const selectionJson = useMemo(() => ({
    ticker, spot: data?.price ?? price ?? null, as_of: data?.as_of,
    regime_overall: data?.regime.overall, favored: data?.regime.favored,
    selected_indicators: selectedLayers.map(l => l.json),
  }), [ticker, data, price, selectedLayers]);
  useTARegister('regime', 'Market Regime', selectionJson, selectedLayers.length);

  const toggle = (id: string) => setSelected(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  return (
    <div className="bg-base-300 rounded-xl overflow-hidden shadow-xl border border-white/[0.05]">
      <button type="button" onClick={() => setExpanded(o => !o)} className="w-full flex items-center justify-between px-4 py-3 bg-gradient-to-r from-base-200/60 to-transparent">
        <div className="flex items-center gap-2 text-left">
          <Gauge className="w-4 h-4 text-secondary" />
          <div>
            <h4 className="text-sm font-semibold text-base-content/90">Market Regime &amp; Statistical Extremes</h4>
            <p className="text-[10px] text-base-content/50">Hurst + Efficiency Ratio (Daily/4H) &amp; 50-day VWAP z-score → trend-vs-mean-revert playbook + candlestick chart</p>
          </div>
        </div>
        {expanded ? <ChevronUp className="w-4 h-4 shrink-0" /> : <ChevronDown className="w-4 h-4 shrink-0" />}
      </button>

      {expanded && (
        <div className="p-3 border-t border-white/[0.05] space-y-3">
          {loading && <div className="flex items-center gap-2 text-sm text-base-content/60 py-6 justify-center"><Loader2 className="w-4 h-4 animate-spin" /> Classifying the regime…</div>}
          {error && !loading && <div className="alert alert-error text-xs flex items-center justify-between"><span>{error}</span><button className="btn btn-ghost btn-xs" onClick={load}><RefreshCw className="w-3.5 h-3.5" /> Retry</button></div>}

          {data && !loading && (
            <>
              <Banner d={data} />
              {data.regime.favored?.length > 0 && (
                <div className="flex flex-wrap gap-1 items-center">
                  <span className="text-[10px] uppercase tracking-wider text-base-content/40">Favored:</span>
                  {data.regime.favored.map((f, i) => <span key={i} className="badge badge-sm bg-base-100/50 border-base-content/10">{f.replace(/_/g, ' ')}</span>)}
                </div>
              )}
              <LayerChips groups={groups.map(([name, ls]) => ({ name, layers: ls }))} selected={selected} onToggle={toggle}
                onGroupAll={(ls, on) => setSelected(prev => { const n = new Set(prev); ls.forEach(l => on ? n.add(l.id) : n.delete(l.id)); return n; })} />
              <TAChart
                ticker={ticker} interval={interval} onInterval={setInterval}
                lines={lines} bands={bands} height={400}
                title={<>Price &amp; 50-day VWAP <span className="text-base-content/40 font-normal">· {lines.length} on chart</span></>}
                rightExtra={confluenceZones?.length ? (
                  <button onClick={() => setShowConfluence(v => !v)}
                    className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold transition ${showConfluence ? 'bg-secondary/20 text-secondary' : 'text-base-content/45 hover:text-base-content'}`}
                    title="Overlay high-conviction confluence zones">
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
                emptyHint="Toggle the regime or z-score layers below to include them in the AI read."
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}
