/**
 * betaLegEdit.ts — the pure logic behind the Beta's leg actions: Roll · Edit · Add leg · Mark covered.
 *
 * It builds the SAME request bodies the classic card sends (same endpoints, same fields), so a leg changed in
 * the Beta is indistinguishable from one changed in classic. Differences are deliberate and only ever safer:
 *   - entry_prices are addressed with the alignment rule the classic Close modal and the backend use
 *     (stock row first, option i at i+1, only when the arrays actually line up) instead of guessing from the
 *     card's group, which mis-addresses a covered call whose shares are held elsewhere;
 *   - a leg with NO live market never pre-fills a $0 buy-back (that reads as a free close);
 *   - inputs are validated (finite numbers, strike > 0, a real date, whole contracts ≥ 1) before anything is sent.
 */
import type { SavedStrategyItem, LivePnlResponse } from '../api';
import { inferStrategyType, tradeHasStock } from '../components/trades/MyTradesV2';
import { legHasMarket } from './betaState';

export type Side = 'buy' | 'sell';
export type Kind = 'call' | 'put';
export type Result<T> = { ok: true; data: T } | { ok: false; error: string };

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const num = (s: string | number) => (typeof s === 'number' ? s : parseFloat(String(s).trim()));
const validDate = (s: string) => DATE_RE.test(s) && !Number.isNaN(new Date(s + 'T00:00:00').getTime());

// ── entry-price alignment ────────────────────────────────────────────────────

/** entry_prices has the stock's row first on a combo; option leg i is then at i+1 (only if the arrays line up). */
export function entryOffset(trade: SavedStrategyItem): 0 | 1 {
  const legs = trade.legs_data || [], ep = trade.entry_prices || [];
  const shares = parseFloat(trade.parameters?.shares ?? '0') || 0;
  return shares > 0 && ep.length === legs.length + 1 ? 1 : 0;
}

/** The entry price (per share) recorded for option leg `idx`. */
export function legEntryPrice(trade: SavedStrategyItem, idx: number): number {
  const ep = trade.entry_prices?.[idx + entryOffset(trade)]?.price;
  const leg = (trade.legs_data || [])[idx] || {};
  const v = ep != null && ep !== '' ? ep : (leg.premium ?? leg.mid ?? leg.price ?? 0);
  return Number(v) || 0;
}

// ── ROLL ─────────────────────────────────────────────────────────────────────

export interface RollFields {
  closePrice: string; newAction: Side; newType: Kind; newContracts: number;
  newStrike: string; newExpiration: string; newPremium: string;
}

/** Form defaults: replace the leg like-for-like; pre-fill the buy-back only from a REAL live market. */
export function rollDefaults(trade: SavedStrategyItem, idx: number, pnl?: LivePnlResponse | null): { fields: RollFields; priceNote: string | null } | null {
  const leg = (trade.legs_data || [])[idx];
  if (!leg) return null;
  const q = (pnl?.current_quotes || []).find((c: any) => c.leg === idx);
  const live = !!pnl && !(pnl as any)._cached;
  const hasMarket = live && legHasMarket(q);
  return {
    fields: {
      closePrice: hasMarket && q?.mid != null ? String(q.mid) : '',
      newAction: leg.action === 'buy' || /^buy/i.test(leg.action || '') ? 'buy' : 'sell',
      newType: String(leg.type || '').toLowerCase() === 'put' ? 'put' : 'call',
      newContracts: Number(leg.qty ?? leg.contracts ?? 1) || 1,
      newStrike: String(leg.strike ?? ''),
      newExpiration: String(leg.expiration || leg.expiry || '').slice(0, 10),
      newPremium: '',
    },
    priceNote: hasMarket ? 'Pre-filled from the live mid — replace it with your actual fill.'
      : live ? 'No live market for this leg — enter the price you actually paid to close.'
      : 'Refresh for a live price, or enter your actual fill.',
  };
}

export function buildRollPayload(legIdx: number, f: RollFields): Result<{
  close_legs: { leg_index: number; exit_price: number }[];
  open_legs: { action: Side; type: Kind; strike: number; expiration: string; qty: number; premium: number }[];
}> {
  const exit = num(f.closePrice), strike = num(f.newStrike), prem = num(f.newPremium), qty = Number(f.newContracts);
  if (!Number.isFinite(exit) || exit < 0) return { ok: false, error: 'Enter the price you paid to buy back the old leg.' };
  if (!Number.isFinite(strike) || strike <= 0) return { ok: false, error: 'Enter the new strike.' };
  if (!validDate(f.newExpiration)) return { ok: false, error: 'Pick the new expiry date.' };
  if (!Number.isFinite(prem) || prem < 0) return { ok: false, error: 'Enter the premium on the new leg.' };
  if (!Number.isInteger(qty) || qty < 1) return { ok: false, error: 'Contracts must be a whole number of at least 1.' };
  return { ok: true, data: {
    close_legs: [{ leg_index: legIdx, exit_price: exit }],
    open_legs: [{ action: f.newAction, type: f.newType, strike, expiration: f.newExpiration, qty, premium: prem }],
  } };
}

// ── EDIT a leg ───────────────────────────────────────────────────────────────

export interface EditFields { action: Side; type: Kind; strike: string; qty: number; expiration: string; entry: string }

export function editDefaults(trade: SavedStrategyItem, idx: number): EditFields | null {
  const leg = (trade.legs_data || [])[idx];
  if (!leg) return null;
  const entry = legEntryPrice(trade, idx);
  return {
    action: /^buy/i.test(leg.action || '') ? 'buy' : 'sell',
    type: String(leg.type || '').toLowerCase() === 'put' ? 'put' : 'call',
    strike: String(leg.strike ?? ''),
    qty: Number(leg.qty ?? leg.contracts ?? 1) || 1,
    expiration: String(leg.expiration || leg.expiry || '').slice(0, 10),
    entry: entry > 0 ? String(entry) : '',
  };
}

export function buildEditPayload(trade: SavedStrategyItem, idx: number, f: EditFields): Result<{
  legs_data: any[]; strategy_type: string; entry_prices?: any[];
}> {
  const strike = num(f.strike), entry = num(f.entry), qty = Number(f.qty);
  if (!(trade.legs_data || [])[idx]) return { ok: false, error: 'That leg no longer exists — refresh and try again.' };
  if (!Number.isFinite(strike) || strike <= 0) return { ok: false, error: 'Enter the strike.' };
  if (!validDate(f.expiration)) return { ok: false, error: 'Pick the expiry date.' };
  if (!Number.isFinite(entry) || entry < 0) return { ok: false, error: 'Enter the entry price.' };
  if (!Number.isInteger(qty) || qty < 1) return { ok: false, error: 'Contracts must be a whole number of at least 1.' };

  const newLegs = (trade.legs_data || []).map((l: any, i: number) =>
    i === idx ? { ...l, action: f.action, type: f.type, strike, qty, expiration: f.expiration, premium: entry } : l);
  const out: { legs_data: any[]; strategy_type: string; entry_prices?: any[] } = {
    legs_data: newLegs,
    strategy_type: inferStrategyType(trade.strategy_type || '', newLegs, tradeHasStock(trade)),
  };
  // Keep the recorded entry in step — but only where that row really exists (never punch a hole in the array).
  const ep = trade.entry_prices;
  const at = idx + entryOffset(trade);
  if (Array.isArray(ep) && at < ep.length) {
    const next = [...ep];
    next[at] = { ...next[at], price: entry };
    out.entry_prices = next;
  }
  return { ok: true, data: out };
}

// ── ADD a leg ────────────────────────────────────────────────────────────────

export interface AddFields { action: Side; type: Kind; contracts: number; strike: string; expiration: string; premium: string }

export function addDefaults(): AddFields {
  return { action: 'sell', type: 'call', contracts: 1, strike: '', expiration: '', premium: '' };
}

export function buildAddPayload(trade: SavedStrategyItem, f: AddFields): Result<{ legs_data: any[]; strategy_type: string }> {
  const strike = num(f.strike), prem = num(f.premium), qty = Number(f.contracts);
  if (!Number.isFinite(strike) || strike <= 0) return { ok: false, error: 'Enter the strike.' };
  if (!validDate(f.expiration)) return { ok: false, error: 'Pick the expiry date.' };
  if (!Number.isFinite(prem) || prem < 0) return { ok: false, error: 'Enter the premium.' };
  if (!Number.isInteger(qty) || qty < 1) return { ok: false, error: 'Contracts must be a whole number of at least 1.' };
  const existing = trade.legs_data || [];
  const newLeg = { action: f.action, type: f.type, qty, strike, expiration: f.expiration, premium: prem, label: `Leg ${existing.length + 1}` };
  const newLegs = [...existing, newLeg];
  return { ok: true, data: { legs_data: newLegs, strategy_type: inferStrategyType(trade.strategy_type || '', newLegs, tradeHasStock(trade)) } };
}

// ── MARK COVERED ─────────────────────────────────────────────────────────────

export function hasShortCall(trade: SavedStrategyItem): boolean {
  return (trade.legs_data || []).some((l: any) =>
    String(l.type || '').toUpperCase().includes('CALL') && String(l.action || '').toUpperCase().startsWith('S'));
}

/**
 * "Mark covered" applies to a short call whose shares are held ELSEWHERE (no stock leg on this trade). Same
 * condition as classic: no stock here, a short call, and either an options-only group or already marked.
 */
export function canMarkCovered(hasStock: boolean, shortCall: boolean, group: string, marked: boolean): boolean {
  return !hasStock && shortCall && (group === 'multi_leg' || group === 'income_options' || marked);
}
