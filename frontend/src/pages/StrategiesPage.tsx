import React, { useState, lazy, Suspense } from 'react';
import {
  Layers, Box, ArrowLeftRight, Building, Shield, Target, ArrowDownRight, Crown, Lock, Briefcase, ClipboardList, ShieldCheck, Coins, Sparkles
} from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';

// Each strategy is a heavy component (charts, quant panels); load only the selected one so the
// Strategies chunk isn't one giant bundle. Named exports → unwrap to default for React.lazy.
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
const DerivativeIncome = lazy(() => import('../components/DerivativeIncome').then(m => ({ default: m.DerivativeIncome })));
const DerivativeIncomeV2 = lazy(() => import('../components/DerivativeIncomeV2').then(m => ({ default: m.DerivativeIncomeV2 })));

type Strategy = 'box' | 'longshort' | 'structured' | 'dual_direction' | 'concentration' | 'poormans' | 'zebra' | 'tax_loss_harvesting' | 'cppi' | 'hedging' | 'derivative_income' | 'derivative_income_v2';

const STRATEGIES: { id: Strategy; label: string; icon: React.ReactNode; description: string }[] = [

  {
    id: 'derivative_income',
    label: 'Derivative Income',
    icon: <Coins className="w-5 h-5" />,
    description: 'Covered calls, cash-secured puts, collars & credit spreads with ≥85% probability of not being exercised — ranked vs SOFR.',
  },
  {
    id: 'derivative_income_v2',
    label: 'Derivative Income v2',
    icon: <Sparkles className="w-5 h-5" />,
    description: 'New master–detail workspace for the income scanner — same engine, calmer layout.',
  },
  {
    id: 'box',
    label: 'Box Spread',
    icon: <Box className="w-5 h-5" />,
    description: 'Synthetic lending/borrowing via 4-leg options strategy.',
  },
  {
    id: 'hedging',
    label: 'Hedging',
    icon: <Shield className="w-5 h-5" />,
    description: 'Protect a long holding with quant-built option hedges and live scenario analysis.',
  },
  {
    id: 'longshort',
    label: 'Long/Short Equity',
    icon: <ArrowLeftRight className="w-5 h-5" />,
    description: 'Hedge market risk by pairing long positions with ETF shorts.',
  },
  {
    id: 'tax_loss_harvesting',
    label: 'Tax Loss Harvesting',
    icon: <ArrowDownRight className="w-5 h-5" />,
    description: 'Optimize losses with correlated proxies and synthetic long options.',
  },
  {
    id: 'structured',
    label: 'Structured Trades',
    icon: <Building className="w-5 h-5" />,
    description: 'Principal-protected notes using zero-coupon bonds + options.',
  },
  {
    id: 'dual_direction',
    label: 'Dual Direction Buffer',
    icon: <Shield className="w-5 h-5" />,
    description: 'Provide upside cap rules with downside buffer returns.',
  },
  {
    id: 'concentration',
    label: 'Concentration Management',
    icon: <Target className="w-5 h-5" />,
    description: 'Tax-efficiently diversify large, concentrated stock positions.',
  },
  {
    id: 'poormans',
    label: "Poor Man's Strategy",
    icon: <Target className="w-5 h-5" />,
    description: 'Capital-efficient substitute for Covered Calls/Puts via LEAPS.',
  },
  {
    id: 'zebra',
    label: 'ZEBRA Strategy',
    icon: <Target className="w-5 h-5" />,
    description: 'Zero Extrinsic BackRatio. Buy 2 ITM, Sell 1 ATM to simulate stock.',
  },
  {
    id: 'cppi',
    label: 'Portfolio Insurance (CPPI)',
    icon: <ShieldCheck className="w-5 h-5" />,
    description: 'Dynamic capital protection using synthetic options replication.',
  }
];

export default function StrategiesPage() {
  const { isPremium } = useAuth();
  const [activeStrategy, setActiveStrategy] = useState<Strategy>('derivative_income');

  if (!isPremium) {
    return (
      <div className="container-app py-6 sm:py-8 animate-fade-in">
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <div className="relative mb-6">
            <div className="bg-base-200 rounded-full p-8">
              <Layers className="w-16 h-16 text-base-content/20" />
            </div>
            <div className="absolute -top-1 -right-1 bg-yellow-400 rounded-full p-2 shadow-lg">
              <Lock className="w-4 h-4 text-gray-900" />
            </div>
          </div>
          <h1 className="text-3xl font-bold tracking-tight mb-3 flex items-center gap-2 justify-center">
            <Crown className="w-7 h-7 text-yellow-400" />
            Premium Feature
          </h1>
          <p className="text-base-content/60 max-w-md text-base mb-2">
            Investment Strategies is available exclusively for Premium users.
          </p>
          <p className="text-base-content/40 max-w-sm text-sm">
            Please contact the admin to upgrade your account to Premium and unlock Box Spread, Long/Short, Tax Loss Harvesting, and more.
          </p>
          <div className="mt-8 px-6 py-4 rounded-2xl bg-yellow-400/10 border border-yellow-400/20 text-sm text-yellow-400 max-w-xs">
            <Crown className="w-4 h-4 inline mr-2" />
            Premium unlocks: AI Agents · Strategies · Advanced Tools
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="container-app py-6 sm:py-8 animate-fade-in">
      {/* Page Header */}
      <div className="page-header mb-6">
        <h1 className="page-title tracking-tight flex items-center gap-3">
          <Layers className="w-7 h-7 text-secondary" />
          Advanced Strategies
        </h1>
      </div>

      <div className="flex flex-col lg:flex-row gap-6">
        {/* Sidebar Nav */}
        <div className="w-full lg:w-1/4">
          <div className="flex flex-col gap-2">
            {STRATEGIES.map((s) => (
              <button
                key={s.id}
                className={`rounded-xl p-4 text-left transition-all border-2 flex items-start gap-3 ${activeStrategy === s.id
                  ? 'bg-secondary/10 border-secondary shadow-md'
                  : 'bg-base-200 border-base-300 hover:border-secondary/40'
                  }`}
                onClick={() => setActiveStrategy(s.id)}
              >
                <div className={`mt-1 ${activeStrategy === s.id ? 'text-secondary' : 'text-base-content/50'}`}>
                  {s.icon}
                </div>
                <div>
                  <div className={`font-bold ${activeStrategy === s.id ? 'text-secondary' : 'text-base-content'}`}>
                    {s.label}
                  </div>
                  <div className="text-xs text-base-content/60 mt-1">{s.description}</div>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Main Strategy Content */}
        <div className="w-full lg:w-3/4">
          <div className="glass-card h-full">
            <div className="card-body">
              <Suspense fallback={<div className="flex items-center justify-center py-24 text-base-content/50"><span className="loading loading-spinner loading-md" /></div>}>
                {activeStrategy === 'box' && <BoxStrategy />}
                {activeStrategy === 'derivative_income' && <DerivativeIncome />}
                {activeStrategy === 'derivative_income_v2' && <DerivativeIncomeV2 />}
                {activeStrategy === 'hedging' && <HedgingStrategy />}
                {activeStrategy === 'longshort' && <LongShortStrategy />}
                {activeStrategy === 'tax_loss_harvesting' && <TaxLossHarvesting />}
                {activeStrategy === 'structured' && <StructuredTrades />}
                {activeStrategy === 'dual_direction' && <DualDirectionBuffer />}
                {activeStrategy === 'concentration' && <ConcentrationManager />}
                {activeStrategy === 'poormans' && <PoorMansCovered />}
                {activeStrategy === 'zebra' && <ZebraStrategy />}
                {activeStrategy === 'cppi' && <CppiStrategy />}
              </Suspense>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
