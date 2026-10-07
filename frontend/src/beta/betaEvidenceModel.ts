/**
 * betaEvidenceModel.ts — the pure logic behind the Beta Technical · Fundamental · News & events evidence views.
 *
 * Everything here turns numbers the Trade Manager already computed into a plain-English read (word + tone) or a
 * display string. It adds NO new analysis and invents NO facts: thresholds are the textbook ones (RSI 30/70, ADX 20/25,
 * stochastic/MFI 20/80, Weinstein stages) and the tone of a pillar uses the same cut-offs as the classic panel
 * (exit pressure ≥45 bad, ≥32 watch, else healthy). Kept free of React so it can be unit-tested with plain node.
 */
import type { Tone } from './BetaKit';

export type Read = { word: string; tone: Tone };

// ── number plumbing ──────────────────────────────────────────────────────────

export const num = (v: any): number | null => {
  if (v == null || v === '' || typeof v === 'boolean') return null;
  const x = Number(v);
  return Number.isFinite(x) ? x : null;
};

const grp = (x: number, d: number) => x.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

/** 1234.5 → "1,234.5" (fixed decimals); missing → "–". */
export const fmtNum = (v: any, d = 1): string => { const x = num(v); return x == null ? '–' : grp(x, d); };

/** A value that is ALREADY a percent (12.3 → "12.3%"). */
export const fmtPct = (v: any, d = 1, signed = false): string => {
  const x = num(v); if (x == null) return '–';
  return `${signed && x > 0 ? '+' : x < 0 ? '−' : ''}${grp(Math.abs(x), d)}%`;
};

/** Signed number: +12.3 / −4.5 / 0.0 (a true minus sign, not a hyphen); missing → "–". */
export const fmtSigned = (v: any, d = 1): string => { const x = num(v); return x == null ? '–' : `${x > 0 ? '+' : x < 0 ? '−' : ''}${grp(Math.abs(x), d)}`; };

/** A FRACTION (0.153 → "15.3%") — yfinance margins, growth rates, ownership. */
export const fromFrac = (v: any, d = 1, signed = false): string => { const x = num(v); return x == null ? '–' : fmtPct(x * 100, d, signed); };

/** 1.06e12 → "$1.06T", 8.8e9 → "$8.84B", 3.6e5 → "$361K". Negative values keep their sign. */
export function fmtBig(v: any): string {
  const x = num(v); if (x == null) return '–';
  const a = Math.abs(x), s = x < 0 ? '−' : '';
  if (a >= 1e12) return `${s}$${grp(a / 1e12, 2)}T`;
  if (a >= 1e9) return `${s}$${grp(a / 1e9, 2)}B`;
  if (a >= 1e6) return `${s}$${grp(a / 1e6, 1)}M`;
  if (a >= 1e3) return `${s}$${grp(a / 1e3, 0)}K`;
  return `${s}$${grp(a, 0)}`;
}

/** Share counts, no dollar sign: 361285 → "361K". */
export function fmtCount(v: any): string {
  const x = num(v); if (x == null) return '–';
  const a = Math.abs(x);
  if (a >= 1e9) return `${grp(a / 1e9, 2)}B`;
  if (a >= 1e6) return `${grp(a / 1e6, 1)}M`;
  if (a >= 1e3) return `${grp(a / 1e3, 0)}K`;
  return grp(a, 0);
}

/** Position of `v` inside [lo, hi] as 0–100 (clamped); null when the range is degenerate. */
export function pos(v: any, lo: any, hi: any): number | null {
  const x = num(v), a = num(lo), b = num(hi);
  if (x == null || a == null || b == null || b <= a) return null;
  return Math.max(0, Math.min(100, ((x - a) / (b - a)) * 100));
}

// ── dates & links ────────────────────────────────────────────────────────────

/** "3h ago" · "2d ago" · "Sep 24" for an RFC-2822 / ISO date; the raw text when it can't be parsed. */
export function relTime(s: any, now: number = Date.now()): string {
  if (!s) return '';
  const t = Date.parse(String(s));
  if (!Number.isFinite(t)) return String(s);
  const mins = Math.round((now - t) / 60000);
  if (mins < 0) return new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  if (mins < 60) return `${Math.max(mins, 1)}m ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
  const days = Math.round(mins / (60 * 24));
  if (days < 14) return `${days}d ago`;
  return new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

/** Whole days from `now` to an ISO date (YYYY-MM-DD) in the user's calendar; null when unparseable. */
export function daysUntil(iso: any, now: Date = new Date()): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso ?? ''));
  if (!m) return null;
  const target = Date.UTC(+m[1], +m[2] - 1, +m[3]);
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((target - today) / 86400000);
}

/** News links come from RSS feeds: only ever follow http(s). */
export function safeUrl(u: any): string | null {
  if (typeof u !== 'string') return null;
  try { const p = new URL(u); return p.protocol === 'https:' || p.protocol === 'http:' ? p.toString() : null; } catch { return null; }
}

// ── technical reads ──────────────────────────────────────────────────────────

export function rsiRead(v: any): Read {
  const x = num(v); if (x == null) return { word: '–', tone: 'neutral' };
  if (x >= 70) return { word: 'Overbought', tone: 'warn' };
  if (x >= 55) return { word: 'Bullish momentum', tone: 'good' };
  if (x > 45) return { word: 'Neutral', tone: 'neutral' };
  if (x > 30) return { word: 'Bearish momentum', tone: 'bad' };
  return { word: 'Oversold', tone: 'warn' };
}

/** Stochastic %K and MFI share the 20/80 convention. */
export function band2080Read(v: any): Read {
  const x = num(v); if (x == null) return { word: '–', tone: 'neutral' };
  if (x >= 80) return { word: 'Overbought', tone: 'warn' };
  if (x <= 20) return { word: 'Oversold', tone: 'warn' };
  return { word: 'Mid-range', tone: 'neutral' };
}

export function adxRead(adx: any, plusDi: any, minusDi: any): Read {
  const a = num(adx), p = num(plusDi), m = num(minusDi);
  if (a == null) return { word: '–', tone: 'neutral' };
  if (a < 20) return { word: 'No clear trend', tone: 'neutral' };
  if (a < 25) return { word: 'Trend building', tone: 'info' };
  const up = p != null && m != null ? p > m : null;
  const word = `${a >= 40 ? 'Very strong' : 'Strong'} ${up == null ? 'trend' : up ? 'uptrend' : 'downtrend'}`;
  return { word, tone: up == null ? 'info' : up ? 'good' : 'bad' };
}

export function macdRead(hist: any, slope3d: any): Read {
  const h = num(hist), s = num(slope3d);
  if (h == null) return { word: '–', tone: 'neutral' };
  const rising = s == null ? null : s >= 0;
  if (h > 0) return rising === false ? { word: 'Bullish but fading', tone: 'warn' } : { word: 'Bullish', tone: 'good' };
  if (h < 0) return rising === true ? { word: 'Bearish but easing', tone: 'warn' } : { word: 'Bearish', tone: 'bad' };
  return { word: 'Flat', tone: 'neutral' };
}

export function bbRead(pctB: any): Read {
  const x = num(pctB); if (x == null) return { word: '–', tone: 'neutral' };
  if (x > 1) return { word: 'Above the upper band', tone: 'warn' };
  if (x >= 0.8) return { word: 'Near the upper band', tone: 'neutral' };
  if (x < 0) return { word: 'Below the lower band', tone: 'warn' };
  if (x <= 0.2) return { word: 'Near the lower band', tone: 'neutral' };
  return { word: 'Mid-band', tone: 'neutral' };
}

export function cmfRead(v: any): Read {
  const x = num(v); if (x == null) return { word: '–', tone: 'neutral' };
  if (x > 0.1) return { word: 'Accumulation', tone: 'good' };
  if (x < -0.1) return { word: 'Distribution', tone: 'bad' };
  return { word: 'Balanced', tone: 'neutral' };
}

export function upDownRead(v: any): Read {
  const x = num(v); if (x == null) return { word: '–', tone: 'neutral' };
  if (x >= 1.5) return { word: 'Buyers dominate', tone: 'good' };
  if (x <= 0.67) return { word: 'Sellers dominate', tone: 'bad' };
  return { word: 'Balanced', tone: 'neutral' };
}

export function percentileRead(v: any): Read {
  const x = num(v); if (x == null) return { word: '–', tone: 'neutral' };
  if (x >= 80) return { word: 'High vs the past year', tone: 'warn' };
  if (x <= 20) return { word: 'Low vs the past year', tone: 'info' };
  return { word: 'Typical', tone: 'neutral' };
}

/** Weinstein's four stages from the weekly chart. */
export function stageRead(v: any): Read {
  const x = num(v);
  if (x === 1) return { word: 'Stage 1 · basing', tone: 'neutral' };
  if (x === 2) return { word: 'Stage 2 · advancing', tone: 'good' };
  if (x === 3) return { word: 'Stage 3 · topping', tone: 'warn' };
  if (x === 4) return { word: 'Stage 4 · declining', tone: 'bad' };
  return { word: '–', tone: 'neutral' };
}

/** A 0–100 lens score → the one set of words used everywhere (Supportive · Leaning supportive · Neutral · Caution · Against). */
export function scoreRead(v: any): Read {
  const x = num(v); if (x == null) return { word: 'Not scored', tone: 'neutral' };
  if (x >= 65) return { word: 'Supportive', tone: 'good' };
  if (x >= 55) return { word: 'Leaning supportive', tone: 'good' };       // clearly above the neutral 50 — 64 is not "Neutral"
  if (x >= 45) return { word: 'Neutral', tone: 'neutral' };
  if (x >= 30) return { word: 'Caution', tone: 'warn' };
  return { word: 'Against', tone: 'bad' };
}

export const trendTone = (d: any): Tone => (d === 'up' ? 'good' : d === 'down' ? 'bad' : 'neutral');

/** Percent distance of a price from a level, signed to the price: (px/level − 1)·100. */
export function pctFrom(px: any, level: any): number | null {
  const p = num(px), l = num(level);
  return p == null || l == null || l === 0 ? null : (p / l - 1) * 100;
}

/** −1…+1 signal strength → a diverging bar: where the fill starts and how wide it is, as % of the full track. */
export function divergingBar(d: any): { left: number; width: number; tone: Tone } {
  const x = Math.max(-1, Math.min(1, num(d) ?? 0));
  const w = Math.abs(x) * 50;
  return { left: x >= 0 ? 50 : 50 - w, width: w, tone: x > 0.15 ? 'good' : x < -0.15 ? 'bad' : 'neutral' };
}

/** How the 15 trader rule-sets split — bull / neutral / bear as % of the bar. */
export function consensusSplit(c: any): { bull: number; neutral: number; bear: number; n: number } | null {
  const bull = num(c?.bull) ?? 0, neutral = num(c?.neutral) ?? 0, bear = num(c?.bear) ?? 0;
  const n = bull + neutral + bear;
  if (!n) return null;
  return { bull: (bull / n) * 100, neutral: (neutral / n) * 100, bear: (bear / n) * 100, n };
}

export interface ZoneRow { center: number; low: number | null; high: number | null; kind: string; distancePct: number | null; nSources: number; sources: { label: string; price: number }[] }
/** Confluence zones as a price ladder: highest first, split into those at/above the spot and those below it. */
export function zoneLadder(zones: any[] | undefined, spot: any): { above: ZoneRow[]; below: ZoneRow[] } {
  const px = num(spot);
  const rows: ZoneRow[] = (zones || []).map((z: any) => ({
    center: Number(z.center), low: num(z.low), high: num(z.high), kind: String(z.kind || ''),
    distancePct: num(z.distance_pct) ?? pctFrom(z.center, px),
    nSources: num(z.n_sources) ?? (z.sources || []).length,
    sources: (z.sources || []).slice(0, 6).map((s: any) => ({ label: String(s.label), price: Number(s.price) })),
  })).filter(r => Number.isFinite(r.center)).sort((a, b) => b.center - a.center);
  if (px == null) return { above: rows, below: [] };
  return { above: rows.filter(r => r.center >= px), below: rows.filter(r => r.center < px) };
}

/** Regime-conditional edge: is the sample big enough to lean on? (the engine's own low_confidence flag, or n < 20) */
export function edgeSample(cur: any): { n: number | null; small: boolean } {
  const n = num(cur?.n);
  return { n, small: !!cur?.low_confidence || n == null || n < 20 };
}

export const EDGE_TONE: Record<string, Tone> = { confirmed: 'good', strong: 'good', weak: 'warn', negative: 'bad', none: 'neutral' };

// ── fundamental pillars ──────────────────────────────────────────────────────

/** The pillar scores are EXIT PRESSURE (lower = healthier); same cut-offs as the classic panel. */
export function pressureRead(score: any): Read {
  const x = num(score); if (x == null) return { word: 'Not scored', tone: 'neutral' };
  if (x >= 45) return { word: 'Pressure', tone: 'bad' };
  if (x >= 32) return { word: 'Watch', tone: 'warn' };
  return { word: 'Healthy', tone: 'good' };
}

type Fmt = 'pct' | 'frac' | 'num' | 'x' | 'text' | 'usd' | 'int' | 'yn';
export interface PillarSpec { label: string; blurb: string; facts: [key: string, label: string, fmt: Fmt][] }

export const PILLARS: Record<string, PillarSpec> = {
  fundamental: { label: 'Business health', blurb: 'Revenue, margin and cash-flow direction; balance sheet', facts: [
    ['revenue_direction', 'Revenue', 'text'], ['margin_direction', 'Margins', 'text'], ['fcf_direction', 'Free cash flow', 'text'],
    ['eps_consistency', 'EPS consistency', 'int'], ['current_ratio', 'Current ratio', 'x'], ['interest_coverage', 'Interest cover', 'x']] },
  valuation: { label: 'Valuation', blurb: 'How expensive vs earnings, growth and analyst targets', facts: [
    ['forward_pe', 'Forward P/E', 'x'], ['peg_ratio', 'PEG', 'x'], ['ev_to_ebitda', 'EV/EBITDA', 'x'], ['price_to_book', 'P/B', 'x'],
    ['target_upside_pct', 'To analyst target', 'pct'], ['price_vs_52w_pct', 'In 52w range', 'pct']] },
  sentiment_targets: { label: 'Trend & sentiment', blurb: 'Price trend and where it sits in its 52-week range', facts: [
    ['sentiment_label', 'Read', 'text'], ['one_year_return_pct', '1-year return', 'pct'], ['pct_from_52w_high', 'From 52w high', 'pct'],
    ['above_50dma', 'Above 50d', 'yn'], ['above_200dma', 'Above 200d', 'yn']] },
  catalyst_revisions: { label: 'Catalysts & revisions', blurb: 'Earnings proximity and which way estimates are moving', facts: [
    ['eps_revision_trend', 'Estimates', 'text'], ['eps_revision_net', 'Net revisions', 'int'], ['days_to_earnings', 'Days to earnings', 'int'],
    ['earnings_qtr_growth_pct', 'Last-qtr EPS growth', 'pct']] },
  quality_capital: { label: 'Quality & capital', blurb: 'Returns on capital, cash conversion, buybacks vs dilution', facts: [
    ['roe', 'ROE', 'pct'], ['roic', 'ROIC', 'pct'], ['gross_margin', 'Gross margin', 'pct'], ['fcf_conversion', 'FCF conversion', 'x'],
    ['buyback_yield_pct', 'Buyback yield', 'pct'], ['shares_trend', 'Share count', 'text']] },
  ownership_flow: { label: 'Ownership & flow', blurb: 'Institutions, insiders and short interest', facts: [
    ['institution_pct', 'Institutions', 'pct'], ['insider_pct', 'Insiders', 'pct'], ['short_pct_float', 'Short % float', 'pct'],
    ['days_to_cover', 'Days to cover', 'num'], ['insider_signal', 'Insider signal', 'text']] },
  structural: { label: 'Industry & reinvestment', blurb: 'Industry growth, R&D and capex intensity', facts: [
    ['industry_growth_signal', 'Industry growth', 'text'], ['rd_as_pct_revenue', 'R&D / revenue', 'pct'], ['capex_to_revenue_pct', 'Capex / revenue', 'pct'],
    ['rd_direction', 'R&D trend', 'text']] },
  macro_sensitivity: { label: 'Macro sensitivity', blurb: 'Beta, rate sensitivity and volatility regime', facts: [
    ['beta', 'Beta', 'num'], ['beta_interpretation', 'Read', 'text'], ['vol_regime', 'Vol regime', 'text'],
    ['sector_cyclicality', 'Cyclicality', 'text'], ['hv30', '30d vol', 'pct']] },
  geopolitical: { label: 'Headline & regulatory risk', blurb: 'News tone and sector regulatory exposure', facts: [
    ['news_sentiment', 'News tone', 'text'], ['sector_regulatory_risk', 'Regulatory risk', 'text'], ['negative_news_count', 'Negative headlines', 'int']] },
  sector_rotation: { label: 'Sector flows', blurb: 'Is money rotating into or out of the sector?', facts: [
    ['rotation_signal', 'Rotation', 'text'], ['sector_etf', 'Sector ETF', 'text'], ['sector_vs_spy_3m', 'vs S&P 3m', 'pct'], ['sector_return_3m', 'Sector 3m', 'pct']] },
};

export function fmtFact(v: any, fmt: Fmt): string {
  if (v == null || v === '') return '–';
  switch (fmt) {
    case 'text': return String(v).replace(/_/g, ' ');
    case 'yn': return v === true ? 'yes' : v === false ? 'no' : String(v);
    case 'int': { const x = num(v); return x == null ? '–' : grp(x, 0); }
    case 'pct': return fmtPct(v, 1, false);
    case 'frac': return fromFrac(v);
    case 'x': { const x = num(v); return x == null ? '–' : `${grp(x, x >= 100 ? 0 : x >= 10 ? 1 : 2)}×`; }
    case 'usd': return fmtBig(v);
    default: return fmtNum(v, 2);
  }
}

/** A pillar's headline facts: the ones its spec lists that actually have data, in order. */
export function pillarFacts(key: string, data: any, max = 4): { label: string; value: string }[] {
  const spec = PILLARS[key]; if (!spec || !data) return [];
  const out: { label: string; value: string }[] = [];
  for (const [k, label, fmt] of spec.facts) {
    if (data[k] == null || data[k] === '') continue;
    out.push({ label, value: fmtFact(data[k], fmt) });
    if (out.length >= max) break;
  }
  return out;
}

/** Healthy / Watch / Pressure counts across the scored pillars. */
export function pillarTally(pillars: Record<string, { score: number | null }> | undefined): { healthy: number; watch: number; pressure: number; scored: number } {
  const t = { healthy: 0, watch: 0, pressure: 0, scored: 0 };
  for (const v of Object.values(pillars || {})) {
    if (num(v?.score) == null) continue;
    t.scored++;
    const w = pressureRead(v.score).word;
    if (w === 'Healthy') t.healthy++; else if (w === 'Watch') t.watch++; else t.pressure++;
  }
  return t;
}

/** Mean analyst target vs the current price, as a percent (a decimal consensus target of 636 vs 649 → −2.0). */
export function targetUpside(pt: any): number | null {
  const cur = num(pt?.current), mean = num(pt?.mean);
  return cur && mean != null ? (mean / cur - 1) * 100 : null;
}

/** yfinance period codes → words. */
export const PERIOD_LABEL: Record<string, string> = { '0q': 'This quarter', '+1q': 'Next quarter', '0y': 'This year', '+1y': 'Next year' };

/** Analyst consensus: yfinance's 1 (strong buy) … 5 (strong sell) mean → a word and a tone. */
export function consensusRead(mean: any, key: any): Read {
  const label = String(key || '').replace(/_/g, ' ');
  const m = num(mean);
  const tone: Tone = m == null ? 'neutral' : m <= 2.2 ? 'good' : m <= 3.2 ? 'neutral' : 'bad';
  return { word: label ? label.charAt(0).toUpperCase() + label.slice(1) : '–', tone };
}

// ── news & events ────────────────────────────────────────────────────────────

export interface NewsRow { title: string; publisher: string; when: string; ts: number | null; link: string | null; tag?: string; flag?: 'risk' | 'positive' }

/** RSS rows → display rows, newest first (undated last), tagged risk/positive when the engine's keyword scan flagged that headline. */
export function newsRows(rows: any[] | undefined, flags?: { risk_headlines?: string[]; positive_headlines?: string[] } | null, now: number = Date.now()): NewsRow[] {
  const risk = new Set((flags?.risk_headlines || []).map(String)), good = new Set((flags?.positive_headlines || []).map(String));
  return (rows || []).filter(r => r && r.title).map(r => {
    const ts = Date.parse(String(r.date ?? ''));
    const title = String(r.title);
    return {
      title, publisher: String(r.publisher || ''), when: relTime(r.date, now), ts: Number.isFinite(ts) ? ts : null, link: safeUrl(r.link),
      tag: r.peer ? String(r.peer) : undefined, flag: risk.has(title) ? 'risk' as const : good.has(title) ? 'positive' as const : undefined,
    };
  }).sort((a, b) => (b.ts ?? -Infinity) - (a.ts ?? -Infinity));
}

/**
 * Where does the next earnings print sit relative to THIS trade? For an option position it matters whether the print lands
 * before the expiry (the position owns the gap) or after it. `dte` is the trade's days to expiry (null for shares).
 */
export function earningsPlacement(daysToEarnings: any, dte: any): { when: 'inside' | 'after' | 'none' | 'shares'; text: string; tone: Tone } {
  const d = num(daysToEarnings), t = num(dte);
  if (d == null || d < 0) return { when: 'none', text: 'No upcoming earnings date on file', tone: 'neutral' };
  if (t == null) return { when: 'shares', text: `${d} day${d === 1 ? '' : 's'} away — you hold the shares through the print`, tone: d <= 7 ? 'warn' : 'neutral' };
  if (d <= t) return { when: 'inside', text: `${d} day${d === 1 ? '' : 's'} away — INSIDE your ${t}-day trade, so the position owns the earnings gap`, tone: 'warn' };
  return { when: 'after', text: `${d} days away — after your ${t}-day expiry`, tone: 'good' };
}

/** Peer table: the stock itself first, then peers; plus the median row. */
export function peerTable(peers: any, ticker: string): { rows: { ticker: string; r21: number | null; r63: number | null; self?: boolean }[]; median: { r21: number | null; r63: number | null } } | null {
  if (!peers || !Array.isArray(peers.rows)) return null;
  const rows = [{ ticker, r21: num(peers.self_21d_pct), r63: num(peers.self_63d_pct), self: true },
    ...peers.rows.map((p: any) => ({ ticker: String(p.ticker), r21: num(p.ret_21d_pct), r63: num(p.ret_63d_pct) }))];
  return { rows, median: { r21: num(peers.median_21d_pct), r63: num(peers.median_63d_pct) } };
}

/** Macro tape order: equities, then rates/vol, then dollar/commodities/credit. */
export const TAPE_ORDER = ['SPY', 'QQQ', 'IWM', '^VIX', '^TNX', 'UUP', 'GLD', 'USO', 'HYG'];
/** Only these tape symbols are coloured by sign — for a rising VIX, 10Y yield or dollar "up" is not simply good or bad, so those stay neutral. */
export const TAPE_DIRECTIONAL = new Set(['SPY', 'QQQ', 'IWM', 'HYG']);

// ── how a lens score is built (the backend's own arithmetic, shown) ─────────────

/** Each signal's share of the total weight, as a percent — so a zero vote carrying 14% of the weight is visible. */
export function signalShares(signals: { w: number }[] | undefined): number[] {
  const tot = (signals || []).reduce((a, s) => a + (num(s.w) ?? 0), 0);
  return (signals || []).map(s => (tot > 0 ? ((num(s.w) ?? 0) / tot) * 100 : 0));
}

/** Weighted average of the signal strengths — must equal the backend's `dir`; used only to show the working line by line. */
export function weightedAverage(signals: { d: number; w: number }[] | undefined): number | null {
  const rows = (signals || []).filter(s => num(s.d) != null && (num(s.w) ?? 0) > 0);
  const tot = rows.reduce((a, s) => a + s.w, 0);
  return tot > 0 ? rows.reduce((a, s) => a + s.d * s.w, 0) / tot : null;
}

export interface AlignmentView {
  avg: number; aligned: number; scaled: number; k: number; gain: number; points: number; adjust: number; lo: number; hi: number;
  reachPct: number | null; line: string; ceilingNote: string | null;
}
/**
 * The technical lens for a directional position: score = 50 + gain × clamp(k × alignment, −1, 1) (+ vol/strike terms for options).
 * `line` is that sum written out with the real numbers; `reachPct` says how far up the lens's own floor→ceiling range the score sits —
 * the reason a clearly bullish chart reads "only" 64 is that the ceiling is 72.
 */
export function alignmentView(b: any, score: any): AlignmentView | null {
  if (!b || b.kind !== 'alignment') return null;
  const sc = num(score), k = num(b.k), gain = num(b.gain), base = num(b.base) ?? 50, pts = num(b.points), adj = num(b.adjust) ?? 0, al = num(b.aligned), avg = num(b.dir);
  if (sc == null || k == null || gain == null || pts == null || al == null || avg == null) return null;
  const scaled = Math.max(-1, Math.min(1, k * al));
  const reachPct = pos(sc, b.lo, b.hi);
  const line = `${fmtNum(base, 0)} ${pts < 0 ? '−' : '+'} ${fmtNum(gain, 0)} × ${fmtNum(Math.abs(scaled), 2)}${adj ? ` ${adj < 0 ? '−' : '+'} ${fmtNum(Math.abs(adj), 1)}` : ''} = ${fmtNum(sc, 1)}`;
  const ceilingNote = reachPct == null ? null : `${fmtNum(sc, 0)} is ${fmtNum(reachPct, 0)}% of the way up this lens's ${fmtNum(b.lo, 0)}–${fmtNum(b.hi, 0)} range`;
  return { avg, aligned: al, scaled, k, gain, points: pts, adjust: adj, lo: b.lo, hi: b.hi, reachPct, line, ceilingNote };
}

/** Position-risk receipt: the base, each signed term, and the total — with the largest drags first. */
export function riskReceipt(b: any, score: any): { base: number; terms: { label: string; pts: number; note: string }[]; total: number | null; sum: number } | null {
  if (!b || b.kind !== 'position_risk') return null;
  const base = num(b.base) ?? 62;
  const terms = (b.terms || []).filter((t: any) => num(t.pts) != null).map((t: any) => ({ label: String(t.label), pts: Number(t.pts), note: String(t.note || '') }));
  const sum = base + terms.reduce((a: number, t: { pts: number }) => a + t.pts, 0);
  return { base, terms, total: num(score), sum };
}

export interface StopMapMark { key: string; label: string; price: number; pct: number; tone: Tone; fromSpot: number | null }
/**
 * Where the −10% discipline stop sits against the price, your cost and the 1σ month: positions (0–100) on one axis.
 * The 1σ band is spot × (1 ± σ21) — the same "≈ ±$/share" the Risk tile quotes.
 */
export function stopMap(stop: any, spot: any, high52?: any): { marks: StopMapMark[]; band: { lo: number; hi: number; loPct: number; hiPct: number } | null } | null {
  const sp = num(spot), lvl = num(stop?.level), cost = num(stop?.cost), s21 = num(stop?.sigma21_pct);
  if (sp == null || lvl == null || cost == null) return null;
  const bandLo = s21 != null ? sp * (1 - s21 / 100) : null, bandHi = s21 != null ? sp * (1 + s21 / 100) : null;
  const h = num(high52);
  const prices = [sp, lvl, cost, ...(bandLo != null && bandHi != null ? [bandLo, bandHi] : []), ...(h != null ? [h] : [])];
  const lo = Math.min(...prices), hi = Math.max(...prices), pad = (hi - lo) * 0.04 || 1;
  const at = (p: number) => ((p - (lo - pad)) / ((hi + pad) - (lo - pad))) * 100;
  const fromSpot = (p: number) => (sp ? (p / sp - 1) * 100 : null);
  const marks: StopMapMark[] = [
    { key: 'stop', label: stop?.crossed ? 'Stop (breached)' : `Stop −${fmtNum(stop?.pct, 0)}%`, price: lvl, pct: at(lvl), tone: 'bad', fromSpot: fromSpot(lvl) },
    { key: 'cost', label: 'Your cost', price: cost, pct: at(cost), tone: 'neutral', fromSpot: fromSpot(cost) },
    { key: 'spot', label: 'Now', price: sp, pct: at(sp), tone: 'info', fromSpot: 0 },
  ];
  if (h != null && Math.abs(h / sp - 1) > 0.015) marks.push({ key: 'high', label: '52-week high', price: h, pct: at(h), tone: 'good', fromSpot: fromSpot(h) });
  return { marks: marks.sort((a, b) => a.price - b.price), band: bandLo != null && bandHi != null ? { lo: bandLo, hi: bandHi, loPct: at(bandLo), hiPct: at(bandHi) } : null };
}

/** A Position-risk metric row (backend `detail.groups`) → the text shown in its tile. */
export function metricText(m: { value: any; fmt?: string }): string {
  const v = m.value;
  if (v == null || v === '') return '–';
  switch (m.fmt) {
    case 'usd': { const x = num(v); return x == null ? String(v) : `${x < 0 ? '−' : ''}$${grp(Math.abs(x), 0)}`; }
    case 'pct': return fmtPct(v, Math.abs(Number(v)) < 10 ? 1 : 0);
    case 'pct_cap': return fmtPct(v, 1);
    case 'pct_signed': return fmtPct(v, 0, true);
    case 'mult': return `${fmtNum(v, 2)}×`;
    case 'ratio': return `${fmtNum(v, 2)} : 1`;
    case 'num': return fmtNum(v, 2);
    default: return typeof v === 'number' ? fmtNum(v, 2) : String(v);
  }
}
