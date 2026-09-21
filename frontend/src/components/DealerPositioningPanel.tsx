import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Boxes, ChevronDown, ChevronUp, Loader2, RefreshCw, Zap, GitMerge } from 'lucide-react';
import { fetchDealerPositioning, analyzeTa } from '../api';
import type { DealerPositioningData, ConfluenceZone, CandleInterval } from '../types';
import IndicatorAIConsole from './IndicatorAIConsole';
import GexChart from './GexChart';
import TAChart from './TAChart';
import type { OverlayLine, OverlayBand } from './taOverlay';
import { useLocalState, usePersistentSet, nearPrice, LevelProximity, LayerChips, confluenceBands, useTARegister, type ProxItem } from './taShared';

const RED = 'rgb(239,68,68)';
const GREEN = 'rgb(34,197,94)';

interface Layer { id: string; group: string; label: string; sub?: string; tone: string; color: string; lines?: OverlayLine[]; bands?: OverlayBand[]; json: Record<string, unknown> }

function buildLayers(d: DealerPositioningData): Layer[] {
  const out: Layer[] = [];
  const g = d.net_gex;
  out.push({
    id: 'net_gex', group: 'Regime', tone: g.sign === 'long' ? 'text-info' : 'text-warning', color: 'rgb(148,163,184)',
    label: `Net GEX ${g.value_millions ?? '—'}M (${g.sign} gamma)`, sub: g.sign === 'long' ? 'vol suppressed / pin' : 'vol expansion / trend',
    json: { indicator: 'net_gex', value_millions: g.value_millions, sign: g.sign, label: g.label },
  });
  const gl = d.gamma_levels;
  if (d.gamma_flip?.level != null) {
    const f = d.gamma_flip;
    const spotAbove = d.price != null ? d.price >= (f.level as number) : f.side === 'below';
    out.push({
      id: 'gamma_flip', group: 'Key levels', tone: 'text-amber-400', color: 'rgb(251,191,36)',
      label: `Gamma flip $${f.level}`,
      sub: `spot ${spotAbove ? 'above' : 'below'} flip · ${Math.abs(f.distance_pct ?? 0)}% away — dealers ${spotAbove ? 'LONG' : 'SHORT'} γ`,
      lines: [{ price: f.level as number, label: `γ-flip $${f.level}`, color: 'rgb(251,191,36)', dash: [1, 2] }],
      json: { indicator: 'gamma_flip', level: f.level, side: f.side, distance_pct: f.distance_pct, note: f.note },
    });
  }
  const addLevel = (id: string, lvl: { strike: number | null; distance_pct: number | null; gex_millions?: number | null } | null | undefined,
                    name: string, color: string, tone: string, note: string) => {
    if (!lvl?.strike) return;
    out.push({
      id, group: 'Key levels', tone, color,
      label: `${name} $${lvl.strike}`,
      sub: `${lvl.distance_pct != null ? `${lvl.distance_pct > 0 ? '+' : ''}${lvl.distance_pct}% vs spot · ` : ''}${note}`,
      lines: [{ price: lvl.strike, label: `${name} $${lvl.strike}`, color, dash: [5, 3] }],
      json: { indicator: id, strike: lvl.strike, distance_pct: lvl.distance_pct, gex_millions: lvl.gex_millions ?? null },
    });
  };
  if (gl) {
    addLevel('call_resistance', gl.call_resistance, 'Call Resistance', RED, 'text-error', 'biggest +GEX — caps rallies');
    addLevel('put_support', gl.put_support, 'Put Support', GREEN, 'text-success', 'biggest −GEX — floors dips');
    addLevel('hvl', gl.hvl, 'HVL', 'rgb(139,92,246)', 'text-violet-400', 'dominant gamma magnet / pin');
  } else {
    const w = d.walls;
    if (w.call_wall?.strike != null) addLevel('call_resistance', { strike: w.call_wall.strike, distance_pct: null, gex_millions: w.call_wall.gex_millions }, 'Call Resistance', RED, 'text-error', 'call wall');
    if (w.put_wall?.strike != null) addLevel('put_support', { strike: w.put_wall.strike, distance_pct: null, gex_millions: w.put_wall.gex_millions }, 'Put Support', GREEN, 'text-success', 'put wall');
  }
  const em = d.expected_move;
  const emLayer = (id: string, e: typeof em.em_30d, color: string, tone: string, tag: string) => {
    if (!e || e.upper == null || e.lower == null) return;
    out.push({
      id, group: 'Expected move', tone, color,
      label: `${tag} expected move ±${e.move_pct}%`, sub: `$${e.lower}–$${e.upper} · ATM IV ${e.iv_atm_pct}%`,
      bands: [{ top: e.upper, bottom: e.lower, label: `${tag} ±1σ`, color }],
      json: { indicator: 'expected_move', horizon: tag, dte: e.dte, iv_atm_pct: e.iv_atm_pct, move_pct: e.move_pct, upper: e.upper, lower: e.lower },
    });
  };
  emLayer('em_30d', em.em_30d, 'rgb(56,189,248)', 'text-sky-400', '30d');
  emLayer('em_45d', em.em_45d, 'rgb(139,92,246)', 'text-violet-400', '45d');
  return out;
}

function Banner({ d }: { d: DealerPositioningData }) {
  const long = d.net_gex.sign === 'long';
  return (
    <div className={`rounded-lg border p-2.5 ${long ? 'text-info border-info/30 bg-info/10' : 'text-warning border-warning/30 bg-warning/10'}`}>
      <div className="flex items-center gap-2">
        <Zap className="w-4 h-4 shrink-0" />
        <div>
          <p className="text-sm font-bold leading-tight">Dealers {long ? 'LONG' : 'SHORT'} gamma · Net GEX {d.net_gex.value_millions ?? '—'}M</p>
          <p className="text-[11px] opacity-70 mt-0.5">{d.net_gex.label}{d.gamma_flip?.note ? ` ${d.gamma_flip.note}` : ''}</p>
        </div>
      </div>
    </div>
  );
}

export default function DealerPositioningPanel({ ticker, price, confluenceZones }: { ticker: string; price?: number; confluenceZones?: ConfluenceZone[] }) {
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<DealerPositioningData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = usePersistentSet('ta:sel:dealer');
  const [interval, setInterval] = useLocalState<CandleInterval>('ta:iv:dealer', '1d');
  const [showConfluence, setShowConfluence] = useLocalState('ta:conf:dealer', false);
  const [chartSpot, setChartSpot] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const r = await fetchDealerPositioning(ticker);
      const dp = r.dealer_positioning;
      setData(dp);
      setSelected(prev => {
        if (prev.size) return prev;
        const def = new Set<string>(['net_gex']);
        if (dp.gamma_flip?.level != null) def.add('gamma_flip');
        if (dp.gamma_levels?.call_resistance || dp.walls.call_wall) def.add('call_resistance');
        if (dp.gamma_levels?.put_support || dp.walls.put_wall) def.add('put_support');
        if (dp.gamma_levels?.hvl) def.add('hvl');
        if (dp.expected_move.em_30d) def.add('em_30d');
        return def;
      });
    } catch (e: any) {
      setError(e?.message || 'Failed to load dealer positioning');
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

  const selectionJson = useMemo(() => ({
    ticker, spot: data?.price ?? null, as_of: data?.as_of,
    net_gex_sign: data?.net_gex.sign, expirations_used: data?.expirations_used,
    selected_indicators: selectedLayers.map(l => l.json),
  }), [ticker, data, selectedLayers]);

  const toggle = (id: string) => setSelected(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const setGroup = (gLayers: { id: string }[], on: boolean) => setSelected(prev => { const n = new Set(prev); gLayers.forEach(l => on ? n.add(l.id) : n.delete(l.id)); return n; });

  const spot = chartSpot ?? data?.price ?? price ?? null;
  const overlays = useMemo(() => ({
    lines: selectedLayers.flatMap(l => (l.lines || []).map(ln => ({ price: ln.price, label: ln.label, color: ln.color || l.color, dash: ln.dash }) as OverlayLine)),
    bands: selectedLayers.flatMap(l => (l.bands || []).map(b => ({ top: b.top, bottom: b.bottom, label: b.label, color: b.color }) as OverlayBand)),
  }), [selectedLayers]);
  const chartBands = useMemo(() => [...overlays.bands, ...(showConfluence ? confluenceBands(confluenceZones, spot) : [])], [overlays.bands, showConfluence, confluenceZones, spot]);
  const near = useMemo(() => nearPrice(layers.flatMap(l => [
    ...(l.lines || []).map(ln => ({ id: l.id, price: ln.price, label: ln.label, color: ln.color || l.color, json: l.json })),
    ...(l.bands || []).map(b => ({ id: l.id, price: (b.top + b.bottom) / 2, label: b.label, color: b.color, json: l.json })),
  ]) as ProxItem[], spot), [layers, spot]);
  useTARegister('dealer', 'Dealer Gamma', selectionJson, selectedLayers.length);

  return (
    <div className="bg-base-300 rounded-xl overflow-hidden shadow-xl border border-white/[0.05]">
      <button type="button" onClick={() => setExpanded(o => !o)} className="w-full flex items-center justify-between px-4 py-3 bg-gradient-to-r from-base-200/60 to-transparent">
        <div className="flex items-center gap-2 text-left">
          <Boxes className="w-4 h-4 text-secondary" />
          <div>
            <h4 className="text-sm font-semibold text-base-content/90">Dealer Positioning <span className="text-[10px] text-base-content/40">(Gamma / GEX)</span></h4>
            <p className="text-[10px] text-base-content/50">Net GEX regime · gamma flip · Call Resistance / Put Support / HVL · expected move — where dealer hedging gates price</p>
          </div>
        </div>
        {expanded ? <ChevronUp className="w-4 h-4 shrink-0" /> : <ChevronDown className="w-4 h-4 shrink-0" />}
      </button>

      {expanded && (
        <div className="p-3 border-t border-white/[0.05] space-y-3">
          {loading && <div className="flex items-center gap-2 text-sm text-base-content/60 py-6 justify-center"><Loader2 className="w-4 h-4 animate-spin" /> Reading the option chain…</div>}
          {error && !loading && <div className="alert alert-error text-xs flex items-center justify-between"><span>{error}</span><button className="btn btn-ghost btn-xs" onClick={load}><RefreshCw className="w-3.5 h-3.5" /> Retry</button></div>}

          {data && !loading && (
            <>
              <Banner d={data} />
              {/* GEX-by-strike distribution — the hero chart */}
              <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2">
                <GexChart profile={data.gex_profile || []} levels={data.gamma_levels} spot={data.price || 0} netGexMM={data.net_gex.value_millions} />
              </div>
              <LayerChips groups={groups.map(([name, ls]) => ({ name, layers: ls }))} selected={selected} onToggle={toggle} onGroupAll={setGroup} />
              <TAChart
                ticker={ticker} interval={interval} onInterval={setInterval}
                lines={overlays.lines} bands={chartBands} height={340}
                title={<>Price · gamma levels · expected move <span className="text-base-content/40 font-normal">· {selectedLayers.filter(l => l.lines || l.bands).length} shown</span></>}
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
                emptyHint="Toggle gamma or expected-move layers below to include them in the AI read."
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}
