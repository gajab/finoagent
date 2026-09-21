import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Loader2, GitMerge, Sparkles, Eraser } from 'lucide-react';
import { fetchMicrostructure, fetchMarketStructure, fetchRegime, fetchDealerPositioning, analyzeTa } from '../api';
import type { ConfluenceZone, CandleInterval, MarketStructureData } from '../types';
import TAChart from './TAChart';
import IndicatorAIConsole from './IndicatorAIConsole';
import { useLocalState, usePersistentSet, nearPrice, LevelProximity, LayerChips, confluenceBands, type ProxItem } from './taShared';
import { vpLayers, structureLayers, liquidityLayers, regimeLayers, gammaLayers, taToOverlays, taToProx, type TALayer } from './taLayers';

interface Method { key: string; label: string; accent: string }
const METHODS: Method[] = [
  { key: 'vp', label: 'Volume Profile', accent: 'text-violet-400' },
  { key: 'structure', label: 'Market Structure', accent: 'text-sky-400' },
  { key: 'liquidity', label: 'Liquidity', accent: 'text-emerald-400' },
  { key: 'regime', label: 'Regime', accent: 'text-amber-400' },
  { key: 'gamma', label: 'Dealer Gamma', accent: 'text-rose-400' },
];

interface MState { layers?: TALayer[]; loading: boolean; err?: string; open: boolean }

const groupBy = (layers: TALayer[]) => {
  const m = new Map<string, TALayer[]>();
  for (const l of layers) { if (!m.has(l.group)) m.set(l.group, []); m.get(l.group)!.push(l); }
  return Array.from(m.entries()).map(([name, ls]) => ({ name, layers: ls }));
};

// Presets select the DAILY tier only (macro POC = the daily-timeframe volume profile).
const PRESETS: { key: string; label: string; methods: string[]; pick: (l: TALayer) => boolean }[] = [
  { key: 'key', label: 'Key levels', methods: ['vp', 'gamma', 'regime'], pick: (l) => l.id === 'vp_daily_poc' || l.id === 'gamma_flip' || l.id === 'gamma_call_res' || l.id === 'gamma_put_sup' || l.id === 'regime_vwap' },
  { key: 'liq', label: 'Liquidity', methods: ['liquidity'], pick: (l) => l.id === 'liquidity_daily_pools' || l.id === 'liquidity_confluence' },
  { key: 'value', label: 'Value & VWAP', methods: ['vp'], pick: (l) => l.id === 'vp_daily_va' || l.id.startsWith('vp_avwap') },
];

export default function UnifiedTAWorkspace({ ticker, spot, confluenceZones }: { ticker: string; spot?: number; confluenceZones?: ConfluenceZone[] }) {
  const [interval, setInterval] = useLocalState<CandleInterval>('ta:uni:iv', '1d');
  const [selected, setSelected] = usePersistentSet('ta:uni:sel');
  const [showConfluence, setShowConfluence] = useLocalState('ta:uni:conf', true);
  const [ms, setMs] = useState<Record<string, MState>>({});
  const [chartSpot, setChartSpot] = useState<number | null>(null);
  const rawMs = useRef<Promise<MarketStructureData> | null>(null);   // structure + liquidity share one fetch

  useEffect(() => { rawMs.current = null; setMs({}); setChartSpot(null); }, [ticker]);

  const spotNow = chartSpot ?? spot ?? null;
  const getRawMs = useCallback(() => {
    if (!rawMs.current) rawMs.current = fetchMarketStructure(ticker).then(r => r.market_structure);
    return rawMs.current;
  }, [ticker]);
  const loaders: Record<string, () => Promise<TALayer[]>> = useMemo(() => ({
    vp: () => fetchMicrostructure(ticker).then(r => vpLayers(r.microstructure)),
    structure: () => getRawMs().then(structureLayers),
    liquidity: () => getRawMs().then(liquidityLayers),
    regime: () => fetchRegime(ticker).then(r => regimeLayers(r.regime)),
    gamma: () => fetchDealerPositioning(ticker).then(r => gammaLayers(r.dealer_positioning)),
  }), [ticker, getRawMs]);

  const loadMethod = useCallback(async (key: string, open = true): Promise<TALayer[]> => {
    const existing = ms[key]?.layers;
    if (existing) { setMs(p => ({ ...p, [key]: { ...p[key], open } })); return existing; }
    setMs(p => ({ ...p, [key]: { ...p[key], loading: true, err: undefined, open } }));
    try {
      const layers = await loaders[key]();
      setMs(p => ({ ...p, [key]: { layers, loading: false, open } }));
      return layers;
    } catch (e) {
      setMs(p => ({ ...p, [key]: { loading: false, open, err: e instanceof Error ? e.message : 'Failed to load' } }));
      return [];
    }
  }, [ms, loaders]);

  const toggleMethod = (key: string) => {
    const st = ms[key];
    if (!st || (!st.layers && !st.loading)) { loadMethod(key, true); return; }
    setMs(p => ({ ...p, [key]: { ...p[key], open: !p[key].open } }));
  };

  const toggle = (id: string) => setSelected(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const setGroupAll = (layers: { id: string }[], on: boolean) => setSelected(prev => { const n = new Set(prev); layers.forEach(l => on ? n.add(l.id) : n.delete(l.id)); return n; });
  const clearIds = (ids: string[]) => setSelected(prev => { const n = new Set(prev); ids.forEach(id => n.delete(id)); return n; });
  const clearAll = () => setSelected(new Set());

  const applyPreset = async (p: typeof PRESETS[number]) => {
    const loaded = await Promise.all(p.methods.map(k => loadMethod(k, true)));
    setSelected(new Set(loaded.flat().filter(p.pick).map(l => l.id)));
  };

  const allLayers = useMemo(() => Object.values(ms).flatMap(s => s.layers || []), [ms]);
  const selectedLayers = useMemo(() => allLayers.filter(l => !l.status && selected.has(l.id)), [allLayers, selected]);
  const overlays = useMemo(() => taToOverlays(selectedLayers), [selectedLayers]);
  const bands = useMemo(() => [...overlays.bands, ...(showConfluence ? confluenceBands(confluenceZones, spotNow) : [])], [overlays.bands, showConfluence, confluenceZones, spotNow]);
  const near = useMemo(() => nearPrice(taToProx(allLayers) as ProxItem[], spotNow), [allLayers, spotNow]);
  const statusContext = useMemo(() => allLayers.filter(l => l.status).map(l => l.json), [allLayers]);

  const selectionJson = useMemo(() => ({
    ticker, spot: spotNow, timeframe: interval,
    note: 'Unified multi-method read — the levels drawn on one chart across volume profile, structure, liquidity, regime and dealer gamma.',
    context: statusContext,
    selected_layers: selectedLayers.map(l => l.json),
  }), [ticker, spotNow, interval, statusContext, selectedLayers]);

  return (
    <div className="bg-base-300 rounded-xl overflow-hidden shadow-xl border border-white/[0.05] p-3 space-y-2">
      {/* presets / actions (no title) */}
      <div className="flex items-center gap-1.5 flex-wrap text-[10px]">
        <span className="text-base-content/40 font-semibold uppercase tracking-wide">Presets</span>
        {PRESETS.map(p => (
          <button key={p.key} onClick={() => applyPreset(p)} className="px-2 py-0.5 rounded-md border border-white/10 bg-base-100/40 hover:border-primary/40 text-base-content/70 font-semibold">{p.label}</button>
        ))}
        <span className="ml-auto text-base-content/40">{selectedLayers.length} on chart</span>
        <button onClick={clearAll} className="px-2 py-0.5 rounded-md border border-white/10 hover:border-error/40 text-base-content/50 flex items-center gap-1"><Eraser className="w-3 h-3" /> Clear all</button>
      </div>

      {/* method accordion — collapsed & unloaded by default, so the chart stays clean */}
      <div className="rounded-lg border border-white/[0.06] bg-base-200/20 divide-y divide-white/[0.04]">
        {METHODS.map(m => {
          const st = ms[m.key];
          const layers = st?.layers || [];
          const statusL = layers.filter(l => l.status);
          const drawableL = layers.filter(l => !l.status);
          const selCount = drawableL.filter(l => selected.has(l.id)).length;
          return (
            <div key={m.key}>
              <button onClick={() => toggleMethod(m.key)} className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-base-100/20">
                {st?.open ? <ChevronDown className="w-3.5 h-3.5 text-base-content/40" /> : <ChevronRight className="w-3.5 h-3.5 text-base-content/40" />}
                <span className={`text-[12px] font-semibold ${m.accent}`}>{m.label}</span>
                {st?.loading && <Loader2 className="w-3 h-3 animate-spin text-base-content/40" />}
                {selCount > 0 && <span className="badge badge-xs badge-primary">{selCount}</span>}
                {!st && <span className="text-[9px] text-base-content/35 ml-auto">click to load</span>}
              </button>
              {st?.open && (
                <div className="px-2.5 pb-2 space-y-1.5">
                  {st.err ? <div className="text-[11px] text-error/80 py-1">{st.err}</div>
                    : st.loading ? <div className="text-[11px] text-base-content/40 py-1 flex items-center gap-1"><Loader2 className="w-3 h-3 animate-spin" /> loading…</div>
                    : (
                      <>
                        {statusL.length > 0 && (
                          <div className="flex flex-wrap gap-1.5">
                            {statusL.map(s => (
                              <span key={s.id} className={`inline-flex items-center gap-1 rounded-md border border-white/10 bg-base-100/50 px-1.5 py-0.5 text-[10.5px] font-semibold ${s.tone}`}>{s.label}</span>
                            ))}
                          </div>
                        )}
                        {drawableL.length > 0 ? (
                          <>
                            <div className="flex items-center justify-between">
                              <span className="text-[9px] uppercase tracking-wide text-base-content/35">Draw on chart</span>
                              {selCount > 0 && <button className="text-[9px] text-base-content/45 hover:text-error flex items-center gap-0.5" onClick={() => clearIds(drawableL.map(l => l.id))}><Eraser className="w-2.5 h-2.5" /> clear</button>}
                            </div>
                            <LayerChips groups={groupBy(drawableL)} selected={selected} onToggle={toggle} onGroupAll={setGroupAll} />
                          </>
                        ) : statusL.length > 0 ? (
                          <p className="text-[10px] text-base-content/40">Status read — nothing to draw on the chart.</p>
                        ) : (
                          <p className="text-[10px] text-base-content/40">No drawable levels from this method right now.</p>
                        )}
                      </>
                    )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <TAChart
        ticker={ticker} interval={interval} onInterval={setInterval}
        lines={overlays.lines} bands={bands} height={440}
        title={<>Unified levels <span className="text-base-content/40 font-normal">· {selectedLayers.length} on chart</span></>}
        rightExtra={confluenceZones?.length ? (
          <button onClick={() => setShowConfluence(v => !v)}
            className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold transition ${showConfluence ? 'bg-secondary/20 text-secondary' : 'text-base-content/45 hover:text-base-content'}`}
            title="High-conviction confluence zones (where the methods agree)">
            <GitMerge className="w-3 h-3" /> Confluence
          </button>
        ) : undefined}
        onSpot={setChartSpot}
      />

      <LevelProximity levels={near} onAdd={(l) => toggle(l.id)} />

      {selectedLayers.length > 0 ? (
        <IndicatorAIConsole
          selectionJson={selectionJson}
          chips={selectedLayers.map(l => ({ key: l.id, label: l.label, tone: l.tone, onRemove: () => toggle(l.id) }))}
          analyzeFn={(sel, msgs) => analyzeTa(ticker, sel, msgs)}
          emptyHint="Open a method above and toggle the levels you want, then analyze them together."
        />
      ) : (
        <div className="rounded-lg border border-primary/20 bg-primary/[0.04] px-3 py-2 text-[11px] text-base-content/55 flex items-center gap-1.5">
          <Sparkles className="w-3.5 h-3.5 text-primary" /> Open a method above (or a preset) and toggle levels onto the chart — then an AI read over exactly that selection appears here.
        </div>
      )}
    </div>
  );
}
