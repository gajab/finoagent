/**
 * betaTaChartModel.ts — what goes ON the Beta chart: the plan, the confluence zones and the method layers, each
 * normalised into one ChartLevel shape; plus the y-range maths. Pure (no React / ECharts) so it is unit-tested.
 *
 * The method layers come from the SAME builders the classic Advanced tab uses (taLayers.ts), so a level is drawn at
 * exactly the price the classic panel reports — the Beta only chooses which to show and where to label them.
 */
import type { CandleInterval, ConfluenceZone } from '../../types';
import type { TALayer } from '../../components/taLayers';
import type { LayerKey, PlanLevels } from './betaTaModel';

export type Tier = 'daily' | 'h4' | 'h1';
/** The structure/volume tier that matches the candle resolution on screen. */
export const tierForInterval = (iv: CandleInterval): Tier => (iv === '1d' || iv === '1wk' ? 'daily' : 'h1');

export const COLOR = {
  up: '#10b981', down: '#ef4444', amber: '#f59e0b', sky: '#38bdf8', violet: '#8b5cf6', slate: '#94a3b8', rose: '#f43f5e', now: '#e2e8f0',
};

export interface ChartLevel {
  id: string; layer: LayerKey; kind: 'line' | 'band';
  /** the level's price (a band's midpoint) */
  price: number; top?: number; bottom?: number;
  label: string; color: string; dashed: boolean; priority: number;
  /** the full, untrimmed name (hover text) */
  title?: string;
}

const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);
const f2 = (n: number) => n.toFixed(2);

/** A level's gutter text: no `$`, no timeframe prefix (the chart already is that timeframe), no parenthetical, and
 *  always ending in the price so a label can be read without hunting for its line. */
export function shortLabel(raw: string, price: number): string {
  const base = raw.replace(/\$/g, '').replace(/^(Daily|4H|1H)\s+/i, '').replace(/\s*\([^)]*\)/g, '').replace(/\s+/g, ' ').trim();
  const has = base.includes(String(Math.trunc(price))) && /\d/.test(base);
  return has ? base : `${base} ${f2(price)}`.trim();
}
/** `226.0–228.2` for a tight band, `219–259` for a wide one. */
export function rangeText(lo: number, hi: number): string {
  const d = hi - lo < 5 ? 1 : 0;
  return `${lo.toFixed(d)}–${hi.toFixed(d)}`;
}

/** `rgb(34,197,94)` -> `#22c55e` (ECharts and the gutter want one colour string; taLayers uses rgb()). */
export function toHex(c: string): string {
  const m = c.match(/rgba?\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
  if (!m) return c;
  return '#' + [m[1], m[2], m[3]].map(x => Math.max(0, Math.min(255, parseInt(x, 10))).toString(16).padStart(2, '0')).join('');
}

export function levelsFromPlan(plan: PlanLevels | null): ChartLevel[] {
  if (!plan) return [];
  const out: ChartLevel[] = [];
  if (plan.entryLow != null && plan.entryHigh != null && plan.entryHigh - plan.entryLow > 1e-9) {
    out.push({ id: 'plan-zone', layer: 'plan', kind: 'band', price: (plan.entryLow + plan.entryHigh) / 2, top: plan.entryHigh, bottom: plan.entryLow, label: `Zone ${rangeText(plan.entryLow, plan.entryHigh)}`, title: 'Entry zone', color: COLOR.amber, dashed: false, priority: 1 });
  }
  out.push({ id: 'plan-entry', layer: 'plan', kind: 'line', price: plan.entry, label: `Entry ${f2(plan.entry)}`, color: COLOR.amber, dashed: true, priority: 9 });
  out.push({ id: 'plan-stop', layer: 'plan', kind: 'line', price: plan.stop, label: `Stop ${f2(plan.stop)}`, color: COLOR.down, dashed: true, priority: 9 });
  out.push({ id: 'plan-t1', layer: 'plan', kind: 'line', price: plan.t1, label: `T1 ${f2(plan.t1)}`, color: COLOR.up, dashed: true, priority: 9 });
  if (plan.t2 != null) out.push({ id: 'plan-t2', layer: 'plan', kind: 'line', price: plan.t2, label: `T2 ${f2(plan.t2)}`, color: COLOR.up, dashed: true, priority: 7 });
  return out;
}

/** Confluence zones (where several methods agree) as bands, nearest first, capped. */
export function levelsFromZones(zones: ConfluenceZone[] | undefined, spot: number | null, maxPct = 12, cap = 6): ChartLevel[] {
  if (!zones || !finite(spot) || spot <= 0) return [];
  return zones
    .filter(z => finite(z.low) && finite(z.high) && (z.distance_pct == null || Math.abs(z.distance_pct) <= maxPct))
    .slice(0, cap)
    .map((z, i): ChartLevel => ({
      id: `zone-${i}`, layer: 'levels', kind: 'band', price: z.center, top: z.high, bottom: z.low,
      label: `${z.kind === 'support' ? 'Support' : z.kind === 'resistance' ? 'Resistance' : 'Zone'} ${f2(z.center)}`,
      title: `${z.kind} zone ${rangeText(z.low, z.high)} — ${z.n_sources} signals agree`,
      color: z.center >= spot ? COLOR.rose : COLOR.up, dashed: false, priority: 6,
    }));
}

const layerIds = (layer: LayerKey, tier: Tier): ((id: string) => boolean) => {
  switch (layer) {
    case 'structure': return id => id === `structure_${tier}_swings`;
    case 'blocks': return id => id === `structure_${tier}_ob` || id === `structure_${tier}_fvg`;
    case 'liquidity': return id => id === `liquidity_${tier}_pools` || id === 'liquidity_confluence';
    case 'dealer': return id => ['gamma_flip', 'gamma_call_res', 'gamma_put_sup', 'gamma_hvl', 'gamma_em30'].includes(id);
    case 'value': return id => id === `vp_${tier}_poc` || id === `vp_${tier}_va` || id === 'vp_naked' || id.startsWith('vp_avwap_') || id === 'regime_vwap';
    default: return () => false;
  }
};
const PRIORITY: Record<LayerKey, number> = { plan: 9, levels: 6, dealer: 5, value: 4, blocks: 4, structure: 3, liquidity: 3 };

/** One method layer -> chart levels, at the tier matching the candles on screen. */
export function levelsFromLayers(layers: TALayer[] | undefined, layer: LayerKey, tier: Tier): ChartLevel[] {
  if (!layers) return [];
  const want = layerIds(layer, tier);
  const out: ChartLevel[] = [];
  for (const l of layers) {
    if (l.status || !want(l.id)) continue;
    (l.lines || []).forEach((ln, i) => {
      if (!finite(ln.price)) return;
      out.push({ id: `${l.id}:l${i}`, layer, kind: 'line', price: ln.price, label: shortLabel(ln.label, ln.price), title: ln.label.replace(/\$/g, ''), color: toHex(ln.color || l.color), dashed: !!(ln.dash && ln.dash.length), priority: PRIORITY[layer] });
    });
    (l.bands || []).forEach((b, i) => {
      if (!finite(b.top) || !finite(b.bottom)) return;
      const mid = (b.top + b.bottom) / 2;
      const top = Math.max(b.top, b.bottom), bottom = Math.min(b.top, b.bottom);
      const name = b.label.replace(/\$/g, '').replace(/^(Daily|4H|1H)\s+/i, '').trim();
      out.push({ id: `${l.id}:b${i}`, layer, kind: 'band', price: mid, top, bottom, label: `${name} ${rangeText(bottom, top)}`, title: b.label.replace(/\$/g, ''), color: toHex(b.color), dashed: false, priority: PRIORITY[layer] });
    });
  }
  return out;
}

/**
 * Visible y-range: the candles on screen plus any level within 35% of that range beyond it (so the plan and the
 * nearby levels are always in view, but one far-off level can't squash the candles). Mirrors the classic TAChart rule.
 */
export function yRange(visible: { h: number | null; l: number | null }[], levels: ChartLevel[]): { min: number; max: number } | null {
  const highs = visible.map(c => c.h).filter(finite), lows = visible.map(c => c.l).filter(finite);
  if (!highs.length || !lows.length) return null;
  let lo = Math.min(...lows), hi = Math.max(...highs);
  const rng = hi - lo || hi * 0.02;
  const near = (p: number) => p >= lo - 0.35 * rng && p <= hi + 0.35 * rng;
  const extra: number[] = [];
  for (const l of levels) {
    for (const p of l.kind === 'band' ? [l.top, l.bottom] : [l.price]) if (finite(p) && near(p)) extra.push(p);
  }
  if (extra.length) { lo = Math.min(lo, ...extra); hi = Math.max(hi, ...extra); }
  const pad = (hi - lo) * 0.05 || hi * 0.01;
  return { min: lo - pad, max: hi + pad };
}

/** A line is drawn only if it lies inside the axis range; a band is drawn if ANY part of it does (the chart clips it). */
export const inRange = (l: ChartLevel, r: { min: number; max: number }): boolean =>
  l.kind === 'band' ? (finite(l.top) && finite(l.bottom) && l.top >= r.min && l.bottom <= r.max) : (l.price >= r.min && l.price <= r.max);

/** A label is shown only when the level's own price is on screen (a clipped band's midpoint may not be). */
export const labelVisible = (l: ChartLevel, r: { min: number; max: number }): boolean => l.price >= r.min && l.price <= r.max;

/**
 * Drop a level that sits (almost) on top of a higher-priority one — e.g. a swing-support line exactly at a confluence
 * zone — so the gutter doesn't carry two labels for one price. Bands never suppress each other; a line is dropped only
 * by a level within `tol` (relative) that outranks it (ties keep the first).
 */
export function dedupeLevels(levels: ChartLevel[], tol = 0.002): ChartLevel[] {
  return levels.filter((l, i) => {
    if (l.kind !== 'line') return true;
    return !levels.some((o, j) => j !== i && Math.abs(o.price - l.price) <= tol * l.price && (o.priority > l.priority || (o.priority === l.priority && j < i)) && (o.kind === 'line' || o.kind === 'band'));
  });
}
