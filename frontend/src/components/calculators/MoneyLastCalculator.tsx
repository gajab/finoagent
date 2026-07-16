import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { apiBase } from '../../api';
import {
  Chart as ChartJS, CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend,
  type TooltipItem,
} from 'chart.js';
import { Bar } from 'react-chartjs-2';
import {
  Wallet, Receipt, TrendingUp, Flame, Percent, Hourglass, Calendar,
  Coins, CircleDollarSign, Info, CheckCircle2, AlertTriangle, Save, UserPlus,
} from 'lucide-react';

ChartJS.register(CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend);

// ---------------------------------------------------------------------------
// Model
// ---------------------------------------------------------------------------

type ExpenseMode = 'annual' | 'monthly';

interface SimResult {
  yearsLasted: number;          // whole + fractional years the corpus covers
  depleted: boolean;            // false ⇒ corpus survived the whole horizon
  series: { year: number; balance: number }[];
  finalBalance: number;         // corpus left at the end of the horizon
  totalWithdrawn: number;       // sum of all withdrawals taken
  realReturnPct: number;        // inflation-adjusted net return
}

const MAX_YEARS = 60;

/**
 * Year-by-year nominal simulation. Each year we withdraw that year's living
 * expenses at the *start* of the year (the conservative convention), let the
 * remainder earn `returnPct`, tax only the gain at `taxPct`, then grow next
 * year's expenses by `inflationPct`.
 */
function simulate(
  corpus: number,
  annualExpense: number,
  returnPct: number,
  inflationPct: number,
  taxPct: number,
): SimResult {
  const r = returnPct / 100;
  const inf = inflationPct / 100;
  const tax = Math.min(Math.max(taxPct, 0), 100) / 100;

  const series: { year: number; balance: number }[] = [{ year: 0, balance: corpus }];
  let balance = corpus;
  let expense = annualExpense;
  let totalWithdrawn = 0;
  let yearsLasted = MAX_YEARS;
  let depleted = false;

  for (let y = 1; y <= MAX_YEARS; y++) {
    // Withdraw the year's living expenses at the start of the year.
    if (balance <= expense) {
      // Corpus runs dry partway through this year.
      const fraction = expense > 0 ? balance / expense : 0;
      totalWithdrawn += balance;
      yearsLasted = (y - 1) + fraction;
      depleted = true;
      series.push({ year: y, balance: 0 });
      break;
    }
    totalWithdrawn += expense;
    let bal = balance - expense;
    // The remaining balance earns a return; tax applies to the gain only.
    const gain = bal * r;
    const netGain = gain > 0 ? gain * (1 - tax) : gain;
    bal += netGain;
    balance = bal;
    series.push({ year: y, balance: Math.max(balance, 0) });
    // Next year's expenses rise with inflation.
    expense *= 1 + inf;
  }

  const realReturnPct = ((1 + r * (1 - tax)) / (1 + inf) - 1) * 100;

  return { yearsLasted, depleted, series, finalBalance: balance, totalWithdrawn, realReturnPct };
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

const CURRENCIES = ['$', '₹', '€', '£'] as const;

function fmtMoney(v: number, sym: string): string {
  return `${sym}${Math.round(v).toLocaleString()}`;
}

function fmtCompact(v: number, sym: string): string {
  const abs = Math.abs(v);
  if (abs >= 1e9) return `${sym}${(v / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${sym}${(v / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sym}${(v / 1e3).toFixed(0)}K`;
  return `${sym}${Math.round(v)}`;
}

function yearsLabel(years: number): string {
  const whole = Math.floor(years + 1e-9);
  let months = Math.round((years - whole) * 12);
  let w = whole;
  if (months >= 12) { w += 1; months = 0; }
  if (months === 0) return `${w} yr${w === 1 ? '' : 's'}`;
  return `${w} yr${w === 1 ? '' : 's'} ${months} mo`;
}

// ---------------------------------------------------------------------------
// Small input field
// ---------------------------------------------------------------------------

interface NumberFieldProps {
  label: string;
  value: string;
  onChange: (v: string) => void;
  icon: React.ReactNode;
  prefix?: string;
  suffix?: string;
  placeholder?: string;
  hint?: string;
}

function NumberField({ label, value, onChange, icon, prefix, suffix, placeholder, hint }: NumberFieldProps) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-base-content/70 flex items-center gap-1.5 mb-1">
        {icon}{label}
      </span>
      <div className="relative">
        {prefix && (
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-base-content/50 text-sm pointer-events-none">
            {prefix}
          </span>
        )}
        <input
          type="text"
          inputMode="decimal"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          className={`input input-bordered w-full bg-base-200 ${prefix ? 'pl-7' : ''} ${suffix ? 'pr-10' : ''}`}
        />
        {suffix && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-base-content/50 text-sm pointer-events-none">
            {suffix}
          </span>
        )}
      </div>
      {hint && <span className="text-[10px] text-base-content/40 mt-0.5 block">{hint}</span>}
    </label>
  );
}

// ---------------------------------------------------------------------------
// Calculator
// ---------------------------------------------------------------------------

export function MoneyLastCalculator() {
  const { isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [countdown, setCountdown] = useState(3);

  const handleSaveClick = () => {
    if (isAuthenticated) {
      alert('Planning scenario saved successfully to your FinoAgent profile!');
    } else {
      setShowSaveModal(true);
      setCountdown(3);
      let count = 3;
      const interval = setInterval(() => {
        count -= 1;
        setCountdown(count);
        if (count <= 0) {
          clearInterval(interval);
          navigate('/');
        }
      }, 1000);
    }
  };

  const [currency, setCurrency] = useState<string>('$');
  const [corpusStr, setCorpusStr] = useState('1000000');
  const [expenseStr, setExpenseStr] = useState('50000');
  const [mode, setMode] = useState<ExpenseMode>('annual');
  const [returnStr, setReturnStr] = useState('7');
  const [inflationStr, setInflationStr] = useState('3');
  const [taxStr, setTaxStr] = useState('');

  const corpus = Math.max(parseFloat(corpusStr) || 0, 0);
  const expenseInput = Math.max(parseFloat(expenseStr) || 0, 0);
  const annualExpense = mode === 'monthly' ? expenseInput * 12 : expenseInput;
  const returnPct = parseFloat(returnStr) || 0;
  const inflationPct = parseFloat(inflationStr) || 0;
  const taxPct = Math.min(Math.max(parseFloat(taxStr) || 0, 0), 100);

  const valid = corpus > 0 && annualExpense > 0;

  const sim = useMemo(
    () => simulate(corpus, annualExpense, returnPct, inflationPct, taxPct),
    [corpus, annualExpense, returnPct, inflationPct, taxPct],
  );

  const thisYear = new Date().getFullYear();

  // Verdict / tone for the headline.
  const verdict = !sim.depleted
    ? { tone: 'success' as const, text: 'Self-sustaining at these assumptions — the corpus is projected to keep growing faster than you withdraw.' }
    : sim.yearsLasted >= 40
    ? { tone: 'success' as const, text: 'Comfortable — your money is projected to last well beyond a typical retirement horizon.' }
    : sim.yearsLasted >= 25
    ? { tone: 'warning' as const, text: 'Reasonable, but tight over a long horizon. A higher return, lower expenses, or a larger corpus would add a buffer.' }
    : { tone: 'error' as const, text: 'Short runway — at this withdrawal rate the corpus depletes relatively quickly. Revisit expenses, returns or the starting amount.' };

  const toneClasses: Record<string, { bg: string; border: string; text: string; ring: string }> = {
    success: { bg: 'bg-emerald-500/10', border: 'border-emerald-500/30', text: 'text-emerald-400', ring: 'text-emerald-400' },
    warning: { bg: 'bg-amber-500/10', border: 'border-amber-500/30', text: 'text-amber-400', ring: 'text-amber-400' },
    error: { bg: 'bg-rose-500/10', border: 'border-rose-500/30', text: 'text-rose-400', ring: 'text-rose-400' },
  };
  const tc = toneClasses[verdict.tone];

  // Bar chart: corpus balance per year, coloured by how healthy it is.
  const chartData = {
    labels: sim.series.map((s) => String(s.year)),
    datasets: [
      {
        label: 'Corpus',
        data: sim.series.map((s) => s.balance),
        backgroundColor: sim.series.map((s) => {
          const ratio = corpus > 0 ? s.balance / corpus : 0;
          if (ratio >= 0.6) return '#34d39990';
          if (ratio >= 0.3) return '#fbbf2490';
          return '#fb718590';
        }),
        borderRadius: 3,
        maxBarThickness: 26,
      },
    ],
  };

  const chartOpts = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          title: (items: TooltipItem<'bar'>[]) => `Year ${items[0]?.label ?? ''} · ${thisYear + Number(items[0]?.label ?? 0)}`,
          label: (ctx: TooltipItem<'bar'>) => `Corpus: ${fmtMoney(ctx.parsed.y ?? 0, currency)}`,
        },
      },
    },
    scales: {
      x: { grid: { display: false }, ticks: { font: { size: 9 }, autoSkip: true, maxTicksLimit: 12, maxRotation: 0 } },
      y: {
        beginAtZero: true,
        grid: { color: 'rgba(255,255,255,0.06)' },
        ticks: { font: { size: 9 }, callback: (v: number | string) => fmtCompact(Number(v), currency) },
      },
    },
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-xl font-bold flex items-center gap-2">
          <Hourglass className="w-5 h-5 text-secondary" />
          How Long Will My Money Last?
        </h2>
        <p className="text-sm text-base-content/60 mt-1">
          Estimate how many years your savings can cover your living expenses, accounting for
          investment returns, inflation and tax.
        </p>
      </div>

      {/* Inputs */}
      <div className="bg-base-200/40 rounded-xl p-4 sm:p-5 border border-base-300/50">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-bold text-base-content/80">Your numbers</h3>
          {/* Currency selector */}
          <div className="join">
            {CURRENCIES.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => setCurrency(c)}
                className={`btn btn-xs join-item ${currency === c ? 'btn-secondary' : 'btn-ghost'}`}
              >
                {c}
              </button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <NumberField
            label="Current savings"
            icon={<Wallet className="w-3.5 h-3.5" />}
            value={corpusStr}
            onChange={setCorpusStr}
            prefix={currency}
            placeholder="1,000,000"
            hint="Total investable corpus today"
          />

          {/* Expenses with annual / monthly toggle */}
          <div className="block">
            <span className="text-xs font-medium text-base-content/70 flex items-center gap-1.5 mb-1">
              <Receipt className="w-3.5 h-3.5" />
              {mode === 'monthly' ? 'Monthly expenses' : 'Annual expenses'}
            </span>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-base-content/50 text-sm pointer-events-none">
                  {currency}
                </span>
                <input
                  type="text"
                  inputMode="decimal"
                  value={expenseStr}
                  placeholder={mode === 'monthly' ? '4,000' : '50,000'}
                  onChange={(e) => setExpenseStr(e.target.value)}
                  className="input input-bordered w-full bg-base-200 pl-7"
                />
              </div>
              <div className="join">
                <button
                  type="button"
                  onClick={() => setMode('annual')}
                  className={`btn btn-sm join-item ${mode === 'annual' ? 'btn-secondary' : 'btn-ghost'}`}
                >
                  Yr
                </button>
                <button
                  type="button"
                  onClick={() => setMode('monthly')}
                  className={`btn btn-sm join-item ${mode === 'monthly' ? 'btn-secondary' : 'btn-ghost'}`}
                >
                  Mo
                </button>
              </div>
            </div>
            <span className="text-[10px] text-base-content/40 mt-0.5 block">
              {mode === 'monthly' && annualExpense > 0
                ? `= ${fmtMoney(annualExpense, currency)} per year`
                : 'Spending in today’s money — grows with inflation'}
            </span>
          </div>

          <NumberField
            label="Expected annual return"
            icon={<TrendingUp className="w-3.5 h-3.5" />}
            value={returnStr}
            onChange={setReturnStr}
            suffix="%"
            placeholder="7"
            hint="Pre-tax return on your corpus"
          />
          <NumberField
            label="Expected annual inflation"
            icon={<Flame className="w-3.5 h-3.5" />}
            value={inflationStr}
            onChange={setInflationStr}
            suffix="%"
            placeholder="3"
            hint="How fast your expenses rise"
          />
          <NumberField
            label="Tax slab (optional)"
            icon={<Percent className="w-3.5 h-3.5" />}
            value={taxStr}
            onChange={setTaxStr}
            suffix="%"
            placeholder="0"
            hint="Tax applied to annual gains"
          />
        </div>

        <div className="mt-4 pt-3 border-t border-base-300/40 flex justify-end">
          <button
            onClick={handleSaveClick}
            className="btn btn-secondary rounded-xl btn-sm font-semibold flex items-center gap-1.5"
          >
            <Save className="w-3.5 h-3.5" /> Save Scenario
          </button>
        </div>
      </div>

      {!valid ? (
        <div className="flex items-center gap-2 text-sm text-base-content/50 bg-base-200/40 rounded-xl p-4 border border-base-300/50">
          <Info className="w-4 h-4 flex-shrink-0" />
          Enter your current savings and expenses to see how long your money will last.
        </div>
      ) : (
        <>
          {/* Headline + verdict */}
          <div className={`rounded-2xl p-5 border ${tc.bg} ${tc.border}`}>
            <div className="flex flex-col sm:flex-row sm:items-center gap-4">
              <div className="flex items-center gap-3">
                {verdict.tone === 'error' ? (
                  <AlertTriangle className={`w-8 h-8 ${tc.ring}`} />
                ) : (
                  <CheckCircle2 className={`w-8 h-8 ${tc.ring}`} />
                )}
                <div>
                  <div className="text-xs uppercase tracking-wide text-base-content/50 font-semibold">
                    Your money lasts
                  </div>
                  <div className={`text-3xl font-extrabold leading-tight ${tc.text}`}>
                    {sim.depleted ? yearsLabel(sim.yearsLasted) : `${MAX_YEARS}+ yrs`}
                  </div>
                </div>
              </div>
              <p className="text-sm text-base-content/70 sm:border-l sm:border-base-300/50 sm:pl-4 flex-1">
                {verdict.text}
              </p>
            </div>
          </div>

          {/* Summary stat cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="bg-base-200 rounded-xl p-3">
              <div className="text-xs text-base-content/60 flex items-center gap-1.5">
                <Calendar className="w-3.5 h-3.5" />
                {sim.depleted ? 'Runs out around' : 'Horizon checked'}
              </div>
              <div className="text-lg font-bold mt-1">
                {sim.depleted ? thisYear + Math.round(sim.yearsLasted) : `${thisYear + MAX_YEARS}`}
              </div>
            </div>
            <div className="bg-base-200 rounded-xl p-3">
              <div className="text-xs text-base-content/60 flex items-center gap-1.5">
                <CircleDollarSign className="w-3.5 h-3.5" />
                {sim.depleted ? 'Corpus at the end' : `Corpus after ${MAX_YEARS} yrs`}
              </div>
              <div className="text-lg font-bold mt-1">
                {fmtMoney(sim.depleted ? 0 : sim.finalBalance, currency)}
              </div>
            </div>
            <div className="bg-base-200 rounded-xl p-3">
              <div className="text-xs text-base-content/60 flex items-center gap-1.5">
                <Coins className="w-3.5 h-3.5" />
                Total withdrawn
              </div>
              <div className="text-lg font-bold mt-1">{fmtMoney(sim.totalWithdrawn, currency)}</div>
            </div>
            <div className="bg-base-200 rounded-xl p-3 group relative">
              <div className="text-xs text-base-content/60 flex items-center gap-1.5">
                <TrendingUp className="w-3.5 h-3.5" />
                Real return
                <span className="opacity-0 group-hover:opacity-100 absolute -top-9 left-0 bg-neutral text-neutral-content text-[10px] px-2 py-1 rounded z-50 whitespace-nowrap transition-opacity">
                  Net-of-tax return minus inflation
                </span>
              </div>
              <div className={`text-lg font-bold mt-1 ${sim.realReturnPct >= 0 ? 'text-success' : 'text-error'}`}>
                {sim.realReturnPct > 0 ? '+' : ''}{sim.realReturnPct.toFixed(1)}%
              </div>
            </div>
          </div>

          {/* Bar chart */}
          <div className="bg-base-200/30 rounded-xl p-4 border border-base-300/40">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-bold">Projected corpus by year</h3>
              <div className="flex items-center gap-3 text-[10px] text-base-content/50">
                <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm" style={{ background: '#34d399' }} />Healthy</span>
                <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm" style={{ background: '#fbbf24' }} />Drawing down</span>
                <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm" style={{ background: '#fb7185' }} />Low</span>
              </div>
            </div>
            <div className="h-64 sm:h-72">
              <Bar data={chartData} options={chartOpts} />
            </div>
          </div>

          {/* Assumptions note */}
          <div className="flex items-start gap-2 text-[11px] text-base-content/40">
            <Info className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
            <p>
              Simplified model: expenses are withdrawn at the start of each year and grow with
              inflation; the remaining corpus earns a constant {returnPct || 0}% return with tax
              applied to the annual gain. Real markets vary year to year — treat this as a planning
              estimate, not a guarantee.
            </p>
          </div>
        </>
      )}

      {/* Redirect Save Modal */}
      {showSaveModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[9999] flex items-center justify-center p-4">
          <div className="glass-card max-w-sm w-full p-6 text-center animate-fade-in-up">
            <div className="w-12 h-12 bg-secondary/15 rounded-full flex items-center justify-center mx-auto mb-4">
              <UserPlus className="w-6 h-6 text-secondary" />
            </div>
            <h3 className="text-lg font-bold text-white mb-2">Sign In Required</h3>
            <p className="text-sm text-gray-300 mb-6">
              You must be signed in to save planning scenarios to your FinoAgent profile. Redirecting you to the sign-in page...
            </p>
            <div className="flex flex-col gap-2.5">
              <a
                href={`${apiBase}/api/auth/google`}
                className="btn btn-secondary rounded-xl font-medium w-full flex items-center justify-center gap-2"
              >
                Sign In Now
              </a>
              <div className="text-[10px] text-gray-500">
                Redirecting in {countdown}s...
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
