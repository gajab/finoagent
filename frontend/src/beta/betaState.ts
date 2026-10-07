/**
 * betaState.ts — the Beta experience's ONE action vocabulary for a position.
 *
 * Classic My Trades answers "hold or exit?" in six places with two vocabularies (Strong close / Close
 * vs Exit / Strong exit). Beta collapses that into a single, deterministic state per position:
 *
 *   act      – tested/under water, or the quant says close while the trade is losing  → defend or close
 *   watch    – expires within 7 days, or a short strike is near spot but the trade is profitable
 *   harvest  – the quant says close AND the trade is profitable                       → take profit
 *   hold     – on plan
 *   noquote  – an option leg has no live market, so any verdict would be a guess      → verdict withheld
 *   pending  – live P&L has not loaded yet
 *
 * Everything here is derived from fields the classic screens already compute (analysis.exit_signal,
 * captured_pct, current_quotes …). No new maths: Beta only decides how to PRESENT them and refuses to
 * present a verdict built on a missing quote.
 */
import type { SavedStrategyItem, LivePnlResponse } from '../api';
import { effectivePnl, expiryFrom, dteFrom, tradeHasStock } from '../components/trades/MyTradesV2';

import { STATE_META, STATE_ORDER, type ActionState } from './betaStateMeta';
export { STATE_META, STATE_ORDER };
export type { ActionState };

// ── Option legs & quotes ─────────────────────────────────────────────────────

/** Indexes (into legs_data) of the option legs — the legs that need a live market to be priced. */
export function optionLegIndexes(trade: SavedStrategyItem): number[] {
  const out: number[] = [];
  (trade.legs_data || []).forEach((l: any, i: number) => { if (/call|put/i.test(l?.type || '')) out.push(i); });
  return out;
}

/** A leg has a market when the backend returned a quote (no `error`) with a positive bid, ask or mid. */
export function legHasMarket(q: any): boolean {
  if (!q || q.error) return false;
  return Number(q.bid) > 0 || Number(q.ask) > 0 || Number(q.mid) > 0;
}

export type QuoteStatus = 'ok' | 'gap' | 'cached' | 'none';
export interface QuoteInfo { status: QuoteStatus; missing: number; total: number; note?: string }

/**
 * Is every option leg priced from a real market?
 *  - live P&L: checked leg by leg against `current_quotes`.
 *  - cached snapshot (no quotes in it): trust a persisted `quote_gap` flag when Beta wrote one; otherwise a
 *    P&L of ≤ −99.5% on an option book is almost always an unpriced leg valued at $0, so flag it.
 */
export function quoteInfo(trade: SavedStrategyItem, pnl: LivePnlResponse | null | undefined): QuoteInfo {
  const opts = optionLegIndexes(trade);
  const total = opts.length;
  if (total === 0) return { status: 'ok', missing: 0, total: 0 };
  if (!pnl) return { status: 'none', missing: 0, total };
  const anyPnl = pnl as any;
  if (anyPnl._cached || !Array.isArray(pnl.current_quotes)) {
    if (anyPnl.quote_gap === true) return { status: 'gap', missing: total, total, note: 'flagged unpriced on the last refresh' };
    if (anyPnl.quote_gap === false) return { status: 'ok', missing: 0, total };
    const pct = Number(anyPnl.pnl_pct);
    if (Number.isFinite(pct) && pct <= -99.5) {
      return { status: 'gap', missing: total, total, note: `P&L ${pct.toFixed(0)}% — an option book valued at $0 is almost always an unpriced leg` };
    }
    // The mirror image: a short option "100% captured" with days still left means it was priced at $0.
    const cap = Number(anyPnl.analysis?.captured_pct);
    const dte = dteFrom(expiryFrom(trade, pnl));
    if (Number.isFinite(cap) && cap >= 99.9 && (dte ?? 0) > 3) {
      return { status: 'gap', missing: total, total, note: `100% of max profit "captured" with ${dte}d left is almost always an unpriced leg` };
    }
    return { status: 'cached', missing: 0, total };
  }
  const missing = opts.filter(i => !legHasMarket(pnl.current_quotes.find((c: any) => c.leg === i))).length;
  return { status: missing > 0 ? 'gap' : 'ok', missing, total };
}

// ── Tested ───────────────────────────────────────────────────────────────────

/**
 * A short option leg near/through the money, within `band` of its strike: a short call with spot ≥ (1−band)·K,
 * or a short put with spot ≤ (1+band)·K. Two bands are used:
 *   10% — "tested": the strike is in play (same threshold classic uses for its NEEDS DEFENSE flag) → Watch
 *    3% — "breached": spot is at or beyond the strike → eligible for Act on its own
 * Act is deliberately NOT triggered by the loose 10% band alone — that contradicted a Hold quant read.
 */
export function isTested(trade: SavedStrategyItem, px: number | null | undefined, band = 0.10): boolean {
  if (!(Number(px) > 0)) return false;
  return (trade.legs_data || []).some((l: any) => {
    if (!(/sell|short/i.test(l?.action || '') && /call|put/i.test(l?.type || '') && l?.strike)) return false;
    const k = Number(l.strike) || 0;
    return /call/i.test(l.type) ? Number(px) >= k * (1 - band) : Number(px) <= k * (1 + band);
  });
}

// ── The state ────────────────────────────────────────────────────────────────

export interface TradeState {
  state: ActionState;
  headline: string;            // the verdict in one sentence
  reasons: string[];           // up to 4 supporting lines
  tested: boolean;             // a short strike is within 10% of spot
  breached: boolean;           // a short strike is within 3% of spot (or through it)
  dte: number | null;
  capturedPct: number | null;  // % of max profit already captured (from the quant read)
  quote: QuoteInfo;
  classicSignal: string | null;// the classic quant label, for the audit trail only
}

const SIGNAL_LABEL: Record<string, string> = {
  STRONG_HOLD: 'Strong hold', HOLD: 'Hold', CLOSE: 'Close', STRONG_CLOSE: 'Strong close',
};

export function deriveTradeState(trade: SavedStrategyItem, pnl: LivePnlResponse | null | undefined): TradeState {
  const quote = quoteInfo(trade, pnl);
  const dte = dteFrom(expiryFrom(trade, pnl));
  const px = (pnl as any)?.underlying_price ?? null;
  const tested = isTested(trade, px, 0.10);
  const breached = isTested(trade, px, 0.03);
  const a: any = (pnl as any)?.analysis || {};
  const signal: string | null = a.exit_signal ?? null;
  const captured: number | null = a.captured_pct != null ? Number(a.captured_pct) : null;
  const base = { tested, breached, dte, capturedPct: captured, quote, classicSignal: signal ? (SIGNAL_LABEL[signal] || signal) : null };

  if (!pnl) {
    return { ...base, state: 'pending', headline: 'Waiting for live P&L — press Refresh.', reasons: [] };
  }

  if (quote.status === 'gap') {
    const n = quote.missing;
    return {
      ...base, state: 'noquote',
      headline: quote.note
        ? `Looks unpriced — ${quote.note}. Verdict withheld until it is priced.`
        : `No live quote for ${n} of ${quote.total} option leg${quote.total === 1 ? '' : 's'} — verdict withheld until priced.`,
      reasons: [
        'A leg with no market is valued at $0, which reads as "100% of max profit captured" and can trigger a false exit call.',
        'Refresh, or switch the quote source, to price it.',
      ],
    };
  }

  const noOptions = optionLegIndexes(trade).length === 0;
  if (noOptions) {
    return { ...base, state: 'hold', headline: 'Stock position — no option management signal.', reasons: [] };
  }

  // The verdict is about the option overlay on covered calls (stock held separately), so judge "profitable"
  // on the options' P&L. Cached snapshots lack exit_scope, so a position that holds shares counts as an overlay.
  const overlay = (a.exit_scope === 'options_overlay' || tradeHasStock(trade)) && (pnl as any).options_pnl != null;
  const edgePnl = overlay ? Number((pnl as any).options_pnl) : effectivePnl(pnl, false);
  const profitable = edgePnl != null && edgePnl > 0;
  const closeSignal = signal === 'CLOSE' || signal === 'STRONG_CLOSE';

  const reasons: string[] = [];
  for (const r of [...(a.exit_reasons || []), ...(a.recommendation?.reasons || [])]) {
    if (r && !reasons.includes(r) && reasons.length < 4) reasons.push(r);
  }

  if (closeSignal && profitable) {
    return {
      ...base, state: 'harvest',
      headline: `Close signal with the trade in profit${captured != null ? ` — ${captured.toFixed(0)}% of max profit captured` : ''}. Take profit.`,
      reasons,
    };
  }
  // ACT needs conviction: the quant says close while it is losing, or spot is AT the strike (within 3%) and losing.
  if ((closeSignal && !profitable) || (breached && !profitable)) {
    return {
      ...base, state: 'act',
      headline: closeSignal
        ? `Quant signal is ${SIGNAL_LABEL[signal as string]} while the trade is losing — review a defend or close.`
        : 'Spot is at a short strike and the trade is under water — defend or close.',
      reasons,
    };
  }
  if ((dte != null && dte <= 7) || tested) {
    return {
      ...base, state: 'watch',
      headline: dte != null && dte <= 7
        ? `Expires in ${dte} day${dte === 1 ? '' : 's'} — decide to close, roll or let it expire.`
        : profitable
          ? 'A short strike is within 10% of spot but the trade is still profitable — keep an eye on it.'
          : `A short strike is within 10% of spot and the trade is under water, but the quant read is still ${signal ? SIGNAL_LABEL[signal] || signal : 'Hold'} — monitor it.`,
      reasons,
    };
  }
  return {
    ...base, state: 'hold',
    headline: a.recommendation?.headline || a.exit_reasons?.[0] || 'On plan — nothing to do.',
    reasons,
  };
}

// ── Book-level totals ────────────────────────────────────────────────────────

export interface BookSummary {
  positions: number;
  priced: number;            // positions with a trustworthy P&L
  excluded: number;          // positions left out of the P&L total (no quote / still loading)
  openPnl: number;           // Σ unrealized over priced positions only
  actCount: number;
  noQuoteCount: number;
  watchCount: number;
  harvestCount: number;
  expiringSoon: number;      // DTE ≤ 7
}

/**
 * Book totals that REFUSE to count unpriced positions. Classic sums every row, so one option book valued
 * at $0 drags the headline to a fake loss; here those rows are excluded and counted separately.
 */
export function summarizeBook(
  trades: SavedStrategyItem[],
  pnlMap: Record<number, LivePnlResponse>,
  states: Record<number, TradeState>,
): BookSummary {
  const s: BookSummary = {
    positions: trades.length, priced: 0, excluded: 0, openPnl: 0,
    actCount: 0, noQuoteCount: 0, watchCount: 0, harvestCount: 0, expiringSoon: 0,
  };
  for (const t of trades) {
    const st = states[t.id];
    if (!st) continue;
    if (st.state === 'act') s.actCount++;
    else if (st.state === 'noquote') s.noQuoteCount++;
    else if (st.state === 'watch') s.watchCount++;
    else if (st.state === 'harvest') s.harvestCount++;
    if (st.dte != null && st.dte <= 7) s.expiringSoon++;
    const v = effectivePnl(pnlMap[t.id], false);
    if (st.state === 'noquote' || st.state === 'pending' || v == null) { s.excluded++; continue; }
    s.priced++;
    s.openPnl += v;
  }
  return s;
}

/** Sort key: urgency first (state order), then soonest expiry, then ticker. */
export function urgencyCompare(
  a: { id: number; ticker: string }, b: { id: number; ticker: string },
  states: Record<number, TradeState>,
): number {
  const sa = states[a.id], sb = states[b.id];
  const oa = STATE_ORDER.indexOf(sa?.state ?? 'pending'), ob = STATE_ORDER.indexOf(sb?.state ?? 'pending');
  if (oa !== ob) return oa - ob;
  const da = sa?.dte ?? Infinity, db = sb?.dte ?? Infinity;
  if (da !== db) return da - db;
  return a.ticker.localeCompare(b.ticker);
}
