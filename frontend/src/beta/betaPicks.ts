/**
 * betaPicks.ts — three honest candidates out of the ranked list, each answering a different question:
 *
 *   best      — the desk's own #1 (highest desk score among non-vetoed trades)
 *   safest    — the highest chance you keep the premium
 *   runnerup  — shown INSTEAD of "safest" when the best trade is already the safest (so the card never
 *               claims a distinction it doesn't have): the next trade on the desk ranking
 *   income    — the largest premium in dollars (and what it costs in capital)
 *
 * Vetoed trades never appear. The picks are always DISTINCT trades.
 */
import type { DeskRankedTrade } from '../types';

export type PickKey = 'best' | 'safest' | 'runnerup' | 'income';
export interface Pick { key: PickKey; label: string; index: number; trade: DeskRankedTrade; why: string }

const vetoed = (t: DeskRankedTrade) => (t.grade_blocking || []).length > 0;

/**
 * What the trade ties up, labelled the way classic does: Reg-T naked margin (BPR) with the full-assignment
 * risk as the subtext, else the defined max loss, else the collateral.
 */
export function capitalOf(t: DeskRankedTrade): { label: string; value: number | null; sub?: string } {
  if (t.capital_basis === 'reg_t_margin' && t.collateral != null) {
    return { label: 'Margin (BPR)', value: Number(t.collateral), sub: t.notional_capital ? `full risk $${Math.round(Number(t.notional_capital)).toLocaleString()}` : 'Reg-T margin' };
  }
  if (t.max_loss != null && Number.isFinite(Number(t.max_loss)) && Number(t.max_loss) !== 0) return { label: 'Max loss', value: Math.abs(Number(t.max_loss)) };
  return { label: 'Collateral', value: t.collateral != null ? Number(t.collateral) : null };
}

export function choosePicks(ranked: DeskRankedTrade[]): Pick[] {
  const idx = ranked.map((t, i) => i).filter(i => !vetoed(ranked[i]));
  if (idx.length === 0) return [];
  const used = new Set<number>();
  const out: Pick[] = [];
  const take = (key: PickKey, label: string, order: (a: number, b: number) => number, why: (t: DeskRankedTrade) => string) => {
    const cand = idx.filter(i => !used.has(i)).sort(order);
    if (cand.length === 0) return;
    used.add(cand[0]);
    out.push({ key, label, index: cand[0], trade: ranked[cand[0]], why: why(ranked[cand[0]]) });
  };
  const n = (v: number | null | undefined) => (v == null || !Number.isFinite(Number(v)) ? -Infinity : Number(v));
  take('best', 'Best overall', (a, b) => a - b,   // ranked list is already best → worst
    t => `Highest desk score (${Math.round(t.desk_score)}).`);
  const safestOrder = (a: number, b: number) =>
    n(ranked[b].prob_keep_pct) - n(ranked[a].prob_keep_pct) || n(ranked[b].desk_score) - n(ranked[a].desk_score) || a - b;
  const safestOverall = [...idx].sort(safestOrder)[0];
  if (safestOverall === out[0]?.index) {
    // The best trade IS the safest. Don't repeat it under a second label — show the runner-up, and say why.
    take('runnerup', 'Runner-up', (a, b) => a - b,
      t => `Next on the desk ranking. The best overall is already the safest (${Math.min(99.9, ranked[safestOverall].prob_keep_pct).toFixed(1)}%).`);
  } else {
    take('safest', 'Safest', safestOrder,
      t => `Highest chance you keep the premium (${Math.min(99.9, t.prob_keep_pct).toFixed(1)}%).`);
  }
  take('income', 'Most income',
    (a, b) => n(ranked[b].premium) - n(ranked[a].premium) || n(ranked[b].desk_score) - n(ranked[a].desk_score) || a - b,
    t => `Largest premium in dollars ($${Math.round(t.premium).toLocaleString()}).`);
  return out;
}
