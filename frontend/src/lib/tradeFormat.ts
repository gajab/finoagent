/**
 * tradeFormat.ts — single source of truth for displayed numbers.
 *
 * Every trade-related surface (MyTrades, BoxStrategy, Strategies tab,
 * TradePortfolio) imports from here. Format rules are documented once,
 * enforced once. If a number looks inconsistent between two screens,
 * the bug is either (a) a screen not using these helpers, or (b) the
 * math layer — NOT the formatting layer.
 *
 * Conventions
 * -----------
 * - Money: always $ prefix, thousand separators, 2dp.
 * - Percent: always % suffix, 2dp unless tiny (<0.01) in which case 4dp.
 * - Annualized: always "X.XX% ann." so you know it's annualized.
 * - Negative money: "−$X" (U+2212 minus, not hyphen) — aligns nicely in tables.
 * - DTE: "23d" short form; "23 days" when space allows.
 */

const MINUS = '\u2212';   // proper minus sign
const currencyFmt = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Format a dollar amount. Negative shown with Unicode minus for monospace alignment. */
export function fmtMoney(n: number | null | undefined, opts?: { signed?: boolean; compact?: boolean }): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  const abs = Math.abs(n);
  const formatted = opts?.compact && abs >= 1000
    ? (abs >= 1_000_000 ? `$${(abs / 1_000_000).toFixed(2)}M` : `$${(abs / 1000).toFixed(1)}k`)
    : currencyFmt.format(abs);
  if (n < 0) return `${MINUS}${formatted}`;
  if (opts?.signed && n > 0) return `+${formatted}`;
  return formatted;
}

/** Format a percent. `n` is expected as a percent already (5.2 → "5.20%"). */
export function fmtPct(n: number | null | undefined, opts?: { signed?: boolean; dp?: number }): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  const dp = opts?.dp ?? (Math.abs(n) < 0.01 ? 4 : 2);
  const formatted = `${Math.abs(n).toFixed(dp)}%`;
  if (n < 0) return `${MINUS}${formatted}`;
  if (opts?.signed && n > 0) return `+${formatted}`;
  return formatted;
}

/** Format an annualized return. Always append "ann." so it's unambiguous. */
export function fmtAnnualized(pct: number | null | undefined): string {
  if (pct === null || pct === undefined || Number.isNaN(pct)) return '—';
  return `${fmtPct(pct)} ann.`;
}

/** Format days-to-expiry. `days` is calendar days. */
export function fmtDTE(days: number | null | undefined, opts?: { long?: boolean }): string {
  if (days === null || days === undefined || Number.isNaN(days)) return '—';
  if (days <= 0) return 'Expired';
  return opts?.long ? `${days} days` : `${days}d`;
}

/** Format a credit/debit with explicit direction word. Used for "you paid $X" / "you received $X". */
export function fmtCredit(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  if (n === 0) return fmtMoney(0);
  return n > 0
    ? `${fmtMoney(n)} received`
    : `${fmtMoney(Math.abs(n))} paid`;
}

/** Format a per-leg option premium. Always 2dp. */
export function fmtLegPrice(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return `$${n.toFixed(2)}`;
}

/** Format shares/contracts with thousand separators. */
export function fmtQty(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return Math.round(n).toLocaleString('en-US');
}

/** Format date as a short user-readable form: "Apr 22, 2026". */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
  } catch {
    return iso;
  }
}

/** Format a small date + time: "Apr 22, 10:34 AM" */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleString('en-US', {
      month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

/** Format a quote-source badge: "yf" or "ibkr". */
export function fmtSource(source: string | null | undefined): string {
  if (!source) return '';
  const s = source.toLowerCase();
  if (s === 'yfinance' || s === 'yahoo' || s === 'yf') return 'yf';
  if (s === 'ibkr' || s === 'interactive' || s === 'ib') return 'ibkr';
  if (s === 'manual') return 'manual';
  return s;
}

/** Compute a concise P&L summary string from cost + current value. */
export function pnlSummary(entryCost: number, currentValue: number): { amount: number; pct: number; up: boolean } {
  const amount = currentValue - entryCost;
  const pct = entryCost !== 0 ? (amount / Math.abs(entryCost)) * 100 : 0;
  return { amount, pct, up: amount >= 0 };
}
