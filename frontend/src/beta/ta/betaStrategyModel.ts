/**
 * betaStrategyModel.ts — the pure logic behind the Beta "Strategies" board: which tiles exist, how the famous-trader
 * rule-sets are grouped / filtered / worded, how a trader's exit is read against price, and how a strategy's levels
 * become chart levels. No React, no network — unit-tested.
 *
 * The rule-sets themselves come from the backend (trader_strategy_service → trader_lenses, the SAME rules the Trade
 * Manager's "Famous-trader consensus" uses). This file never re-derives a rule; it only words and arranges them.
 */
import type { TraderStrategy, TraderStrategiesData, TraderFamily, TraderStop, TraderRule } from '../../types';
import { COLOR, type ChartLevel } from './betaTaChartModel';

export type Stance = 'bullish' | 'neutral' | 'bearish';
export const FAMILY_ORDER: TraderFamily[] = ['trend', 'leaders', 'timing', 'volatility'];
export const FAMILY_LABEL: Record<TraderFamily, string> = {
  trend: 'Trend following', leaders: 'Leaders & momentum', timing: 'Pullbacks & timing', volatility: 'Volatility & volume',
};
export const GRADE_WORD: Record<string, string> = { all: 'All rules met', most: 'Most rules met', some: 'Some rules met', few: 'Few rules met', no_data: 'Not scored' };

const num = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);
const stanceOf = (s: string): Stance => (s === 'bullish' || s === 'bearish' ? s : 'neutral');

// ── tiles ────────────────────────────────────────────────────────────────────────────────────────────────────────
export type TileKind = 'engine' | 'named' | 'trader';
export interface Tile {
  id: string; kind: TileKind; label: string; hint: string;
  family: TraderFamily | null;
  stance: Stance | null;
  /** rules met / scored — traders only */
  met: number | null; total: number | null;
  available: boolean;
  /** engine setups: how many */
  count: number | null;
}

export const ENGINE_TILES: { id: 'swing' | 'position' | 'day'; label: string; hint: string }[] = [
  { id: 'swing', label: 'Swing', hint: 'days to weeks' },
  { id: 'position', label: 'Position', hint: 'weeks to months' },
  { id: 'day', label: 'Day trade', hint: 'intraday' },
];

export function engineTiles(counts: { swing: number | null; position: number | null; day: number | null }): Tile[] {
  return ENGINE_TILES.map(e => ({ id: e.id, kind: 'engine', label: e.label, hint: e.hint, family: null, stance: null, met: null, total: null, available: true, count: counts[e.id] }));
}

export function namedTiles(named: { key: string; label: string; hint: string }[]): Tile[] {
  return named.map(n => ({ id: n.key, kind: 'named', label: n.label, hint: n.hint, family: null, stance: null, met: null, total: null, available: true, count: null }));
}

/** The 13 traders that do NOT already have a full named-strategy screen, grouped by family, most bullish first inside a family. */
export function traderTiles(data: TraderStrategiesData | null | undefined): Tile[] {
  if (!data) return [];
  const rows = data.strategies.filter(s => !s.named_elsewhere);
  const fam = (s: TraderStrategy) => FAMILY_ORDER.indexOf(s.family);
  return [...rows]
    .sort((a, b) => fam(a) - fam(b) || (b.available ? b.d : -9) - (a.available ? a.d : -9) || a.trader.localeCompare(b.trader))
    .map(s => ({
      id: s.key, kind: 'trader' as const, label: s.trader, hint: s.available ? (s.philosophy || '') : (s.reason || s.philosophy || ''), family: s.family,
      stance: s.available ? stanceOf(s.stance) : null, met: s.available ? s.met : null, total: s.available ? s.total : null, available: s.available, count: null,
    }));
}

export interface Filter { family: 'all' | TraderFamily; stance: 'all' | Stance }
export const NO_FILTER: Filter = { family: 'all', stance: 'all' };
/** Filters apply to the trader group only — engine and named tiles are never hidden by a trader filter. */
export function filterTraders(tiles: Tile[], f: Filter): Tile[] {
  return tiles.filter(t => (f.family === 'all' || t.family === f.family) && (f.stance === 'all' || t.stance === f.stance));
}

/** How many traders in each family / stance, for the filter chips (so a chip never promises an empty list). */
export function filterCounts(tiles: Tile[]): { family: Record<string, number>; stance: Record<string, number> } {
  const family: Record<string, number> = { all: tiles.length }, stance: Record<string, number> = { all: tiles.length };
  for (const t of tiles) {
    if (t.family) family[t.family] = (family[t.family] || 0) + 1;
    if (t.stance) stance[t.stance] = (stance[t.stance] || 0) + 1;
  }
  return { family, stance };
}

// ── consensus ────────────────────────────────────────────────────────────────────────────────────────────────────
export interface ConsensusView { bull: number; neutral: number; bear: number; n: number; pct: { bull: number; neutral: number; bear: number }; lead: Stance | 'split' }
export function consensusView(c: TraderStrategiesData['consensus'] | null | undefined): ConsensusView | null {
  if (!c || !c.n) return null;
  const pct = (x: number) => (x / c.n) * 100;
  const lead: ConsensusView['lead'] = c.bull > c.bear && c.bull > c.neutral ? 'bullish' : c.bear > c.bull && c.bear > c.neutral ? 'bearish'
    : c.neutral > c.bull && c.neutral > c.bear ? 'neutral' : 'split';
  return { bull: c.bull, neutral: c.neutral, bear: c.bear, n: c.n, pct: { bull: pct(c.bull), neutral: pct(c.neutral), bear: pct(c.bear) }, lead };
}

// ── words ────────────────────────────────────────────────────────────────────────────────────────────────────────
/** A rule's measured value as text: numbers trimmed, booleans yes/no, python's "None" never shown. */
export function fmtRuleValue(v: unknown): string {
  if (v == null || v === '') return '—';
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'number') return Number.isFinite(v) ? (Number.isInteger(v) ? String(v) : String(Math.round(v * 100) / 100)) : '—';
  return String(v).replace(/\bNone\b/g, 'n/a').replace(/\bnan\b/gi, 'n/a');
}
export type RuleState = 'met' | 'unmet' | 'unknown';
export const ruleState = (r: Pick<TraderRule, 'met'>): RuleState => (r.met === true ? 'met' : r.met === false ? 'unmet' : 'unknown');

export function rulesLine(s: Pick<TraderStrategy, 'met' | 'total' | 'available'>): string {
  return !s.available ? 'Not scored' : s.total === 0 ? 'No rules scored' : `${s.met} of ${s.total} rules met`;
}

export interface StopRead { tone: 'good' | 'warn' | 'neutral'; headline: string; detail: string; breached: boolean; isLevel: boolean }
/**
 * The trader's own exit line, read against price — worded by what a breach actually MEANS:
 *  - their exit IS a close beyond this line (``isLevel``): a lost line means the line their exit is built on has gone, and it
 *    becomes the level to reclaim (the Trade Manager's rule — a lost level is never presented as a future stop);
 *  - their exit is something else (relative strength, an oscillator, a bar colour): the line is only a reference, and a breach
 *    alone has NOT triggered their exit — it must not be worded as if it had.
 */
export function stopRead(stop: TraderStop | null | undefined, exitRule: string | undefined, isLevel = true): StopRead | null {
  if (!stop || !num(stop.level)) return null;
  const lvl = `$${stop.level.toFixed(2)}`, d = Math.abs(stop.distance_pct).toFixed(1);
  const atr = num(stop.atr_multiple) ? ` (${Math.abs(stop.atr_multiple).toFixed(1)} ATR)` : '';
  const rule = exitRule ? exitRule.replace(/\.$/, '') : '';
  if (isLevel) {
    return stop.breached ? {
      breached: true, isLevel, tone: 'warn',
      headline: `Price has closed below ${lvl}, the line their exit is built on`,
      detail: `That is ${d}%${atr} under it.${rule ? ` Their rule: ${rule}.` : ''} ${lvl} is now the level to reclaim, not a future exit.`,
    } : {
      breached: false, isLevel, tone: 'good',
      headline: `Their exit sits at ${lvl}`,
      detail: `${d}% below price${atr}.${rule ? ` ${rule}.` : ''}`,
    };
  }
  return stop.breached ? {
    breached: true, isLevel, tone: 'neutral',
    headline: `Price is below ${lvl}, a reference line`,
    detail: `${d}%${atr} under it. Their exit is not a price line${rule ? ` — ${rule}` : ''} — so this alone has not triggered it.`,
  } : {
    breached: false, isLevel, tone: 'neutral',
    headline: `Reference line at ${lvl}`,
    detail: `${d}% below price${atr}. Their exit is not a price line${rule ? ` — ${rule}` : ''}.`,
  };
}

/** Distance of a reference level from price, signed. */
export const levelDistancePct = (price: number, spot: number | null): number | null => (num(spot) && spot > 0 ? ((price - spot) / spot) * 100 : null);

// ── chart overlay ────────────────────────────────────────────────────────────────────────────────────────────────
const ROLE_COLOR = { exit: COLOR.down, trigger: COLOR.amber, reference: COLOR.violet } as const;
/**
 * A strategy's drawable levels as chart levels (layer 'strategy'). The trader's own exit is drawn solid red-dashed and
 * labelled; entry lines amber; context lines violet. Only levels with a real positive price are drawn.
 */
export function strategyChartLevels(s: Pick<TraderStrategy, 'key' | 'levels'> & { exit_is_level?: boolean } | null | undefined): ChartLevel[] {
  if (!s) return [];
  const isLevel = s.exit_is_level !== false;
  return (s.levels || []).filter(l => num(l.price) && l.price > 0).map((l, i): ChartLevel => {
    const role = l.role === 'exit' && !isLevel ? 'reference' : l.role;      // only a real price-line exit is drawn as THE exit
    return {
      id: `strat:${s.key}:${i}`, layer: 'strategy', kind: 'line', price: l.price,
      label: `${l.label} ${l.price.toFixed(2)}`, title: `${l.label} (${role === 'exit' ? 'their exit' : role === 'trigger' ? 'their entry' : 'reference'})`,
      color: ROLE_COLOR[role] ?? COLOR.slate, dashed: role !== 'exit', priority: role === 'exit' ? 8 : 7,
    };
  });
}
