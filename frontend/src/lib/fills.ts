// Premium after the EXPECTED FILL — one helper so every Income Desk surface states it the same way.
// The backend prices each leg at mid ∓ a share of its half-spread (25% on a tight, deep book → 75% on a wide or
// thin one) plus a $0.65/contract commission (derivative_income_service._expected_fill). The mid premium stays the
// headline; this is what you realistically collect, and it is what the desk score is computed on.

type Fillable = { premium_fill?: number | null; entry_cost?: number | null } | null | undefined;

export const FILL_NOTE =
  'Expected fill: each leg at mid minus 25–75% of its half-spread (wider or thinner books give up more) ' +
  'plus $0.65/contract commission. The desk score uses this, not the mid.';

/** Net premium collected after the expected fill, scaled to `contracts` (null when the trade carries no fill). */
export function premiumAfterFill(o: Fillable, contracts = 1): number | null {
  if (!o || o.premium_fill == null || !Number.isFinite(o.premium_fill)) return null;
  return o.premium_fill * (contracts || 1);
}

/** "≈ $93 after fill" — or null when there is nothing to say. */
export function afterFillText(o: Fillable, contracts = 1): string | null {
  const net = premiumAfterFill(o, contracts);
  if (net == null) return null;
  const sign = net < 0 ? '−' : '';
  return `≈ ${sign}$${Math.round(Math.abs(net)).toLocaleString()} after fill`;
}

/** Tooltip: the opening cost in dollars + how it is estimated. */
export function afterFillTitle(o: Fillable, contracts = 1): string {
  const cost = (o?.entry_cost ?? 0) * (contracts || 1);
  return `Opening cost ≈ $${cost < 10 ? cost.toFixed(2) : Math.round(cost).toLocaleString()} (spread + commissions). ${FILL_NOTE}`;
}
