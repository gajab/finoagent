/**
 * StrategiesBeta — Beta navigation for the Strategies area. Classic shows eleven tall description cards in a
 * sidebar that takes ~a quarter of the page. Beta groups the same eleven strategies into four families and
 * shows two short rows (family tabs + strategy pills), so the strategy itself gets the full width.
 * Every strategy component is the classic one, lazily loaded; only the Income Desk has a Beta layout (so far).
 */
import React, { useEffect, lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router-dom';

const BoxStrategy = lazy(() => import('../components/BoxStrategy').then(m => ({ default: m.BoxStrategy })));
const LongShortStrategy = lazy(() => import('../components/LongShortStrategy').then(m => ({ default: m.LongShortStrategy })));
const StructuredTrades = lazy(() => import('../components/StructuredTrades').then(m => ({ default: m.StructuredTrades })));
const DualDirectionBuffer = lazy(() => import('../components/DualDirectionBuffer').then(m => ({ default: m.DualDirectionBuffer })));
const ConcentrationManager = lazy(() => import('../components/ConcentrationManager').then(m => ({ default: m.ConcentrationManager })));
const PoorMansCovered = lazy(() => import('../components/PoorMansCovered').then(m => ({ default: m.PoorMansCovered })));
const ZebraStrategy = lazy(() => import('../components/ZebraStrategy').then(m => ({ default: m.ZebraStrategy })));
const TaxLossHarvesting = lazy(() => import('../components/TaxLossHarvesting').then(m => ({ default: m.TaxLossHarvesting })));
const CppiStrategy = lazy(() => import('../components/CppiStrategy').then(m => ({ default: m.CppiStrategy })));
const HedgingStrategy = lazy(() => import('../components/HedgingStrategy').then(m => ({ default: m.HedgingStrategy })));
const IncomeDeskBeta = lazy(() => import('./IncomeDeskBeta'));

export type StrategyId = 'derivative_income' | 'poormans' | 'zebra' | 'box' | 'hedging' | 'cppi' | 'dual_direction' | 'structured'
  | 'tax_loss_harvesting' | 'concentration' | 'longshort';

const STRATEGY: Record<StrategyId, { label: string; hint: string }> = {
  derivative_income: { label: 'Income Desk', hint: 'Grades every option-selling trade, then the Quant · Risk · PM desk debates the best.' },
  poormans: { label: "Poor man's", hint: 'Capital-efficient substitute for covered calls/puts via LEAPS.' },
  zebra: { label: 'ZEBRA', hint: 'Zero Extrinsic BackRatio: buy 2 ITM, sell 1 ATM to simulate stock.' },
  box: { label: 'Box spread', hint: 'Synthetic lending/borrowing via a 4-leg options strategy.' },
  hedging: { label: 'Hedging', hint: 'Protect a long holding with quant-built option hedges.' },
  cppi: { label: 'Portfolio insurance', hint: 'Dynamic capital protection using synthetic options replication (CPPI).' },
  dual_direction: { label: 'Dual direction buffer', hint: 'Upside cap rules with downside buffer returns.' },
  structured: { label: 'Structured trades', hint: 'Principal-protected notes using zero-coupon bonds + options.' },
  tax_loss_harvesting: { label: 'Tax-loss harvesting', hint: 'Optimize losses with correlated proxies and synthetic long options.' },
  concentration: { label: 'Concentration', hint: 'Tax-efficiently diversify large, concentrated stock positions.' },
  longshort: { label: 'Long/short equity', hint: 'Hedge market risk by pairing long positions with ETF shorts.' },
};

const FAMILIES: { id: string; label: string; items: StrategyId[] }[] = [
  { id: 'income', label: 'Income', items: ['derivative_income', 'poormans', 'zebra', 'box'] },
  { id: 'protect', label: 'Protect', items: ['hedging', 'cppi', 'dual_direction', 'structured'] },
  { id: 'tax', label: 'Tax & concentration', items: ['tax_loss_harvesting', 'concentration'] },
  { id: 'equity', label: 'Equity', items: ['longshort'] },
];

const familyOf = (s: StrategyId) => FAMILIES.find(f => f.items.includes(s)) ?? FAMILIES[0];

export default function StrategiesBeta() {
  const [params, setParams] = useSearchParams();
  const raw = params.get('strategy') as StrategyId | null;
  const active: StrategyId = raw && raw in STRATEGY ? raw : 'derivative_income';
  const family = familyOf(active);

  const pick = (s: StrategyId) => { const n = new URLSearchParams(params); n.set('strategy', s); setParams(n); };
  useEffect(() => { /* keep the URL canonical so a Beta view is a shareable link */
    if (!raw || !(raw in STRATEGY)) { const n = new URLSearchParams(params); n.set('strategy', active); setParams(n, { replace: true }); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-1 border-b border-white/[0.07] overflow-x-auto overflow-y-hidden" role="tablist" aria-label="Strategy families">
          {FAMILIES.map(f => (
            <button key={f.id} role="tab" aria-selected={family.id === f.id} onClick={() => pick(f.items[0])}
              className={`px-3 py-2 text-sm whitespace-nowrap border-b-2 -mb-px transition-colors ${family.id === f.id ? 'border-secondary text-base-content font-semibold' : 'border-transparent text-base-content/55 hover:text-base-content/80'}`}>{f.label}</button>
          ))}
        </div>
        <div className="flex flex-wrap gap-1.5 mt-3" role="tablist" aria-label={`${family.label} strategies`}>
          {family.items.map(s => (
            <button key={s} role="tab" aria-selected={active === s} onClick={() => pick(s)} title={STRATEGY[s].hint}
              className={`rounded-full border px-3 py-1 text-sm transition-colors ${active === s ? 'border-secondary/60 bg-secondary/15 text-secondary font-semibold' : 'border-white/10 text-base-content/65 hover:border-white/25'}`}>{STRATEGY[s].label}</button>
          ))}
        </div>
        <p className="text-xs text-base-content/50 mt-2">{STRATEGY[active].hint}</p>
      </div>

      <div className="glass-card">
        <div className="card-body">
          <Suspense fallback={<div className="flex items-center justify-center py-24 text-base-content/50"><span className="loading loading-spinner loading-md" /></div>}>
            {active === 'derivative_income' && <IncomeDeskBeta />}
            {active === 'poormans' && <PoorMansCovered />}
            {active === 'zebra' && <ZebraStrategy />}
            {active === 'box' && <BoxStrategy />}
            {active === 'hedging' && <HedgingStrategy />}
            {active === 'cppi' && <CppiStrategy />}
            {active === 'dual_direction' && <DualDirectionBuffer />}
            {active === 'structured' && <StructuredTrades />}
            {active === 'tax_loss_harvesting' && <TaxLossHarvesting />}
            {active === 'concentration' && <ConcentrationManager />}
            {active === 'longshort' && <LongShortStrategy />}
          </Suspense>
        </div>
      </div>
    </div>
  );
}
