import React, { createContext, useContext, useState, useCallback, useEffect, useMemo } from 'react';
import { Sparkles } from 'lucide-react';
import { toFill, type OverlayBand } from './taOverlay';
import type { ConfluenceZone, MicroChatMessage } from '../types';
import IndicatorAIConsole from './IndicatorAIConsole';

// ── localStorage-backed state (per-viewer convenience; safe in private mode) ──
export function useLocalState<T>(key: string, initial: T): [T, (v: T | ((p: T) => T)) => void] {
  const [v, setV] = useState<T>(() => {
    try { const s = localStorage.getItem(key); return s != null ? (JSON.parse(s) as T) : initial; } catch { return initial; }
  });
  const set = useCallback((nv: T | ((p: T) => T)) => {
    setV(prev => {
      const val = typeof nv === 'function' ? (nv as (p: T) => T)(prev) : nv;
      try { localStorage.setItem(key, JSON.stringify(val)); } catch { /* ignore */ }
      return val;
    });
  }, [key]);
  return [v, set];
}

// A Set<string> mirrored to localStorage (persists the user's chosen layers across sessions/tickers).
export function usePersistentSet(key: string): [Set<string>, React.Dispatch<React.SetStateAction<Set<string>>>] {
  const [s, setS] = useState<Set<string>>(() => {
    try { const v = localStorage.getItem(key); if (v) return new Set(JSON.parse(v) as string[]); } catch { /* ignore */ }
    return new Set();
  });
  useEffect(() => { try { localStorage.setItem(key, JSON.stringify([...s])); } catch { /* ignore */ } }, [key, s]);
  return [s, setS];
}

// ── "near price now" proximity read ──
export interface ProxItem { id: string; price: number; label: string; color: string; json?: Record<string, unknown> }
export interface NearLevel extends ProxItem { distPct: number }
export function nearPrice(items: ProxItem[], spot: number | null, pct = 4): NearLevel[] {
  if (!spot) return [];
  return items
    .filter(l => l.price != null && Math.abs((l.price - spot) / spot) * 100 <= pct)
    .map(l => ({ ...l, distPct: ((l.price - spot) / spot) * 100 }))
    .sort((a, b) => Math.abs(a.distPct) - Math.abs(b.distPct));
}

// strip "$138.47" / "$128–$167" price fragments so a verbose level label fits on a compact chip
export const stripPrices = (s: string) => s.replace(/\s*\$[\d.,]+(?:\s*[–-]\s*\$?[\d.,]+)?/g, '').trim() || s;

// Compact horizontal "what's in play right now" strip — each chip adds the level to the AI read.
export function LevelProximity({ levels, onAdd }: { levels: NearLevel[]; onAdd?: (l: NearLevel) => void }) {
  if (!levels.length) return null;
  return (
    <div className="rounded-lg border border-white/[0.06] bg-base-200/25 px-2 py-1.5">
      <div className="flex flex-wrap gap-1.5">
        {levels.slice(0, 12).map((l, i) => (
          <button key={`${l.id}-${i}`} onClick={() => onAdd?.(l)} title={`${l.label} · ＋AI`}
            className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-base-100/40 px-1.5 py-0.5 text-[10px] hover:border-primary/40 transition">
            <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: l.color }} />
            <span className="text-base-content/70 truncate max-w-[120px]">{stripPrices(l.label)}</span>
            <span className={`tabular-nums font-semibold ${l.distPct >= 0 ? 'text-error/80' : 'text-success/80'}`}>{l.distPct >= 0 ? '+' : ''}{l.distPct.toFixed(1)}%</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// Dense, colored toggle-chip toolbar (replaces the tall left checkbox tray). Grouped, each chip
// filled when active; price/detail live on the chart + in the tooltip, keeping the chips terse.
export interface ChipLayer { id: string; label: string; tone: string; color?: string; sub?: string }
export function LayerChips({ groups, selected, onToggle, onGroupAll }: {
  groups: { name: string; layers: ChipLayer[] }[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  onGroupAll: (layers: ChipLayer[], on: boolean) => void;
}) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-base-200/20 px-2 py-1.5">
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        {groups.map(g => {
          const allOn = g.layers.every(l => selected.has(l.id));
          return (
            <div key={g.name} className="min-w-0">
              <div className="flex items-center gap-1.5 mb-1">
                <span className="text-[9.5px] font-bold uppercase tracking-wide text-base-content/40">{g.name}</span>
                <button className="text-[9px] text-primary/70 hover:text-primary font-semibold" onClick={() => onGroupAll(g.layers, !allOn)}>{allOn ? 'clear' : 'all'}</button>
              </div>
              <div className="flex flex-wrap gap-1">
                {g.layers.map(l => {
                  const on = selected.has(l.id);
                  const activeStyle = on && l.color ? { color: l.color, backgroundColor: toFill(l.color, 0.14), borderColor: toFill(l.color, 0.5) } : undefined;
                  return (
                    <button key={l.id} onClick={() => onToggle(l.id)} title={`${l.label}${l.sub ? ' · ' + l.sub : ''}`} style={activeStyle}
                      className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10.5px] font-medium transition ${on ? (l.color ? '' : `${l.tone} border-current/40`) : 'text-base-content/40 border-base-content/15 hover:border-base-content/35'}`}>
                      <span className="w-1.5 h-1.5 rounded-full" style={{ background: on ? (l.color || 'currentColor') : 'rgba(148,163,184,0.3)' }} />
                      {stripPrices(l.label)}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── confluence zones (from the setup engine) → chart bands ──
export function confluenceBands(zones: ConfluenceZone[] | undefined, spot: number | null, maxPct = 12): OverlayBand[] {
  if (!zones || !spot) return [];
  return zones
    .filter(z => z.distance_pct == null || Math.abs(z.distance_pct) <= maxPct)
    .slice(0, 6)
    .map(z => ({ top: z.high, bottom: z.low, label: `⋈ ×${z.n_sources} confluence`, color: z.center >= spot ? 'rgb(244,63,94)' : 'rgb(34,197,94)' }));
}

// ── cross-panel workspace: compose every panel's selection into one AI read ──
export interface TAEntry { key: string; title: string; selectionJson: Record<string, unknown>; chipCount: number }
interface TAWorkspaceCtx { entries: Record<string, TAEntry>; register: (e: TAEntry) => void; unregister: (key: string) => void }
const Ctx = createContext<TAWorkspaceCtx | null>(null);

export function TAWorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [entries, setEntries] = useState<Record<string, TAEntry>>({});
  const register = useCallback((e: TAEntry) => setEntries(p => (p[e.key] && p[e.key].chipCount === e.chipCount && JSON.stringify(p[e.key].selectionJson) === JSON.stringify(e.selectionJson) ? p : { ...p, [e.key]: e })), []);
  const unregister = useCallback((key: string) => setEntries(p => { if (!p[key]) return p; const n = { ...p }; delete n[key]; return n; }), []);
  return <Ctx.Provider value={{ entries, register, unregister }}>{children}</Ctx.Provider>;
}
export function useTAWorkspace() { return useContext(Ctx); }

/** A panel calls this to publish its current selection into the shared workspace. */
export function useTARegister(key: string, title: string, selectionJson: Record<string, unknown>, chipCount: number) {
  const ws = useTAWorkspace();
  const sig = JSON.stringify(selectionJson);
  useEffect(() => {
    if (!ws) return;
    if (chipCount > 0) ws.register({ key, title, selectionJson, chipCount });
    else ws.unregister(key);
    return () => ws.unregister(key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ws, key, title, chipCount, sig]);
}

/** The top-of-Advanced bar that analyzes every panel's selection together. */
export function UnifiedAnalyze({ ticker, analyzeFn }: { ticker: string; analyzeFn: (sel: Record<string, unknown>, msgs: MicroChatMessage[]) => Promise<MicroChatMessage> }) {
  const ws = useTAWorkspace();
  const entries = useMemo(() => Object.values(ws?.entries || {}), [ws?.entries]);
  const total = entries.reduce((n, e) => n + e.chipCount, 0);
  const composed = useMemo(() => ({
    ticker,
    note: 'Unified multi-method Institutional-TA selection — analyze across volume profile, market structure, regime and dealer gamma together.',
    methods: entries.map(e => ({ method: e.title, ...e.selectionJson })),
  }), [entries, ticker]);
  if (total === 0) return null;
  return (
    <div className="rounded-xl border border-primary/25 bg-primary/[0.05] p-3 space-y-2">
      <div className="flex items-center gap-1.5 text-xs font-bold text-primary">
        <Sparkles className="w-4 h-4" /> Analyze everything — {total} layer{total === 1 ? '' : 's'} across {entries.length} method{entries.length === 1 ? '' : 's'}
      </div>
      <p className="text-[10px] text-base-content/50">Selections you make in any panel below are gathered here. Get one AI read that reasons over volume profile, structure, regime and dealer positioning at once.</p>
      <IndicatorAIConsole
        selectionJson={composed}
        chips={entries.map(e => ({ key: e.key, label: `${e.title} (${e.chipCount})` }))}
        analyzeFn={analyzeFn}
        emptyHint="Select layers in the panels below to compose a unified read."
      />
    </div>
  );
}
