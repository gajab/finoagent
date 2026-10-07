/**
 * betaRows.ts — pure helpers for the My Trades Beta table: the trade-TYPE filter (covered call, naked call,
 * cash-secured put, straddle …) and the per-row DEPLOYED figure (Reg-T margin for options, stock notional for
 * covered calls). No React, no I/O.
 */
import type { SavedStrategyItem, LivePnlResponse } from '../api';
import { tradeStructure, deployedCapitalWithBasis, type DeployedBasis } from '../components/trades/MyTradesV2';

export interface TypeCount { key: string; label: string; n: number }

/** The trade types present in a list, most common first (ties alphabetical) — the filter's options. */
export function structureCounts(trades: SavedStrategyItem[]): TypeCount[] {
  const m = new Map<string, TypeCount>();
  for (const t of trades) {
    const s = tradeStructure(t);
    const cur = m.get(s.key);
    if (cur) cur.n++; else m.set(s.key, { key: s.key, label: s.label, n: 1 });
  }
  return [...m.values()].sort((a, b) => b.n - a.n || a.label.localeCompare(b.label));
}

export function filterByStructure(trades: SavedStrategyItem[], key: string): SavedStrategyItem[] {
  return key === 'all' ? trades : trades.filter(t => tradeStructure(t).key === key);
}

const hasShortOption = (t: SavedStrategyItem) =>
  ((t.legs_data || []) as any[]).some(l => /call|put/i.test(l.type || '') && /sell|short/i.test(l.action || ''));

const BASIS: Record<DeployedBasis, { tag: string; title: string }> = {
  margin: { tag: 'Reg-T', title: 'Reg-T margin — the buying power your broker holds for this position (portfolio margin if that is what Settings uses). From the last refresh.' },
  stock: { tag: 'stock', title: 'Stock held behind the position (shares × average cost) — the stock is the collateral, so no extra margin.' },
  futures: { tag: 'futures', title: 'Futures margin requirement.' },
  max_loss: { tag: 'max loss', title: 'Defined-risk collateral: the position\'s maximum loss (the spread width).' },
  collateral: { tag: 'collateral', title: 'Collateral recorded on the trade.' },
  debit: { tag: 'debit', title: 'The debit paid — a long option has no margin; its cost is the money at work.' },
};

export interface RowDeployed { value: number | null; tag: string; title: string }

/** What to show in a row's "Deployed" cell. Never invents a number: a position with a SHORT option whose margin has not
 *  loaded yet falls back to the premium in the shared chain (which understates a naked short), so it shows "—" instead. */
export function rowDeployed(t: SavedStrategyItem, p?: LivePnlResponse | null): RowDeployed {
  const { value, basis } = deployedCapitalWithBasis(t, p);
  if (basis === 'debit' && hasShortOption(t)) {
    return { value: null, tag: 'no margin yet', title: 'Margin has not loaded for this short option yet — press Refresh all. (Showing the premium would understate a naked short.)' };
  }
  if (!(value > 0)) return { value: null, tag: '—', title: 'Nothing deployed.' };
  return { value, tag: BASIS[basis].tag, title: `${BASIS[basis].title}` };
}

/** $12.4k · $1.2M — compact money for a dense table cell. */
export function compactMoney(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e6) return `$${(v / 1e6).toFixed(2).replace(/\.?0+$/, '')}M`;
  if (a >= 1e5) return `$${Math.round(v / 1e3)}k`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(1).replace(/\.0$/, '')}k`;
  return `$${Math.round(v)}`;
}
