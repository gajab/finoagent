/**
 * betaBookFixes.ts — turns the Manage Book guardrail scorecard into FIXES GROUPED BY TRADE.
 *
 * The scorecard is organised by guardrail: each breached check lists the trades that would fix it. A trade that
 * breaches several guardrails therefore repeats once per check (live example: AMD under tail symmetry, correlated
 * cluster AND sector concentration, each time with the identical "buy a 770C / close the 700C"). Here each trade
 * appears ONCE, with the list of guardrails that fix addresses; two different fixes for one trade stay separate
 * variants. Pure — no React, no I/O — and nothing is invented: every number is copied from the scorecard.
 */
import type { BookTailRiskResult, RemediationLeg } from '../api';

type ScoreCard = NonNullable<BookTailRiskResult['risk_scorecard']>;
type Check = ScoreCard['checks'][number];
type Target = NonNullable<NonNullable<Check['fix']>['targets']>[number];

export interface CheckRef { key: string; label: string; status: 'pass' | 'warn' | 'breach' }

export interface FixVariant {
  key: string;
  rec: RemediationLeg;                 // the recommended action (cap or close) with its real legs and numbers
  alt: RemediationLeg | null;          // the alternative, if any
  tailBefore: number;                  // position P&L at the stress move BEFORE the fix
  checks: CheckRef[];                  // the guardrails this exact fix addresses
}

export interface TradeFix {
  tradeKey: string;
  tradeId: number | null;
  ticker: string;
  structure: string | null;
  premiumLeft: number;
  short: Target['short'];
  variants: FixVariant[];              // best first (most guardrails addressed, then largest risk removed)
  checks: CheckRef[];                  // union of guardrails across variants
}

export interface PortfolioFix { check: CheckRef; headline: string; effect?: string; cost?: string; alt?: string }

export interface GroupedFixes {
  trades: TradeFix[];
  portfolio: PortfolioFix[];           // flagged guardrails whose fix is book-level (no per-trade targets)
  flagged: number;                     // breached + watch guardrails
  cardsToday: number;                  // how many per-trade cards the guardrail-first layout shows
}

const ref = (c: Check): CheckRef => ({ key: c.key, label: c.label, status: c.status });
const r0 = (n: number) => Math.round(Number(n) || 0);
const addUnique = (list: CheckRef[], c: CheckRef) => { if (!list.some(x => x.key === c.key)) list.push(c); };

export function groupFixesByTrade(sc: ScoreCard | null | undefined): GroupedFixes {
  const out: GroupedFixes = { trades: [], portfolio: [], flagged: 0, cardsToday: 0 };
  if (!sc) return out;
  const flagged = sc.checks.filter(c => c.status !== 'pass');
  out.flagged = flagged.length;
  const byTrade = new Map<string, TradeFix>();

  for (const c of flagged) {
    const targets = c.fix?.targets || [];
    if (c.fix && targets.length === 0) {
      out.portfolio.push({ check: ref(c), headline: c.fix.headline, effect: c.fix.effect, cost: c.fix.cost, alt: c.fix.alt });
    }
    for (const t of targets) {
      out.cardsToday++;
      const tradeKey = t.trade_id != null ? `id:${t.trade_id}` : `tk:${t.ticker}|${t.structure ?? ''}`;
      let tf = byTrade.get(tradeKey);
      if (!tf) {
        tf = { tradeKey, tradeId: t.trade_id ?? null, ticker: t.ticker, structure: t.structure ?? null, premiumLeft: t.premium_left, short: t.short ?? null, variants: [], checks: [] };
        byTrade.set(tradeKey, tf);
      }
      addUnique(tf.checks, ref(c));
      // Identical fixes (same action, legs and numbers) collapse into one variant; a different fix stays separate.
      const vkey = `${t.recommended.action}|${t.recommended.legs}|${r0(t.tail_before)}|${r0(t.recommended.tail_after)}|${r0(t.recommended.cost)}`;
      let v = tf.variants.find(x => x.key === vkey);
      if (!v) {
        v = { key: vkey, rec: t.recommended, alt: t.alt ?? null, tailBefore: t.tail_before, checks: [] };
        tf.variants.push(v);
      }
      addUnique(v.checks, ref(c));
    }
  }

  const removed = (v: FixVariant) => Math.abs(v.tailBefore) - Math.abs(v.rec.tail_after);
  for (const tf of byTrade.values()) tf.variants.sort((a, b) => b.checks.length - a.checks.length || removed(b) - removed(a));
  const worst = (tf: TradeFix) => Math.max(...tf.variants.map(v => Math.abs(v.tailBefore)));
  out.trades = [...byTrade.values()].sort((a, b) => b.checks.length - a.checks.length || worst(b) - worst(a));
  return out;
}

/** % of the position's stress loss a fix removes (0–100), or null when there was no loss to remove. */
export function riskRemovedPct(v: FixVariant): number | null {
  const before = Math.abs(v.tailBefore);
  if (!(before > 0)) return null;
  return Math.max(0, Math.min(100, ((before - Math.abs(v.rec.tail_after)) / before) * 100));
}
