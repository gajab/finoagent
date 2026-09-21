import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Layers, ChevronDown, ChevronUp, Loader2, RefreshCw, GitMerge } from 'lucide-react';
import { fetchMicrostructure, analyzeTa } from '../api';
import type { MicrostructureData, AnchoredVWAP, ConfluenceZone, CandleInterval } from '../types';
import IndicatorAIConsole from './IndicatorAIConsole';
import TAChart from './TAChart';
import type { OverlayLine, OverlayBand } from './taOverlay';
import { useLocalState, usePersistentSet, nearPrice, LevelProximity, LayerChips, confluenceBands, useTARegister, type ProxItem } from './taShared';

// ─────────────────────────────────────────────────────────────────────────
// Indicator "layers" — one selectable unit that both draws on the chart and
// serializes into the AI-summary JSON (unified selection).
// ─────────────────────────────────────────────────────────────────────────
type LayerKind = 'poc' | 'va' | 'lvn' | 'naked' | 'avwap';
interface Layer {
  id: string;
  group: string;
  label: string;
  sub?: string;
  kind: LayerKind;
  tone: string;                 // tailwind text colour for the chip
  color: string;                // rgb() for the chart
  dash?: number[];
  price?: number;
  band?: [number, number];
  points?: number[];
  json: Record<string, unknown>;
}

const distTxt = (level: number, spot: number) => {
  if (!spot) return '';
  const d = ((level - spot) / spot) * 100;
  return `${d >= 0 ? '+' : ''}${d.toFixed(1)}% vs spot`;
};

function buildLayers(data: MicrostructureData): Layer[] {
  const out: Layer[] = [];
  const spot = data.price ?? 0;
  const tfDefs = [
    ['daily', data.timeframe_profiles.daily, 'rgb(139,92,246)', 'text-violet-400'],
    ['h4', data.timeframe_profiles.h4, 'rgb(56,189,248)', 'text-sky-400'],
    ['h1', data.timeframe_profiles.h1, 'rgb(251,191,36)', 'text-amber-400'],
  ] as const;

  for (const [key, tf, color, tone] of tfDefs) {
    if (!tf) continue;
    const g = tf.label;
    out.push({
      id: `${key}_poc`, group: g, kind: 'poc', tone, color, price: tf.poc,
      label: `POC $${tf.poc}`, sub: distTxt(tf.poc, spot),
      json: { indicator: 'volume_profile_poc', timeframe: key, label: tf.label, poc: tf.poc },
    });
    out.push({
      id: `${key}_va`, group: g, kind: 'va', tone, color, band: [tf.val, tf.vah],
      label: `Value Area $${tf.val}–$${tf.vah}`, sub: `${tf.value_area_pct}% of volume`,
      json: { indicator: 'value_area', timeframe: key, vah: tf.vah, val: tf.val, value_area_pct: tf.value_area_pct },
    });
    const lvnPts = (tf.lvns || []).map(n => n.price).filter((x): x is number => x != null);
    if (lvnPts.length) {
      out.push({
        id: `${key}_lvn`, group: g, kind: 'lvn', tone, color, dash: [2, 2], points: lvnPts,
        label: `LVNs (${lvnPts.length})`, sub: lvnPts.map(p => `$${p}`).join(', '),
        json: { indicator: 'low_volume_nodes', timeframe: key, nodes: tf.lvns },
      });
    }
  }

  const nk = data.naked_pocs || [];
  if (nk.length) {
    out.push({
      id: 'naked_pocs', group: 'Naked / Virgin POCs', kind: 'naked', tone: 'text-rose-400',
      color: 'rgb(244,63,94)', dash: [6, 3], points: nk.map(n => n.price),
      label: `Naked POCs (${nk.length})`, sub: 'untested — magnet targets',
      json: { indicator: 'naked_pocs', levels: nk },
    });
  }

  const avwapEntries: [string, AnchoredVWAP | null][] = [
    ['ytd', data.avwap.ytd], ['earnings', data.avwap.earnings],
    ['high_52w', data.avwap.high_52w], ['low_52w', data.avwap.low_52w],
  ];
  for (const [k, a] of avwapEntries) {
    if (!a) continue;
    out.push({
      id: `avwap_${k}`, group: 'Anchored VWAP', kind: 'avwap', tone: 'text-emerald-400',
      color: 'rgb(16,185,129)', dash: [8, 4], price: a.value,
      label: `${a.label} $${a.value}`, sub: distTxt(a.value, spot),
      json: {
        indicator: 'anchored_vwap', anchor: k, label: a.label, anchor_date: a.anchor_date,
        value: a.value, side: a.side, distance_pct: a.distance_pct,
      },
    });
  }
  return out;
}

function toOverlays(sel: Layer[]): { lines: OverlayLine[]; bands: OverlayBand[] } {
  const lines: OverlayLine[] = []; const bands: OverlayBand[] = [];
  for (const l of sel) {
    if (l.kind === 'va' && l.band) bands.push({ top: l.band[1], bottom: l.band[0], label: `${l.group} VA`, color: l.color });
    else if (l.points?.length) l.points.forEach(p => lines.push({ price: p, label: l.kind === 'naked' ? `Naked $${p}` : 'LVN', color: l.color, dash: l.dash }));
    else if (l.price != null) lines.push({ price: l.price, label: l.label, color: l.color, dash: l.dash });
  }
  return { lines, bands };
}

function proxItems(layers: Layer[]): ProxItem[] {
  const items: ProxItem[] = [];
  for (const l of layers) {
    if (l.price != null) items.push({ id: l.id, price: l.price, label: l.label, color: l.color, json: l.json });
    else if (l.points?.length) l.points.forEach(p => items.push({ id: l.id, price: p, label: `${l.label.split(' (')[0]} $${p}`, color: l.color, json: l.json }));
    else if (l.band) items.push({ id: l.id, price: (l.band[0] + l.band[1]) / 2, label: l.label, color: l.color, json: l.json });
  }
  return items;
}

// ─────────────────────────────────────────────────────────────────────────

export default function MicrostructurePanel({ ticker, price, confluenceZones }: { ticker: string; price?: number; confluenceZones?: ConfluenceZone[] }) {
  const [expanded, setExpanded] = useState(false);
  const [data, setData] = useState<MicrostructureData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = usePersistentSet('ta:sel:micro');
  const [interval, setInterval] = useLocalState<CandleInterval>('ta:iv:micro', '1d');
  const [showConfluence, setShowConfluence] = useLocalState('ta:conf:micro', false);
  const [chartSpot, setChartSpot] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetchMicrostructure(ticker);
      const md = r.microstructure;
      setData(md);
      setSelected(prev => {
        if (prev.size) return prev;                       // keep the user's persisted choice
        const def = new Set<string>();
        (['daily', 'h4', 'h1'] as const).forEach(k => { if (md.timeframe_profiles[k]) def.add(`${k}_poc`); });
        if (md.naked_pocs?.length) def.add('naked_pocs');
        if (md.avwap.ytd) def.add('avwap_ytd');
        return def;
      });
    } catch (e: any) {
      setError(e?.message || 'Failed to load microstructure');
    } finally {
      setLoading(false);
    }
  }, [ticker, setSelected]);

  useEffect(() => { setData(null); setError(null); }, [ticker]);   // keep persisted selection across tickers
  useEffect(() => { if (expanded && !data && !loading && !error) load(); }, [expanded, data, loading, error, load]);

  const layers = useMemo(() => (data ? buildLayers(data) : []), [data]);
  const selectedLayers = useMemo(() => layers.filter(l => selected.has(l.id)), [layers, selected]);
  const groups = useMemo(() => {
    const m = new Map<string, Layer[]>();
    for (const l of layers) { if (!m.has(l.group)) m.set(l.group, []); m.get(l.group)!.push(l); }
    return Array.from(m.entries());
  }, [layers]);

  const spot = chartSpot ?? data?.price ?? price ?? null;
  const overlays = useMemo(() => toOverlays(selectedLayers), [selectedLayers]);
  const bands = useMemo(() => [...overlays.bands, ...(showConfluence ? confluenceBands(confluenceZones, spot) : [])], [overlays.bands, showConfluence, confluenceZones, spot]);
  const near = useMemo(() => nearPrice(proxItems(layers), spot), [layers, spot]);

  const selectionJson = useMemo(() => ({
    ticker,
    spot: data?.price ?? price ?? null,
    as_of: data?.as_of,
    selected_indicators: selectedLayers.map(l => l.json),
  }), [ticker, data, price, selectedLayers]);
  useTARegister('micro', 'Volume Profile', selectionJson, selectedLayers.length);

  const toggle = (id: string) => setSelected(prev => {
    const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n;
  });
  const setGroup = (gLayers: { id: string }[], on: boolean) => setSelected(prev => {
    const n = new Set(prev); gLayers.forEach(l => on ? n.add(l.id) : n.delete(l.id)); return n;
  });

  // ── render ──
  return (
    <div className="bg-base-300 rounded-xl overflow-hidden shadow-xl border border-white/[0.05]">
      <button type="button" onClick={() => setExpanded(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-gradient-to-r from-base-200/60 to-transparent">
        <div className="flex items-center gap-2 text-left">
          <Layers className="w-4 h-4 text-secondary" />
          <div>
            <h4 className="text-sm font-semibold text-base-content/90">Microstructure &amp; Multi-Timeframe Volume Profile</h4>
            <p className="text-[10px] text-base-content/50">POC · Value Area · LVNs across 3 horizons, naked POCs &amp; anchored-VWAP matrix → candlestick chart + AI trade read</p>
          </div>
        </div>
        {expanded ? <ChevronUp className="w-4 h-4 shrink-0" /> : <ChevronDown className="w-4 h-4 shrink-0" />}
      </button>

      {expanded && (
        <div className="p-3 border-t border-white/[0.05] space-y-3">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-base-content/60 py-6 justify-center">
              <Loader2 className="w-4 h-4 animate-spin" /> Building volume-profile hierarchy…
            </div>
          )}
          {error && !loading && (
            <div className="alert alert-error text-xs flex items-center justify-between">
              <span>{error}</span>
              <button className="btn btn-ghost btn-xs" onClick={load}><RefreshCw className="w-3.5 h-3.5" /> Retry</button>
            </div>
          )}

          {data && !loading && (
            <div className="space-y-2">
              <LayerChips groups={groups.map(([name, ls]) => ({ name, layers: ls }))} selected={selected} onToggle={toggle} onGroupAll={setGroup} />
              <TAChart
                ticker={ticker} interval={interval} onInterval={setInterval}
                lines={overlays.lines} bands={bands} height={400}
                title={<>Volume-profile levels <span className="text-base-content/40 font-normal">· {selectedLayers.length} shown</span></>}
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
                emptyHint="Toggle layers below (or ＋a nearby level) to include them in the AI read."
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
