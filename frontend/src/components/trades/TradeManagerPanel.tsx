/**
 * TradeManagerPanel — the hold / exit desk for a PLACED trade.
 *
 * Flow (top → bottom):  VERDICT → WHAT TO DO (steps) → how the score is built (3 lenses + event risk) → tabs
 *   Plan        — grouped exit plan with real prices:  against you · take profit · time & events · key levels
 *   Watch next  — levels above / below, what a close through each means for THIS position, indicators, events
 *   Evidence    — Position-risk/Quant · Technical · Fundamental · Event & news (what is behind each number)
 *   AI assist   — facts-only evidence JSON → LLM, on the explicit click only
 * Shares and options get different content: no expiry / theta / strikes for shares, no scale-out ladder for options.
 */
import { useEffect, useState, useCallback, useSyncExternalStore, type ReactNode } from 'react';
import {
  Loader2, AlertTriangle, RefreshCw, Sparkles, Target, Eye, Layers, FileJson, Check, X, Minus, Copy, ShieldAlert, Flag,
} from 'lucide-react';
import { fetchTradeManager, runTradeManagerAI } from '../../api';
import { getDeskScore, subscribeDeskScore } from '../../lib/deskScoreStore';
import type {
  LivePnlResponse, SavedStrategyItem, TradeManagerResult, TradeManagerAI, ManagerExitItem,
  ManagerSignal, ManagerTraderLens, ManagerWatchLevel, ManagerSinceEntry,
} from '../../api';

export type LensKey = 'quant' | 'technical' | 'fundamental' | 'event';

const SIGNAL: Record<ManagerSignal, { label: string; cls: string; box: string }> = {
  STRONG_HOLD: { label: 'STRONG HOLD', cls: 'badge-success', box: 'border-success/30 bg-success/[0.06]' },
  HOLD:        { label: 'HOLD',        cls: 'badge-success badge-outline', box: 'border-success/20 bg-success/[0.03]' },
  EXIT:        { label: 'EXIT',        cls: 'badge-warning', box: 'border-warning/30 bg-warning/[0.06]' },
  STRONG_EXIT: { label: 'STRONG EXIT', cls: 'badge-error', box: 'border-error/30 bg-error/[0.06]' },
};
const ACTION_LABEL: Record<string, string> = {
  EXIT: 'Exit', TRIM: 'Trim', TAKE_PROFIT: 'Take profit', DEFEND_OR_EXIT: 'Defend / exit', REVIEW: 'Review', DERISK: 'De-risk', TIGHTEN: 'Tighten', WATCH: 'Watch',
};
const ACTION_CLS: Record<string, string> = {
  EXIT: 'badge-error', DEFEND_OR_EXIT: 'badge-error', TRIM: 'badge-warning', TIGHTEN: 'badge-warning', DERISK: 'badge-warning',
  TAKE_PROFIT: 'badge-success', REVIEW: 'badge-info', WATCH: 'badge-ghost',
};
const STEP_CLS: Record<string, string> = {
  EXIT: 'badge-error', TRIM: 'badge-warning', PROFIT: 'badge-success', REVIEW: 'badge-info', INFO: 'badge-ghost', HOLD: 'badge-success badge-outline',
};
const STEP_LABEL: Record<string, string> = { EXIT: 'Exit', TRIM: 'Trim', PROFIT: 'Take profit', REVIEW: 'Review', INFO: 'Note', HOLD: 'Hold' };
const GROUPS: { key: NonNullable<ManagerExitItem['group']>; title: string }[] = [
  { key: 'against', title: 'If it goes against you' },
  { key: 'for', title: 'Take profit' },
  { key: 'rules', title: 'Time & events' },
  { key: 'levels', title: 'Your cost & key levels' },
];
const TONE: Record<string, string> = { good: 'text-success', warn: 'text-warning', bad: 'text-error' };

const n = (v: any, d = 2) => (v == null || Number.isNaN(Number(v)) ? '–' : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const money = (v: any) => (v == null ? '–' : `${Number(v) < 0 ? '−' : '+'}$${Math.abs(Number(v)).toLocaleString(undefined, { maximumFractionDigits: 0 })}`);
const usd = (v: any) => (v == null ? '–' : `${Number(v) < 0 ? '−' : ''}$${Math.abs(Number(v)).toLocaleString(undefined, { maximumFractionDigits: 0 })}`);
const usd2 = (v: any) => (v == null ? '–' : `${Number(v) < 0 ? '−' : '+'}$${Math.abs(Number(v)).toLocaleString(undefined, { maximumFractionDigits: Math.abs(Number(v)) < 10 ? 2 : 0 })}`);
const barColor = (v: number) => (v >= 66 ? 'bg-success' : v >= 45 ? 'bg-warning' : 'bg-error');
const wordOf = (v: number) => (v >= 65 ? { w: 'Supportive', c: 'text-success' } : v >= 45 ? { w: 'Neutral', c: 'text-base-content/70' } : v >= 30 ? { w: 'Caution', c: 'text-warning' } : { w: 'Against', c: 'text-error' });

/** Render any value for a table cell — never "[object Object]" (e.g. the MTF bias is {direction, strength, score, rationale}). */
const show = (v: any): string => {
  if (v == null) return '';
  if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (Array.isArray(v)) return v.map(show).filter(Boolean).join(', ');
  if (typeof v === 'object') {
    if (v.direction != null) return [v.direction, v.strength].filter(x => x != null && x !== '').join(' · ');
    if (v.label != null) return String(v.label);
    if (v.overall != null) return String(v.overall);
    try { return JSON.stringify(v); } catch { return ''; }
  }
  return String(v);
};
const fmtMetric = (m: { value: any; fmt: string }) => {
  const v = m.value;
  if (v == null) return '–';
  switch (m.fmt) {
    case 'pct': return `${v}%`;
    case 'pct_cap': return `${v}% of capital`;
    case 'pct_signed': return `${Number(v) > 0 ? '+' : ''}${v}%`;
    case 'usd': return usd(v);
    case 'ratio': return `${v} : 1`;
    case 'mult': return `${v}×`;
    case 'text': return String(v);
    default: return String(Math.abs(Number(v)) >= 100 ? Number(v).toFixed(0) : Number(v).toFixed(2).replace(/\.?0+$/, ''));
  }
};

/** Plain-English meaning of the jargon labels that show up as "reads that agree" on a level. */
const GLOSS: [RegExp, string][] = [
  [/dealer put support/i, 'Strike with the most put positioning. Market-makers hedging those puts tend to BUY dips there, so it often acts as support.'],
  [/dealer call resistance/i, 'Strike with the most call positioning. Market-makers hedging those calls tend to SELL rallies there, so it often caps price.'],
  [/gamma flip/i, 'Price where dealer hedging flips from calming moves to amplifying them. Below it, moves tend to accelerate.'],
  [/hvl|gamma pin/i, 'Strike with the heaviest option positioning — price tends to gravitate toward it near expiry.'],
  [/\bfvg\b|fair value gap/i, 'Fair-value gap: a price gap left behind by a fast move. Price often comes back to fill it.'],
  [/\bob\b|order block|demand ob|supply ob/i, 'Order block: the zone where large players bought/sold before a strong move — often defended when retested.'],
  [/\bpoc\b/i, 'Point of control: the price with the most volume traded in that period — a magnet and a support/resistance level.'],
  [/vah|val\b|value area/i, 'Edge of the value area (where ~70% of the volume traded). Price outside it is "accepted" at new levels or rejected back in.'],
  [/avwap|vwap/i, 'Volume-weighted average price (anchored to an event or period) — the average price paid by that group of buyers.'],
  [/ssl pool|bsl pool|liquidity/i, 'A cluster of resting stop orders just beyond a swing low/high; price is often pulled there to trigger them.'],
  [/sma|ema/i, 'Moving average: the average closing price over that many days — a common trend line.'],
  [/pivot/i, 'Floor-trader pivot level computed from the prior session\'s high, low and close.'],
  [/swing (high|low)/i, 'A confirmed recent turning point in price.'],
  [/52-wk|55-day|20-day/i, 'The highest/lowest price over that look-back — a breakout/breakdown reference.'],
  [/expected-move/i, 'Edge of the move the options market is pricing over the period.'],
  [/chandelier/i, 'A volatility-based trailing stop: the recent extreme minus a multiple of ATR.'],
  [/supertrend/i, 'A trend-following line based on ATR; price closing through it flips the trend.'],
  [/ichimoku|kijun|cloud/i, 'Ichimoku trend lines — the cloud/base line act as dynamic support or resistance.'],
  [/last earnings|ytd open/i, 'A reference price many holders anchor to (the price at the last earnings report / the year\'s open).'],
  [/bull fvg|demand/i, 'A zone where buyers stepped in before.'],
];
export const gloss = (label: string) => GLOSS.find(([re]) => re.test(label))?.[1];

/* ───────────────────────── small building blocks ───────────────────────── */

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-base-200/20 overflow-hidden">
      <div className="px-3 py-1.5 border-b border-white/[0.05] flex items-baseline gap-2">
        <span className="text-[11px] font-semibold text-base-content/80">{title}</span>
        {hint && <span className="text-[10px] text-base-content/35">{hint}</span>}
      </div>
      <div className="px-3">{children}</div>
    </div>
  );
}

function Pill({ id, cur, set, icon, label }: { id: string; cur: string; set: (s: string) => void; icon: ReactNode; label: string }) {
  return (
    <button onClick={() => set(id)}
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[11px] font-semibold whitespace-nowrap transition-colors ${cur === id ? 'bg-secondary/15 text-secondary' : 'text-base-content/55 hover:bg-base-200/60'}`}>
      {icon}{label}
    </button>
  );
}

function LensTile({ label, word, score, weight, caption, active, onClick, muted }: {
  label: string; word: { w: string; c: string }; score?: string; weight?: string; caption: string; active: boolean; onClick: () => void; muted?: boolean;
}) {
  return (
    <button onClick={onClick} title="See what is behind this"
      className={`text-left rounded-md px-2.5 py-2 border transition-colors ${active ? 'border-secondary/40 bg-secondary/[0.06]' : 'border-white/[0.05] hover:bg-base-200/50'} ${muted ? 'opacity-60' : ''}`}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[10px] uppercase tracking-wider text-base-content/55 font-semibold whitespace-nowrap">{label}{weight && <span className="text-base-content/30 font-normal"> · {weight}</span>}</span>
        {score && <span className="font-mono text-[11px] text-base-content/50">{score}</span>}
      </div>
      <div className={`text-[13px] font-semibold ${word.c}`}>{word.w}</div>
      <div className="text-[10px] text-base-content/45 leading-snug line-clamp-2 min-h-[2.4em]">{caption}</div>
    </button>
  );
}

function KV({ rows }: { rows: [string, any][] }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-x-5">
      {rows.filter(r => r[1] != null && r[1] !== '').map(([k, v]) => (
        <div key={k} className="flex justify-between gap-3 text-[11px] border-b border-white/[0.04] py-1">
          <span className="text-base-content/45">{k}</span><span className="font-mono text-base-content/85 text-right">{show(v)}</span>
        </div>
      ))}
    </div>
  );
}

function Notes({ notes }: { notes?: string[] }) {
  if (!notes || !notes.length) return null;
  return (
    <ul className="space-y-0.5 py-2">
      {notes.map((x, i) => <li key={i} className="text-[11px] text-base-content/70 flex gap-1.5 leading-snug"><span className="text-base-content/30">•</span><span>{x}</span></li>)}
    </ul>
  );
}

/** "Dealer put support 1,045 · Gamma flip 1,025" — the reads that agree at a level, each with its own price and a plain-English tooltip. */
function SourceChips({ src, labels }: { src?: { label: string; price: number }[]; labels?: string[] }) {
  const rows = src && src.length ? src : (labels || []).map(l => ({ label: l, price: NaN }));
  if (!rows.length) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {rows.map((r, i) => (
        <span key={i} title={gloss(r.label) || r.label} className="inline-flex items-baseline gap-1 rounded bg-base-300/30 px-1.5 py-0.5 text-[10px] text-base-content/65 whitespace-nowrap cursor-help">
          {r.label}{Number.isFinite(r.price) && <b className="font-mono text-base-content/85">{n(r.price)}</b>}
        </span>))}
    </div>
  );
}

/** TRACKING: the position you are already in — when you entered, how long you've held, what the stock did since, and what it has banked. */
function SinceEntry({ se, p }: { se?: ManagerSinceEntry; p: any }) {
  if (!se) return null;
  const pnl = p?.pnl || {};
  const dateTxt = se.entry_date ? new Date(se.entry_date + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : null;
  const bits: ReactNode[] = [];
  if (se.days_held != null) bits.push(<span key="h">Held <b className="text-base-content/85">{se.days_held} day{se.days_held === 1 ? '' : 's'}</b>{dateTxt ? ` (since ${dateTxt})` : ''}</span>);
  if (pnl.unrealized != null) bits.push(<span key="p">P&amp;L <b className={pnl.unrealized >= 0 ? 'text-success' : 'text-error'}>{money(pnl.unrealized)}{pnl.pct != null ? ` (${pnl.pct > 0 ? '+' : ''}${pnl.pct}%)` : ''}</b></span>);
  if (se.underlying_entry != null && se.move_pct != null)
    bits.push(<span key="u">Stock {n(se.underlying_entry)} → {n(se.underlying_now)} <b className={se.vs_you === 'with you' ? 'text-success' : se.vs_you === 'against you' ? 'text-error' : 'text-base-content/80'}>{se.move_pct > 0 ? '+' : ''}{se.move_pct}%{se.vs_you ? ` · ${se.vs_you}` : ''}</b>
      <span className="text-base-content/35"> ({se.underlying_entry_source})</span></span>);
  if (se.high_since_pct != null && se.low_since_pct != null) bits.push(<span key="r">Since entry: high <b className="text-base-content/80">{se.high_since_pct > 0 ? '+' : ''}{se.high_since_pct}%</b> · low <b className="text-base-content/80">{se.low_since_pct > 0 ? '+' : ''}{se.low_since_pct}%</b></span>);
  if ((se.rolls || 0) > 0) bits.push(<span key="ro">Rolled <b className="text-base-content/80">{se.rolls}×</b>{se.roll_realized_pnl != null ? ` (banked ${money(se.roll_realized_pnl)})` : ''}{se.effective_breakevens?.length ? ` · effective breakeven ${se.effective_breakevens.map(b => n(b)).join(' / ')}` : ''}</span>);
  if (se.realized_banked) bits.push(<span key="b">Realized from partial closes <b className="text-base-content/80">{money(se.realized_banked)}</b></span>);
  if (!bits.length && !p?.notes) return null;
  return (
    <div className="mt-2 space-y-0.5">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5 text-[11px] text-base-content/55" title="You are already in this trade — everything below is from here forward, given what you own and paid.">
        <span className="text-[10px] uppercase tracking-wider text-base-content/35 font-semibold">Since you entered</span>{bits}
      </div>
      {p?.notes ? <div className="text-[11px] text-base-content/45 line-clamp-2"><span className="text-[10px] uppercase tracking-wider text-base-content/35 font-semibold">Your thesis </span>{String(p.notes)}</div> : null}
    </div>
  );
}

/* ───────────────────────── PLAN ───────────────────────── */

function PlanRow({ it }: { it: ManagerExitItem }) {
  const [open, setOpen] = useState(false);
  const left = it.level != null ? n(it.level) : it.pnl_level != null ? money(it.pnl_level) : it.in_days != null ? `in ${it.in_days}d` : '—';
  const dist = it.level != null && it.distance_pct != null && it.kind !== 'breakeven'
    ? `${usd2(it.distance_usd)} · ${it.distance_pct > 0 ? '+' : ''}${it.distance_pct}%${it.distance_atr != null ? ` · ${it.distance_atr} ATR` : ''}`
    : it.level != null && it.kind === 'breakeven' && it.distance_pct != null ? `${it.distance_pct > 0 ? '+' : ''}${it.distance_pct}% from price` : '';
  const more = it.detail || it.evidence || it.hard_stop != null || it.kind === 'stop';
  return (
    <div className="grid grid-cols-[96px_1fr] sm:grid-cols-[120px_1fr] gap-x-3 py-2.5 border-b border-white/[0.05] last:border-0">
      <div>
        <div className="font-mono text-[13px] text-base-content/90 whitespace-nowrap">{left}</div>
        {dist && <div className="text-[10px] text-base-content/40 leading-tight">{dist}</div>}
      </div>
      <div className="min-w-0">
        <div className="flex items-center flex-wrap gap-x-2 gap-y-1">
          <span className="text-[12px] font-semibold text-base-content/90">{it.title || it.kind}</span>
          <span className={`badge badge-xs whitespace-nowrap ${ACTION_CLS[it.action] || 'badge-ghost'}`}>{ACTION_LABEL[it.action] || it.action}{it.fraction ? ` ${it.fraction}` : ''}</span>
        </div>
        {it.status_text && <div className={`text-[11px] mt-0.5 ${TONE[it.status_tone || ''] || 'text-base-content/55'}`}>{it.status_text}</div>}
        <div className="text-[11px] text-base-content/65 leading-snug mt-0.5">{it.why}</div>
        {(it.source_levels && it.source_levels.length > 0) ? <SourceChips src={it.source_levels} /> : null}
        {more && <button className="text-[10px] text-secondary/70 hover:text-secondary mt-1" onClick={() => setOpen(v => !v)}>{open ? 'hide' : 'why this / evidence'}</button>}
        {open && (
          <div className="mt-1 text-[10px] text-base-content/50 leading-snug space-y-0.5 border-l border-white/[0.08] pl-2">
            {it.detail && <div>{it.detail}</div>}
            {it.hard_stop != null && !it.detail && <div>Intraday hard stop {n(it.hard_stop)}.</div>}
            {it.evidence && <div className="text-secondary/60">{it.evidence}</div>}
            {(it.source_levels || []).map((s, i) => gloss(s.label) ? <div key={i}><b className="text-base-content/65">{s.label}:</b> {gloss(s.label)}</div> : null)}
          </div>
        )}
      </div>
    </div>
  );
}

function PlanTab({ res }: { res: TradeManagerResult }) {
  const plan = res.exit_plan;
  const vol = plan.vol;
  const isStock = res.profile?.kind === 'stock';
  const chips: ReactNode[] = [];
  if (plan.atr) chips.push(<span key="atr" title="Average True Range (14 days): how far this stock typically moves in a day, including gaps.">Typical daily move (ATR) <b className="text-base-content/85">${n(plan.atr)}{plan.atr_pct != null ? ` · ${plan.atr_pct}%` : ''}</b></span>);
  if (vol?.sigma_dte_pct != null) chips.push(<span key="s" title={`σ ${vol.sigma_ann_pct}% annualised`}>Normal swing over {vol.horizon_short || 'expiry'} <b className="text-base-content/85">±{vol.sigma_dte_pct}%</b></span>);
  if (plan.risk_reward) chips.push(<span key="rr" title={plan.risk_reward.note}>Reward : risk <b className={plan.risk_reward.ratio >= 1.5 ? 'text-success' : 'text-warning'}>{plan.risk_reward.ratio} : 1</b></span>);
  (vol?.strikes || []).forEach(k => chips.push(
    <span key={k.side}>Short {k.side} {k.strike}: <b className={k.breached ? 'text-error' : (k.p_touch ?? 0) >= 0.35 ? 'text-warning' : 'text-base-content/85'}>{k.breached ? 'breached' : `${Math.round((k.p_touch ?? 0) * 100)}% to touch`}</b></span>));
  return (
    <div className="space-y-2">
      {chips.length > 0 && <div className="flex flex-wrap gap-x-5 gap-y-1 px-1 text-[11px] text-base-content/55">{chips}</div>}
      {GROUPS.map(g => {
        const rows = plan.items.filter(i => (i.group || 'against') === g.key);
        if (!rows.length) return null;
        return <Section key={g.key} title={g.title}>{rows.map((it, i) => <PlanRow key={i} it={it} />)}</Section>;
      })}
      {plan.hold_odds && !isStock && (
        <details className="rounded-lg border border-white/[0.06] bg-base-200/20 px-3 py-2 text-[11px] text-base-content/60">
          <summary className="cursor-pointer select-none font-semibold text-base-content/70">How often did closing beat holding in similar spots? (backtest)</summary>
          <div className="mt-1.5 space-y-0.5">
            {plan.hold_odds.rows.map((r, i) => (
              <div key={i} className="grid grid-cols-[150px_1fr] gap-2">
                <span className="text-base-content/75">{r.state}</span>
                <span>closing was better <b className={r.p_close_better >= 25 ? 'text-warning' : ''}>{r.p_close_better}%</b> of the time · holding averaged <b className={r.mean_delta >= 0 ? 'text-success/80' : 'text-error/80'}>{r.mean_delta >= 0 ? '+' : ''}{r.mean_delta}%</b> of collateral · worst-5% hold {r.worst5}% <span className="text-base-content/30">(n={r.n.toLocaleString()})</span></span>
              </div>))}
            <div className="text-[10px] text-base-content/35 pt-1">All open positions: closing better {plan.hold_odds.baseline.p_close_better}% · hold avg +{plan.hold_odds.baseline.mean_delta}%. Synthetic model, 2016-26.</div>
          </div>
        </details>
      )}
    </div>
  );
}

/* ───────────────────────── WATCH ───────────────────────── */

function LevelCard({ r, atr }: { r: ManagerWatchLevel; atr?: number | null }) {
  const chip = r.effect_if_break === 'good' ? { t: 'Good for you', c: 'badge-success' } : r.effect_if_break === 'bad' ? { t: 'Bad for you', c: 'badge-error' } : { t: r.effect_note || 'No clear effect', c: 'badge-ghost' };
  const above = (r.distance_usd ?? 0) >= 0;
  return (
    <div className="py-2.5 border-b border-white/[0.05] last:border-0">
      <div className="flex items-baseline flex-wrap gap-x-2 gap-y-1">
        <span className="font-mono text-[13px] font-semibold text-base-content/90">{n(r.level)}</span>
        <span className="text-[10px] text-base-content/45">{usd2(r.distance_usd)} · {r.distance_pct != null ? `${r.distance_pct > 0 ? '+' : ''}${r.distance_pct}%` : ''} · {above ? 'above' : 'below'} price</span>
        <span className={`badge badge-xs badge-outline whitespace-nowrap ${chip.c}`}>{chip.t}</span>
      </div>
      <SourceChips src={r.source_levels} labels={r.source_levels?.length ? undefined : (r.what ? r.what.split(', ') : [])} />
      <div className="text-[11px] text-base-content/75 leading-snug mt-1"><b className="text-base-content/50 font-medium">If it breaks:</b> {r.if_break}</div>
      <div className="text-[11px] text-base-content/55 leading-snug"><b className="text-base-content/40 font-medium">If it holds:</b> {r.if_reject || r.if_hold}</div>
      {r.noise && <div className="text-[10px] text-base-content/35 mt-0.5">Inside one normal day's move{atr ? ` (ATR $${n(atr)})` : ''} — treat it as noise unless it closes through on heavy volume.</div>}
    </div>
  );
}

function WatchTab({ res }: { res: TradeManagerResult }) {
  const m = res.monitor;
  const atr = res.exit_plan.atr;
  return (
    <div className="space-y-2">
      <div className="text-[10px] text-base-content/45 px-1">“Breaks” means a daily <b>close</b> through the level (ideally on volume ≥ 1.5× average) — not an intraday wick. Hover a chip for what it means.</div>
      <div className="grid md:grid-cols-2 gap-2">
        <Section title="Above price">
          {m.up.length ? m.up.map((r, i) => <LevelCard key={i} r={r} atr={atr} />) : <div className="text-[11px] text-base-content/35 py-2">No strong level overhead — watch the 20-day high.</div>}
        </Section>
        <Section title="Below price">
          {m.down.length ? m.down.map((r, i) => <LevelCard key={i} r={r} atr={atr} />) : <div className="text-[11px] text-base-content/35 py-2">No strong level below — watch the 20-day low.</div>}
        </Section>
      </div>
      <Section title="Indicators to watch">
        {m.indicators.map((x, i) => (
          <div key={i} className="grid grid-cols-[170px_1fr] gap-x-3 py-1.5 border-b border-white/[0.05] last:border-0">
            <div><div className="text-[11px] font-semibold text-base-content/85">{x.metric}</div><div className="font-mono text-[11px] text-base-content/50">{show(x.now)}</div></div>
            <ul className="text-[11px] text-base-content/60 leading-snug">{x.watch.slice(0, 2).map((w, j) => <li key={j}>• {w}</li>)}</ul>
          </div>))}
      </Section>
      <Section title="Fundamentals · events · macro">
        {m.fundamental_events.map((x, i) => (
          <div key={i} className="grid grid-cols-[170px_1fr] gap-x-3 py-1.5 border-b border-white/[0.05] last:border-0">
            <div><div className="text-[11px] font-semibold text-base-content/85">{x.item}</div><div className="font-mono text-[10px] text-base-content/45">{x.when}</div></div>
            <div className="text-[11px] text-base-content/60 leading-snug">{x.watch}{x.url && <> · <a className="link" href={x.url} target="_blank" rel="noreferrer">open</a></>}</div>
          </div>))}
      </Section>
    </div>
  );
}

/* ───────────────────────── EVIDENCE ───────────────────────── */

function RuleIcon({ met }: { met: boolean | null }) {
  if (met === true) return <Check className="w-3 h-3 text-success shrink-0 mt-0.5" />;
  if (met === false) return <X className="w-3 h-3 text-error shrink-0 mt-0.5" />;
  return <Minus className="w-3 h-3 text-base-content/30 shrink-0 mt-0.5" />;
}

function TraderCard({ l }: { l: ManagerTraderLens }) {
  const tone = l.stance === 'bullish' ? 'text-success' : l.stance === 'bearish' ? 'text-error' : 'text-base-content/50';
  return (
    <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] font-semibold text-base-content/90">{l.trader}</span>
        <span className={`text-[10px] font-mono whitespace-nowrap ${tone}`}>{l.met}/{l.total} · {l.stance}</span>
      </div>
      <div className="text-[10px] text-base-content/40 italic mb-1.5 leading-snug">{l.philosophy}</div>
      <ul className="space-y-0.5">
        {l.rules.map(r => (
          <li key={r.id} className="flex items-start gap-1.5 text-[10px] text-base-content/65 leading-snug">
            <RuleIcon met={r.met} /><span>{r.rule}{r.value != null && r.value !== '' && <span className="text-base-content/35 font-mono"> — {show(r.value)}</span>}</span>
          </li>))}
      </ul>
      <div className="mt-1.5 pt-1.5 border-t border-white/[0.05] text-[10px] text-base-content/50">
        <span className="text-base-content/35">Their exit · </span>{l.exit_rule}{l.exit_level != null && <span className="font-mono text-base-content/70"> ({n(l.exit_level)})</span>}
      </div>
    </div>
  );
}

export function QuantView({ lens, isStock }: { lens: any; isStock: boolean }) {
  const d = lens.detail;
  if (!d) return null;
  const desk = d.desk || {};
  const SUB: [string, string][] = [['edge', 'Edge'], ['pop', 'Hit-rate'], ['sortino', 'Risk-adj.'], ['tail', 'Tail'], ['carry', 'Carry']];
  const hasDesk = !isStock && lens.source !== 'fallback';
  return (
    <div className="space-y-2">
      <div className="text-[11px] text-base-content/55 leading-snug px-1">
        {isStock ? 'Shares have no option payoff to price, so this lens measures POSITION RISK: how big a normal move is, how close the −10% stop is, drawdown, beta.'
          : 'Asks whether this trade\'s own math still works — odds, edge left, theta/gamma, strike distance — independent of the chart.'}
      </div>
      <Section title="Why this score"><Notes notes={lens.notes} /></Section>
      {hasDesk && (
        <Section title={`Quant desk — hold read · ${String(desk.signal || '—').replace(/_/g, ' ')} ${desk.score != null ? Math.round(desk.score) + '/100' : ''}`}
          hint={desk.hold_base != null ? `hold anchor ${Math.round(desk.hold_base)} = neutral 50 + holder factors` : undefined}>
          {(desk.adjustments || []).length > 0 && (
            <div className="py-1.5">
              <div className="text-[10px] uppercase tracking-wider text-base-content/35 mb-0.5">What moved the hold score</div>
              {desk.adjustments.map((a: any, i: number) => (
                <div key={i} className="grid grid-cols-[46px_1fr] gap-2 text-[11px] py-0.5">
                  <span className={`font-mono text-right ${(a.pts ?? 0) > 0 ? 'text-success' : (a.pts ?? 0) < 0 ? 'text-error' : 'text-base-content/40'}`}>{a.pts != null ? `${a.pts > 0 ? '+' : ''}${a.pts}` : '·'}</span>
                  <span className="text-base-content/70"><b className="text-base-content/85">{a.label}</b>{a.note ? ` — ${a.note}` : ''}</span>
                </div>))}
            </div>)}
          {(desk.holder_factors || []).length > 0 && (
            <div className="py-1.5 border-t border-white/[0.05]">
              <div className="text-[10px] uppercase tracking-wider text-base-content/35 mb-0.5">Holder's view of the market</div>
              {desk.holder_factors.map((f: any, i: number) => (
                <div key={i} className="flex gap-1.5 text-[11px] text-base-content/65 py-0.5"><RuleIcon met={f.favorable} /><span><b className="text-base-content/80">{f.label}</b> — {f.note}</span></div>))}
            </div>)}
          {(desk.overrides || []).length > 0 && <div className="py-1.5 text-[11px] text-warning flex gap-1.5"><ShieldAlert className="w-3 h-3 shrink-0 mt-0.5" />{desk.overrides.join(' · ')}</div>}
          {desk.hold_vs_close && <div className="py-1.5 text-[11px] text-base-content/60 border-t border-white/[0.05]"><b>Hold vs close:</b> {String(desk.hold_vs_close).replace(/_/g, ' ')}{(desk.hold_vs_close_reasons || []).length > 0 && ` — ${desk.hold_vs_close_reasons.join('; ')}`}</div>}
          {Object.keys(desk.subscores || {}).length > 0 && (
            <details className="py-1.5 border-t border-white/[0.05]">
              <summary className="cursor-pointer select-none text-[10px] text-base-content/40">Entry-style quality of this payoff — reference only, not used for the hold decision{desk.entry_reference?.quality != null ? ` (${Math.round(desk.entry_reference.quality)})` : ''}</summary>
              <div className="grid grid-cols-5 gap-2 py-2">
                {SUB.map(([k, lab]) => desk.subscores[k] != null && (
                  <div key={k}><div className="flex justify-between text-[10px] text-base-content/50"><span>{lab}</span><span className="font-mono">{desk.subscores[k]}</span></div>
                    <div className="h-1 rounded-full bg-base-300/40 overflow-hidden"><div className={`h-full ${barColor(desk.subscores[k])}`} style={{ width: `${Math.max(3, desk.subscores[k])}%` }} /></div></div>))}
              </div>
              <div className="text-[10px] text-base-content/35">How attractive this payoff would be as a NEW trade. You are already in it, so the hold read above is what matters.</div>
            </details>)}
        </Section>)}
      {Object.entries(d.groups || {}).map(([g, rows]: any) => (
        <Section key={g} title={isStock && g === 'Risk' ? 'Position risk' : g}>
          {rows.map((m: any) => (
            <div key={m.key} className="grid grid-cols-[minmax(130px,200px)_96px_1fr] gap-x-3 py-1.5 border-b border-white/[0.05] last:border-0 items-baseline">
              <span className="text-[11px] text-base-content/70">{m.label}</span>
              <span className={`font-mono text-[12px] text-right whitespace-nowrap ${TONE[m.tone] || 'text-base-content/90'}`}>{fmtMetric(m)}</span>
              <span className="text-[10px] text-base-content/45 leading-snug">{m.note}</span>
            </div>))}
        </Section>))}
    </div>
  );
}

export function TechnicalView({ res, lens, isStock }: { res: TradeManagerResult; lens: any; isStock: boolean }) {
  const tech = res.technical || {};
  const suite = tech.suite || {};
  const bt = res.backtest;
  const sp = res.profile?.short_premium;
  return (
    <div className="space-y-2">
      <div className="text-[11px] text-base-content/55 leading-snug px-1">{lens.scope}</div>
      <Section title="Why this score"><Notes notes={lens.notes} />
        {lens.signals && (
          <table className="w-full text-[10px] mb-2"><tbody>
            {lens.signals.map((s: any) => (
              <tr key={s.name} className="border-t border-white/[0.04]"><td className="py-0.5 pr-2 text-base-content/60">{s.name}</td>
                <td className="font-mono pr-2 text-right"><span className={s.d > 0.15 ? 'text-success' : s.d < -0.15 ? 'text-error' : 'text-base-content/50'}>{s.d > 0 ? '+' : ''}{s.d.toFixed(2)}</span></td>
                <td className="text-base-content/40">{s.note}</td></tr>))}
          </tbody></table>)}
      </Section>
      <Section title="Key readings">
        <div className="py-1.5"><KV rows={[['RSI(14)', suite.rsi14], ['MACD hist', suite.macd?.hist], ['ADX', suite.adx?.adx], ['+DI / −DI', suite.adx ? `${suite.adx.plus_di} / ${suite.adx.minus_di}` : null],
          ['ATR %', suite.atr_pct], ['Vol 21d ÷ 63d', suite.vol_forecast?.ratio_21_63], ['BB width pctile', suite.bollinger?.bandwidth_percentile_1y], ['Squeeze', suite.squeeze_on == null ? null : suite.squeeze_on ? 'ON' : 'off'],
          ['RVOL', suite.rvol], ['CMF20', suite.cmf20], ['Up/down vol 20d', suite.up_down_volume_ratio_20d], ['Dist / accum days', suite.distribution_days_25d != null ? `${suite.distribution_days_25d} / ${suite.accumulation_days_25d}` : null],
          ['vs 50d SMA %', suite.pct_vs_sma?.['50']], ['vs 200d SMA %', suite.pct_vs_sma?.['200']], ['From 52w high %', suite.range?.pct_from_52w_high], ['12-1 momentum %', suite.tsmom_12_1_pct],
          ['Supertrend', suite.supertrend ? `${suite.supertrend.direction} @ ${suite.supertrend.line}` : null], ['Ichimoku', suite.ichimoku?.price_vs_cloud], ['Weekly stage', suite.weekly?.stage],
          ['RS vs SPY 63d %', suite.relative_strength?.excess_return_pct?.['63']], ['Regime', tech.structure_context?.regime?.label], ['Multi-timeframe bias', tech.structure_context?.bias],
          ...(isStock ? [] : [['Dealer gamma', tech.structure_context?.dealer?.gamma] as [string, any]])]} /></div>
        {tech.volume?.read && <div className="py-1.5 text-[11px] text-base-content/55 border-t border-white/[0.05]"><b className="text-base-content/75">Volume.</b> {tech.volume.read.join(' ')}</div>}
      </Section>
      {(tech.zones || []).length > 0 && (
        <Section title="Confluence zones" hint="where several reads agree">
          {tech.zones.map((z: any, i: number) => (
            <div key={i} className="py-1.5 border-b border-white/[0.04] last:border-0">
              <div className="flex items-baseline gap-2 text-[11px]"><span className="font-mono">{n(z.center)}</span>
                <span className={z.kind === 'support' ? 'text-success' : z.kind === 'resistance' ? 'text-error' : 'text-base-content/50'}>{z.kind}</span></div>
              <SourceChips src={(z.sources || []).slice(0, 5).map((s: any) => ({ label: s.label, price: s.price }))} />
            </div>))}
        </Section>)}
      {(tech.patterns || []).length > 0 && (
        <Section title="Chart patterns">{tech.patterns.map((p: any, i: number) => <div key={i} className="text-[11px] py-1 text-base-content/65">{p.name} — {p.direction}, {p.status}, confidence {p.confidence}{p.breakout?.level ? `, breakout ${p.breakout.level}` : ''}{p.target ? `, target ${show(p.target?.price ?? p.target)}${p.target?.pct != null ? ` (${p.target.pct > 0 ? '+' : ''}${p.target.pct}%)` : ''}` : ''}{p.stop ? `, stop ${show(p.stop)}` : ''}</div>)}</Section>)}
      <details className="rounded-lg border border-white/[0.06] bg-base-200/20 px-3 py-2">
        <summary className="cursor-pointer select-none text-[11px] font-semibold text-base-content/75">15 famous-trader rule-sets ({tech.consensus?.bull} bullish · {tech.consensus?.bear} bearish · {tech.consensus?.neutral} neutral)</summary>
        <div className="text-[10px] text-base-content/45 py-1.5">Read each card as: does the stock still meet this trader's criteria for HOLDING a position like yours, and where would they sell? A systematic reading of their published rules — not what they'd actually do.</div>
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-2">{res.trader_lenses.map(l => <TraderCard key={l.key} l={l} />)}</div>
      </details>
      {bt && (
        <details className="rounded-lg border border-warning/25 bg-warning/[0.04] px-3 py-2 text-[11px] text-base-content/65">
          <summary className="cursor-pointer select-none font-semibold text-warning/80">Backtest reality-check — how much weight these signals deserve</summary>
          <div className="mt-1.5 space-y-1.5 leading-snug">
            <div><b>Universe:</b> {bt.universe}</div>
            <div><b>Direction:</b> {bt.direction.finding}</div>
            <div className="text-base-content/50">{bt.direction.so}</div>
            <div><b>Volatility:</b> {bt.vol_ratio}</div>
            <div><b>Exits (bullish entries, 126-day max hold):</b> {bt.exit_takeaway}</div>
            <table className="w-full"><thead><tr className="text-base-content/40 text-left"><th>Exit rule</th><th className="text-right">avg</th><th className="text-right">win</th><th className="text-right">worst-5%</th><th className="text-right">hold</th></tr></thead>
              <tbody>{Object.entries(bt.exit_rules).map(([k, r]) => (
                <tr key={k} className="border-t border-white/[0.04]"><td className="py-0.5">{r.label}</td><td className="text-right font-mono">{r.mean > 0 ? '+' : ''}{r.mean}%</td>
                  <td className="text-right font-mono">{r.win}%</td><td className="text-right font-mono">{r.p5}%</td><td className="text-right font-mono">{r.hold}d</td></tr>))}</tbody></table>
            {sp && bt.hold_state && <div className="pt-1"><b>Hold vs close, open short put:</b> {bt.hold_state.takeaway}
              <div className="text-base-content/50 mt-0.5"><b>Close trigger:</b> {bt.hold_state.close_trigger.rule}. {bt.hold_state.close_trigger.regime_note}</div></div>}
            {sp && bt.short_put && (
              <div className="pt-1"><b>Short-put management ({bt.short_put.universe}):</b> {bt.short_put.takeaway}
                <table className="w-full mt-1"><thead><tr className="text-base-content/40 text-left"><th>Rule</th><th className="text-right">avg %</th><th className="text-right">win</th><th className="text-right">CVaR5</th><th className="text-right">worst</th><th className="text-right">mean/sd</th></tr></thead>
                  <tbody>{Object.entries(bt.short_put.rows).map(([k, r]: any) => (
                    <tr key={k} className="border-t border-white/[0.04]"><td className="py-0.5">{r.label}</td><td className="text-right font-mono">{r.mean}</td><td className="text-right font-mono">{r.win}%</td>
                      <td className="text-right font-mono">{r.cvar5}%</td><td className="text-right font-mono">{r.worst}%</td><td className="text-right font-mono">{r.mean_sd}</td></tr>))}</tbody></table>
              </div>)}
          </div>
        </details>)}
    </div>
  );
}

export function FundamentalView({ res, lens }: { res: TradeManagerResult; lens: any }) {
  const f = res.fundamental || {};
  return (
    <div className="space-y-2">
      <Section title="Why this score"><Notes notes={lens.notes} /></Section>
      <Section title={`${f.name || 'Company'}`} hint={[f.sector, f.industry].filter(Boolean).join(' · ')}>
        {f.is_fund && <div className="text-[11px] text-base-content/50 py-2">Fund / index — company fundamentals don't apply, so this lens is left out of the score.</div>}
        <div className="py-1.5"><KV rows={[['Trailing P/E', f.valuation_multiples?.trailingPE], ['Forward P/E', f.valuation_multiples?.forwardPE], ['PEG', f.valuation_multiples?.pegRatio],
          ['P/S', f.valuation_multiples?.priceToSalesTrailing12Months], ['EV/EBITDA', f.valuation_multiples?.enterpriseToEbitda],
          ['Gross margin', f.profitability?.grossMargins], ['Op margin', f.profitability?.operatingMargins], ['Net margin', f.profitability?.profitMargins],
          ['ROE', f.profitability?.returnOnEquity], ['ROA', f.profitability?.returnOnAssets], ['Revenue growth', f.profitability?.revenueGrowth], ['Earnings growth', f.profitability?.earningsGrowth],
          ['Debt/Equity', f.balance_sheet?.debtToEquity], ['Current ratio', f.balance_sheet?.currentRatio], ['Free cash flow', f.balance_sheet?.freeCashflow],
          ['Div yield', f.dividend?.yield], ['Payout ratio', f.dividend?.payout_ratio], ['Short % float', f.shares?.short_pct_float], ['Beta', f.shares?.beta]]} /></div>
        {Object.keys(res.pillars || {}).length > 0 && (
          <div className="flex flex-wrap gap-1 py-2 border-t border-white/[0.05]">
            {Object.entries(res.pillars).map(([k, v]) => v.score != null && (
              <span key={k} className={`badge badge-xs badge-outline ${v.score >= 45 ? 'badge-error' : v.score >= 32 ? 'badge-warning' : 'badge-success'}`} title="exit pressure — lower is healthier">{k.replace(/_/g, ' ')} {Math.round(v.score)}</span>))}
          </div>)}
      </Section>
      {f.analyst && Object.keys(f.analyst).length > 0 && (
        <Section title="The Street">
          <div className="py-1.5"><KV rows={[['Consensus', f.analyst.recommendation_key], ['# analysts', f.analyst.n_analysts], ['Target mean', f.analyst.target_mean], ['Target hi / lo', f.analyst.target_high ? `${f.analyst.target_high} / ${f.analyst.target_low}` : null],
            ['Upgrades 90d', f.analyst.upgrades_90d], ['Downgrades 90d', f.analyst.downgrades_90d], ['Insider buys / sells', f.insiders?.recent_buys != null ? `${f.insiders.recent_buys} / ${f.insiders.recent_sells}` : null]]} /></div>
          {(f.analyst.rating_changes_90d || []).slice(0, 6).map((r: any, i: number) => (
            <div key={i} className="text-[11px] text-base-content/55 py-0.5">{r.date} · {r.firm}: {r.from ? `${r.from} → ` : ''}{r.to} ({r.action}{r.price_target ? `, PT ${r.price_target}` : ''})</div>))}
        </Section>)}
    </div>
  );
}

export function EventView({ res, lens }: { res: TradeManagerResult; lens: any }) {
  const ev = res.events || {};
  const mk = res.market || {};
  const List = ({ rows, k }: { rows: any[]; k: string }) => <>{rows.map((x, i) => <div key={`${k}${i}`} className="text-[11px] text-base-content/65 py-1 border-b border-white/[0.04] last:border-0">{x.title} <span className="text-base-content/30">— {x.publisher}</span></div>)}</>;
  return (
    <div className="space-y-2">
      <Section title={lens.clear ? 'Event risk: clear' : `Event risk: ${lens.adjustment} pts on the score`}>
        <Notes notes={lens.notes?.length ? lens.notes : ['No earnings, ex-dividend, volatility spike or risky headline inside the trade window — nothing is subtracted from the score.']} />
      </Section>
      <Section title="Dates & filings">
        <div className="py-1.5"><KV rows={[['Next earnings', ev.earnings_date ? `${ev.earnings_date} (${ev.days_to_earnings}d)` : null], ['Next ex-dividend', ev.ex_dividend_date], ['Last ex-dividend', ev.last_ex_dividend_date]]} /></div>
        {(ev.filings || []).map((x: any, i: number) => <div key={i} className="text-[11px] text-base-content/60 py-0.5">{x.form} {x.date} · <a className="link" href={x.url} target="_blank" rel="noreferrer">filing</a></div>)}
      </Section>
      <Section title="Company news">
        {(ev.headline_flags?.risk_headlines || []).map((h: string, i: number) => <div key={`r${i}`} className="text-[11px] text-error/80 flex gap-1.5 py-1"><AlertTriangle className="w-3 h-3 shrink-0 mt-0.5" />{h}</div>)}
        <List rows={(ev.news || []).slice(0, 8)} k="n" />
        {(ev.news || []).length === 0 && <div className="text-[11px] text-base-content/35 py-2">No headlines returned by the data source.</div>}
      </Section>
      {((ev.industry_news || []).length > 0 || (ev.macro_geopolitical_news || []).length > 0) && (
        <div className="grid md:grid-cols-2 gap-2">
          <Section title="Industry"><List rows={(ev.industry_news || []).slice(0, 5)} k="i" /></Section>
          <Section title="Macro · geopolitical"><List rows={(ev.macro_geopolitical_news || []).slice(0, 6)} k="m" /></Section>
        </div>)}
      <Section title="Peers & market">
        {mk.peers?.rows && <div className="text-[11px] text-base-content/65 py-1.5">Vs peer median: <b>{mk.peers.self_vs_peers_21d_pct}%</b> (21d) · <b>{mk.peers.self_vs_peers_63d_pct}%</b> (63d) — {mk.peers.rows.map((p: any) => `${p.ticker} ${p.ret_21d_pct}%`).join(' · ')}</div>}
        {mk.tape && <div className="flex flex-wrap gap-x-4 gap-y-0.5 py-1.5 border-t border-white/[0.05]">{Object.entries(mk.tape).map(([k, v]: any) => <span key={k} className="text-[11px] text-base-content/55 font-mono">{k} {v.last} <span className={v.change_pct_5d >= 0 ? 'text-success/80' : 'text-error/80'}>{v.change_pct_5d >= 0 ? '+' : ''}{v.change_pct_5d}%</span></span>)}</div>}
        {mk.fred && Object.keys(mk.fred).length > 0 && <div className="text-[11px] text-base-content/50 py-1.5 border-t border-white/[0.05]">{Object.entries(mk.fred).map(([k, v]) => `${k} ${v}`).join(' · ')}</div>}
        {(mk.peer_news || []).slice(0, 6).map((x: any, i: number) => <div key={i} className="text-[11px] text-base-content/50 py-0.5">[{x.peer}] {x.title}</div>)}
      </Section>
    </div>
  );
}

/* ───────────────────────── AI ───────────────────────── */

export function AIResult({ r }: { r: TradeManagerAI }) {
  if (r.parse_error) return <pre className="text-[11px] whitespace-pre-wrap text-base-content/70">{r.raw}</pre>;
  const s = r.verdict ? SIGNAL[r.verdict] : null;
  const Sec = ({ t, children }: { t: string; children: ReactNode }) => (<div className="mt-3"><div className="text-[10px] uppercase tracking-wider text-base-content/40 font-semibold mb-1">{t}</div>{children}</div>);
  const Li = ({ items }: { items?: string[] }) => (items && items.length ? <ul className="space-y-0.5">{items.map((x, i) => <li key={i} className="text-[11px] text-base-content/75 flex gap-1.5"><span className="text-base-content/30">•</span><span>{x}</span></li>)}</ul> : null);
  return (
    <div className="text-[11px] text-base-content/80 leading-relaxed">
      <div className="flex items-center gap-2 flex-wrap">
        {s && <span className={`badge badge-sm font-semibold ${s.cls}`}>{s.label}</span>}
        {r.conviction != null && <span className="text-[10px] font-mono text-base-content/50">conviction {r.conviction}</span>}
        <span className="text-[10px] text-base-content/30">{r._meta?.model} · {r._meta ? Math.round(r._meta.packet_chars / 1000) : '?'}k chars of evidence</span>
      </div>
      {r.one_line && <div className="mt-1 text-[12px] font-medium text-base-content/90">{r.one_line}</div>}
      <Sec t="Why"><Li items={r.why} /></Sec>
      {r.exit_plan && (
        <Sec t="Exit plan"><div className="space-y-0.5">
          {r.exit_plan.primary_exit && <div><b>Exit:</b> {r.exit_plan.primary_exit.level != null ? <span className="font-mono">{r.exit_plan.primary_exit.level}</span> : '—'} <span className="text-base-content/45">({r.exit_plan.primary_exit.basis})</span> — {r.exit_plan.primary_exit.why}</div>}
          {r.exit_plan.stop && <div><b>Stop:</b> {r.exit_plan.stop.level != null ? <span className="font-mono">{r.exit_plan.stop.level}</span> : '—'} — {r.exit_plan.stop.why}</div>}
          {(r.exit_plan.profit_targets || []).map((t, i) => <div key={i}><b>Target:</b> <span className="font-mono">{t.level}</span> — {t.why}</div>)}
          {r.exit_plan.time_or_event_exit && <div><b>Time / event:</b> {r.exit_plan.time_or_event_exit}</div>}
        </div></Sec>)}
      <div className="grid sm:grid-cols-2 gap-2 mt-3">
        {r.hold_case && <div className="rounded-md bg-success/[0.05] border border-success/15 p-2"><div className="text-[10px] uppercase tracking-wider text-success/70 mb-0.5">Case to hold</div>{r.hold_case}</div>}
        {r.exit_case && <div className="rounded-md bg-error/[0.05] border border-error/15 p-2"><div className="text-[10px] uppercase tracking-wider text-error/70 mb-0.5">Case to exit</div>{r.exit_case}</div>}
      </div>
      {r.watch && (
        <Sec t="Watch"><div className="grid sm:grid-cols-2 gap-2">
          {([['Upside', r.watch.upside], ['Downside', r.watch.downside]] as const).map(([t, rows]) => (rows && rows.length ? (<div key={t}><div className="text-[10px] font-semibold text-base-content/60">{t}</div>{rows.map((x, i) => <div key={i}><b>{x.trigger}</b> → {x.means}</div>)}</div>) : null))}
          {r.watch.indicators && r.watch.indicators.length > 0 && <div><div className="text-[10px] font-semibold text-base-content/60">Indicators</div>{r.watch.indicators.map((x, i) => <div key={i}><b>{x.metric}</b>: {x.trigger} → {x.means}</div>)}</div>}
          {r.watch.fundamental_events && r.watch.fundamental_events.length > 0 && <div><div className="text-[10px] font-semibold text-base-content/60">Fundamentals / events</div>{r.watch.fundamental_events.map((x, i) => <div key={i}><b>{x.item}</b> — {x.why}</div>)}</div>}
        </div></Sec>)}
      {r.trader_views && r.trader_views.length > 0 && (
        <Sec t="What the traders would do"><div className="grid sm:grid-cols-2 gap-1.5">
          {r.trader_views.map((t, i) => (<div key={i} className="rounded-md border border-white/[0.06] p-1.5 text-[11px]"><span className="font-semibold text-base-content/90">{t.trader}</span> <span className="badge badge-xs badge-ghost">{t.would}</span>
            <div className="text-base-content/65">{t.because}</div>{t.their_exit && <div className="text-base-content/40">exit: {t.their_exit}</div>}</div>))}
        </div></Sec>)}
      {r.fundamental_read && (
        <Sec t="Fundamentals · news · macro"><div className="space-y-1">
          {([['Recent changes', r.fundamental_read.recent_changes], ['Competitors / industry', r.fundamental_read.competitor_industry], ['Macro / geopolitical', r.fundamental_read.macro_geopolitical], ['Street', r.fundamental_read.analyst_street]] as const)
            .filter(x => x[1]).map(([k, v]) => <div key={k}><b>{k}:</b> {v}</div>)}
          <Li items={r.fundamental_read.what_could_change_direction} />
        </div></Sec>)}
      {r.risks_to_this_call && r.risks_to_this_call.length > 0 && <Sec t="Risks to this call"><Li items={r.risks_to_this_call} /></Sec>}
      {r.data_gaps && r.data_gaps.length > 0 && <Sec t="Data gaps"><Li items={r.data_gaps} /></Sec>}
    </div>
  );
}

/* ───────────────────────── PANEL ───────────────────────── */

export default function TradeManagerPanel({ trade, pnl }: { trade: SavedStrategyItem; pnl: LivePnlResponse }) {
  const [res, setRes] = useState<TradeManagerResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState('plan');
  const [lens, setLens] = useState<LensKey>('quant');
  const [ai, setAi] = useState<TradeManagerAI | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiErr, setAiErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // The deep Quant Analysis score, once the user has run it, IS this panel's Quant lens (the server prefers it over the
  // light read) — so both panels show the SAME number. It arriving re-reads the verdict; until then the lens is labelled "light read".
  const desk = useSyncExternalStore((fn) => subscribeDeskScore(trade.id, fn), () => getDeskScore(trade.id));
  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try { setRes(await fetchTradeManager(trade.id, pnl, desk ?? undefined)); }
    catch (e: any) { setErr(e?.message || 'Trade manager failed'); }
    finally { setLoading(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trade.id, desk]);
  useEffect(() => { load(); }, [load]);

  const askAI = async () => {
    setAiLoading(true); setAiErr(null);
    try { setAi(await runTradeManagerAI(trade.id, pnl)); }
    catch (e: any) { setAiErr(e?.message || 'AI assist failed'); }
    finally { setAiLoading(false); }
  };
  const copyJson = async () => {
    if (!res) return;
    try { await navigator.clipboard.writeText(JSON.stringify(res.evidence_json, null, 2)); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard blocked */ }
  };
  const openLens = (k: LensKey) => { setLens(k); setTab('evidence'); };

  if (loading && !res) {
    return <div className="py-6 flex items-center gap-2 text-[12px] text-base-content/50"><Loader2 className="w-4 h-4 animate-spin" />Reading structure, volatility, fundamentals and events for {trade.ticker}… (up to ~30s cold, instant when cached)</div>;
  }
  if (err && !res) {
    return <div className="text-[12px] text-error flex items-center gap-1.5 py-2"><AlertTriangle className="w-3.5 h-3.5" />{err}<button className="btn btn-ghost btn-xs" onClick={load}>Retry</button></div>;
  }
  if (!res) return null;

  const d = res.decision;
  const sig = SIGNAL[d.signal];
  const p = res.profile || {};
  const isStock = p.kind === 'stock';
  const L = d.lenses;
  const quantLabel = L.quant.label || (isStock ? 'Position risk' : 'Quant');
  const steps = res.exit_plan.recommendation.steps || [];
  const first = (x?: string[]) => (x && x[0]) || '';
  const pct = (w: number) => `${Math.round(w * 100)}%`;
  const lensBody = (k: LensKey) => k === 'quant' ? <QuantView lens={L.quant} isStock={isStock} />
    : k === 'technical' ? <TechnicalView res={res} lens={L.technical} isStock={isStock} />
      : k === 'fundamental' ? <FundamentalView res={res} lens={L.fundamental} /> : <EventView res={res} lens={L.event} />;
  const evAdj = d.event_adj;

  return (
    <div className="space-y-3 pt-1">
      {/* VERDICT + WHAT TO DO */}
      <div className={`rounded-lg border p-3 ${sig.box}`}>
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`badge font-semibold ${sig.cls}`}>{sig.label}</span>
          <span className="font-mono text-base text-base-content/90">{Math.round(d.score)}<span className="text-base-content/35 text-[11px]">/100</span></span>
          <span className="badge badge-ghost badge-sm whitespace-nowrap">confidence {d.confidence}</span>
          <span className="text-[11px] text-base-content/50">· {p.label}{p.dte != null && !isStock ? ` · ${p.dte} DTE` : ''}</span>
          <button className="btn btn-ghost btn-xs ml-auto gap-1" disabled={loading} onClick={(e) => { e.stopPropagation(); load(); }}>
            {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}Refresh
          </button>
        </div>

        <SinceEntry se={res.since_entry} p={p} />

        <div className="mt-2.5 rounded-md bg-base-100/40 border border-white/[0.06] p-2.5">
          <div className="text-[10px] uppercase tracking-wider text-base-content/40 font-semibold mb-1 flex items-center gap-1"><Target className="w-3 h-3" />What to do from here</div>
          {steps.length > 0 ? (
            <ul className="space-y-1">
              {steps.map((s, i) => (
                <li key={i} className="flex items-baseline gap-2 text-[12px] text-base-content/90 leading-snug">
                  <span className={`badge badge-xs whitespace-nowrap shrink-0 ${STEP_CLS[s.tag] || 'badge-ghost'}`}>{STEP_LABEL[s.tag] || s.tag}</span><span>{s.text}</span>
                </li>))}
            </ul>) : <div className="text-[13px] text-base-content/90 leading-snug">{res.exit_plan.recommendation.text}</div>}
        </div>

        {(d.overrides.length > 0 || d.conflicts.length > 0) && (
          <ul className="mt-2 space-y-0.5">
            {d.overrides.map((o, i) => <li key={`o${i}`} className="text-[11px] text-warning flex gap-1.5"><ShieldAlert className="w-3 h-3 shrink-0 mt-0.5" />{o}</li>)}
            {d.conflicts.map((o, i) => <li key={`c${i}`} className="text-[11px] text-base-content/55 flex gap-1.5"><Flag className="w-3 h-3 shrink-0 mt-0.5" />{o}</li>)}
          </ul>)}

        <div className="mt-3 text-[10px] uppercase tracking-wider text-base-content/40 font-semibold">How the score is built</div>
        <div className="mt-1 grid grid-cols-2 lg:grid-cols-4 gap-1.5">
          <LensTile label={quantLabel} word={L.quant.available === false ? { w: 'Not scored', c: 'text-base-content/40' } : wordOf(L.quant.score)}
            score={L.quant.available === false ? undefined : String(Math.round(L.quant.score))} weight={L.quant.available === false ? undefined : pct(L.quant.weight)}
            caption={first(L.quant.notes)} muted={L.quant.available === false} active={tab === 'evidence' && lens === 'quant'} onClick={() => openLens('quant')} />
          <LensTile label="Technical" word={wordOf(L.technical.score)} score={String(Math.round(L.technical.score))} weight={pct(L.technical.weight)}
            caption={first(L.technical.notes) || 'context only'} active={tab === 'evidence' && lens === 'technical'} onClick={() => openLens('technical')} />
          <LensTile label="Fundamental" word={L.fundamental.available === false ? { w: 'Not scored', c: 'text-base-content/40' } : wordOf(L.fundamental.score)}
            score={L.fundamental.available === false ? undefined : String(Math.round(L.fundamental.score))} weight={L.fundamental.available === false ? undefined : pct(L.fundamental.weight)}
            caption={L.fundamental.available === false ? 'fund / no company data' : first(L.fundamental.notes)} muted={L.fundamental.available === false} active={tab === 'evidence' && lens === 'fundamental'} onClick={() => openLens('fundamental')} />
          <LensTile label="Event risk" word={L.event.clear ? { w: 'Clear', c: 'text-success' } : { w: `${evAdj > 0 ? '+' : ''}${evAdj} pts`, c: evAdj < -6 ? 'text-error' : evAdj < 0 ? 'text-warning' : 'text-success' }}
            caption={first(L.event.notes) || 'nothing scheduled that changes the trade'} active={tab === 'evidence' && lens === 'event'} onClick={() => openLens('event')} />
        </div>
        <div className="text-[10px] text-base-content/40 mt-1.5 leading-snug">
          Score <b className="text-base-content/60">{Math.round(d.raw_score)}</b> = blend <b className="text-base-content/60">{Math.round(d.blend)}</b> of the scored lenses (weights above; a lens without data is left out)
          {evAdj !== 0 ? <> {evAdj < 0 ? '−' : '+'} event risk <b className="text-base-content/60">{Math.abs(evAdj)}</b></> : ' — no event risk to add or subtract'}
          {d.raw_score !== d.score ? <> · capped to <b className="text-warning">{Math.round(d.score)}</b> by a discipline rule above</> : ''}. 50 is neutral; ≥ 68 strong hold · 45-68 hold · 28-45 exit · &lt; 28 strong exit.
        </div>
      </div>

      {/* TABS */}
      <div className="flex gap-1 overflow-x-auto pb-0.5 border-b border-white/[0.06]">
        <Pill id="plan" cur={tab} set={setTab} icon={<Target className="w-3.5 h-3.5" />} label="Plan" />
        <Pill id="watch" cur={tab} set={setTab} icon={<Eye className="w-3.5 h-3.5" />} label="Watch next" />
        <Pill id="evidence" cur={tab} set={setTab} icon={<Layers className="w-3.5 h-3.5" />} label="Evidence" />
        <Pill id="ai" cur={tab} set={setTab} icon={<Sparkles className="w-3.5 h-3.5" />} label="AI assist" />
      </div>

      {tab === 'plan' && <PlanTab res={res} />}
      {tab === 'watch' && <WatchTab res={res} />}
      {tab === 'evidence' && (
        <div className="space-y-2">
          <div className="flex gap-1 flex-wrap">
            {([['quant', quantLabel], ['technical', 'Technical'], ['fundamental', 'Fundamental'], ['event', 'Event & news']] as [LensKey, string][]).map(([k, lab]) => (
              <button key={k} onClick={() => setLens(k)} className={`px-2.5 py-1 rounded-full text-[11px] border transition-colors ${lens === k ? 'border-secondary/40 bg-secondary/10 text-secondary' : 'border-white/[0.08] text-base-content/55 hover:bg-base-200/50'}`}>{lab}</button>))}
          </div>
          {lensBody(lens)}
        </div>)}
      {tab === 'ai' && (
        <div className="space-y-2">
          <div className="rounded-lg border border-secondary/20 bg-secondary/[0.04] p-3">
            <div className="flex items-center gap-2 flex-wrap">
              <Sparkles className="w-3.5 h-3.5 text-secondary" />
              <span className="text-[11px] uppercase tracking-wider font-semibold text-secondary/80">AI assist</span>
              <button className="btn btn-secondary btn-xs gap-1.5 ml-auto" disabled={aiLoading} onClick={askAI}>
                {aiLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                {aiLoading ? 'Thinking…' : ai ? 'Re-run' : 'Ask AI to assist the decision'}
              </button>
            </div>
            <p className="text-[11px] text-base-content/50 mt-1">Sends the evidence JSON below — every input we collected, <b>none</b> of our own verdict, scores or trader stances — so the model forms an independent view from the facts and lists what's missing.</p>
            {aiErr && <div className="text-[11px] text-error mt-1.5 flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{aiErr}</div>}
            {ai && <div className="mt-2"><AIResult r={ai} /></div>}
          </div>
          <details className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2.5">
            <summary className="cursor-pointer text-[11px] uppercase tracking-wider font-semibold text-base-content/60 flex items-center gap-1.5 select-none">
              <FileJson className="w-3 h-3" />Evidence JSON (facts only · {Math.round(JSON.stringify(res.evidence_json).length / 1000)}k chars)
              <button className="btn btn-ghost btn-xs ml-auto gap-1" onClick={(e) => { e.preventDefault(); copyJson(); }}><Copy className="w-3 h-3" />{copied ? 'Copied' : 'Copy'}</button>
            </summary>
            <pre className="mt-2 text-[10px] leading-snug max-h-96 overflow-auto text-base-content/60">{JSON.stringify(res.evidence_json, null, 2)}</pre>
          </details>
        </div>)}
      <div className="text-[10px] text-base-content/30">As of {res.as_of}{res.cached_evidence ? ' · market evidence cached ≤10 min, position read is live' : ''} · deterministic desk read, educational — not investment advice.</div>
    </div>
  );
}
