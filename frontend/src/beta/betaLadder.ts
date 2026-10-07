/**
 * betaLadder.ts — geometry for the per-trade price ladder (spot, short strikes and the zone where the trade
 * keeps its premium). Pure and deterministic; uses only fields the desk already returns per opportunity.
 *
 * "Safe zone" = the price range over which the trade makes money at expiry:
 *   put-side structures   (cash-secured put, put credit spread)      → from the break-even up
 *   call-side structures  (naked call, call credit spread)           → up to the break-even
 *   two-sided structures  (iron condor, strangle, jade lizard)       → between band_low and band_high
 *   covered call                                                     → from the break-even up (upside is capped)
 */
import type { DeskRankedTrade } from '../types';

export interface LadderSpec {
  lo: number; hi: number; spot: number;
  shorts: number[];
  safeLow: number | null;    // null = open on that side
  safeHigh: number | null;
  hasZone: boolean;
}

const num = (v: any): number | null => (v == null || !Number.isFinite(Number(v)) ? null : Number(v));

export function ladderSpec(t: DeskRankedTrade, spot: number): LadderSpec | null {
  if (!(spot > 0)) return null;
  const s = t.structure;
  const be = num(t.breakeven);
  let shorts: number[] = [];
  let safeLow: number | null = null, safeHigh: number | null = null, hasZone = true;

  if (s === 'iron_condor' || s === 'short_strangle' || s === 'jade_lizard') {
    safeLow = num(t.band_low); safeHigh = num(t.band_high);
    shorts = [num(t.put_short), num(t.call_short)].filter((x): x is number => x != null);
    if (shorts.length === 0 && num(t.short_strike) != null) shorts = [num(t.short_strike) as number];
    if (safeLow == null && safeHigh == null) hasZone = false;
  } else if (s === 'call_credit_spread' || s === 'naked_call') {
    safeHigh = be; shorts = [num(t.short_strike)].filter((x): x is number => x != null);
    if (safeHigh == null) hasZone = false;
  } else if (s === 'covered_call' || s === 'cash_secured_put' || s === 'put_credit_spread') {
    safeLow = be; shorts = [num(t.short_strike)].filter((x): x is number => x != null);
    if (safeLow == null) hasZone = false;
  } else {
    shorts = [num(t.short_strike)].filter((x): x is number => x != null);
    safeLow = be; if (safeLow == null) hasZone = false;
  }

  // Symmetric window around spot wide enough to hold every marker, with a floor so nearby strikes don't fill it.
  const pts = [...shorts, ...(safeLow != null ? [safeLow] : []), ...(safeHigh != null ? [safeHigh] : [])];
  const reach = Math.max(spot * 0.06, ...pts.map(p => Math.abs(p - spot))) * 1.15;
  return { lo: spot - reach, hi: spot + reach, spot, shorts, safeLow, safeHigh, hasZone };
}

/** x position (0..width) of a price within the ladder window. */
export function ladderX(spec: LadderSpec, price: number, width: number): number {
  const f = (price - spec.lo) / (spec.hi - spec.lo);
  return Math.max(0, Math.min(1, f)) * width;
}
