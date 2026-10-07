/**
 * betaTaModel.ts — the pure logic behind the Beta Technical view: ONE verdict, six lenses, a plan, the levels that
 * matter, and collision-free chart labels.
 *
 * Nothing here invents a signal. The verdict IS the setup engine's own bias (trade_setup_service._derive_bias —
 * direction, strength and the list of signals that voted). The lenses regroup those votes; three more lenses
 * (stretch, volume, regime) are context the engine does not vote on, and the UI says so ("counts toward the call"
 * vs "context"). The plan is the engine's top setup for the chosen style. Odds come from firstPassage.ts.
 */
import type { SetupContext, TradeSetup, TradeSetupsData, ConfluenceZone, TechnicalData } from '../../types';
import { tradeOdds, type OddsResult } from './firstPassage';

export type Dir = 'long' | 'short' | 'neutral';
export type LensKey = 'trend' | 'momentum' | 'dealer' | 'stretch' | 'volume' | 'regime';
export type LensState = 'supports' | 'caution' | 'against' | 'neutral' | 'not_scored';
export type LayerKey = 'plan' | 'levels' | 'structure' | 'blocks' | 'liquidity' | 'dealer' | 'value';
export type Style = 'day' | 'swing' | 'position';

export interface Lens {
  key: LensKey; label: string; state: LensState;
  /** one line built from real numbers */
  detail: string;
  /** true = the engine's bias counts it; false = shown as context only */
  counted: boolean;
  /** the bias this lens leans toward, regardless of the verdict */
  lean: 'bullish' | 'bearish' | 'mixed' | null;
  /** the chart layer this lens can switch on */
  layer?: LayerKey;
}

export interface Verdict {
  dir: Dir; mixed: boolean;
  label: string;
  conviction: 'low' | 'medium' | 'high' | null;
  headline: string;
  score: number;
}

// ── the verdict ──────────────────────────────────────────────────────────────────────────────────────────────────
const DIR_OF: Record<string, Dir> = { bullish: 'long', bearish: 'short' };

export function countReads(ctx: SetupContext | null | undefined): { bull: number; bear: number } {
  let bull = 0, bear = 0;
  for (const c of ctx?.bias?.confirmations || []) { if (c.reads === 'bullish') bull++; else if (c.reads === 'bearish') bear++; }
  return { bull, bear };
}

function headlineFor(dir: Dir, mixed: boolean, regime: string | null | undefined): string {
  if (mixed) return 'The signals pull both ways — wait for structure to resolve, or trade small.';
  if (dir === 'neutral') return 'Nothing lines up — trade the range or stand aside until structure resolves.';
  if (regime === 'trending') return dir === 'long' ? "Buy pullbacks into support — don't chase green candles." : "Sell bounces into resistance — don't try to catch the bottom.";
  if (regime === 'mean_reverting') return dir === 'long' ? 'Range-bound with a bullish lean — buy the low end, take profits near the middle.' : 'Range-bound with a bearish lean — sell the high end, take profits near the middle.';
  return dir === 'long' ? 'A bullish lean without a strong regime — buy into support and keep size modest.' : 'A bearish lean without a strong regime — sell into resistance and keep size modest.';
}

export function deriveVerdict(ctx: SetupContext | null | undefined): Verdict | null {
  const bias = ctx?.bias;
  if (!bias) return null;
  const dir = DIR_OF[bias.direction] ?? 'neutral';
  const { bull, bear } = countReads(ctx);
  const mixed = dir === 'neutral' && bull > 0 && bear > 0;
  const conviction = dir === 'neutral' ? null : bias.strength === 'strong' ? 'high' : bias.strength === 'moderate' ? 'medium' : 'low';
  return {
    dir, mixed, conviction, score: bias.score,
    label: mixed ? 'Mixed — no clear edge' : dir === 'long' ? 'Lean long' : dir === 'short' ? 'Lean short' : 'No clear edge',
    headline: headlineFor(dir, mixed, ctx?.regime?.label),
  };
}

// ── the six lenses ───────────────────────────────────────────────────────────────────────────────────────────────
const GROUPS: Record<'trend' | 'momentum' | 'dealer', string[]> = {
  trend: ['market_structure', 'daily_trend', 'sma50', 'sma_cross', 'chart_pattern'],
  momentum: ['rsi', 'macd', 'bollinger'],
  dealer: ['gamma'],
};
const WANT: Record<Dir, 'bullish' | 'bearish' | null> = { long: 'bullish', short: 'bearish', neutral: null };
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const num = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);
const fx = (n: number | null | undefined, d = 2) => (num(n) ? n.toFixed(d) : '—');

/** Direction-relative state from a lens's own reads. */
function stateFromReads(dir: Dir, reads: ('bullish' | 'bearish')[]): { state: LensState; lean: Lens['lean'] } {
  if (!reads.length) return { state: 'neutral', lean: null };
  const bu = reads.filter(r => r === 'bullish').length, be = reads.length - bu;
  const lean: Lens['lean'] = bu && be ? 'mixed' : bu ? 'bullish' : 'bearish';
  const want = WANT[dir];
  if (!want) return { state: lean === 'mixed' ? 'caution' : 'neutral', lean };
  const a = want === 'bullish' ? bu : be, o = reads.length - a;
  return { state: a && !o ? 'supports' : a && o ? 'caution' : 'against', lean };
}

const TF_ARROW = (t?: string | null) => (t === 'up' ? '▲' : t === 'down' ? '▼' : t ? '◦' : '–');

export function deriveLenses(ctx: SetupContext | null | undefined, tech: TechnicalData | null | undefined, spot: number | null): Lens[] {
  const dir = deriveVerdict(ctx)?.dir ?? 'neutral';
  const conf = ctx?.bias?.confirmations || [];
  const grp = (k: keyof typeof GROUPS) => conf.filter(c => GROUPS[k].includes(c.signal));
  const out: Lens[] = [];

  // Trend & structure — counted
  {
    const cs = grp('trend'), ta = ctx?.trend_alignment;
    const have = !!ctx && (cs.length > 0 || !!ta || tech?.movingAverages?.sma50 != null);
    const { state, lean } = stateFromReads(dir, cs.map(c => c.reads as 'bullish' | 'bearish'));
    const tf = ta ? `D ${TF_ARROW(ta.daily)}  4H ${TF_ARROW(ta.h4)}  1H ${TF_ARROW(ta.h1)}` : '';
    const text = cs.map(c => cap(c.detail)).join(' · ');
    out.push({ key: 'trend', label: 'Trend', counted: true, layer: 'structure', lean,
      state: have ? state : 'not_scored',
      detail: !ctx ? 'Needs the market read' : have ? [text || 'No trend signal', tf].filter(Boolean).join(' · ') : 'No structure or moving-average data' });
  }
  // Momentum — counted
  {
    const cs = grp('momentum');
    const rsi = tech?.currentRSI;
    const have = cs.length > 0 || num(rsi) || tech?.macd?.histogram != null;
    const { state, lean } = stateFromReads(dir, cs.map(c => c.reads as 'bullish' | 'bearish'));
    const text = cs.map(c => cap(c.detail)).join(' · ');
    out.push({ key: 'momentum', label: 'Momentum', counted: true, lean,
      state: have && ctx ? state : 'not_scored',
      detail: !have ? 'No RSI / MACD data'
        : text ? text
          : ctx ? `RSI ${fx(rsi, 0)} — no momentum signal`
            // before the engine read arrives there is no signal to quote, only the raw numbers
            : [num(rsi) ? `RSI ${fx(rsi, 0)}` : null, tech?.macd?.histogram != null ? `MACD histogram ${tech.macd.histogram > 0 ? '+' : ''}${fx(tech.macd.histogram, 2)}` : null].filter(Boolean).join(' · ') });
  }
  // Dealer flow — counted
  {
    const cs = grp('dealer'), d = ctx?.dealer;
    const { state, lean } = stateFromReads(dir, cs.map(c => c.reads as 'bullish' | 'bearish'));
    const flow = d?.gamma === 'long' ? 'Dealers are long gamma — moves get dampened' : d?.gamma === 'short' ? 'Dealers are short gamma — moves get amplified' : null;
    const flip = num(d?.flip) ? `γ-flip ${fx(d!.flip)}` : null;
    const read = cs.map(c => cap(c.detail)).join(' · ');
    out.push({ key: 'dealer', label: 'Dealer flow', counted: true, layer: 'dealer', lean,
      state: d ? state : 'not_scored',
      detail: d ? [read, flow, flip].filter(Boolean).join(' · ') || 'No dealer-positioning signal' : 'No options data' });
  }
  // Stretch — context (not in the engine's vote)
  {
    const rsi = tech?.currentRSI, pb = tech?.bollingerBands?.percentB, sma50 = tech?.movingAverages?.sma50;
    const ext = num(sma50) && num(spot) && sma50 > 0 ? (spot / sma50 - 1) * 100 : null;
    const have = num(rsi) || num(pb) || ext != null;
    const up = (num(rsi) && rsi >= 70) || (num(pb) && pb >= 1) || (ext != null && ext >= 15);
    const dn = (num(rsi) && rsi <= 30) || (num(pb) && pb <= 0) || (ext != null && ext <= -15);
    const bits = [num(rsi) ? `RSI ${fx(rsi, 0)}` : null, num(pb) ? `%B ${fx(pb * 100, 0)}` : null,
      ext != null ? `${ext >= 0 ? '+' : ''}${fx(ext, 1)}% vs 50-day` : null].filter(Boolean).join(' · ');
    const lean: Lens['lean'] = up ? 'bullish' : dn ? 'bearish' : null;
    let state: LensState = 'neutral';
    if (up) state = dir === 'long' ? 'caution' : dir === 'short' ? 'supports' : 'neutral';
    if (dn) state = dir === 'short' ? 'caution' : dir === 'long' ? 'supports' : 'neutral';
    out.push({ key: 'stretch', label: 'Stretch', counted: false, lean, state: have ? state : 'not_scored',
      detail: have ? `${up ? 'Stretched up' : dn ? 'Stretched down' : 'Not stretched'} · ${bits}` : 'No RSI / band data' });
  }
  // Volume — context (not in the engine's vote)
  {
    const va = tech?.volumeAnalysis, ph = va?.phase;
    let state: LensState = 'neutral', lean: Lens['lean'] = null;
    if (ph) {
      const bullish = ph === 'accumulation', bearish = ph === 'distribution';
      const weakRally = ph === 'weak_rally', weakDecline = ph === 'weak_decline';
      lean = bullish ? 'bullish' : bearish ? 'bearish' : null;
      if (dir === 'long') state = bullish ? 'supports' : bearish ? 'against' : weakRally ? 'caution' : 'neutral';
      if (dir === 'short') state = bearish ? 'supports' : bullish ? 'against' : weakDecline ? 'caution' : 'neutral';
    }
    const trend = va?.volumeTrend && num(va.volumeChangePct) ? `volume ${va.volumeTrend} ${fx(Math.abs(va.volumeChangePct), 0)}%` : null;
    out.push({ key: 'volume', label: 'Volume', counted: false, layer: 'value', lean, state: ph ? state : 'not_scored',
      detail: ph ? [cap(ph.replace(/_/g, ' ')), trend].filter(Boolean).join(' · ') : 'No volume read' });
  }
  // Regime — context (selects the setup types, but is not a directional vote)
  {
    const r = ctx?.regime, lbl = r?.label;
    let state: LensState = 'neutral';
    const daily = ctx?.trend_alignment?.daily;
    if (lbl === 'trending') {
      if (dir === 'long') state = daily === 'up' ? 'supports' : daily === 'down' ? 'against' : 'neutral';
      if (dir === 'short') state = daily === 'down' ? 'supports' : daily === 'up' ? 'against' : 'neutral';
    } else if (lbl === 'mean_reverting') state = dir === 'neutral' ? 'neutral' : 'caution';
    const nm = lbl === 'trending' ? 'Trending' : lbl === 'mean_reverting' ? 'Mean-reverting' : lbl ? 'Transitional' : null;
    out.push({ key: 'regime', label: 'Regime', counted: false, lean: null, state: lbl ? state : 'not_scored',
      detail: nm ? [nm, num(r?.hurst) ? `Hurst ${r!.hurst}` : null, r?.confidence ? `${r.confidence} confidence` : null,
        lbl === 'mean_reverting' && dir !== 'neutral' ? 'fade extremes — breakouts tend to fail' : null].filter(Boolean).join(' · ') : 'No regime read' });
  }
  return out;
}

/** The lenses that argue with the verdict, plus plan-level warnings — each one a real field, worded once. */
export interface Disagreement { who: string; text: string; tone: 'against' | 'caution' }
export function disagreements(lenses: Lens[], verdict: Verdict | null, plan: TradeSetup | null, odds: OddsResult | null): Disagreement[] {
  const out: Disagreement[] = [];
  if (verdict && verdict.dir !== 'neutral') {
    for (const l of lenses) if (l.state === 'against' || l.state === 'caution') out.push({ who: l.label, text: l.detail, tone: l.state });
  }
  if (plan) {
    if (plan.sizing?.within_expected_move === false && plan.sizing.note) out.push({ who: 'Target', text: plan.sizing.note, tone: 'caution' });
    if (plan.event_risk?.warning) out.push({ who: 'Event', text: plan.event_risk.warning, tone: 'caution' });
    if (plan.regime_fit === 'counter_regime') out.push({ who: 'Regime', text: 'This setup trades against the prevailing regime.', tone: 'caution' });
  }
  if (odds && odds.ok && odds.edgePts != null && odds.edgePts <= -2) {
    out.push({ who: 'Odds', text: `Market-implied odds are ${Math.abs(odds.edgePts).toFixed(0)} points below the win rate this reward:risk needs.`, tone: 'caution' });
  }
  return out;
}

/** For a mixed / no-edge call: the nearest confluence zone above and below spot — the two prices whose break shows which
 *  side is winning. Both are real zones from the setup engine; either may be absent. */
export function settleLevels(zones: ConfluenceZone[] | undefined, spot: number | null): { above: number | null; below: number | null } {
  if (!zones || !num(spot)) return { above: null, below: null };
  // the engine tags a zone by POSITION (support below spot, resistance above, "pivot" when spot is inside it); a pivot
  // is not a level to wait for, so it is skipped here
  const real = zones.filter(z => num(z.center) && z.kind !== 'pivot');
  const above = real.filter(z => z.center > spot).sort((a, b) => a.center - b.center)[0];
  const below = real.filter(z => z.center < spot).sort((a, b) => b.center - a.center)[0];
  return { above: above ? above.center : null, below: below ? below.center : null };
}

// ── the plan ─────────────────────────────────────────────────────────────────────────────────────────────────────
/** Same split the classic style tabs use: position = style 'position'; swing = everything else from /trade-setups. */
export function setupsForStyle(data: TradeSetupsData | null | undefined, style: Style, day: TradeSetup[] | null): TradeSetup[] {
  if (style === 'day') return day || [];
  const all = data?.setups || [];
  return style === 'position' ? all.filter(s => s.style === 'position') : all.filter(s => (s.style || 'swing') !== 'position');
}

/**
 * Holding window the odds are quoted over, in calendar-day equivalents:
 *  - day      one TRADING session = 365/252 calendar days of variance (implied vol is annualised over 252 sessions);
 *  - swing    the engine's own options expiry (what its implied vol and expected move are quoted at), 30 if none;
 *  - position at least 90 days — a weeks-to-months hold judged over a month would badly understate its chance.
 * Null when there is no plan. The caller prints the window next to the odds, so it is never hidden.
 */
export const SESSION_DAYS = 365 / 252;
export function planWindowDays(plan: TradeSetup | null, style: Style): number | null {
  if (!plan) return null;
  if (style === 'day') return SESSION_DAYS;
  const d = plan.options_plan?.expiry?.dte ?? plan.options?.expiry?.dte;
  const dte = typeof d === 'number' && d > 0 ? d : 30;
  return style === 'position' ? Math.max(90, dte) : dte;
}
export function windowLabel(days: number, style: Style): string {
  return style === 'day' ? '1 session' : `${Math.round(days)} days`;
}

export interface PlanLevels { direction: 'long' | 'short'; entry: number; entryLow: number | null; entryHigh: number | null; stop: number; t1: number; t2: number | null }
export function planLevels(plan: TradeSetup | null): PlanLevels | null {
  if (!plan || (plan.direction !== 'long' && plan.direction !== 'short')) return null;
  const e = plan.entry;
  const entry = num(e?.level) ? e.level : (num(e?.low) && num(e?.high) ? (e.low + e.high) / 2 : null);
  const stop = plan.stop?.level, t = (plan.targets || []).map(x => x.level).filter(num);
  if (entry == null || !num(stop) || !t.length) return null;
  return { direction: plan.direction, entry, entryLow: num(e.low) ? e.low : null, entryHigh: num(e.high) ? e.high : null, stop, t1: t[0], t2: t[1] ?? null };
}

export function planOdds(plan: TradeSetup | null, ctx: SetupContext | null | undefined, spot: number | null, style: Style): OddsResult | null {
  const lv = planLevels(plan);
  if (!lv || !num(spot)) return null;
  const ivPct = ctx?.expected_move?.iv;
  const days = planWindowDays(plan, style);
  return tradeOdds({ direction: lv.direction, spot, entry: lv.entry, stop: lv.stop, target: lv.t1, iv: num(ivPct) && ivPct > 0 ? ivPct / 100 : null, days: days ?? 0 });
}

// ── the levels that matter ───────────────────────────────────────────────────────────────────────────────────────
export interface LevelRow { id: string; price: number; label: string; kind: string; distPct: number; priority: number }
const pct = (p: number, spot: number) => ((p - spot) / spot) * 100;

export function levelRows(spot: number | null, plan: PlanLevels | null, zones: ConfluenceZone[] | undefined, tech: TechnicalData | null | undefined,
  ctx: SetupContext | null | undefined, extra: { id: string; price: number; label: string; kind: string }[] = [], perSide = 5): LevelRow[] {
  if (!num(spot) || spot <= 0) return [];
  const rows: LevelRow[] = [];
  const add = (id: string, price: unknown, label: string, kind: string, priority: number) => {
    if (num(price) && price > 0) rows.push({ id, price, label, kind, distPct: pct(price, spot), priority });
  };
  if (plan) {
    add('plan-entry', plan.entry, 'Entry', 'plan', 9); add('plan-stop', plan.stop, 'Stop', 'plan', 9);
    add('plan-t1', plan.t1, 'Target 1', 'plan', 9); add('plan-t2', plan.t2, 'Target 2', 'plan', 8);
  }
  (zones || []).forEach((z, i) => add(`zone-${i}`, z.center, `${z.kind === 'support' ? 'Support' : z.kind === 'resistance' ? 'Resistance' : cap(z.kind || 'Zone')} zone (${z.n_sources} signals agree)`, 'confluence', 5 + Math.min(3, z.score / 4)));
  add('flip', ctx?.dealer?.flip, 'Dealer gamma flip', 'dealer', 4);
  add('em-hi', ctx?.expected_move?.upper, '30-day expected move high', 'range', 2);
  add('em-lo', ctx?.expected_move?.lower, '30-day expected move low', 'range', 2);
  add('ta-s', tech?.supportLevel, 'Swing support', 'structure', 3);
  add('ta-r', tech?.resistanceLevel, 'Swing resistance', 'structure', 3);
  for (const x of extra) add(x.id, x.price, x.label, x.kind, 3);
  // drop near-duplicates of the SAME label+price; keep the nearest perSide each side plus every plan level
  const seen = new Set<string>();
  const uniq = rows.filter(r => { const k = `${r.label}|${r.price.toFixed(2)}`; if (seen.has(k)) return false; seen.add(k); return true; });
  const above = uniq.filter(r => r.price > spot && r.kind !== 'plan').sort((a, b) => a.distPct - b.distPct).slice(0, perSide);
  const below = uniq.filter(r => r.price <= spot && r.kind !== 'plan').sort((a, b) => b.distPct - a.distPct).slice(0, perSide);
  return [...uniq.filter(r => r.kind === 'plan'), ...above, ...below].sort((a, b) => b.price - a.price);
}

// ── collision-free labels in the chart gutter ────────────────────────────────────────────────────────────────────
export interface GutterItem { id: string; y: number; priority: number }
export interface GutterPlaced { id: string; y: number; at: number; displaced: boolean }
/**
 * Place labels so no two are closer than `minGap` px and all stay inside [top, bottom]. `y` is each level's true
 * pixel; `at` is where its label sits (a leader line joins them when they differ). If the labels cannot fit at all,
 * the lowest-priority ones are dropped and returned in `dropped` — never overlapped.
 */
export function layoutGutter(items: GutterItem[], top: number, bottom: number, minGap: number): { placed: GutterPlaced[]; dropped: string[] } {
  const room = Math.max(0, bottom - top);
  const cap_ = Math.max(0, Math.floor(room / minGap) + 1);
  let keep = items.filter(i => Number.isFinite(i.y));
  const dropped: string[] = [];
  if (keep.length > cap_) {
    const byPri = [...keep].sort((a, b) => b.priority - a.priority || a.y - b.y);
    const kept = new Set(byPri.slice(0, cap_).map(i => i.id));
    keep.filter(i => !kept.has(i.id)).forEach(i => dropped.push(i.id));
    keep = keep.filter(i => kept.has(i.id));
  }
  const s = [...keep].sort((a, b) => a.y - b.y);
  const at = s.map(i => Math.min(bottom, Math.max(top, i.y)));
  for (let i = 1; i < at.length; i++) at[i] = Math.max(at[i], at[i - 1] + minGap);
  if (at.length && at[at.length - 1] > bottom) {
    at[at.length - 1] = bottom;
    for (let i = at.length - 2; i >= 0; i--) at[i] = Math.min(at[i], at[i + 1] - minGap);
  }
  return { placed: s.map((i, k) => ({ id: i.id, y: i.y, at: at[k], displaced: Math.abs(at[k] - i.y) > 0.5 })), dropped };
}

// ── small formatters shared by the views ─────────────────────────────────────────────────────────────────────────
export const money = (n: number | null | undefined, d = 2) => (num(n) ? `$${n.toFixed(d)}` : '—');
export const pctStr = (n: number | null | undefined, d = 1, sign = true) => (num(n) ? `${sign && n > 0 ? '+' : ''}${n.toFixed(d)}%` : '—');
export const probStr = (p: number | null | undefined) => (num(p) ? `${(p * 100).toFixed(p < 0.1 ? 1 : 0)}%` : '—');
