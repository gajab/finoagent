/**
 * IncomeDeskBeta — the Beta Income Desk: five modes as one tab row (the long intro card is a single line),
 * with the new Scan workbench as the default. Evaluate / WatchList / Screener / Portfolio reuse the classic
 * components unchanged (hosted without their own intro + mode toggle). Picking a ticker from WatchList or the
 * Screener hands off to the Beta Scan and runs it. Visited modes stay mounted so a long scan survives tab switches.
 */
import React, { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Search, Briefcase, ClipboardCheck, List, ScanSearch } from 'lucide-react';
import { DerivativeIncome, type DerivativeIncomeMode } from '../components/DerivativeIncome';
import IncomeScanBeta, { type ScanSeed } from './IncomeScanBeta';

type Mode = 'scan' | DerivativeIncomeMode;
const MODES: { id: Mode; label: string; icon: React.ReactNode; hint: string }[] = [
  { id: 'scan', label: 'Scan', icon: <Search className="w-4 h-4" />, hint: 'Find and rank income trades for one ticker' },
  { id: 'evaluate', label: 'Evaluate', icon: <ClipboardCheck className="w-4 h-4" />, hint: 'Score your own multi-leg trade on the full desk' },
  { id: 'watchlist', label: 'Watchlist', icon: <List className="w-4 h-4" />, hint: 'Your saved tickers' },
  { id: 'screener', label: 'Screener', icon: <ScanSearch className="w-4 h-4" />, hint: 'Screen a universe for the best setups' },
  { id: 'portfolio', label: 'My portfolio', icon: <Briefcase className="w-4 h-4" />, hint: 'Income ideas on the stocks you hold' },
];

export default function IncomeDeskBeta() {
  // Deep link: /strategies?ui=beta&strategy=derivative_income&mode=evaluate (e.g. Defend's "Evaluate" hand-off) opens that mode.
  const [params] = useSearchParams();
  const urlMode = params.get('mode');
  const initial: Mode = (['evaluate', 'watchlist', 'screener', 'portfolio'] as string[]).includes(urlMode || '') ? (urlMode as Mode) : 'scan';
  const [mode, setMode] = useState<Mode>(initial);
  const [visited, setVisited] = useState<Set<Mode>>(new Set<Mode>(['scan', initial]));
  const [seed, setSeed] = useState<ScanSeed | null>(null);
  const go = (m: Mode) => { setMode(m); setVisited(v => new Set(v).add(m)); };
  const openTicker = (ticker: string, o?: { expiry?: string | null; minProb?: number; minIncome?: number }) => {
    setSeed({ ticker, expiry: o?.expiry ?? null, minProb: o?.minProb, minIncome: o?.minIncome });
    go('scan');
  };

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-1 border-b border-white/[0.07] overflow-x-auto overflow-y-hidden" role="tablist" aria-label="Income Desk modes">
          {MODES.map(m => (
            <button key={m.id} role="tab" aria-selected={mode === m.id} title={m.hint} onClick={() => go(m.id)}
              className={`flex items-center gap-1.5 px-3 py-2 text-sm whitespace-nowrap border-b-2 -mb-px transition-colors ${mode === m.id ? 'border-primary text-base-content font-semibold' : 'border-transparent text-base-content/55 hover:text-base-content/80'}`}>
              {m.icon}{m.label}
            </button>
          ))}
        </div>
        <p className="text-xs text-base-content/50 mt-2">Every option-selling trade graded A–F on volatility edge, dealer gamma and technicals. Probabilities come from the market-implied distribution; only executable quotes are shown.</p>
      </div>

      <div hidden={mode !== 'scan'}><IncomeScanBeta seed={seed} onSeedConsumed={() => setSeed(null)} /></div>
      {(['evaluate', 'watchlist', 'screener', 'portfolio'] as DerivativeIncomeMode[]).map(m =>
        visited.has(m) ? <div key={m} hidden={mode !== m}><DerivativeIncome initialMode={m} hideChrome onOpenTicker={openTicker} /></div> : null)}
    </div>
  );
}
