import React, { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Calculator, Hourglass, PiggyBank, GraduationCap, Landmark } from 'lucide-react';
import { MoneyLastCalculator } from '../components/calculators/MoneyLastCalculator';
import { RetirementLongevity } from '../components/calculators/retirement/RetirementLongevity';
import { CollegeSavings529 } from '../components/calculators/CollegeSavings529';
import { useDocumentMetadata } from '../hooks/useDocumentMetadata';

// ---------------------------------------------------------------------------
// Calculator registry — add a new entry here to expose another calculator.
// Each entry self-describes (label/icon/description) and renders its component.
// ---------------------------------------------------------------------------

type CalcId = 'money_last' | 'retirement_longevity' | 'college_529';
 
const CALCULATORS: {
  id: CalcId;
  label: string;
  icon: React.ReactNode;
  description: string;
  render: () => React.ReactNode;
}[] = [
  {
    id: 'money_last',
    label: 'How Long Will My Money Last',
    icon: <Hourglass className="w-5 h-5" />,
    description: 'Project how many years your savings cover your expenses, adjusted for returns, inflation and tax.',
    render: () => <MoneyLastCalculator />,
  },
  {
    id: 'retirement_longevity',
    label: 'Retirement Portfolio Longevity',
    icon: <PiggyBank className="w-5 h-5" />,
    description: 'Monte Carlo retirement planner over 50 years of market history — success rates, percentile bands, Social Security timing, RMD/tax awareness and tailored recommendations.',
    render: () => <RetirementLongevity />,
  },
  {
    id: 'college_529',
    label: 'College 529 Savings & Selection',
    icon: <GraduationCap className="w-5 h-5" />,
    description: 'Personalized plan selection comparing in-state tax deductions vs. out-of-state fees, matching S&P 500 index preferences, and highlighting 2026 rules (SECURE 2.0, gift limits).',
    render: () => <CollegeSavings529 />,
  },
];

export default function CalculatorsPage() {
  useDocumentMetadata(
    'Financial Planning Calculators — Retirement planning, 529 College Savings, & Roth IRA Conversion | FinoAgent',
    'Evaluate your retirement finances and longevity, compare 529 plans, and analyze Traditional vs. Roth IRA conversions using FinoAgent\'s free financial planning calculators. No login required.',
    'Financial calculator, how long my money will last, retirement planning, retirement finances, when to convert to ROTH IRA, Best 529 planning, Best 529 plan, FinoAgent'
  );

  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get('tab') as CalcId;
  const initialTab = CALCULATORS.some((c) => c.id === tabParam) ? tabParam : 'money_last';
  const [active, setActive] = useState<CalcId>(initialTab);

  // Sync state if query parameter changes externally
  useEffect(() => {
    const currentTab = searchParams.get('tab') as CalcId;
    if (currentTab && CALCULATORS.some((c) => c.id === currentTab) && currentTab !== active) {
      setActive(currentTab);
    }
  }, [searchParams, active]);

  const handleTabChange = (id: CalcId) => {
    setActive(id);
    setSearchParams({ tab: id });
  };

  const current = CALCULATORS.find((c) => c.id === active) ?? CALCULATORS[0];

  return (
    <div className="container-app py-6 sm:py-8 animate-fade-in">
      {/* Page Header */}
      <div className="page-header mb-6">
        <h1 className="page-title tracking-tight flex items-center gap-3">
          <Calculator className="w-7 h-7 text-secondary" />
          Calculators
        </h1>
        <p className="page-subtitle">
          Quick financial-planning tools — no market data or API keys required.
        </p>
      </div>

      <div className="flex flex-col lg:flex-row gap-6">
        {/* Sidebar: calculator picker */}
        <div className="w-full lg:w-1/4">
          <div className="flex flex-col gap-2">
            {CALCULATORS.map((c) => (
              <button
                key={c.id}
                className={`rounded-xl p-4 text-left transition-all border-2 flex items-start gap-3 ${
                  active === c.id
                    ? 'bg-secondary/10 border-secondary shadow-md'
                    : 'bg-base-200 border-base-300 hover:border-secondary/40'
                }`}
                onClick={() => handleTabChange(c.id)}
              >
                <div className={`mt-0.5 ${active === c.id ? 'text-secondary' : 'text-base-content/50'}`}>
                  {c.icon}
                </div>
                <div>
                  <div className={`font-bold ${active === c.id ? 'text-secondary' : 'text-base-content'}`}>
                    {c.label}
                  </div>
                  <div className="text-xs text-base-content/60 mt-1">{c.description}</div>
                </div>
              </button>
            ))}
            <div className="rounded-xl p-4 border-2 border-dashed border-base-300/60 text-center text-xs text-base-content/40">
              More calculators coming soon
            </div>
          </div>
        </div>

        {/* Main: selected calculator */}
        <div className="w-full lg:w-3/4">
          <div className="glass-card h-full">
            <div className="card-body">{current.render()}</div>
          </div>
        </div>
      </div>

      {/* Dynamic SEO Informational Content for Search Engines */}
      <div className="mt-12 pt-8 border-t border-base-300/40 grid grid-cols-1 md:grid-cols-3 gap-6 text-sm text-base-content/60">
        <div>
          <h3 className="font-bold text-base-content mb-2 flex items-center gap-1.5">
            <Hourglass className="w-4 h-4 text-secondary" /> How Long Will My Money Last?
          </h3>
          <p className="leading-relaxed">
            Planning for retirement finances requires estimating how long your savings will last under different safe withdrawal rates. 
            Adjusting for inflation, expected annual investment returns, and tax brackets allows you to calculate if your portfolio is self-sustaining. 
            A robust retirement planning framework helps prevent running out of money by identifying your safe withdrawal rate today.
          </p>
        </div>
        <div>
          <h3 className="font-bold text-base-content mb-2 flex items-center gap-1.5">
            <Landmark className="w-4 h-4 text-secondary" /> When to Convert to a Roth IRA?
          </h3>
          <p className="leading-relaxed">
            A traditional pre-tax to Roth IRA conversion is a powerful tax planning technique. It is typically optimal when your tax bracket today is lower 
            than your expected tax rate in retirement, or if you can pay the conversion tax from outside cash. Tax-free compounding in a Roth IRA protects 
            your wealth from future tax increases and avoids Required Minimum Distributions (RMDs).
          </p>
        </div>
        <div>
          <h3 className="font-bold text-base-content mb-2 flex items-center gap-1.5">
            <GraduationCap className="w-4 h-4 text-secondary" /> Best 529 College Savings Planning
          </h3>
          <p className="leading-relaxed">
            The best 529 plan planning balances state tax deductions (or credits) against management fees and index fund choices. 
            If your state offers tax parity or no tax incentive, choosing a national plan with low average fees and S&P 500 index preferences is optimal. 
            Our 529 plan calculator computes maximum write-offs to secure your student's college savings.
          </p>
        </div>
      </div>
    </div>
  );
}
