import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../../contexts/AuthContext';
import {
  Chart as ChartJS, CategoryScale, LinearScale, BarElement, PointElement,
  LineElement, BarController, LineController, Title, Tooltip, Legend, Filler,
  type TooltipItem,
} from 'chart.js';
import { Line, Chart } from 'react-chartjs-2';
import {
  PiggyBank, Wallet, Receipt, TrendingUp, Percent, Calendar, Info,
  AlertTriangle, CheckCircle2, Landmark, Plus, Trash2, Play, Loader2,
  Coins, ShieldCheck, HeartPulse, FlaskConical, Table, RefreshCw,
  MapPin, Gift, Users, Save, ArrowLeftRight, TrendingDown, ArrowRight, Sparkles, UserPlus, Rocket,
} from 'lucide-react';
import {
  fetchSavedStrategies, saveStrategy, updateSavedStrategy, deleteSavedStrategy,
  SavedStrategyItem,
} from '../../../api';
import {
  EXPENSE_CATEGORIES, CategoryId, HISTORICAL, HIST_START, HIST_END,
  HIST_MIN_WINDOW, historicalWindow, topYears, worstReturnWindow,
  STOCK_INDICES, StockIndexId, StockComposition, compositeMean, compositeStockForYear,
  stockStats, MIX_PRESETS, k401ElectiveLimit,
  SS_CLAIM_AGES, SS_CLAIM_FACTORS, rmdStartAge,
} from './data';
import {
  zipToState, STATE_NAMES, STATE_TAX, LTC_CARE_LABELS, LtcCareType, ltcCostEstimate,
  STATE_COL, STATE_HOUSING, colRatio, housingRatio, ltcCostRatio,
} from './locations';
import {
  SimInputs, MCResult, SweepResults, IncomeSource, YearRecord, SpendingPhases,
  SequenceReturn, FireScenario, runMonteCarlo, runSweeps, outcomeBetter, SMILE_DEFAULT,
} from './engine';

ChartJS.register(
  CategoryScale, LinearScale, BarElement, PointElement, LineElement,
  BarController, LineController, Title, Tooltip, Legend, Filler,
);

// ---------------------------------------------------------------------------
// Formatting / small helpers
// ---------------------------------------------------------------------------

const num = (s: string, fallback = 0): number => {
  const v = parseFloat(s);
  return Number.isFinite(v) ? v : fallback;
};

const fmtMoney = (v: number) => `$${Math.round(v).toLocaleString()}`;

function fmtCompact(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${Math.round(v)}`;
}

const fmtPct = (v: number, d = 1) => `${(v * 100).toFixed(d)}%`;

const CHART_FONT = { size: 9 };
const GRID = { color: 'rgba(255,255,255,0.06)' };

const CAT_COLORS: Record<CategoryId, string> = {
  housing: '#3abff8', utilities: '#a78bfa', auto: '#22d3ee', medical: '#fb7185', travel: '#fbbf24',
  grocery: '#34d399', restaurant: '#fb923c', misc: '#94a3b8',
};

interface FieldProps {
  label: string;
  value: string;
  onChange: (v: string) => void;
  prefix?: string;
  suffix?: string;
  placeholder?: string;
  hint?: string;
  small?: boolean;
}

function Field({ label, value, onChange, prefix, suffix, placeholder, hint, small }: FieldProps) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-base-content/70 mb-1 block">{label}</span>
      <div className="relative">
        {prefix && (
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-base-content/50 text-sm pointer-events-none">{prefix}</span>
        )}
        <input
          type="text"
          inputMode="decimal"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          className={`input input-bordered w-full bg-base-200 ${small ? 'input-sm' : ''} ${prefix ? 'pl-7' : ''} ${suffix ? 'pr-9' : ''}`}
        />
        {suffix && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-base-content/50 text-sm pointer-events-none">{suffix}</span>
        )}
      </div>
      {hint && <span className="text-[10px] text-base-content/40 mt-0.5 block">{hint}</span>}
    </label>
  );
}

function Section({ icon, title, subtitle, children, right, step, toggle }: {
  icon: React.ReactNode; title: string; subtitle?: string;
  children: React.ReactNode; right?: React.ReactNode; step?: number;
  // A standardized enable/include toggle, rendered left-aligned under the
  // header so every section's toggle looks and sits the same.
  toggle?: { label: React.ReactNode; checked: boolean; onChange: (v: boolean) => void };
}) {
  return (
    <div className="bg-base-200/40 rounded-xl p-4 sm:p-5 border border-base-300/50">
      <div className="flex items-start justify-between mb-3 gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-bold text-base-content/80 flex items-center gap-2">
            {step != null && (
              <span className="w-4.5 h-4.5 min-w-[18px] min-h-[18px] rounded-full bg-secondary/15 text-secondary text-[10px] font-bold inline-flex items-center justify-center">
                {step}
              </span>
            )}
            {icon}{title}
          </h3>
          {subtitle && <p className="text-[11px] text-base-content/40 mt-0.5">{subtitle}</p>}
        </div>
        {right && <div className="flex-shrink-0">{right}</div>}
      </div>
      {toggle && (
        <label className="inline-flex items-center gap-2.5 cursor-pointer mb-3">
          <input type="checkbox" className="toggle toggle-secondary toggle-sm" checked={toggle.checked}
            onChange={(e) => toggle.onChange(e.target.checked)} />
          <span className="text-xs font-medium text-base-content/70">{toggle.label}</span>
        </label>
      )}
      {children}
    </div>
  );
}

// ── Dual-thumb range slider (both ends draggable) ──
function DualRange({ min, max, lo, hi, minGap, onChange }: {
  min: number; max: number; lo: number; hi: number; minGap: number;
  onChange: (lo: number, hi: number) => void;
}) {
  const pct = (v: number) => ((v - min) / (max - min)) * 100;
  return (
    <div className="relative h-6 select-none">
      <style>{`
        .dual-range { -webkit-appearance: none; appearance: none; background: transparent; pointer-events: none; }
        .dual-range::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; pointer-events: auto;
          height: 16px; width: 16px; border-radius: 9999px; background: hsl(var(--s)); cursor: grab; border: 2px solid rgba(255,255,255,.25); }
        .dual-range::-moz-range-thumb { pointer-events: auto; height: 16px; width: 16px; border-radius: 9999px;
          background: hsl(var(--s)); cursor: grab; border: 2px solid rgba(255,255,255,.25); }
        .dual-range::-webkit-slider-runnable-track { background: transparent; }
        .dual-range::-moz-range-track { background: transparent; }
      `}</style>
      <div className="absolute top-1/2 -translate-y-1/2 h-1.5 w-full rounded-full bg-base-300" />
      <div className="absolute top-1/2 -translate-y-1/2 h-1.5 rounded-full bg-secondary/60"
        style={{ left: `${pct(lo)}%`, width: `${Math.max(0, pct(hi) - pct(lo))}%` }} />
      <input type="range" min={min} max={max} step={1} value={lo} aria-label="Window start year"
        onChange={(e) => onChange(Math.min(Number(e.target.value), hi - minGap), hi)}
        className="dual-range absolute inset-0 w-full z-10" />
      <input type="range" min={min} max={max} step={1} value={hi} aria-label="Window end year"
        onChange={(e) => onChange(lo, Math.max(Number(e.target.value), lo + minGap))}
        className="dual-range absolute inset-0 w-full z-20" />
    </div>
  );
}

// ── Historical series chart (used in the form AND repeated in results).
//    `series` hides datasets the simulation didn't sample (manual overrides). ──
function HistoryChart({ start, end, excluded, height = 'h-44', series, comp }: {
  start: number; end: number; excluded: number[]; height?: string;
  series?: { stocks?: boolean; bonds?: boolean; cpi?: boolean };
  comp?: StockComposition | null;
}) {
  const show = { stocks: true, bonds: true, cpi: true, ...series };
  const data = useMemo(() => {
    const all = HISTORICAL.filter(h => h[0] >= start && h[0] <= end);
    const exSet = new Set(excluded);
    const bg = (base: string) => all.map(h => (exSet.has(h[0]) ? '#6b728055' : base));
    type MixedDataset =
      | { type: 'bar'; label: string; data: number[]; backgroundColor: string[]; borderRadius: number }
      | { type: 'line'; label: string; data: number[]; borderColor: string; borderWidth: number; pointRadius: number; tension: number; fill: boolean };
    const datasets: MixedDataset[] = [];
    // With a custom index mix, the Stocks bar shows the blended return per year.
    const stockData = all.map(h => (comp ? compositeStockForYear(h[0], comp) : h[1]));
    if (show.stocks) datasets.push({ type: 'bar', label: comp ? 'Stocks (your mix)' : 'Stocks', data: stockData, backgroundColor: bg('#3abff8aa'), borderRadius: 2 });
    if (show.bonds) datasets.push({ type: 'bar', label: 'Bonds', data: all.map(h => h[2]), backgroundColor: bg('#fbbf24aa'), borderRadius: 2 });
    if (show.cpi) datasets.push({ type: 'line', label: 'Inflation (CPI)', data: all.map(h => h[3]), borderColor: '#fb7185', borderWidth: 2, pointRadius: 0, tension: 0.2, fill: false });
    return { labels: all.map(h => String(h[0])), datasets };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [start, end, excluded, show.stocks, show.bonds, show.cpi, comp]);
  return (
    <div className={height}>
      <Chart type="bar" data={data} options={{
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index' as const, intersect: false },
        plugins: {
          legend: { display: true, position: 'bottom' as const, labels: { boxWidth: 10, font: CHART_FONT } },
          tooltip: { callbacks: { label: (ctx: TooltipItem<'bar' | 'line'>) => `${ctx.dataset.label}: ${(ctx.parsed.y ?? 0).toFixed(1)}%` } },
        },
        scales: {
          x: { grid: { display: false }, ticks: { font: CHART_FONT, autoSkip: true, maxTicksLimit: 17, maxRotation: 0 } },
          y: { grid: GRID, ticks: { font: CHART_FONT, callback: (v: number | string) => `${v}%` } },
        },
      }} />
    </div>
  );
}

/** Consistent group heading for the Strategy Lab so every section shares one
 *  font/size/color treatment. */
function LabGroup({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex items-baseline gap-2 flex-wrap border-b border-secondary/20 pb-1">
        <h4 className="text-[11px] font-bold uppercase tracking-wider text-secondary">{title}</h4>
        {hint && <span className="text-[10px] text-base-content/40">{hint}</span>}
      </div>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const DEFAULT_CATS: Record<CategoryId, string> = {
  housing: '24000', utilities: '6000', auto: '9000', medical: '12000',
  travel: '8000', grocery: '9600', restaurant: '6000', misc: '8400',
};
const DEFAULT_SPREADS: Record<CategoryId, string> = Object.fromEntries(
  EXPENSE_CATEGORIES.map(c => [c.id, String(c.spread)]),
) as Record<CategoryId, string>;

// ── Ledger views: the 27-column audit table split into scannable slices ──
type LedgerView = 'overview' | 'sources' | 'taxes' | 'balances' | 'all';
const LEDGER_VIEWS: { id: LedgerView; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'sources', label: 'Income & sales' },
  { id: 'taxes', label: 'Taxes' },
  { id: 'balances', label: 'Balances' },
  { id: 'all', label: 'Everything' },
];
interface LedgerCol {
  l: string;
  v: Exclude<LedgerView, 'all'>[];
  cell: (r: YearRecord) => React.ReactNode;
  cls?: (r: YearRecord) => string;
  hideInAll?: boolean;   // summary columns that duplicate detail columns
}

let rowIdSeq = 1;

export function RetirementLongevity() {
  const { isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [countdown, setCountdown] = useState(3);

  // ── Basics ──
  const [currentAge, setCurrentAge] = useState('55');
  const [retireAge, setRetireAge] = useState('65');
  const [planAge, setPlanAge] = useState('95');
  const [zip, setZip] = useState('');
  const zipRef = useRef<HTMLInputElement>(null);
  const focusZip = () => {
    zipRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => zipRef.current?.focus(), 300);
  };

  // ── Expenses ──
  const [expenseMode, setExpenseMode] = useState<'simple' | 'category'>('simple');
  const [annualExpStr, setAnnualExpStr] = useState('80000');
  const [catStr, setCatStr] = useState<Record<CategoryId, string>>(DEFAULT_CATS);
  const [spreadStr, setSpreadStr] = useState<Record<CategoryId, string>>(DEFAULT_SPREADS);
  const [medicareFracStr, setMedicareFracStr] = useState('0.5');

  // ── Spending phases ──
  const [phasePreset, setPhasePreset] = useState<'flat' | 'smile' | 'custom'>('flat');
  const [gogoEndStr, setGogoEndStr] = useState('75');
  const [slowgoEndStr, setSlowgoEndStr] = useState('85');
  const [gogoMultStr, setGogoMultStr] = useState('100');
  const [slowgoMultStr, setSlowgoMultStr] = useState('85');
  const [nogoMultStr, setNogoMultStr] = useState('75');

  // ── One-time expenses ──
  const [lumps, setLumps] = useState<Array<{ id: number; name: string; amount: string; age: string }>>([]);

  // ── Allocation per account (the single-pot mix is the balance-weighted average) ──
  const [allocTaxable, setAllocTaxable] = useState(60);
  const [allocTraditional, setAllocTraditional] = useState(60);
  const [allocRoth, setAllocRoth] = useState(60);
  const [divYieldStr, setDivYieldStr] = useState('1');
  const [couponStr, setCouponStr] = useState('4');
  const [stockGainStr, setStockGainStr] = useState('30');
  const [bondGainStr, setBondGainStr] = useState('0');
  const [showTaxHelp, setShowTaxHelp] = useState(false);
  // Optional equity-index composition (default S&P 500 only).
  const [stockCompMode, setStockCompMode] = useState<'sp500' | 'custom'>('sp500');
  const [stockWeights, setStockWeights] = useState<Record<StockIndexId, string>>({
    sp500: '60', nasdaq100: '15', russell2000: '10', intl: '10', em: '5',
  });

  // ── Income sources ──
  const [incomes, setIncomes] = useState<Array<{
    id: number; name: string; annual: string; startAge: string; endAge: string; growthPct: string;
  }>>([]);

  // ── Social Security ──
  const [ssFraStr, setSsFraStr] = useState('36000');
  const [ssClaimAge, setSsClaimAge] = useState(67);

  // ── Spouse ──
  const [spouseEnabled, setSpouseEnabled] = useState(false);
  const [spouseAgeStr, setSpouseAgeStr] = useState('53');
  const [spousePlanAgeStr, setSpousePlanAgeStr] = useState('95');
  const [spouseFraStr, setSpouseFraStr] = useState('24000');
  const [spouseClaimAge, setSpouseClaimAge] = useState(67);
  const [survivorPctStr, setSurvivorPctStr] = useState('80');

  // ── Saved plans ──
  const [plans, setPlans] = useState<SavedStrategyItem[]>([]);
  const [planName, setPlanName] = useState('');
  const [loadedPlanId, setLoadedPlanId] = useState<number | null>(null);
  const [planBusy, setPlanBusy] = useState(false);
  const [planMsg, setPlanMsg] = useState('');

  // ── Long-term care ──
  const [ltcEnabled, setLtcEnabled] = useState(false);
  const [ltcType, setLtcType] = useState<LtcCareType>('assistedLiving');
  const [ltcCostStr, setLtcCostStr] = useState('');
  const [ltcCostTouched, setLtcCostTouched] = useState(false);
  const [ltcPersons, setLtcPersons] = useState<1 | 2>(2);
  const [ltcProbStr, setLtcProbStr] = useState('50');
  const [ltcOnsetStr, setLtcOnsetStr] = useState('83');
  const [ltcDurStr, setLtcDurStr] = useState('3');

  // ── Assumptions ──
  const [returnsMode, setReturnsMode] = useState<'historical' | 'manual'>('historical');
  const [inflationMode, setInflationMode] = useState<'historical' | 'manual'>('historical');
  const [windowStart, setWindowStart] = useState(HIST_START);
  const [windowEnd, setWindowEnd] = useState(HIST_END);
  const [excludedYears, setExcludedYears] = useState<number[]>([]);
  const [manualStockStr, setManualStockStr] = useState('8');
  const [manualBondStr, setManualBondStr] = useState('4.5');
  const [manualInflStr, setManualInflStr] = useState('3');
  const [numSims, setNumSims] = useState(1000);

  // ── Taxes ──
  const [taxEnabled, setTaxEnabled] = useState(false);
  const [filing, setFiling] = useState<'single' | 'mfj'>('mfj');
  const [taxableStr, setTaxableStr] = useState('800000');
  const [tradStr, setTradStr] = useState('1000000');
  const [rothStr, setRothStr] = useState('200000');

  // ── Strategies ──
  const [guardrails, setGuardrails] = useState(false);
  const [withdrawalStrategy, setWithdrawalStrategy] = useState<'sequential' | 'guarded' | 'guarded_rebalance' | 'holistic'>('sequential');
  const [downThrStr, setDownThrStr] = useState('5');
  const [upThrStr, setUpThrStr] = useState('5');
  const [holisticPostStr, setHolisticPostStr] = useState('40');
  const [glideAgeStr, setGlideAgeStr] = useState('65');
  const [cashBufferStr, setCashBufferStr] = useState('0');
  // Future savings (accumulation years). Collapsed/off by default.
  const [savingsEnabled, setSavingsEnabled] = useState(false);
  const [savUntilStr, setSavUntilStr] = useState('');   // blank ⇒ retire age
  const [savTaxableStr, setSavTaxableStr] = useState('0');
  const [savRothStr, setSavRothStr] = useState('7000');
  const [sav401kMode, setSav401kMode] = useState<'amount' | 'max'>('amount');
  const [sav401kStr, setSav401kStr] = useState('15000');
  const [sav401kEmployerMode, setSav401kEmployerMode] = useState<'amount' | 'match'>('amount');
  const [sav401kEmployerStr, setSav401kEmployerStr] = useState('5000');
  const [sav401kMatchStr, setSav401kMatchStr] = useState('50');
  const [rothMode, setRothMode] = useState<'off' | 'custom' | 'auto'>('off');
  const [rothCeiling, setRothCeiling] = useState<12 | 22 | 24>(22);
  const [rothStartStr, setRothStartStr] = useState('');
  const [rothEndStr, setRothEndStr] = useState('');

  // ── Run / display state ──
  const [computing, setComputing] = useState(false);
  const [result, setResult] = useState<MCResult | null>(null);
  const [sweeps, setSweeps] = useState<SweepResults | null>(null);
  const [sweeping, setSweeping] = useState(false);

  // ── What-If: relocate to another state ──
  const [whatIfState, setWhatIfState] = useState<string>('');
  const [whatIfAdjStr, setWhatIfAdjStr] = useState<string>('');
  const [whatIfAdjTouched, setWhatIfAdjTouched] = useState(false);
  const [whatIfResult, setWhatIfResult] = useState<MCResult | null>(null);
  const [whatIfBusy, setWhatIfBusy] = useState(false);

  // ── What-If: sequence-of-returns risk (bad start to retirement) ──
  const [seqMode, setSeqMode] = useState<'historical' | 'custom'>('historical');
  const [seqYearsStr, setSeqYearsStr] = useState('5');
  const [seqCustom, setSeqCustom] = useState<{ stock: string; bond: string }[]>(
    Array.from({ length: 5 }, () => ({ stock: '-12', bond: '2' })),
  );
  const [seqResult, setSeqResult] = useState<MCResult | null>(null);
  const [seqMitigations, setSeqMitigations] = useState<
    { id: string; label: string; detail: string; success: number; endReal: number }[] | null
  >(null);
  const [seqBusy, setSeqBusy] = useState(false);
  // A sequence-of-returns permanently baked into the plan (first N years fixed,
  // rest Monte Carlo). Feeds SimInputs.sequenceReturns.
  const [planSequence, setPlanSequence] = useState<{ seq: SequenceReturn[]; label: string } | null>(null);
  const [ranInputs, setRanInputs] = useState<SimInputs | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [displayReal, setDisplayReal] = useState(true);
  const [showLedger, setShowLedger] = useState(false);
  const [ledgerView, setLedgerView] = useState<LedgerView>('overview');

  // ── Derived ──
  const stateCode = useMemo(() => zipToState(zip), [zip]);
  const stateTaxInfo = stateCode ? STATE_TAX[stateCode] : undefined;
  const catTotal = EXPENSE_CATEGORIES.reduce((s, c) => s + num(catStr[c.id]), 0);
  // The three account balances ARE the corpus — total is derived, never mismatched.
  const accountSum = num(taxableStr) + num(tradStr) + num(rothStr);
  const corpus = accountSum;
  const weightedStockPct = accountSum > 0
    ? Math.round((num(taxableStr) * allocTaxable + num(tradStr) * allocTraditional + num(rothStr) * allocRoth) / accountSum)
    : 60;
  const rmdAge = rmdStartAge(new Date().getFullYear() - (num(currentAge) || 55));
  const rothDefStart = Math.max(num(retireAge), num(currentAge));
  const rothDefEnd = Math.min(ssClaimAge, rmdAge) - 1;

  // Prefill LTC cost from location whenever it hasn't been hand-edited.
  useEffect(() => {
    if (!ltcCostTouched) setLtcCostStr(String(ltcCostEstimate(stateCode, ltcType)));
  }, [stateCode, ltcType, ltcEnabled, ltcCostTouched]);

  // Load saved plans once.
  useEffect(() => {
    fetchSavedStrategies('retirement_plan').then(setPlans).catch(() => { /* not logged in / offline — save bar still works for this session */ });
  }, []);

  // The composition currently configured in the form (null = plain S&P 500).
  const uiStockComposition: StockComposition | null = stockCompMode === 'custom'
    ? (Object.fromEntries(STOCK_INDICES.map(x => [x.id, Math.max(num(stockWeights[x.id], 0), 0)])) as StockComposition)
    : null;
  const uiCompKey = uiStockComposition ? JSON.stringify(uiStockComposition) : '';

  const windowInfo = useMemo(() => {
    const used = historicalWindow(windowStart, windowEnd, excludedYears);
    const all = HISTORICAL.filter(h => h[0] >= windowStart && h[0] <= windowEnd);
    const mean = (i: 1 | 2 | 3) => (used.length ? used.reduce((s, h) => s + h[i], 0) / used.length : 0);
    // Stock mean & extreme years follow the custom mix when one is set.
    const stock = uiStockComposition ? compositeMean(windowStart, windowEnd, excludedYears, uiStockComposition) : mean(1);
    const bond = mean(2), cpi = mean(3);
    // Chips: for stocks AND bonds, the 7 best + 3 worst years in the window
    // (the latest year rides along in the stock row).
    const stockChipMap = new Map<number, (typeof HISTORICAL)[number]>();
    for (const h of topYears(windowStart, windowEnd, 1, 7, 3, uiStockComposition)) stockChipMap.set(h[0], h);
    const latest = all[all.length - 1];
    if (latest) stockChipMap.set(latest[0], latest);
    return {
      yearsUsed: used.length, stock, bond, cpi,
      blended: (weightedStockPct / 100) * stock + (1 - weightedStockPct / 100) * bond,
      stockChips: [...stockChipMap.values()].sort((a, b) => a[0] - b[0]),
      bondChips: topYears(windowStart, windowEnd, 2, 7, 3),
      latestYear: latest?.[0],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [windowStart, windowEnd, excludedYears, weightedStockPct, uiCompKey]);

  // Per-index and blended historical stock returns over the current window,
  // for the optional "Stock composition" panel.
  const compStats = useMemo(() => {
    const single = (id: StockIndexId): StockComposition =>
      Object.fromEntries(STOCK_INDICES.map(x => [x.id, x.id === id ? 100 : 0])) as StockComposition;
    const perIndex = Object.fromEntries(
      STOCK_INDICES.map(x => [x.id, compositeMean(windowStart, windowEnd, excludedYears, single(x.id))]),
    ) as Record<StockIndexId, number>;
    const weights = Object.fromEntries(
      STOCK_INDICES.map(x => [x.id, Math.max(num(stockWeights[x.id], 0), 0)]),
    ) as StockComposition;
    const total = Object.values(weights).reduce((a, b) => a + b, 0);
    const blended = total > 0 ? compositeMean(windowStart, windowEnd, excludedYears, weights) : perIndex.sp500;
    return { perIndex, blended, total };
  }, [windowStart, windowEnd, excludedYears, stockWeights]);

  // Stock mix Lab: descriptive risk/return for the presets + the user's current
  // mix over the active window, ranked by risk-adjusted (Sharpe) return.
  const [showMixLab, setShowMixLab] = useState(false);
  const mixLab = useMemo(() => {
    const rf = inflationMode === 'manual' ? num(manualInflStr) : windowInfo.cpi; // real-ish risk-free ≈ inflation
    const rows = MIX_PRESETS.map(p => ({
      id: p.id, label: p.label, note: p.note, weights: p.weights,
      isCurrent: false,
      stats: stockStats(windowStart, windowEnd, excludedYears, p.weights, rf),
    }));
    const curWeights = Object.fromEntries(
      STOCK_INDICES.map(x => [x.id, Math.max(num(stockWeights[x.id], 0), 0)]),
    ) as StockComposition;
    const curTotal = Object.values(curWeights).reduce((a, b) => a + b, 0);
    // Only surface the user's row if it isn't identical to a preset already listed.
    const curNorm = STOCK_INDICES.map(x => (curTotal > 0 ? (curWeights[x.id] / curTotal) : 0));
    const dupOfPreset = MIX_PRESETS.some(p => {
      const t = Object.values(p.weights).reduce((a, b) => a + b, 0);
      return STOCK_INDICES.every((x, i) => Math.abs((t > 0 ? p.weights[x.id] / t : 0) - curNorm[i]) < 0.005);
    });
    if (curTotal > 0 && !dupOfPreset) {
      rows.push({
        id: 'current', label: 'Your mix', note: 'as entered above', weights: curWeights,
        isCurrent: true, stats: stockStats(windowStart, windowEnd, excludedYears, curWeights, rf),
      });
    }
    rows.sort((a, b) => b.stats.sharpe - a.stats.sharpe);
    const bestSharpe = rows.length ? rows[0].id : null;
    const bestCagr = rows.reduce((m, r) => (r.stats.cagr > m.stats.cagr ? r : m), rows[0]);
    const lowestVol = rows.reduce((m, r) => (r.stats.vol < m.stats.vol ? r : m), rows[0]);
    // Concentration: largest single-sleeve share of the current mix.
    const maxShare = curTotal > 0 ? Math.max(...curNorm) : 1;
    return { rows, bestSharpe, bestCagrId: bestCagr?.id, lowestVolId: lowestVol?.id, maxShare, hasCurrent: curTotal > 0 };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [windowStart, windowEnd, excludedYears, stockWeights, inflationMode, manualInflStr, windowInfo.cpi]);

  const applyMix = (w: StockComposition) => {
    const total = Object.values(w).reduce((a, b) => a + b, 0) || 1;
    setStockWeights(Object.fromEntries(
      STOCK_INDICES.map(x => [x.id, String(Math.round((w[x.id] / total) * 100))]),
    ) as Record<StockIndexId, string>);
    setStockCompMode('custom');
  };

  const baseInflPct = inflationMode === 'manual' ? num(manualInflStr) : windowInfo.cpi;

  const phases = useMemo((): SpendingPhases | null => {
    if (phasePreset === 'flat') return null;
    if (phasePreset === 'smile') return SMILE_DEFAULT;
    return {
      gogoEndAge: num(gogoEndStr, 75), slowgoEndAge: num(slowgoEndStr, 85),
      gogoMult: num(gogoMultStr, 100) / 100, slowgoMult: num(slowgoMultStr, 85) / 100,
      nogoMult: num(nogoMultStr, 75) / 100,
    };
  }, [phasePreset, gogoEndStr, slowgoEndStr, gogoMultStr, slowgoMultStr, nogoMultStr]);

  const phaseMultUI = (age: number): number => {
    if (!phases) return 1;
    if (age < phases.gogoEndAge) return phases.gogoMult;
    if (age < phases.slowgoEndAge) return phases.slowgoMult;
    return phases.nogoMult;
  };

  // Deterministic expense-growth projection chart (avg inflation, phases, Medicare).
  const expenseProjection = useMemo(() => {
    const from = num(retireAge, 65), to = num(planAge, 95), cur = num(currentAge, 55);
    if (to <= from) return null;
    const ages: number[] = [];
    for (let a = from; a <= to; a++) ages.push(a);
    const medFrac = Math.min(Math.max(num(medicareFracStr, 0.5), 0), 3);
    const mkSeries = (base: number, spread: number, isMedical: boolean) =>
      ages.map(a => {
        let v = base * Math.pow(1 + (baseInflPct + spread) / 100, a - cur);
        if (isMedical) { if (a >= 65) v *= medFrac; } else v *= phaseMultUI(a);
        return v;
      });
    const datasets = expenseMode === 'category'
      ? EXPENSE_CATEGORIES.map(c => ({
          label: c.label,
          data: mkSeries(num(catStr[c.id]), num(spreadStr[c.id], c.spread), c.id === 'medical'),
          borderColor: CAT_COLORS[c.id], backgroundColor: `${CAT_COLORS[c.id]}55`,
          borderWidth: 1, pointRadius: 0, fill: true, stack: 'exp', tension: 0.2,
        }))
      : [{
          label: 'Annual expenses',
          data: mkSeries(num(annualExpStr), 0, false),
          borderColor: '#3abff8', backgroundColor: '#3abff833',
          borderWidth: 2, pointRadius: 0, fill: true, tension: 0.2,
        }];
    return { labels: ages.map(String), datasets };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expenseMode, catStr, spreadStr, annualExpStr, baseInflPct, retireAge, planAge, currentAge, phases, medicareFracStr]);

  // ── Build inputs ──
  const buildInputs = (): SimInputs => ({
    currentAge: num(currentAge), retireAge: num(retireAge), planAge: num(planAge),
    corpus,
    expenseMode,
    annualExpenses: num(annualExpStr),
    categoryExpenses: Object.fromEntries(
      EXPENSE_CATEGORIES.map(c => [c.id, num(catStr[c.id])]),
    ) as Record<CategoryId, number>,
    categorySpreads: Object.fromEntries(
      EXPENSE_CATEGORIES.map(c => [c.id, num(spreadStr[c.id], c.spread)]),
    ) as Record<CategoryId, number>,
    phases,
    medicareFraction: Math.min(Math.max(num(medicareFracStr, 0.5), 0), 3),
    lumpSums: lumps
      .map(l => ({ id: String(l.id), name: l.name || 'One-time expense', amount: num(l.amount), age: num(l.age) }))
      .filter(l => l.amount > 0),
    ltc: {
      enabled: ltcEnabled,
      annualCost: num(ltcCostStr),
      persons: ltcPersons,
      probabilityPct: Math.min(Math.max(num(ltcProbStr, 50), 0), 100),
      onsetAge: num(ltcOnsetStr, 83),
      durationYears: num(ltcDurStr, 3),
    },
    stockPct: weightedStockPct,
    accountStockPct: { taxable: allocTaxable, traditional: allocTraditional, roth: allocRoth },
    stockDividendPct: num(divYieldStr, 1),
    bondInterestPct: num(couponStr, 4),
    taxableStockGainPct: Math.min(Math.max(num(stockGainStr, 30), 0), 95),
    taxableBondGainPct: Math.min(Math.max(num(bondGainStr, 0), 0), 95),
    stockComposition: stockCompMode === 'custom'
      ? (Object.fromEntries(STOCK_INDICES.map(x => [x.id, Math.max(num(stockWeights[x.id], 0), 0)])) as StockComposition)
      : null,
    incomes: incomes.map((s): IncomeSource => ({
      id: String(s.id), name: s.name || 'Income', annual: num(s.annual),
      startAge: num(s.startAge), endAge: num(s.endAge), growthPct: num(s.growthPct),
    })).filter(s => s.annual > 0),
    ssFraAnnual: num(ssFraStr), ssClaimAge,
    spouse: {
      enabled: spouseEnabled,
      age: num(spouseAgeStr, 53),
      planAge: num(spousePlanAgeStr, 95),
      ssFraAnnual: num(spouseFraStr),
      ssClaimAge: spouseClaimAge,
      survivorExpensePct: num(survivorPctStr, 80),
    },
    returnsMode, inflationMode,
    windowStart, windowEnd, excludedYears: [...excludedYears].sort(),
    manualStockPct: num(manualStockStr), manualBondPct: num(manualBondStr),
    manualInflationPct: num(manualInflStr),
    numSims,
    taxEnabled, filing,
    stateCode: stateCode ?? '',
    taxableBal: num(taxableStr), traditionalBal: num(tradStr), rothBal: num(rothStr),
    guardrails,
    roth: {
      mode: taxEnabled ? rothMode : 'off',
      ceiling: rothCeiling,
      startAge: num(rothStartStr) || rothDefStart,
      endAge: num(rothEndStr) || rothDefEnd,
    },
    withdrawalStrategy,
    downThresholdPct: Math.min(Math.max(num(downThrStr, 5), 0), 50),
    upThresholdPct: Math.min(Math.max(num(upThrStr, 5), 0), 50),
    holisticPostStockPct: Math.min(Math.max(num(holisticPostStr, 40), 0), 100),
    glideAge: num(glideAgeStr, 65),
    cashBufferYears: Math.min(Math.max(num(cashBufferStr, 0), 0), 10),
    sequenceReturns: planSequence?.seq ?? null,
    savings: (savingsEnabled && num(currentAge, 55) < num(retireAge, 65)) ? {
      untilAge: Math.min(num(savUntilStr, num(retireAge, 65)) || num(retireAge, 65), num(retireAge, 65)),
      taxable: Math.max(num(savTaxableStr, 0), 0),
      roth: Math.max(num(savRothStr, 0), 0),
      k401Employee: sav401kMode === 'max' ? 0 : Math.max(num(sav401kStr, 0), 0),
      k401Max: sav401kMode === 'max',
      k401EmployerMode: sav401kEmployerMode,
      k401Employer: Math.max(num(sav401kEmployerStr, 0), 0),
      k401EmployerMatchPct: Math.max(num(sav401kMatchStr, 0), 0),
    } : null,
  });

  const validate = (inp: SimInputs): string[] => {
    const errs: string[] = [];
    if (inp.currentAge < 18 || inp.currentAge > 100) errs.push('Current age should be between 18 and 100.');
    if (inp.retireAge < inp.currentAge) errs.push('Retirement age must be ≥ current age (already retired? set both equal).');
    if (inp.planAge <= inp.retireAge) errs.push('"Plan to age" must be greater than the retirement age.');
    if (inp.planAge > 110) errs.push('"Plan to age" caps at 110.');
    if (inp.corpus <= 0) errs.push('Enter your account balances in the Corpus section.');
    if (zip && !stateCode) errs.push(`ZIP "${zip}" not recognized — leave it empty or use a valid 5-digit US ZIP.`);
    const exp = inp.expenseMode === 'category' ? catTotal : inp.annualExpenses;
    if (exp <= 0) errs.push('Enter your first-year retirement expenses.');
    if (windowInfo.yearsUsed < HIST_MIN_WINDOW) {
      errs.push(`The historical window needs at least ${HIST_MIN_WINDOW} usable years (currently ${windowInfo.yearsUsed} after exclusions).`);
    }
    if (inp.phases && !(inp.phases.gogoEndAge < inp.phases.slowgoEndAge)) {
      errs.push('Spending phases: go-go must end before the slow-go phase ends.');
    }
    if (inp.ltc.enabled && inp.ltc.annualCost <= 0) errs.push('Long-term care: enter an annual cost (or use the location estimate).');
    if (inp.roth.mode === 'custom' && inp.roth.endAge < inp.roth.startAge) errs.push('Roth conversions: end age must be ≥ start age.');
    if (inp.spouse.enabled) {
      if (inp.spouse.age < 18 || inp.spouse.age > 100) errs.push('Spouse: current age should be between 18 and 100.');
      if (inp.spouse.planAge <= inp.spouse.age) errs.push('Spouse: "plan to age" must be greater than their current age.');
      if (inp.spouse.planAge > 110) errs.push('Spouse: "plan to age" caps at 110.');
      if (inp.spouse.survivorExpensePct < 30 || inp.spouse.survivorExpensePct > 100) {
        errs.push('Survivor expenses should be between 30% and 100% of the couple budget.');
      }
    }
    for (const l of inp.lumpSums) {
      if (l.age < inp.currentAge || l.age > inp.planAge) errs.push(`One-time expense "${l.name}": age must be between ${inp.currentAge} and ${inp.planAge}.`);
    }
    for (const s of inp.incomes) {
      if (s.startAge < inp.retireAge) errs.push(`Income "${s.name}": start age must be ≥ retirement age (${inp.retireAge}).`);
      if (s.endAge < s.startAge) errs.push(`Income "${s.name}": end age must be ≥ start age.`);
    }
    return errs;
  };

  const dirty = ranInputs !== null && JSON.stringify(buildInputs()) !== JSON.stringify(ranInputs);

  const handleRun = () => {
    const inp = buildInputs();
    const errs = validate(inp);
    setErrors(errs);
    if (errs.length) return;
    setComputing(true);
    setSweeps(null);
    setTimeout(() => {
      try {
        setResult(runMonteCarlo(inp));
        setRanInputs(inp);
      } catch (e) {
        setErrors([`Simulation failed: ${e instanceof Error ? e.message : String(e)}`]);
      } finally {
        setComputing(false);
      }
    }, 30);
  };

  const handleSweeps = () => {
    if (!ranInputs) return;
    setSweeping(true);
    setTimeout(() => {
      try { setSweeps(runSweeps(ranInputs)); }
      catch (e) { setErrors([`Strategy Lab failed: ${e instanceof Error ? e.message : String(e)}`]); }
      finally { setSweeping(false); }
    }, 30);
  };

  const toggleYear = (y: number) =>
    setExcludedYears(prev => prev.includes(y) ? prev.filter(v => v !== y) : [...prev, y]);

  // ── What-If relocate: cost-of-living-based expense adjustment ──
  // Category-aware recommendation: housing scales by the housing index, the
  // rest by the overall index. Applied as one editable % the user controls.
  const whatIfHome = ranInputs?.stateCode || null;
  const recomputeWhatIfAdj = (target: string): number => {
    if (!ranInputs || !target) return 0;
    const hr = housingRatio(whatIfHome, target);
    const orr = colRatio(whatIfHome, target);
    if (ranInputs.expenseMode === 'category') {
      let oldT = 0, newT = 0;
      for (const c of EXPENSE_CATEGORIES) {
        const amt = ranInputs.categoryExpenses[c.id] || 0;
        oldT += amt;
        newT += amt * (c.id === 'housing' ? hr : orr);
      }
      return oldT > 0 ? newT / oldT - 1 : orr - 1;
    }
    return orr - 1;
  };

  // Prefill the adjustment whenever the target changes (until hand-edited); a
  // fresh target clears any prior comparison.
  useEffect(() => {
    if (whatIfState && ranInputs && !whatIfAdjTouched) {
      setWhatIfAdjStr((recomputeWhatIfAdj(whatIfState) * 100).toFixed(0));
    }
    setWhatIfResult(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [whatIfState, ranInputs]);

  const handleWhatIf = () => {
    if (!ranInputs || !whatIfState) return;
    setWhatIfBusy(true);
    setTimeout(() => {
      try {
        const adj = 1 + num(whatIfAdjStr, 0) / 100;
        const target: SimInputs = {
          ...ranInputs,
          stateCode: whatIfState,
          annualExpenses: ranInputs.annualExpenses * adj,
          categoryExpenses: Object.fromEntries(
            EXPENSE_CATEGORIES.map(c => [c.id, (ranInputs.categoryExpenses[c.id] || 0) * adj]),
          ) as Record<CategoryId, number>,
          ltc: { ...ranInputs.ltc, annualCost: ranInputs.ltc.annualCost * ltcCostRatio(whatIfHome, whatIfState) },
        };
        setWhatIfResult(runMonteCarlo(target));
      } catch (e) {
        setErrors([`What-If failed: ${e instanceof Error ? e.message : String(e)}`]);
      } finally {
        setWhatIfBusy(false);
      }
    }, 30);
  };

  // ── Sequence-of-returns: N years (3–7), keep custom rows sized to N ──
  const seqN = Math.min(Math.max(Math.round(num(seqYearsStr, 5)), 3), 7);
  useEffect(() => {
    setSeqCustom(prev => {
      if (prev.length === seqN) return prev;
      const next = prev.slice(0, seqN);
      while (next.length < seqN) next.push({ stock: '-12', bond: '2' });
      return next;
    });
  }, [seqN]);

  // The worst N-consecutive-year window in history for the user's mix.
  const seqWorst = useMemo(() => {
    if (!ranInputs) return null;
    const stockW = result?.windowStats.effectiveStockPct ?? ranInputs.stockPct;
    return worstReturnWindow(ranInputs.windowStart, ranInputs.windowEnd, ranInputs.excludedYears, seqN, stockW, ranInputs.stockComposition);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ranInputs, result, seqN]);

  // A fresh selection clears any prior stress result.
  useEffect(() => { setSeqResult(null); setSeqMitigations(null); }, [seqMode, seqYearsStr, seqCustom, ranInputs]);

  // The sequence currently configured in the What-If controls, and a label.
  const seqReturnsNow = useMemo((): SequenceReturn[] =>
    seqMode === 'historical'
      ? (seqWorst ?? []).map(h => ({
          // With a custom stock mix, use that blend's return for the year, not the raw S&P.
          stock: ranInputs?.stockComposition ? compositeStockForYear(h[0], ranInputs.stockComposition) : h[1],
          bond: h[2], infl: h[3],
        }))
      : seqCustom.slice(0, seqN).map(r => ({ stock: num(r.stock, 0), bond: num(r.bond, 0), infl: null })),
    [seqMode, seqWorst, seqCustom, seqN, ranInputs]);
  const seqLabel = seqMode === 'historical'
    ? (seqWorst && seqWorst.length ? `worst ${seqN}yr, ${seqWorst[0][0]}–${seqWorst[seqWorst.length - 1][0]}` : `worst ${seqN}yr`)
    : `custom ${seqN}yr`;

  const addSeqToPlan = () => setPlanSequence({ seq: seqReturnsNow, label: seqLabel });
  const removeSeqFromPlan = () => setPlanSequence(null);
  const seqInPlan = planSequence !== null
    && JSON.stringify(planSequence.seq) === JSON.stringify(seqReturnsNow);

  const handleSeq = () => {
    if (!ranInputs) return;
    const seq = seqReturnsNow;
    if (seq.length === 0) return;
    setSeqBusy(true);
    setTimeout(() => {
      try {
        // The bad start with the plan AS-IS (no mitigation).
        const badPlan: SimInputs = { ...ranInputs, sequenceReturns: seq };
        setSeqResult(runMonteCarlo(badPlan));
        // Now simulate each mitigation against the SAME bad sequence and rank
        // by how much it recovers — proof, not prose.
        const configs: { id: string; label: string; detail: string; mod: Partial<SimInputs> }[] = [
          { id: 'guardrails', label: 'Spending guardrails', detail: 'Cut discretionary spending after big declines', mod: { guardrails: true } },
          { id: 'guarded', label: 'Down-market guard', detail: `Sell bonds before stocks after a −${ranInputs.downThresholdPct}% year`, mod: { withdrawalStrategy: 'guarded' } },
          { id: 'guarded_rebalance', label: 'Guard + up-year rebalance', detail: 'Guard, and only rebalance after strong years', mod: { withdrawalStrategy: 'guarded_rebalance' } },
          ...(ranInputs.taxEnabled ? [{ id: 'holistic', label: 'Holistic household glide', detail: `De-risk to ${ranInputs.holisticPostStockPct}% stocks, rebalance tax-free`, mod: { withdrawalStrategy: 'holistic' as const } }] : []),
          { id: 'cash3', label: '3-year cash buffer', detail: 'Hold 3 years of cash, spend it in down years', mod: { cashBufferYears: 3 } },
        ];
        const mits = configs.map(c => {
          const r = runMonteCarlo({ ...badPlan, ...c.mod });
          return { id: c.id, label: c.label, detail: c.detail, success: r.successRate, endReal: r.medianEnding.real };
        });
        setSeqMitigations(mits);
      } catch (e) {
        setErrors([`Sequence stress failed: ${e instanceof Error ? e.message : String(e)}`]);
      } finally { setSeqBusy(false); }
    }, 30);
  };

  // ── Saved plans: serialize the raw form state so a load restores exactly ──
  const serializeForm = () => ({
    version: 2,
    currentAge, retireAge, planAge, zip,
    expenseMode, annualExpStr, catStr, spreadStr, medicareFracStr,
    phasePreset, gogoEndStr, slowgoEndStr, gogoMultStr, slowgoMultStr, nogoMultStr,
    lumps, allocTaxable, allocTraditional, allocRoth, divYieldStr, couponStr,
    stockGainStr, bondGainStr, stockCompMode, stockWeights, incomes,
    ssFraStr, ssClaimAge,
    spouseEnabled, spouseAgeStr, spousePlanAgeStr, spouseFraStr, spouseClaimAge, survivorPctStr,
    ltcEnabled, ltcType, ltcCostStr, ltcCostTouched, ltcPersons, ltcProbStr, ltcOnsetStr, ltcDurStr,
    returnsMode, inflationMode, windowStart, windowEnd, excludedYears,
    manualStockStr, manualBondStr, manualInflStr, numSims,
    taxEnabled, filing, taxableStr, tradStr, rothStr,
    guardrails, withdrawalStrategy, downThrStr, upThrStr, holisticPostStr, glideAgeStr, cashBufferStr, planSequence,
    savingsEnabled, savUntilStr, savTaxableStr, savRothStr, sav401kMode, sav401kStr,
    sav401kEmployerMode, sav401kEmployerStr, sav401kMatchStr,
    rothMode, rothCeiling, rothStartStr, rothEndStr,
  });

  const restoreForm = (p: Record<string, unknown>) => {
    const s = (v: unknown, d: string) => (typeof v === 'string' ? v : d);
    const n = (v: unknown, d: number) => (typeof v === 'number' && Number.isFinite(v) ? v : d);
    const b = (v: unknown, d: boolean) => (typeof v === 'boolean' ? v : d);
    setCurrentAge(s(p.currentAge, '55')); setRetireAge(s(p.retireAge, '65')); setPlanAge(s(p.planAge, '95'));
    setZip(s(p.zip, ''));
    setExpenseMode(p.expenseMode === 'category' ? 'category' : 'simple');
    setAnnualExpStr(s(p.annualExpStr, '80000'));
    setCatStr({ ...DEFAULT_CATS, ...(typeof p.catStr === 'object' && p.catStr ? p.catStr as Record<CategoryId, string> : {}) });
    setSpreadStr({ ...DEFAULT_SPREADS, ...(typeof p.spreadStr === 'object' && p.spreadStr ? p.spreadStr as Record<CategoryId, string> : {}) });
    setMedicareFracStr(s(p.medicareFracStr, '0.5'));
    setPhasePreset(p.phasePreset === 'smile' || p.phasePreset === 'custom' ? p.phasePreset : 'flat');
    setGogoEndStr(s(p.gogoEndStr, '75')); setSlowgoEndStr(s(p.slowgoEndStr, '85'));
    setGogoMultStr(s(p.gogoMultStr, '100')); setSlowgoMultStr(s(p.slowgoMultStr, '85')); setNogoMultStr(s(p.nogoMultStr, '75'));
    setLumps(Array.isArray(p.lumps) ? p.lumps.map((l: Record<string, unknown>) => ({
      id: rowIdSeq++, name: s(l.name, ''), amount: s(l.amount, ''), age: s(l.age, ''),
    })) : []);
    setAllocTaxable(n(p.allocTaxable, 60)); setAllocTraditional(n(p.allocTraditional, 60)); setAllocRoth(n(p.allocRoth, 60));
    setDivYieldStr(s(p.divYieldStr, '1')); setCouponStr(s(p.couponStr, '4'));
    setStockGainStr(s(p.stockGainStr, '30')); setBondGainStr(s(p.bondGainStr, '0'));
    setStockCompMode(p.stockCompMode === 'custom' ? 'custom' : 'sp500');
    if (p.stockWeights && typeof p.stockWeights === 'object') {
      const sw = p.stockWeights as Record<string, unknown>;
      setStockWeights({
        sp500: s(sw.sp500, '60'), nasdaq100: s(sw.nasdaq100, '15'),
        russell2000: s(sw.russell2000, '10'), intl: s(sw.intl, '10'), em: s(sw.em, '5'),
      });
    }
    setIncomes(Array.isArray(p.incomes) ? p.incomes.map((x: Record<string, unknown>) => ({
      id: rowIdSeq++, name: s(x.name, ''), annual: s(x.annual, ''),
      startAge: s(x.startAge, ''), endAge: s(x.endAge, ''), growthPct: s(x.growthPct, '0'),
    })) : []);
    setSsFraStr(s(p.ssFraStr, '36000')); setSsClaimAge(n(p.ssClaimAge, 67));
    setSpouseEnabled(b(p.spouseEnabled, false));
    setSpouseAgeStr(s(p.spouseAgeStr, '53')); setSpousePlanAgeStr(s(p.spousePlanAgeStr, '95'));
    setSpouseFraStr(s(p.spouseFraStr, '24000')); setSpouseClaimAge(n(p.spouseClaimAge, 67));
    setSurvivorPctStr(s(p.survivorPctStr, '80'));
    setLtcEnabled(b(p.ltcEnabled, false));
    setLtcType(p.ltcType === 'homeHealth' || p.ltcType === 'nursingHome' ? p.ltcType : 'assistedLiving');
    setLtcCostStr(s(p.ltcCostStr, '')); setLtcCostTouched(b(p.ltcCostTouched, false));
    setLtcPersons(n(p.ltcPersons, 2) === 1 ? 1 : 2);
    setLtcProbStr(s(p.ltcProbStr, '50')); setLtcOnsetStr(s(p.ltcOnsetStr, '83')); setLtcDurStr(s(p.ltcDurStr, '3'));
    setReturnsMode(p.returnsMode === 'manual' ? 'manual' : 'historical');
    setInflationMode(p.inflationMode === 'manual' ? 'manual' : 'historical');
    setWindowStart(n(p.windowStart, HIST_START)); setWindowEnd(n(p.windowEnd, HIST_END));
    setExcludedYears(Array.isArray(p.excludedYears) ? p.excludedYears.filter((y: unknown) => typeof y === 'number') : []);
    setManualStockStr(s(p.manualStockStr, '8')); setManualBondStr(s(p.manualBondStr, '4.5')); setManualInflStr(s(p.manualInflStr, '3'));
    setNumSims(n(p.numSims, 1000));
    setTaxEnabled(b(p.taxEnabled, false));
    setFiling(p.filing === 'single' ? 'single' : 'mfj');
    setTaxableStr(s(p.taxableStr, '800000')); setTradStr(s(p.tradStr, '1000000')); setRothStr(s(p.rothStr, '200000'));
    setGuardrails(b(p.guardrails, false));
    setWithdrawalStrategy(p.withdrawalStrategy === 'guarded' || p.withdrawalStrategy === 'guarded_rebalance' || p.withdrawalStrategy === 'holistic' ? p.withdrawalStrategy : 'sequential');
    setDownThrStr(s(p.downThrStr, '5')); setUpThrStr(s(p.upThrStr, '5'));
    setHolisticPostStr(s(p.holisticPostStr, '40')); setGlideAgeStr(s(p.glideAgeStr, '65'));
    setCashBufferStr(s(p.cashBufferStr, '0'));
    setSavingsEnabled(b(p.savingsEnabled, false));
    setSavUntilStr(s(p.savUntilStr, '')); setSavTaxableStr(s(p.savTaxableStr, '0'));
    setSavRothStr(s(p.savRothStr, '7000'));
    setSav401kMode(p.sav401kMode === 'max' ? 'max' : 'amount');
    setSav401kStr(s(p.sav401kStr, '15000'));
    setSav401kEmployerMode(p.sav401kEmployerMode === 'match' ? 'match' : 'amount');
    setSav401kEmployerStr(s(p.sav401kEmployerStr, '5000')); setSav401kMatchStr(s(p.sav401kMatchStr, '50'));
    setPlanSequence(
      p.planSequence && typeof p.planSequence === 'object' && Array.isArray((p.planSequence as { seq?: unknown }).seq)
        ? (p.planSequence as { seq: SequenceReturn[]; label: string })
        : null,
    );
    // Older saved plans used a boolean rothEnabled → map to 'custom'.
    setRothMode(p.rothMode === 'custom' || p.rothMode === 'auto' ? p.rothMode
      : b(p.rothEnabled, false) ? 'custom' : 'off');
    setRothCeiling(n(p.rothCeiling, 22) === 12 ? 12 : n(p.rothCeiling, 22) === 24 ? 24 : 22);
    setRothStartStr(s(p.rothStartStr, '')); setRothEndStr(s(p.rothEndStr, ''));
  };

  const planSnapshot = () => (result && ranInputs ? {
    success_rate_pct: Math.round(result.successRate * 1000) / 10,
    median_ending_real: Math.round(result.medianEnding.real),
    plan_age: ranInputs.planAge,
  } : {});

  const handleSavePlan = async (asNew = false) => {
    if (!isAuthenticated) {
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
      return;
    }

    const name = planName.trim() || 'My retirement plan';
    setPlanBusy(true); setPlanMsg('');
    try {
      if (loadedPlanId !== null && !asNew) {
        const upd = await updateSavedStrategy(loadedPlanId, { name, parameters: serializeForm(), result_snapshot: planSnapshot() });
        setPlans(prev => prev.map(pl => (pl.id === upd.id ? upd : pl)));
      } else {
        const created = await saveStrategy({
          strategy_type: 'retirement_plan', name, ticker: 'PLAN',
          parameters: serializeForm(), legs_data: [], result_snapshot: planSnapshot(),
        });
        setPlans(prev => [created, ...prev]);
        setLoadedPlanId(created.id);
      }
      setPlanName(name);
      setPlanMsg('Saved ✓');
    } catch (e) {
      setPlanMsg(`Save failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setPlanBusy(false);
      setTimeout(() => setPlanMsg(''), 4000);
    }
  };

  const handleLoadPlan = (id: number) => {
    const p = plans.find(x => x.id === id);
    if (!p) return;
    restoreForm((p.parameters ?? {}) as Record<string, unknown>);
    setPlanName(p.name);
    setLoadedPlanId(p.id);
  };

  const handleDeletePlan = async () => {
    if (loadedPlanId === null) return;
    if (!window.confirm(`Delete saved plan "${planName || 'this plan'}"?`)) return;
    setPlanBusy(true);
    try {
      await deleteSavedStrategy(loadedPlanId);
      setPlans(prev => prev.filter(pl => pl.id !== loadedPlanId));
      setLoadedPlanId(null); setPlanName(''); setPlanMsg('Deleted');
    } catch (e) {
      setPlanMsg(`Delete failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setPlanBusy(false);
      setTimeout(() => setPlanMsg(''), 4000);
    }
  };

  // ── Display helpers (real vs nominal) ──
  const flowD = (r: YearRecord) => (displayReal ? r.defl : 1);
  const balD = (r: YearRecord) => (displayReal ? r.defl * (1 + r.infl) : 1);
  const money = (nominal: number, real: number) => (displayReal ? real : nominal);

  // ── Result charts ──
  const charts = useMemo(() => {
    if (!result || !ranInputs) return null;
    const pct = displayReal ? result.percentilesReal : result.percentilesNominal;
    const band = (color: string, alpha: string) => ({ borderColor: color, backgroundColor: `${color}${alpha}` });

    const fan = {
      labels: pct.map(p => String(p.age)),
      datasets: [
        { label: '90th pct', data: pct.map(p => p.p90), ...band('#34d399', '00'), borderWidth: 1, pointRadius: 0, fill: false },
        { label: '75th pct', data: pct.map(p => p.p75), ...band('#34d399', '22'), borderWidth: 0, pointRadius: 0, fill: '-1' },
        { label: 'Median', data: pct.map(p => p.p50), borderColor: '#3abff8', backgroundColor: '#3abff833', borderWidth: 2, pointRadius: 0, fill: '-1' },
        { label: '25th pct', data: pct.map(p => p.p25), ...band('#fbbf24', '22'), borderWidth: 0, pointRadius: 0, fill: '-1' },
        { label: '10th pct', data: pct.map(p => p.p10), ...band('#fb7185', '26'), borderWidth: 1, pointRadius: 0, fill: '-1' },
      ],
    };

    const survival = {
      labels: result.survivalByAge.map(p => String(p.age)),
      datasets: [{
        label: 'P(portfolio survives)',
        data: result.survivalByAge.map(p => p.prob * 100),
        borderColor: '#a78bfa', backgroundColor: '#a78bfa26',
        borderWidth: 2, pointRadius: 0, fill: true, tension: 0.25,
      }],
    };

    const retired = result.representative.filter(r => r.age >= ranInputs.retireAge);
    const cashflow = {
      labels: retired.map(r => String(r.age)),
      datasets: [
        { type: 'bar' as const, label: 'Social Security', data: retired.map(r => r.ss / flowD(r)), backgroundColor: '#34d39990', stack: 'in', borderRadius: 2 },
        { type: 'bar' as const, label: 'Other income', data: retired.map(r => r.income / flowD(r)), backgroundColor: '#3abff890', stack: 'in', borderRadius: 2 },
        { type: 'bar' as const, label: 'Portfolio withdrawals', data: retired.map(r => r.withdrawal / flowD(r)), backgroundColor: '#a78bfa90', stack: 'in', borderRadius: 2 },
        { type: 'line' as const, label: 'Expenses + taxes', data: retired.map(r => (r.expenses + r.taxes) / flowD(r)), borderColor: '#fb7185', borderWidth: 2, pointRadius: 0, fill: false, tension: 0.2 },
      ],
    };

    const accounts = ranInputs.taxEnabled ? {
      labels: result.representative.map(r => String(r.age)),
      datasets: [
        { label: 'Taxable', data: result.representative.map(r => r.taxable / balD(r)), borderColor: '#3abff8', backgroundColor: '#3abff855', borderWidth: 1, pointRadius: 0, fill: true, stack: 'acct' },
        { label: 'Traditional', data: result.representative.map(r => r.traditional / balD(r)), borderColor: '#fbbf24', backgroundColor: '#fbbf2455', borderWidth: 1, pointRadius: 0, fill: true, stack: 'acct' },
        { label: 'Roth', data: result.representative.map(r => r.roth / balD(r)), borderColor: '#34d399', backgroundColor: '#34d39955', borderWidth: 1, pointRadius: 0, fill: true, stack: 'acct' },
      ],
    } : null;

    return { fan, survival, cashflow, accounts };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, ranInputs, displayReal]);

  // The Lab sweeps run at a faster, fixed simulation count than the headline,
  // so their raw baseline differs from the headline success by sampling noise.
  // Anchor every Lab figure by the (headline − sweep-baseline) offset: the
  // current plan then reads exactly the headline number, and the clean
  // common-random-number deltas between options are preserved.
  const labAdj = useMemo(() => {
    if (!sweeps || !result) return { s: (x: number) => x, e: (x: number) => x, t: (x: number) => x };
    const dS = result.successRate - sweeps.baseline.success;
    const dE = result.medianEnding.real - sweeps.baseline.end;
    const dT = result.medianTotalTaxes - sweeps.baseline.tax;
    return {
      s: (x: number) => Math.max(0, Math.min(1, x + dS)),
      e: (x: number) => Math.max(0, x + dE),
      t: (x: number) => Math.max(0, x + dT),
    };
  }, [sweeps, result]);

  const sweepCharts = useMemo(() => {
    if (!sweeps) return null;
    const alloc = {
      labels: sweeps.allocation.map(p => `${p.x}%`),
      datasets: [{
        label: 'Success rate',
        data: sweeps.allocation.map(p => labAdj.s(p.success) * 100),
        borderColor: '#3abff8', backgroundColor: '#3abff826',
        borderWidth: 2, fill: true, tension: 0.3,
        pointRadius: sweeps.allocation.map(p => (p.x === ranInputs?.stockPct ? 5 : 3)),
        pointBackgroundColor: sweeps.allocation.map(p => (p.x === ranInputs?.stockPct ? '#fb7185' : '#3abff8')),
      }],
    };
    const claim = sweeps.claimAge.length ? {
      labels: sweeps.claimAge.map(p => String(p.x)),
      datasets: [{
        label: 'Success rate',
        data: sweeps.claimAge.map(p => labAdj.s(p.success) * 100),
        backgroundColor: sweeps.claimAge.map(p => (p.x === ranInputs?.ssClaimAge ? '#fb7185aa' : '#34d399aa')),
        borderRadius: 3,
      }],
    } : null;
    const spouseClaim = sweeps.spouseClaimAge.length ? {
      labels: sweeps.spouseClaimAge.map(p => String(p.x)),
      datasets: [{
        label: 'Success rate',
        data: sweeps.spouseClaimAge.map(p => labAdj.s(p.success) * 100),
        backgroundColor: sweeps.spouseClaimAge.map(p => (p.x === ranInputs?.spouse.ssClaimAge ? '#fb7185aa' : '#a78bfaaa')),
        borderRadius: 3,
      }],
    } : null;
    return { alloc, claim, spouseClaim };
  }, [sweeps, ranInputs, labAdj]);

  // Strategy on/off cards, grouped: renders the subset whose id is in `ids`
  // (helpful-first), or null if none apply. Shared card markup keeps every
  // Strategy Lab group visually identical.
  const renderStrategyCards = (ids: string[]) => {
    if (!sweeps || !ranInputs) return null;
    const list = [...sweeps.strategies]
      .filter(s => ids.includes(s.id))
      .map(s => ({ s, improves: outcomeBetter(
        { success: s.on.success, end: s.on.medianEndReal },
        { success: s.off.success, end: s.off.medianEndReal },
      ) }))
      .sort((a, b) => Number(b.improves) - Number(a.improves));
    if (!list.length) return null;
    return (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {list.map(({ s, improves }) => {
          const dSuccess = (s.on.success - s.off.success) * 100;
          const isOn = s.currentlyOn;
          const toggle = () => {
            if (s.id === 'guardrails') setGuardrails(!guardrails);
            else if (s.id === 'smile') setPhasePreset(phasePreset === 'flat' ? 'smile' : 'flat');
            else if (s.id === 'down_guard') setWithdrawalStrategy(isOn ? 'sequential' : 'guarded');
            else if (s.id === 'smart_rebalance') setWithdrawalStrategy(isOn ? 'sequential' : 'guarded_rebalance');
            else if (s.id === 'holistic') setWithdrawalStrategy(isOn ? 'sequential' : 'holistic');
            else if (s.id === 'asset_location' && s.applyAllocation) {
              setAllocTaxable(s.applyAllocation.taxable);
              setAllocTraditional(s.applyAllocation.traditional);
              setAllocRoth(s.applyAllocation.roth);
            }
          };
          const dEnd = s.on.medianEndReal - s.off.medianEndReal;
          const dTax = s.on.medianTaxes - s.off.medianTaxes;
          const sign = (v: number) => (v >= 0 ? '+' : '−');
          return (
            <div key={s.id} className={`rounded-lg p-3 border ${improves && !isOn ? 'bg-emerald-500/5 border-emerald-500/25' : 'bg-base-200/40 border-base-300/40'}`}>
              <div className="flex items-center justify-between gap-2 mb-1 flex-wrap">
                <h4 className="text-xs font-bold flex items-center gap-1.5">
                  {s.label}
                  {improves && !isOn && (
                    <span className="badge badge-xs bg-emerald-400/15 text-emerald-400 border-emerald-400/25">recommended</span>
                  )}
                </h4>
                {isOn ? (
                  <button type="button" onClick={toggle} className="btn btn-xs btn-ghost text-base-content/50">
                    Remove from plan
                  </button>
                ) : (
                  <button type="button" onClick={toggle}
                    className={`btn btn-xs ${improves ? 'btn-secondary' : 'btn-ghost text-base-content/40'}`}>
                    {s.id === 'asset_location' ? 'Apply allocation' : improves ? 'Add to plan' : 'Try anyway'}
                  </button>
                )}
              </div>
              <p className="text-[10px] text-base-content/50 mb-2">{s.detail}</p>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div className="bg-base-300/30 rounded p-1.5">
                  <div className="text-[9px] text-base-content/40 uppercase">Success</div>
                  <div className={`text-sm font-bold ${dSuccess >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                    {dSuccess >= 0 ? '+' : ''}{dSuccess.toFixed(1)}pp
                  </div>
                </div>
                <div className="bg-base-300/30 rounded p-1.5">
                  <div className="text-[9px] text-base-content/40 uppercase">Median ending</div>
                  <div className={`text-sm font-bold ${dEnd >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                    {sign(dEnd)}{fmtCompact(Math.abs(dEnd))}
                  </div>
                </div>
                <div className="bg-base-300/30 rounded p-1.5">
                  <div className="text-[9px] text-base-content/40 uppercase">{ranInputs.taxEnabled ? 'Lifetime taxes' : 'Taxes'}</div>
                  <div className={`text-sm font-bold ${!ranInputs.taxEnabled ? 'text-base-content/40' : dTax <= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                    {ranInputs.taxEnabled ? `${sign(dTax)}${fmtCompact(Math.abs(dTax))}` : 'n/a'}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  // Apply a F.I.R.E. scenario into the form: set the earlier retirement age and
  // any portfolio / spending lever it uses, then let the plan go dirty for re-run
  // (so the What-If, ledger, hero and every other strategy update together).
  const applyFire = (sc: FireScenario) => {
    if (sc.earliestAge == null) return;
    setRetireAge(String(sc.apply.retireAge));
    if (sc.apply.stockPct != null) {
      setAllocTaxable(sc.apply.stockPct);
      setAllocTraditional(sc.apply.stockPct);
      setAllocRoth(sc.apply.stockPct);
    }
    if (sc.apply.expenseScale != null) {
      const k = sc.apply.expenseScale;
      setAnnualExpStr(String(Math.round(num(annualExpStr, 80000) * k)));
      setCatStr(prev => Object.fromEntries(
        Object.entries(prev).map(([id, v]) => [id, String(Math.round(num(v, 0) * k))]),
      ) as Record<CategoryId, string>);
    }
  };

  // The equity-mix risk/return table (historical) — lives in the Stock allocation group.
  const mixLabPanel = (
    <div className="bg-base-200/40 rounded-lg p-3 border border-base-300/40">
      <button type="button" onClick={() => setShowMixLab(v => !v)}
        className="text-xs font-bold text-secondary hover:underline flex items-center gap-1">
        {showMixLab ? '▾' : '▸'} Mix Lab — compare equity-index mixes on risk &amp; return
      </button>
      {showMixLab && (
        <div className="mt-2">
          <p className="text-[10px] text-base-content/50 mb-2">
            How each mix would have behaved over <b>{windowStart}–{windowEnd}</b>. <b>CAGR</b> = compound
            annual growth; <b>Vol</b> = year-to-year swing (lower = steadier); <b>Worst yr</b> / <b>Worst 3-yr</b> =
            deepest single-year and three-year cumulative losses (sequence risk); <b>Risk-adj.</b> = return earned
            per unit of risk (higher is better). Concentrated, high-return mixes usually carry the deepest drawdowns —
            diversifying trades a little return for a smoother ride.
          </p>
          <div className="overflow-x-auto">
            <table className="table table-xs w-full">
              <thead>
                <tr className="text-[10px] text-base-content/50">
                  <th>Mix</th><th className="text-right">CAGR</th><th className="text-right">Vol</th>
                  <th className="text-right">Worst yr</th><th className="text-right">Worst 3-yr</th>
                  <th className="text-right">Risk-adj.</th><th></th>
                </tr>
              </thead>
              <tbody className="text-[11px]">
                {mixLab.rows.map(r => (
                  <tr key={r.id} className={r.isCurrent ? 'bg-secondary/5' : r.id === mixLab.bestSharpe ? 'bg-emerald-400/5' : ''}>
                    <td>
                      <div className="font-semibold flex items-center gap-1">
                        {r.label}
                        {r.id === mixLab.bestSharpe && <span className="badge badge-xs bg-emerald-400/15 text-emerald-400 border-emerald-400/30">best balance</span>}
                        {r.isCurrent && <span className="badge badge-xs bg-secondary/15 text-secondary border-secondary/30">you</span>}
                      </div>
                      <div className="text-[9px] text-base-content/40">{r.note}</div>
                    </td>
                    <td className="text-right font-semibold">{r.stats.cagr.toFixed(1)}%</td>
                    <td className="text-right text-base-content/70">{r.stats.vol.toFixed(1)}%</td>
                    <td className="text-right text-rose-400/80">{r.stats.worst.toFixed(0)}%</td>
                    <td className="text-right text-rose-400/80">{r.stats.worstStreak.toFixed(0)}%</td>
                    <td className="text-right font-semibold">{r.stats.sharpe.toFixed(2)}</td>
                    <td className="text-right">
                      {!r.isCurrent && (
                        <button type="button" onClick={() => applyMix(r.weights)}
                          className="btn btn-ghost btn-xs text-secondary">Use</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {mixLab.hasCurrent && mixLab.maxShare >= 0.7 && (
            <p className="text-[10px] text-amber-400/80 mt-2">
              ⚠ Your mix is concentrated — {(mixLab.maxShare * 100).toFixed(0)}% sits in a single index. A more
              diversified blend (e.g. the “best balance” row) historically cushioned the worst years without giving up much growth.
            </p>
          )}
          <p className="text-[10px] text-base-content/40 mt-2 italic">
            These are historical, descriptive statistics over your chosen window — <b>past performance does not
            guarantee future results</b>. Use them to gauge relative risk, not to predict returns. A balanced,
            diversified mix is generally more robust to whichever decade comes next.
          </p>
        </div>
      )}
    </div>
  );

  const moneyChartOpts = (stacked = false, legend = true) => ({
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index' as const, intersect: false },
    plugins: {
      legend: { display: legend, position: 'bottom' as const, labels: { boxWidth: 10, font: CHART_FONT } },
      tooltip: {
        callbacks: { label: (ctx: TooltipItem<'bar' | 'line'>) => `${ctx.dataset.label}: ${fmtMoney(ctx.parsed.y ?? 0)}` },
      },
    },
    scales: {
      x: { stacked, grid: { display: false }, ticks: { font: CHART_FONT, autoSkip: true, maxTicksLimit: 15, maxRotation: 0 } },
      y: { stacked, beginAtZero: true, grid: GRID, ticks: { font: CHART_FONT, callback: (v: number | string) => fmtCompact(Number(v)) } },
    },
  });

  const successChartOpts = (min: number) => ({
    responsive: true, maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: { callbacks: { label: (ctx: TooltipItem<'bar' | 'line'>) => `Success: ${(ctx.parsed.y ?? 0).toFixed(1)}%` } },
    },
    scales: {
      x: { grid: { display: false }, ticks: { font: CHART_FONT } },
      y: { min: Math.max(0, Math.floor(min - 5)), max: 100, grid: GRID, ticks: { font: CHART_FONT, callback: (v: number | string) => `${v}%` } },
    },
  });

  const success = result ? result.successRate * 100 : 0;
  const failShare = result ? (1 - result.successRate) * 100 : 0;
  const successTone = success >= 90
    ? { text: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30' }
    : success >= 75
    ? { text: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/30' }
    : { text: 'text-rose-400', bg: 'bg-rose-500/10', border: 'border-rose-500/30' };

  const ledgerRows = result?.representative ?? [];
  // Ages whose returns are the baked-in sequence-of-returns (fixed, not sampled).
  const seqLen = ranInputs?.sequenceReturns?.length ?? 0;
  const isSeqYear = (age: number) =>
    seqLen > 0 && ranInputs != null && age >= ranInputs.retireAge && age < ranInputs.retireAge + seqLen;
  const showLumpCol = (ranInputs?.lumpSums.length ?? 0) > 0;
  const showLtcCol = ranInputs?.ltc.enabled ?? false;
  const showPenaltyCol = ledgerRows.some(r => r.penalty > 0);
  const showAllocCol = ranInputs?.withdrawalStrategy === 'holistic';
  const showContribCol = ledgerRows.some(r => r.contrib > 0);

  // ── Ledger columns, data-driven so every view stays consistent ──
  const ledgerCols: LedgerCol[] = [];
  if (ranInputs) {
    const taxOn = ranInputs.taxEnabled;
    const mC = (get: (r: YearRecord) => number, neg = false): Pick<LedgerCol, 'cell' | 'cls'> => ({
      cell: (r) => (get(r) > 0 ? fmtCompact(get(r) / flowD(r)) : '—'),
      cls: neg ? (r) => (get(r) > 0 ? 'text-rose-400' : '') : undefined,
    });
    const rC = (get: (r: YearRecord) => number): Pick<LedgerCol, 'cell' | 'cls'> => ({
      cell: (r) => `${(get(r) * 100).toFixed(1)}%`,
      cls: (r) => (get(r) < 0 ? 'text-rose-400' : 'text-emerald-400'),
    });
    ledgerCols.push(
      { l: 'Stocks', v: ['overview'], ...rC(r => r.stockR) },
      { l: 'Bonds', v: ['overview'], ...rC(r => r.bondR) },
      { l: 'CPI', v: ['overview'], cell: (r) => `${(r.infl * 100).toFixed(1)}%` },
    );
    if (showAllocCol) ledgerCols.push({
      l: 'Alloc', v: ['overview', 'balances'],
      cell: (r) => (r.stockShare > 0 ? `${(r.stockShare * 100).toFixed(0)}%` : '—'),
      cls: () => 'text-sky-400',
    });
    if (showContribCol) ledgerCols.push({
      l: 'Saved', v: ['overview', 'sources'],
      cell: (r) => (r.contrib > 0 ? fmtCompact(r.contrib / flowD(r)) : '—'),
      cls: (r) => (r.contrib > 0 ? 'text-emerald-400' : ''),
    });
    ledgerCols.push({ l: 'Expenses', v: ['overview', 'sources'], ...mC(r => r.expenses) });
    if (showLumpCol) ledgerCols.push({ l: 'One-off', v: ['sources'], ...mC(r => r.lump) });
    if (showLtcCol) ledgerCols.push({ l: 'LTC', v: ['sources'], ...mC(r => r.ltcCost, true) });
    if (taxOn) ledgerCols.push({ l: 'IRMAA', v: ['sources', 'taxes'], ...mC(r => r.irmaa) });
    ledgerCols.push(
      { l: 'Income', v: ['overview', 'sources', 'taxes'], ...mC(r => r.income) },
      { l: 'Soc. Sec.', v: ['overview', 'sources', 'taxes'], ...mC(r => r.ss) },
    );
    if (taxOn) {
      ledgerCols.push(
        { l: 'Div.', v: ['sources', 'taxes'], ...mC(r => r.dividends) },
        { l: 'Int.', v: ['sources', 'taxes'], ...mC(r => r.interest) },
        { l: 'Sell stk', v: ['sources'], ...mC(r => r.sellStock) },
        { l: 'Sell bnd', v: ['sources'], ...mC(r => r.sellBond) },
        { l: 'RMD', v: ['sources', 'taxes'], ...mC(r => r.rmd) },
        { l: 'Trad w/d', v: ['sources', 'taxes'], ...mC(r => r.tradExtra) },
        { l: 'Roth w/d', v: ['sources'], ...mC(r => r.rothW) },
        { l: 'Conv.', v: ['sources', 'taxes'], ...mC(r => r.conversion) },
        { l: 'Gains', v: ['taxes'], ...mC(r => r.gains) },
        { l: 'Taxbl inc.', v: ['taxes'], ...mC(r => r.taxableIncome) },
        { l: 'Fed', v: ['taxes'], ...mC(r => r.fedTax) },
      );
      if (ranInputs.stateCode) ledgerCols.push({ l: 'State', v: ['taxes'], ...mC(r => r.stateTax) });
      ledgerCols.push({ l: 'NIIT', v: ['taxes'], ...mC(r => r.niit) });
      if (showPenaltyCol) ledgerCols.push({ l: 'Penalty', v: ['taxes'], ...mC(r => r.penalty, true) });
      ledgerCols.push(
        { l: 'Marg.', v: ['taxes'], cell: (r) => (r.taxableIncome > 0 ? `${(r.marginalRate * 100).toFixed(0)}%` : '—') },
        { l: 'Taxes', v: ['overview'], hideInAll: true, ...mC(r => r.taxes) },
        { l: 'Withdrawn', v: ['overview'], hideInAll: true, ...mC(r => r.withdrawal) },
        { l: 'Taxable', v: ['balances'], cell: (r) => fmtCompact(r.taxable / balD(r)) },
        { l: 'Trad.', v: ['balances'], cell: (r) => fmtCompact(r.traditional / balD(r)) },
        { l: 'Roth', v: ['balances'], cell: (r) => fmtCompact(r.roth / balD(r)) },
      );
    } else {
      ledgerCols.push({ l: 'Withdrawn', v: ['overview', 'sources'], ...mC(r => r.withdrawal) });
    }
    ledgerCols.push({
      l: 'Total', v: ['overview', 'balances'],
      cell: (r) => fmtCompact(r.balance / balD(r)), cls: () => 'font-semibold',
    });
  }
  const effLedgerView: LedgerView = ranInputs?.taxEnabled ? ledgerView : 'all';
  const visibleLedgerCols = ledgerCols.filter(c =>
    effLedgerView === 'all' ? !c.hideInAll : c.v.includes(effLedgerView),
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-xl font-bold flex items-center gap-2">
          <PiggyBank className="w-5 h-5 text-secondary" />
          Retirement Portfolio Longevity
        </h2>
        <p className="text-sm text-base-content/60 mt-1">
          Monte Carlo simulation over real market history: stocks, bonds and inflation are sampled
          jointly so bad years hit everything at once — the way they really do. Every input stays
          editable after a run, so experiment freely.
        </p>
      </div>

      {/* ── Saved plans bar ── */}
      <div className="flex flex-wrap items-center gap-2 bg-base-200/40 rounded-xl px-3 py-2.5 border border-base-300/50">
        <Save className="w-4 h-4 text-secondary flex-shrink-0" />
        <input
          type="text" value={planName} placeholder="Plan name (e.g. Retire at 62)"
          onChange={(e) => setPlanName(e.target.value)}
          className="input input-bordered input-sm bg-base-200 w-52"
        />
        <button type="button" onClick={() => handleSavePlan(false)} disabled={planBusy}
          className="btn btn-xs btn-secondary gap-1">
          {planBusy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
          {loadedPlanId !== null ? 'Update plan' : 'Save plan'}
        </button>
        {loadedPlanId !== null && (
          <button type="button" onClick={() => handleSavePlan(true)} disabled={planBusy}
            className="btn btn-xs btn-ghost">
            Save as new
          </button>
        )}
        {plans.length > 0 && (
          <select
            value={loadedPlanId ?? ''}
            onChange={(e) => { const id = Number(e.target.value); if (id) handleLoadPlan(id); }}
            className="select select-bordered select-xs bg-base-200 max-w-[220px]"
          >
            <option value="">Load a saved plan…</option>
            {plans.map(p => (
              <option key={p.id} value={p.id}>
                {p.name}{p.result_snapshot?.success_rate_pct != null ? ` (${p.result_snapshot.success_rate_pct}%)` : ''}
              </option>
            ))}
          </select>
        )}
        {loadedPlanId !== null && (
          <button type="button" onClick={handleDeletePlan} disabled={planBusy} title="Delete this saved plan"
            className="btn btn-ghost btn-xs btn-square text-base-content/40 hover:text-error">
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
        {planMsg && (
          <span className={`text-[11px] ${planMsg.includes('failed') ? 'text-rose-400' : 'text-emerald-400'}`}>{planMsg}</span>
        )}
      </div>

      {/* ── Sticky mini-summary: results follow you while you experiment ── */}
      {result && ranInputs && (
        <div className="sticky top-16 z-40">
          <div className={`flex items-center gap-x-2 gap-y-1 flex-wrap rounded-xl border px-4 py-2 backdrop-blur-xl shadow-[0_8px_24px_rgba(0,0,0,0.4)] transition-colors ${dirty
            ? 'bg-amber-500/10 border-amber-500/30'
            : 'bg-base-100/85 border-base-300/60'}`}>
            <span className={`text-sm font-extrabold ${successTone.text}`}>{success.toFixed(1)}%</span>
            <span className="text-[10px] text-base-content/50 mr-3">success rate</span>
            {failShare >= 0.05 && (
              <>
                <span className="text-xs font-bold text-rose-400">{failShare.toFixed(failShare < 10 ? 1 : 0)}%</span>
                <span className="text-[10px] text-base-content/50 mr-3">
                  risk of running short
                  {result.medianDepletionAge !== null ? ` (typically ~age ${Math.round(result.medianDepletionAge)})` : ''}
                </span>
              </>
            )}
            <span className="text-xs font-bold">{fmtCompact(money(result.medianEnding.nominal, result.medianEnding.real))}</span>
            <span className="text-[10px] text-base-content/50 mr-3">median end · {displayReal ? "today's $" : 'future $'}</span>
            {ranInputs.taxEnabled && (
              <>
                <span className="text-xs font-bold">{fmtCompact(result.medianTotalTaxes)}</span>
                <span className="text-[10px] text-base-content/50 mr-3">lifetime taxes</span>
              </>
            )}
            <span className="flex-1" />
            {dirty ? (
              <>
                <span className="text-[10px] text-amber-400 hidden sm:inline">inputs changed —</span>
                <button type="button" onClick={handleRun} disabled={computing} className="btn btn-xs btn-secondary gap-1">
                  {computing ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                  {computing ? 'Running…' : 'Re-run'}
                </button>
              </>
            ) : (
              <span className="text-[10px] text-emerald-400/80 flex items-center gap-1">
                <CheckCircle2 className="w-3 h-3" /> up to date
              </span>
            )}
          </div>
        </div>
      )}

      {/* ── Basics ── */}
      <Section step={1} icon={<Calendar className="w-4 h-4" />} title="Basics">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <Field label="Current age" value={currentAge} onChange={setCurrentAge} placeholder="55" />
          <Field label="Retirement age" value={retireAge} onChange={setRetireAge} placeholder="65" />
          <Field label="Plan to age" value={planAge} onChange={setPlanAge} placeholder="95" hint="Simulate until this age" />
          <div className="block">
            <span className="text-xs font-medium text-base-content/70 mb-1 flex items-center gap-1">
              <MapPin className="w-3 h-3" /> ZIP code (optional)
            </span>
            <input ref={zipRef} type="text" inputMode="numeric" value={zip} placeholder="94105" maxLength={5}
              onChange={(e) => setZip(e.target.value.replace(/\D/g, ''))}
              className="input input-bordered w-full bg-base-200" />
            <span className="text-[10px] text-base-content/40 mt-0.5 block">
              {stateCode
                ? `${STATE_NAMES[stateCode] ?? stateCode}: ${!stateTaxInfo || stateTaxInfo.rate === 0
                    ? 'no state income tax'
                    : `~${stateTaxInfo.rate}% state tax${stateTaxInfo.retirementExempt ? ' (retirement income exempt)' : ''}${stateTaxInfo.taxesSS ? ', taxes SS' : ''}`} · sets LTC costs`
                : 'Drives state tax & local long-term-care costs'}
            </span>
          </div>
        </div>
      </Section>

      {/* ── Corpus & allocation ── */}
      <Section step={2} icon={<Wallet className="w-4 h-4" />} title="Corpus"
        subtitle="Balance and stock/bond mix per account — each account is rebalanced to its target every year"
        right={
          <div className="text-right">
            <span className="text-[10px] text-base-content/40 block">Total corpus</span>
            <span className="text-lg font-bold">{fmtMoney(accountSum)}</span>
          </div>
        }
      >
        <div className="space-y-2">
          {([
            ['Taxable brokerage', taxableStr, setTaxableStr, allocTaxable, setAllocTaxable, 'Dividends/interest taxed yearly; stock sales at LTCG rates'],
            ['Traditional 401k / IRA', tradStr, setTradStr, allocTraditional, setAllocTraditional, `Ordinary income on withdrawal; RMDs from age ${rmdAge}`],
            ['Roth 401k / IRA', rothStr, setRothStr, allocRoth, setAllocRoth, 'Tax-free growth & withdrawals — drawn last'],
          ] as const).map(([label, balStr, setBal, alloc, setAlloc, hint]) => (
            <div key={label} className="grid grid-cols-2 sm:grid-cols-[200px_150px_1fr_110px] gap-x-4 gap-y-1.5 items-center bg-base-200/50 rounded-lg px-3 py-2">
              <div className="text-xs">
                <span className="font-medium">{label}</span>
                <span className="text-base-content/40 block text-[10px]">{hint}</span>
              </div>
              <div className="relative">
                <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-base-content/50 text-xs pointer-events-none">$</span>
                <input type="text" inputMode="decimal" value={balStr} placeholder="0"
                  onChange={(e) => setBal(e.target.value)}
                  className="input input-bordered input-sm w-full bg-base-200 pl-6" />
              </div>
              <input type="range" min={0} max={100} step={5} value={alloc}
                onChange={(e) => setAlloc(Number(e.target.value))}
                className="range range-xs range-info" />
              <div className="text-[11px] font-semibold whitespace-nowrap">
                <span className="text-sky-400">{alloc}% stk</span>
                <span className="text-base-content/30"> / </span>
                <span className="text-amber-400">{100 - alloc}% bnd</span>
              </div>
            </div>
          ))}
          <div className="pt-1">
            <div className="flex justify-between text-[10px] text-base-content/50 mb-1">
              <span>Overall portfolio (weighted by balances)</span>
              <span className="font-bold text-base-content/80">{weightedStockPct}% stocks / {100 - weightedStockPct}% bonds</span>
            </div>
            <div className="h-2.5 rounded-full overflow-hidden flex">
              <div className="bg-sky-400/70 transition-all" style={{ width: `${weightedStockPct}%` }} />
              <div className="bg-amber-400/70 transition-all" style={{ width: `${100 - weightedStockPct}%` }} />
            </div>
            <p className="text-[10px] text-base-content/40 mt-1.5">
              {taxEnabled
                ? 'Tax-smart rule of thumb: bonds in the 401k (interest hides from annual tax), stocks in Roth (tax-free growth) and taxable (cheaper LTCG rates) — the Strategy Lab quantifies it for your plan.'
                : 'Tax-aware planning is off, so the accounts are simulated as one pot at the weighted mix above. Turn it on (below) to model each account\'s tax treatment separately.'}
            </p>
          </div>
        </div>
      </Section>

      {/* ── Future savings (accumulation before retirement) ── */}
      <Section
        step={3}
        icon={<PiggyBank className="w-4 h-4" />}
        title="Future savings (optional)"
        subtitle="Money you'll keep adding before you retire — it compounds with your portfolio"
        toggle={{ label: 'Add future savings', checked: savingsEnabled, onChange: setSavingsEnabled }}
      >
        {!savingsEnabled ? (
          <p className="text-xs text-base-content/40">
            Off — the plan grows only today's corpus, with no further contributions. Turn on if you're still
            working and will keep saving into a taxable account, Roth IRA or 401(k) before retirement.
          </p>
        ) : num(currentAge, 55) >= num(retireAge, 65) ? (
          <p className="text-xs text-amber-400/80">
            Your current age ({num(currentAge, 55)}) is at or past your retirement age ({num(retireAge, 65)}),
            so there's no accumulation phase to model. Lower your current age or raise your retirement age to use this.
          </p>
        ) : (() => {
          const cAge = num(currentAge, 55), rAge = num(retireAge, 65);
          const k401Now = k401ElectiveLimit(1, cAge);
          const savUntil = Math.min(num(savUntilStr, rAge) || rAge, rAge);
          const cEmployee = sav401kMode === 'max' ? k401Now : Math.max(num(sav401kStr, 0), 0);
          const cEmployer = sav401kEmployerMode === 'match'
            ? cEmployee * (Math.max(num(sav401kMatchStr, 0), 0) / 100)
            : Math.max(num(sav401kEmployerStr, 0), 0);
          const firstYr = Math.max(num(savTaxableStr, 0), 0) + Math.max(num(savRothStr, 0), 0) + cEmployee + cEmployer;
          const catchupNote = cAge < 50 ? '' : cAge >= 60 && cAge <= 63 ? ' (incl. 60–63 catch-up)' : ' (incl. 50+ catch-up)';
          const AcctHead = ({ tint, icon, name, tag }: { tint: string; icon: React.ReactNode; name: string; tag: string }) => (
            <div className="flex items-center gap-2 mb-2">
              <span className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 ${tint}`}>{icon}</span>
              <div className="leading-tight min-w-0">
                <div className="text-xs font-bold truncate">{name}</div>
                <div className="text-[9px] text-base-content/40 truncate">{tag}</div>
              </div>
            </div>
          );
          return (
            <div className="space-y-3">
              <div className="flex items-end justify-between flex-wrap gap-3">
                <p className="text-[11px] text-base-content/50 max-w-md">
                  Choose the accounts your savings flow into — the type matters for taxes later. You have{' '}
                  <b>{savUntil - cAge}</b> working {savUntil - cAge === 1 ? 'year' : 'years'} left (through age {savUntil - 1}).
                  Amounts are today's dollars, grown with inflation, and compound at your portfolio allocation.
                </p>
                <div className="w-28">
                  <Field label="Save until age" value={savUntilStr} onChange={setSavUntilStr} small
                    placeholder={String(rAge)} hint={`≤ ${rAge}`} />
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="rounded-lg p-3 border border-base-300/40 bg-base-200/40">
                  <AcctHead tint="bg-sky-400/15 text-sky-400" icon={<Wallet className="w-4 h-4" />}
                    name="Taxable brokerage" tag="after-tax · fully flexible" />
                  <Field label="Contribution / yr" value={savTaxableStr} onChange={setSavTaxableStr} prefix="$" placeholder="0" small />
                </div>
                <div className="rounded-lg p-3 border border-base-300/40 bg-base-200/40">
                  <AcctHead tint="bg-emerald-400/15 text-emerald-400" icon={<TrendingUp className="w-4 h-4" />}
                    name="Roth IRA" tag="after-tax in · tax-free growth" />
                  <Field label="Contribution / yr" value={savRothStr} onChange={setSavRothStr} prefix="$" placeholder="7000" small />
                </div>
              </div>

              <div className="rounded-lg p-3 border border-base-300/40 bg-base-200/40">
                <AcctHead tint="bg-amber-400/15 text-amber-400" icon={<Landmark className="w-4 h-4" />}
                  name="401(k)" tag="pre-tax · employer plan" />
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <div className="flex items-center justify-between gap-2 mb-1.5">
                      <span className="text-[11px] font-semibold text-base-content/70">Your contribution</span>
                      <div className="join">
                        <button type="button" onClick={() => setSav401kMode('amount')}
                          className={`btn btn-xs join-item ${sav401kMode === 'amount' ? 'btn-secondary' : 'btn-ghost'}`}>Fixed</button>
                        <button type="button" onClick={() => setSav401kMode('max')}
                          className={`btn btn-xs join-item ${sav401kMode === 'max' ? 'btn-secondary' : 'btn-ghost'}`}>Max out</button>
                      </div>
                    </div>
                    {sav401kMode === 'amount' ? (
                      <Field label="Employee / yr" value={sav401kStr} onChange={setSav401kStr} prefix="$" placeholder="15000" small />
                    ) : (
                      <p className="text-[11px] text-base-content/60 bg-base-300/20 rounded px-2 py-1.5">
                        IRS elective max ≈ <b className="text-base-content/80">${k401Now.toLocaleString()}</b>/yr at age {cAge}{catchupNote}, indexed every year.
                      </p>
                    )}
                  </div>
                  <div>
                    <div className="flex items-center justify-between gap-2 mb-1.5">
                      <span className="text-[11px] font-semibold text-base-content/70">Employer adds</span>
                      <div className="join">
                        <button type="button" onClick={() => setSav401kEmployerMode('amount')}
                          className={`btn btn-xs join-item ${sav401kEmployerMode === 'amount' ? 'btn-secondary' : 'btn-ghost'}`}>Amount</button>
                        <button type="button" onClick={() => setSav401kEmployerMode('match')}
                          className={`btn btn-xs join-item ${sav401kEmployerMode === 'match' ? 'btn-secondary' : 'btn-ghost'}`}>% match</button>
                      </div>
                    </div>
                    {sav401kEmployerMode === 'amount' ? (
                      <Field label="Employer / yr" value={sav401kEmployerStr} onChange={setSav401kEmployerStr} prefix="$" placeholder="5000" small hint="match / profit-share" />
                    ) : (
                      <Field label="Match" value={sav401kMatchStr} onChange={setSav401kMatchStr} suffix="%" placeholder="50" small hint="of your contribution" />
                    )}
                  </div>
                </div>
                <p className="text-[10px] text-base-content/40 mt-2">
                  Employer adds ≈ <b className="text-base-content/70">{fmtMoney(cEmployer)}</b>/yr
                  {sav401kEmployerMode === 'match' ? ` — ${Math.max(num(sav401kMatchStr, 0), 0)}% of your ${fmtMoney(cEmployee)} deferral.` : '.'}
                </p>
              </div>

              <div className="flex items-center gap-2 text-[11px] text-base-content/60 bg-emerald-500/5 border border-emerald-500/20 rounded-lg px-3 py-2">
                <PiggyBank className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />
                First-year contribution ≈ <b className="text-base-content/80">{fmtMoney(firstYr)}</b>
                {' '}· continues (inflation-adjusted) through age {savUntil - 1}.
                {!taxEnabled && ' Turn on tax-aware planning to model each account\'s tax treatment; otherwise it all grows as one pot.'}
              </div>
            </div>
          );
        })()}
      </Section>

      {/* ── Spending (first-year expenses · pattern over retirement · one-time) ── */}
      <Section step={4} icon={<Receipt className="w-4 h-4" />} title="Spending"
        subtitle="What retirement costs: the first-year budget, how it changes with age, and one-time hits">
        <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
          <div>
            <h4 className="text-xs font-bold">First-year retirement expenses</h4>
            <p className="text-[10px] text-base-content/40">In today's dollars — the simulation inflates them along each path</p>
          </div>
          <div className="join">
            <button type="button" onClick={() => setExpenseMode('simple')} className={`btn btn-xs join-item ${expenseMode === 'simple' ? 'btn-secondary' : 'btn-ghost'}`}>Total</button>
            <button type="button" onClick={() => setExpenseMode('category')} className={`btn btn-xs join-item ${expenseMode === 'category' ? 'btn-secondary' : 'btn-ghost'}`}>By category</button>
          </div>
        </div>
        {expenseMode === 'simple' ? (
          <div className="max-w-xs">
            <Field label="Annual expenses" value={annualExpStr} onChange={setAnnualExpStr} prefix="$" placeholder="80,000"
              hint={`Inflated at CPI (${inflationMode === 'manual' ? 'your' : 'window avg'} ≈ ${baseInflPct.toFixed(1)}%/yr)`} />
          </div>
        ) : (
          <>
            <div className="space-y-1.5">
              {EXPENSE_CATEGORIES.map(c => {
                const rate = baseInflPct + num(spreadStr[c.id], c.spread);
                return (
                  <div key={c.id} className="grid grid-cols-2 sm:grid-cols-4 gap-2 items-center bg-base-200/50 rounded-lg px-2.5 py-1.5">
                    <span className="text-xs font-medium flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full inline-block" style={{ background: CAT_COLORS[c.id] }} />
                      {c.label}
                    </span>
                    <div className="relative">
                      <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-base-content/50 text-xs pointer-events-none">$</span>
                      <input type="text" inputMode="decimal" value={catStr[c.id]}
                        onChange={(e) => setCatStr(prev => ({ ...prev, [c.id]: e.target.value }))}
                        className="input input-bordered input-sm w-full bg-base-200 pl-6" />
                    </div>
                    <div className="relative" title="Inflation spread vs headline CPI">
                      <input type="text" inputMode="decimal" value={spreadStr[c.id]}
                        onChange={(e) => setSpreadStr(prev => ({ ...prev, [c.id]: e.target.value }))}
                        className="input input-bordered input-sm w-full bg-base-200 pr-16" />
                      <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-base-content/40 text-[10px] pointer-events-none">pp vs CPI</span>
                    </div>
                    <span className="text-[10px] text-base-content/50 text-right">inflates ≈{rate.toFixed(1)}%/yr</span>
                  </div>
                );
              })}
            </div>
            <div className="flex flex-wrap items-end justify-between mt-3 gap-3">
              <div className="max-w-[200px]">
                <Field small label="Medical after 65 (Medicare)" value={medicareFracStr} onChange={setMedicareFracStr} suffix="×"
                  hint="Enter your CURRENT medical cost above; from 65, it becomes this fraction of it (0.5 = half), medical-inflation adjusted" />
              </div>
              <div className="text-xs pb-1">
                <span className="text-base-content/50">Total today </span>
                <span className="font-bold">{fmtMoney(catTotal)}</span><span className="text-base-content/50">/yr</span>
              </div>
            </div>
          </>
        )}

        {/* Expense growth projection */}
        {expenseProjection && (
          <div className="bg-base-200/30 rounded-lg p-3 border border-base-300/40 mt-3">
            <h4 className="text-xs font-bold mb-2">
              How your spending grows by age
              <span className="font-normal text-base-content/40"> — at avg inflation {baseInflPct.toFixed(1)}%{expenseMode === 'category' ? ' + category spreads' : ''}{phases ? ', with your spending phases' : ''}</span>
            </h4>
            <div className="h-48">
              <Line data={expenseProjection} options={{
                responsive: true, maintainAspectRatio: false,
                interaction: { mode: 'index' as const, intersect: false },
                plugins: {
                  legend: { display: expenseMode === 'category', position: 'bottom' as const, labels: { boxWidth: 10, font: CHART_FONT } },
                  tooltip: { callbacks: { label: (ctx: TooltipItem<'line'>) => `${ctx.dataset.label}: ${fmtMoney(ctx.parsed.y ?? 0)}` } },
                },
                scales: {
                  x: { grid: { display: false }, ticks: { font: CHART_FONT, autoSkip: true, maxTicksLimit: 15, maxRotation: 0 } },
                  y: { stacked: expenseMode === 'category', beginAtZero: true, grid: GRID, ticks: { font: CHART_FONT, callback: (v: number | string) => fmtCompact(Number(v)) } },
                },
              }} />
            </div>
            <p className="text-[10px] text-base-content/40 mt-1">
              Deterministic preview in future dollars (each simulation uses its own inflation path).
              {expenseMode === 'category' && ' Notice medical: your current cost until 65, stepping down to your Medicare fraction after — exempt from spending phases, fastest inflation.'}
            </p>
          </div>
        )}
        <div className="divider my-3 opacity-50" />

        <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
          <div>
            <h4 className="text-xs font-bold">Spending pattern over retirement</h4>
            <p className="text-[10px] text-base-content/40">Real spending declines with age (go-go → slow-go → no-go) while medical keeps climbing</p>
          </div>
          <div className="join">
            {(['flat', 'smile', 'custom'] as const).map(p => (
              <button key={p} type="button" onClick={() => setPhasePreset(p)}
                className={`btn btn-xs join-item ${phasePreset === p ? 'btn-secondary' : 'btn-ghost'}`}>
                {p === 'flat' ? 'Flat' : p === 'smile' ? 'Smile (recommended)' : 'Custom'}
              </button>
            ))}
          </div>
        </div>
        {phasePreset === 'flat' && (
          <p className="text-xs text-base-content/40">
            Constant real spending for all of retirement — the conservative default. Try the “smile”:
            most retirees spend freely early (travel!), less in their 80s, least in their 90s.
          </p>
        )}
        {phasePreset === 'smile' && (
          <div className="flex flex-wrap gap-2 text-[11px]">
            <span className="badge badge-sm bg-emerald-400/10 text-emerald-400 border-emerald-400/20">Go-go to {SMILE_DEFAULT.gogoEndAge}: {Math.round(SMILE_DEFAULT.gogoMult * 100)}%</span>
            <span className="badge badge-sm bg-amber-400/10 text-amber-400 border-amber-400/20">Slow-go {SMILE_DEFAULT.gogoEndAge}–{SMILE_DEFAULT.slowgoEndAge}: {Math.round(SMILE_DEFAULT.slowgoMult * 100)}%</span>
            <span className="badge badge-sm bg-sky-400/10 text-sky-400 border-sky-400/20">No-go {SMILE_DEFAULT.slowgoEndAge}+: {Math.round(SMILE_DEFAULT.nogoMult * 100)}%</span>
            <span className="text-base-content/40 basis-full">Multipliers apply to non-medical spending; medical keeps its own (faster) inflation all the way.</span>
          </div>
        )}
        {phasePreset === 'custom' && (
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 max-w-2xl">
            <Field small label="Go-go until age" value={gogoEndStr} onChange={setGogoEndStr} placeholder="75" />
            <Field small label="Go-go spend" value={gogoMultStr} onChange={setGogoMultStr} suffix="%" placeholder="100" />
            <Field small label="Slow-go until" value={slowgoEndStr} onChange={setSlowgoEndStr} placeholder="85" />
            <Field small label="Slow-go spend" value={slowgoMultStr} onChange={setSlowgoMultStr} suffix="%" placeholder="85" />
            <Field small label="No-go spend" value={nogoMultStr} onChange={setNogoMultStr} suffix="%" placeholder="75" />
          </div>
        )}
        <div className="divider my-3 opacity-50" />

        <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
          <div>
            <h4 className="text-xs font-bold flex items-center gap-1.5"><Gift className="w-3.5 h-3.5" /> One-time large expenses</h4>
            <p className="text-[10px] text-base-content/40">College, weddings, a roof, the boat — today's dollars, inflated to the year they land</p>
          </div>
          <button type="button" className="btn btn-xs btn-secondary gap-1"
            onClick={() => setLumps(prev => [...prev, { id: rowIdSeq++, name: '', amount: '', age: retireAge }])}>
            <Plus className="w-3 h-3" /> Add expense
          </button>
        </div>
        {lumps.length === 0 ? (
          <p className="text-xs text-base-content/40">None — add kids' college, a wedding, home renovation, a new car every decade…</p>
        ) : (
          <div className="space-y-2">
            {lumps.map((l, i) => (
              <div key={l.id} className="grid grid-cols-2 sm:grid-cols-4 gap-2 items-end bg-base-200/50 rounded-lg p-2.5">
                <label className="block sm:col-span-2">
                  <span className="text-[10px] text-base-content/50 block mb-0.5">Name</span>
                  <input type="text" value={l.name} placeholder={`Expense ${i + 1} (e.g. college)`}
                    onChange={(e) => setLumps(prev => prev.map(x => x.id === l.id ? { ...x, name: e.target.value } : x))}
                    className="input input-bordered input-sm w-full bg-base-200" />
                </label>
                <label className="block">
                  <span className="text-[10px] text-base-content/50 block mb-0.5">Amount (today's $)</span>
                  <input type="text" inputMode="decimal" value={l.amount} placeholder="100,000"
                    onChange={(e) => setLumps(prev => prev.map(x => x.id === l.id ? { ...x, amount: e.target.value } : x))}
                    className="input input-bordered input-sm w-full bg-base-200" />
                </label>
                <div className="flex gap-2 items-end">
                  <label className="block flex-1">
                    <span className="text-[10px] text-base-content/50 block mb-0.5">At age</span>
                    <input type="text" inputMode="numeric" value={l.age}
                      onChange={(e) => setLumps(prev => prev.map(x => x.id === l.id ? { ...x, age: e.target.value } : x))}
                      className="input input-bordered input-sm w-full bg-base-200" />
                  </label>
                  <button type="button" title="Remove"
                    onClick={() => setLumps(prev => prev.filter(x => x.id !== l.id))}
                    className="btn btn-ghost btn-sm btn-square text-base-content/40 hover:text-error">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* ── Income sources ── */}
      <Section
        step={5}
        icon={<Coins className="w-4 h-4" />}
        title="Other income in retirement"
        subtitle="Pension, rental, part-time work… anything besides Social Security and the portfolio"
        right={
          <button type="button" className="btn btn-xs btn-secondary gap-1"
            onClick={() => setIncomes(prev => [...prev, {
              id: rowIdSeq++, name: '', annual: '', startAge: retireAge, endAge: planAge, growthPct: '0',
            }])}>
            <Plus className="w-3 h-3" /> Add source
          </button>
        }
      >
        {incomes.length === 0 ? (
          <p className="text-xs text-base-content/40">No additional income sources — add one if you expect a pension, rent, annuity or part-time work.</p>
        ) : (
          <div className="space-y-2">
            {incomes.map((s, i) => (
              <div key={s.id} className="grid grid-cols-2 sm:grid-cols-6 gap-2 items-end bg-base-200/50 rounded-lg p-2.5">
                <label className="block sm:col-span-2">
                  <span className="text-[10px] text-base-content/50 block mb-0.5">Name</span>
                  <input type="text" value={s.name} placeholder={`Income ${i + 1}`}
                    onChange={(e) => setIncomes(prev => prev.map(x => x.id === s.id ? { ...x, name: e.target.value } : x))}
                    className="input input-bordered input-sm w-full bg-base-200" />
                </label>
                <label className="block">
                  <span className="text-[10px] text-base-content/50 block mb-0.5">$ / year</span>
                  <input type="text" inputMode="decimal" value={s.annual} placeholder="12,000"
                    onChange={(e) => setIncomes(prev => prev.map(x => x.id === s.id ? { ...x, annual: e.target.value } : x))}
                    className="input input-bordered input-sm w-full bg-base-200" />
                </label>
                <label className="block">
                  <span className="text-[10px] text-base-content/50 block mb-0.5">Start age (≥ {num(retireAge) || 'ret.'})</span>
                  <input type="text" inputMode="numeric" value={s.startAge}
                    onChange={(e) => setIncomes(prev => prev.map(x => x.id === s.id ? { ...x, startAge: e.target.value } : x))}
                    className="input input-bordered input-sm w-full bg-base-200" />
                </label>
                <label className="block">
                  <span className="text-[10px] text-base-content/50 block mb-0.5">End age</span>
                  <input type="text" inputMode="numeric" value={s.endAge}
                    onChange={(e) => setIncomes(prev => prev.map(x => x.id === s.id ? { ...x, endAge: e.target.value } : x))}
                    className="input input-bordered input-sm w-full bg-base-200" />
                </label>
                <div className="flex gap-2 items-end">
                  <label className="block flex-1">
                    <span className="text-[10px] text-base-content/50 block mb-0.5">±% / yr</span>
                    <input type="text" inputMode="decimal" value={s.growthPct}
                      onChange={(e) => setIncomes(prev => prev.map(x => x.id === s.id ? { ...x, growthPct: e.target.value } : x))}
                      className="input input-bordered input-sm w-full bg-base-200" />
                  </label>
                  <button type="button" title="Remove"
                    onClick={() => setIncomes(prev => prev.filter(x => x.id !== s.id))}
                    className="btn btn-ghost btn-sm btn-square text-base-content/40 hover:text-error">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* ── Social Security ── */}
      <Section step={6} icon={<Landmark className="w-4 h-4" />} title="Social Security"
        subtitle="COLA is applied automatically from each simulated year's inflation (never negative)"
        toggle={{
          label: <span className="flex items-center gap-1"><Users className="w-3.5 h-3.5" />Include spouse</span>,
          checked: spouseEnabled, onChange: setSpouseEnabled,
        }}
      >
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 max-w-2xl">
          <Field
            label={spouseEnabled ? 'Your benefit at FRA (67)' : 'Benefit at FRA (age 67)'}
            value={ssFraStr} onChange={setSsFraStr} prefix="$" suffix="/yr"
            placeholder="36,000"
            hint="Today's dollars — from your statement at ssa.gov/myaccount"
          />
          <label className="block">
            <span className="text-xs font-medium text-base-content/70 mb-1 block">{spouseEnabled ? 'Your claiming age' : 'Claiming age'}</span>
            <select value={ssClaimAge} onChange={(e) => setSsClaimAge(Number(e.target.value))}
              className="select select-bordered w-full bg-base-200">
              {SS_CLAIM_AGES.map(a => (
                <option key={a} value={a}>{a} — {Math.round(SS_CLAIM_FACTORS[a] * 100)}% of FRA</option>
              ))}
            </select>
            <span className="text-[10px] text-base-content/40 mt-0.5 block">62 = −30%, 70 = +24% (born 1960+)</span>
          </label>
          <div className="flex flex-col justify-end pb-0.5">
            <span className="text-xs text-base-content/50">{spouseEnabled ? 'Household at both claims' : `Benefit at ${ssClaimAge} (today's $)`}</span>
            <span className="text-lg font-bold">
              {fmtMoney(
                num(ssFraStr) * (SS_CLAIM_FACTORS[ssClaimAge] ?? 1)
                + (spouseEnabled ? num(spouseFraStr) * (SS_CLAIM_FACTORS[spouseClaimAge] ?? 1) : 0),
              )}<span className="text-xs font-normal text-base-content/40">/yr</span>
            </span>
            <span className="text-[10px] text-base-content/40">
              ≈ {fmtMoney(
                num(ssFraStr) * (SS_CLAIM_FACTORS[ssClaimAge] ?? 1)
                * Math.pow(1 + baseInflPct / 100, Math.max(0, ssClaimAge - num(currentAge, 55))),
              )} nominal in your first claim year after COLA (~{baseInflPct.toFixed(1)}%/yr), then COLA every year
            </span>
          </div>
        </div>

        {spouseEnabled && (
          <div className="mt-3 bg-base-200/50 rounded-lg p-3">
            <div className="text-xs font-bold mb-2 flex items-center gap-1.5">
              <Users className="w-3.5 h-3.5 text-secondary" /> Spouse
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
              <Field small label="Current age" value={spouseAgeStr} onChange={setSpouseAgeStr} placeholder="53" />
              <Field small label="Plan to age" value={spousePlanAgeStr} onChange={setSpousePlanAgeStr} placeholder="95"
                hint="Their own timeline" />
              <Field small label="Benefit at FRA (67)" value={spouseFraStr} onChange={setSpouseFraStr} prefix="$" placeholder="24,000" />
              <label className="block">
                <span className="text-xs font-medium text-base-content/70 mb-1 block">Claiming age</span>
                <select value={spouseClaimAge} onChange={(e) => setSpouseClaimAge(Number(e.target.value))}
                  className="select select-bordered select-sm w-full bg-base-200">
                  {SS_CLAIM_AGES.map(a => (
                    <option key={a} value={a}>{a} — {Math.round(SS_CLAIM_FACTORS[a] * 100)}%</option>
                  ))}
                </select>
              </label>
              <Field small label="Survivor expenses" value={survivorPctStr} onChange={setSurvivorPctStr} suffix="%"
                hint="Household spend after the first death" />
            </div>
            <p className="text-[10px] text-base-content/40 mt-2">
              The simulation runs until the last survivor's plan-to age. After the first death: the survivor keeps
              the <span className="font-semibold">larger</span> of the two benefits (survivor step-down), household
              expenses drop to {num(survivorPctStr, 80)}%, and — in tax mode — filing switches MFJ → single
              (the "widow's tax cliff"), which the Roth optimizer takes into account.
            </p>
          </div>
        )}
      </Section>

      {/* ── Assumptions ── */}
      <Section
        step={7}
        icon={<TrendingUp className="w-4 h-4" />}
        title="Market & inflation assumptions"
        subtitle="Pick the slice of history to resample — and independently override returns and/or inflation with your own numbers"
      >
        <div className="space-y-4">
          {/* Dual-thumb window slider */}
          <div>
            <div className="flex items-center justify-between flex-wrap gap-2 mb-1">
              <span className="text-xs font-medium text-base-content/70">
                Historical window: <span className="font-bold text-base-content">{windowStart} – {windowEnd}</span>
              </span>
              <div className="flex items-center gap-2">
                <span className={`badge badge-sm ${windowInfo.yearsUsed < HIST_MIN_WINDOW ? 'badge-error' : 'bg-base-300/60 border-base-300 text-base-content/60'}`}>
                  {windowInfo.yearsUsed} usable years{excludedYears.length > 0 && ` (${excludedYears.length} excluded)`}
                </span>
                {excludedYears.length > 0 && (
                  <button type="button" className="btn btn-ghost btn-xs" onClick={() => setExcludedYears([])}>Reset exclusions</button>
                )}
              </div>
            </div>
            <DualRange min={HIST_START} max={HIST_END} lo={windowStart} hi={windowEnd} minGap={HIST_MIN_WINDOW - 1}
              onChange={(lo, hi) => {
                setWindowStart(lo); setWindowEnd(hi);
                setExcludedYears(prev => prev.filter(y => y >= lo && y <= hi));
              }} />
            <div className="flex justify-between text-[10px] text-base-content/40 mt-0.5">
              <span>{HIST_START}</span>
              <span>drag either end — drop old decades or the newest years</span>
              <span>{HIST_END}</span>
            </div>
            {(returnsMode === 'manual' || inflationMode === 'manual') && (
              <p className="text-[10px] text-amber-400/80 mt-1">
                {returnsMode === 'manual' && inflationMode === 'manual'
                  ? 'Both series are fixed below — this window is not used at all.'
                  : returnsMode === 'manual'
                  ? 'Returns are fixed below — this window only feeds the inflation samples.'
                  : 'Inflation is fixed below — this window only feeds the return samples.'}
              </p>
            )}
          </div>

          {/* Extreme-year chips: stocks & bonds, 7 best + 3 worst each */}
          <div className="space-y-2">
            {([
              ['Stocks', windowInfo.stockChips, 1],
              ['Bonds', windowInfo.bondChips, 2],
            ] as const).map(([label, chips, idx]) => (
              <div key={label}>
                <span className="text-[10px] text-base-content/40 block mb-1.5">
                  <span className={`font-semibold ${label === 'Stocks' ? 'text-sky-400/80' : 'text-amber-400/80'}`}>{label}</span>
                  {' '}— 7 best & 3 worst years in this window{label === 'Stocks' ? ' (plus the latest)' : ''} — click to exclude/include:
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {chips.map(h => {
                    const excluded = excludedYears.includes(h[0]);
                    const stkV = idx === 1 && uiStockComposition ? compositeStockForYear(h[0], uiStockComposition) : h[1];
                    const v = idx === 1 ? stkV : h[idx];
                    return (
                      <button key={h[0]} type="button" onClick={() => toggleYear(h[0])}
                        title={`Stocks ${stkV > 0 ? '+' : ''}${stkV.toFixed(1)}% · Bonds ${h[2] > 0 ? '+' : ''}${h[2].toFixed(1)}% · CPI ${h[3].toFixed(1)}% — excluding a year removes it from BOTH rows`}
                        className={`badge gap-1 cursor-pointer transition-all border ${excluded
                          ? 'bg-base-300/40 text-base-content/30 border-base-300 line-through'
                          : v >= 0
                            ? 'bg-emerald-400/10 text-emerald-400 border-emerald-400/25'
                            : 'bg-rose-400/10 text-rose-400 border-rose-400/25'}`}>
                        {h[0]}{h[0] === windowInfo.latestYear && label === 'Stocks' ? '·latest' : ''} {v > 0 ? '+' : ''}{v.toFixed(0)}%
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>

          {/* Historical series chart */}
          <div className="bg-base-200/30 rounded-lg p-3 border border-base-300/40">
            <h4 className="text-xs font-bold mb-2">Annual returns & inflation in your window (excluded years grayed)</h4>
            <HistoryChart start={windowStart} end={windowEnd} excluded={excludedYears} comp={uiStockComposition} />
          </div>

          {/* Window means + independent manual overrides */}
          <div className="flex flex-wrap gap-2 text-[10px]">
            <span className="badge badge-sm bg-sky-400/10 text-sky-400 border-sky-400/20">Stocks avg {windowInfo.stock.toFixed(1)}%</span>
            <span className="badge badge-sm bg-amber-400/10 text-amber-400 border-amber-400/20">Bonds avg {windowInfo.bond.toFixed(1)}%</span>
            <span className="badge badge-sm bg-rose-400/10 text-rose-400 border-rose-400/20">CPI avg {windowInfo.cpi.toFixed(1)}%</span>
            <span className="badge badge-sm bg-emerald-400/10 text-emerald-400 border-emerald-400/20">Your mix avg {windowInfo.blended.toFixed(1)}%</span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="bg-base-200/40 rounded-lg p-3 border border-base-300/40">
              <span className="text-xs font-bold block mb-1.5">Returns</span>
              <div className="join w-full mb-2">
                <button type="button" onClick={() => setReturnsMode('historical')} className={`btn btn-xs join-item flex-1 ${returnsMode === 'historical' ? 'btn-secondary' : 'btn-ghost'}`}>Historical</button>
                <button type="button" onClick={() => setReturnsMode('manual')} className={`btn btn-xs join-item flex-1 ${returnsMode === 'manual' ? 'btn-secondary' : 'btn-ghost'}`}>I'll provide</button>
              </div>
              {returnsMode === 'manual' ? (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    <Field small label="Stocks" value={manualStockStr} onChange={setManualStockStr} suffix="%" placeholder="8" />
                    <Field small label="Bonds" value={manualBondStr} onChange={setManualBondStr} suffix="%" placeholder="4.5" />
                  </div>
                  <p className="text-[10px] text-base-content/40 mt-1.5">Applied as fixed returns every single year — no market randomness.</p>
                </>
              ) : (
                <p className="text-[10px] text-base-content/40">Resamples the window's actual yearly returns (avg {windowInfo.stock.toFixed(1)}% / {windowInfo.bond.toFixed(1)}%).</p>
              )}
            </div>
            <div className="bg-base-200/40 rounded-lg p-3 border border-base-300/40">
              <span className="text-xs font-bold block mb-1.5">Inflation</span>
              <div className="join w-full mb-2">
                <button type="button" onClick={() => setInflationMode('historical')} className={`btn btn-xs join-item flex-1 ${inflationMode === 'historical' ? 'btn-secondary' : 'btn-ghost'}`}>Historical</button>
                <button type="button" onClick={() => setInflationMode('manual')} className={`btn btn-xs join-item flex-1 ${inflationMode === 'manual' ? 'btn-secondary' : 'btn-ghost'}`}>I'll provide</button>
              </div>
              {inflationMode === 'manual' ? (
                <>
                  <div className="max-w-[140px]">
                    <Field small label="Fixed CPI" value={manualInflStr} onChange={setManualInflStr} suffix="%" placeholder="3" />
                  </div>
                  <p className="text-[10px] text-base-content/40 mt-1.5">Applied as fixed inflation every single year.</p>
                </>
              ) : (
                <p className="text-[10px] text-base-content/40">Resamples the window's actual yearly CPI (avg {windowInfo.cpi.toFixed(1)}%).</p>
              )}
            </div>
            <div className="bg-base-200/40 rounded-lg p-3 border border-base-300/40">
              <span className="text-xs font-bold block mb-2">Cash yields</span>
              <div className="grid grid-cols-2 gap-2">
                <Field small label="Stock dividends" value={divYieldStr} onChange={setDivYieldStr} suffix="%" placeholder="1" />
                <Field small label="Bond interest" value={couponStr} onChange={setCouponStr} suffix="%" placeholder="4" />
              </div>
              <span className="text-[10px] text-base-content/40 mt-1 block">
                Paid as cash from the taxable account. Dividends are qualified (capital-gains rates yearly);
                bond interest is ordinary income yearly.
              </span>
            </div>
            <div className="bg-base-200/40 rounded-lg p-3 border border-base-300/40">
              <span className="text-xs font-bold block mb-2">Simulations</span>
              <select value={numSims} onChange={(e) => setNumSims(Number(e.target.value))}
                className="select select-bordered select-sm w-full bg-base-200">
                {[500, 1000, 2500, 5000, 10000].map(n => <option key={n} value={n}>{n.toLocaleString()}</option>)}
              </select>
              <span className="text-[10px] text-base-content/40 mt-1 block">
                {returnsMode === 'manual' && inflationMode === 'manual' && (!ltcEnabled || num(ltcProbStr, 50) >= 100)
                  ? 'Both series fixed and no LTC randomness → a single deterministic projection runs instead of Monte Carlo.'
                  : 'Randomness comes from whichever series stays historical (and LTC events below 100% chance).'}
              </span>
            </div>
          </div>

          {/* ── Optional equity-index composition ── */}
          {returnsMode !== 'manual' && (
            <div className="mt-4 bg-base-200/40 rounded-lg p-3 border border-base-300/40">
              <div className="flex flex-wrap items-start justify-between gap-2 mb-1">
                <div className="min-w-[200px] flex-1">
                  <span className="text-xs font-bold">Stock composition (optional)</span>
                  <p className="text-[10px] text-base-content/40 mt-0.5">
                    By default your stock sleeve tracks the S&amp;P 500. Pick a custom index mix and each sampled
                    year's stock return becomes the weight-averaged blend of these indices' <em>actual</em> historical
                    returns — so the projection reflects what you really hold.
                  </p>
                </div>
                <div className="join">
                  <button type="button" onClick={() => setStockCompMode('sp500')}
                    className={`btn btn-xs join-item ${stockCompMode === 'sp500' ? 'btn-secondary' : 'btn-ghost'}`}>S&amp;P 500</button>
                  <button type="button" onClick={() => setStockCompMode('custom')}
                    className={`btn btn-xs join-item ${stockCompMode === 'custom' ? 'btn-secondary' : 'btn-ghost'}`}>Custom mix</button>
                </div>
              </div>
              {stockCompMode === 'custom' && (
                <>
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2 mt-2">
                    {STOCK_INDICES.map(ix => (
                      <div key={ix.id}>
                        <Field small label={ix.short} value={stockWeights[ix.id]} suffix="%"
                          onChange={(v) => setStockWeights(w => ({ ...w, [ix.id]: v }))} />
                        <span className="text-[10px] text-base-content/40 block mt-0.5">
                          avg {compStats.perIndex[ix.id].toFixed(1)}%/yr
                        </span>
                      </div>
                    ))}
                  </div>
                  <p className="text-[11px] text-base-content/60 mt-2">
                    Blended stock return in this window:{' '}
                    <span className="font-bold text-base-content">{compStats.blended.toFixed(1)}%/yr</span>{' '}
                    <span className="text-base-content/40">
                      (vs {compStats.perIndex.sp500.toFixed(1)}% for the S&amp;P 500 alone).{' '}
                      {compStats.total <= 0
                        ? 'Enter at least one weight — falling back to 100% S&P 500.'
                        : 'Weights are normalized, so they need not sum to 100.'}
                    </span>
                  </p>
                </>
              )}
            </div>
          )}
        </div>
      </Section>

      {/* ── Taxes ── */}
      <Section
        step={8}
        icon={<Percent className="w-4 h-4" />}
        title="Tax-aware planning (optional)"
        subtitle={`Federal 2026 brackets (CPI-indexed in-sim), RMDs, SS taxation, LTCG, NIIT, IRMAA${stateCode && stateTaxInfo ? ` + ${STATE_NAMES[stateCode]} state tax` : ' — add a ZIP for state tax'}`}
        toggle={{ label: 'Model taxes', checked: taxEnabled, onChange: setTaxEnabled }}
      >
        {!taxEnabled ? (
          <p className="text-xs text-base-content/40">
            Off — the simulation treats the corpus as a single pot with no tax drag.
            Turn on to split the corpus by account type and model RMDs, bracket taxes, NIIT, IRMAA
            surcharges and withdrawal ordering (taxable → traditional → Roth).
          </p>
        ) : (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <label className="block">
                <span className="text-xs font-medium text-base-content/70 mb-1 block">Filing status</span>
                <select value={filing} onChange={(e) => setFiling(e.target.value as 'single' | 'mfj')}
                  className="select select-bordered w-full bg-base-200">
                  <option value="mfj">Married filing jointly</option>
                  <option value="single">Single</option>
                </select>
                <span className="text-[10px] text-base-content/40 mt-0.5 block">
                  Account balances live in the Corpus section; RMDs start at age {rmdAge}.
                </span>
              </label>
              <Field small label="Unrealized gain — stocks" value={stockGainStr} onChange={setStockGainStr} suffix="%"
                placeholder="30" hint="% of taxable stock value that is gain" />
              <Field small label="Unrealized gain — bonds" value={bondGainStr} onChange={setBondGainStr} suffix="%"
                placeholder="0" hint="Usually near 0" />
            </div>
            <p className="text-[10px] text-base-content/40 mt-2">
              Unrealized gains set your cost basis today (30% ⇒ basis is 70% of value) — sales are taxed on the
              gain slice only, and long-held portfolios often carry 30–60%. Growth accrues on top from here.
            </p>

            {/* How taxes are computed */}
            <button type="button" onClick={() => setShowTaxHelp(v => !v)}
              className="mt-3 text-[11px] text-secondary flex items-center gap-1.5">
              <Info className="w-3.5 h-3.5" />
              How taxes are computed {showTaxHelp ? '▾' : '▸'}
            </button>
            {showTaxHelp && (
              <div className="mt-2 text-[11px] text-base-content/60 bg-base-200/50 rounded-lg p-3 space-y-1.5">
                <p><span className="font-semibold text-base-content/80">Each simulated year:</span></p>
                <p>1. <span className="font-semibold">Rebalance</span> the taxable account to its target mix — selling appreciated stock realizes long-term capital gains.</p>
                <p>2. <span className="font-semibold">Cash arrives</span>: outside income, Social Security, qualified dividends ({num(divYieldStr, 1)}% of the taxable stock sleeve, taxed at LTCG rates), bond coupons ({num(couponStr, 4)}% of the bond sleeve, taxed as ordinary income), and any forced RMD.</p>
                <p>3. <span className="font-semibold">Withdrawal waterfall</span> for whatever cash doesn't cover: sell taxable stock, then taxable bonds — each taxed only on its gain slice, tracked against a cost basis seeded from your "unrealized gains at start" (Corpus section) and grown from there → traditional 401k/IRA (100% ordinary income) → Roth (tax-free, last). Down-market strategies can swap the stock/bond sale order. Traditional/Roth withdrawals before 59½ add a <span className="font-semibold">10% penalty</span> (own ledger column).</p>
                <p>4. <span className="font-semibold">Federal</span>: standard deduction, then 2026 brackets (CPI-indexed in-sim — the deduction and every bracket edge grow with each simulated year's inflation) on ordinary income; LTCG + qualified dividends stack on top at 0/15/20%; the taxable share of Social Security uses the provisional-income formula.</p>
                <p>5. <span className="font-semibold">NIIT</span> 3.8% on investment income above $200k/$250k MAGI; <span className="font-semibold">state tax</span> from your ZIP (retirement-income exemptions respected); <span className="font-semibold">IRMAA</span> Medicare surcharges per person 65+ by MAGI tier.</p>
                <p>6. Taxes change how much you must withdraw, which changes taxes — solved iteratively until stable. Every number appears in the year-by-year table below after a run.</p>
              </div>
            )}
          </>
        )}
      </Section>

      {/* ── Long-term care ── */}
      <Section step={9} icon={<HeartPulse className="w-4 h-4" />} title="Long-term care"
        subtitle="Care starts at exactly the onset age you set, for exactly the duration you set — only WHETHER it happens is random (100% = certain event, no randomness)"
        toggle={{ label: 'Model long-term care', checked: ltcEnabled, onChange: setLtcEnabled }}
      >
        {!ltcEnabled ? (
          <p className="text-xs text-base-content/40">
            Off. ~50% of people turning 65 will need paid long-term care; a couple has a very high chance at least
            one partner does. Turn on to stress-test the plan{stateCode ? ` with ${STATE_NAMES[stateCode]} costs` : ' (enter your ZIP for local costs)'}.
          </p>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 items-start">
              <label className="block lg:col-span-2">
                <span className="text-xs font-medium text-base-content/70 mb-1 block">Care setting</span>
                <select value={ltcType} onChange={(e) => { setLtcType(e.target.value as LtcCareType); setLtcCostTouched(false); }}
                  className="select select-bordered select-sm w-full bg-base-200">
                  {(Object.keys(LTC_CARE_LABELS) as LtcCareType[]).map(t => (
                    <option key={t} value={t}>{LTC_CARE_LABELS[t]}</option>
                  ))}
                </select>
                <span className="text-[10px] text-base-content/40 mt-0.5 block">
                  {stateCode ? `${STATE_NAMES[stateCode]} estimate: ${fmtMoney(ltcCostEstimate(stateCode, ltcType))}/yr` : `National median: ${fmtMoney(ltcCostEstimate(null, ltcType))}/yr`}
                </span>
              </label>
              <Field small label="Annual cost" value={ltcCostStr}
                onChange={(v) => { setLtcCostStr(v); setLtcCostTouched(true); }} prefix="$"
                hint="Today's $ — inflates at CPI +1.5pp" />
              <label className="block">
                <span className="text-xs font-medium text-base-content/70 mb-1 block">People to cover</span>
                <select value={ltcPersons} onChange={(e) => setLtcPersons(Number(e.target.value) as 1 | 2)}
                  className="select select-bordered select-sm w-full bg-base-200">
                  <option value={1}>1 (single)</option>
                  <option value={2}>2 (couple)</option>
                </select>
              </label>
            </div>
            <div className="grid grid-cols-3 gap-4 items-start max-w-lg">
              <Field small label="Chance per person" value={ltcProbStr} onChange={setLtcProbStr} suffix="%" hint="100% = certain, no randomness" />
              <Field small label="Onset age" value={ltcOnsetStr} onChange={setLtcOnsetStr} hint="Exact — care starts here" />
              <Field small label="Duration" value={ltcDurStr} onChange={setLtcDurStr} suffix="yr" hint="Exact years of care" />
            </div>
          </div>
        )}
      </Section>

      {/* ── Strategies ── */}
      <Section step={10} icon={<ShieldCheck className="w-4 h-4" />} title="Strategies"
        subtitle="Withdrawal sequencing, Roth conversions and spending behaviors — the Strategy Lab quantifies each one after a run">

        {/* Withdrawal strategy */}
        <div className="mb-3">
          <div className="text-xs font-bold mb-2">Withdrawal strategy</div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
            {([
              ['sequential', '1 · Sequential (default)',
                'Each year: bond interest → dividends → sell stock (LTCG) → sell bonds; taxable first, then 401k, then Roth. Taxable account rebalances to target yearly.'],
              ['guarded', '2 · Down-market guard',
                `Same order, but after a stock year worse than −${num(downThrStr, 5)}%, bonds are sold before stocks so equities aren't dumped at a low.`],
              ['guarded_rebalance', '3 · Guard + up-year rebalance',
                `Down-market guard, plus the taxable account only rebalances back to target after stock years better than +${num(upThrStr, 5)}% (rebalancing sales realize LTCG).`],
              ['holistic', '4 · Holistic household glide',
                `Down-market guard, plus the WHOLE portfolio holds a household target (${weightedStockPct}% stocks now → ${num(holisticPostStr, 40)}% after ${num(glideAgeStr, 65)}): sell stock in taxable, buy it back inside the 401k/Roth — rebalancing is tax-free.`],
            ] as const).map(([id, label, desc]) => (
              <button key={id} type="button" onClick={() => setWithdrawalStrategy(id)}
                className={`text-left rounded-lg p-3 border transition-all ${withdrawalStrategy === id
                  ? 'bg-secondary/10 border-secondary/50'
                  : 'bg-base-200/50 border-base-300/50 hover:border-secondary/30'}`}>
                <div className={`text-xs font-bold ${withdrawalStrategy === id ? 'text-secondary' : ''}`}>{label}</div>
                <div className="text-[11px] text-base-content/50 mt-0.5">{desc}</div>
              </button>
            ))}
          </div>
          {withdrawalStrategy !== 'sequential' && (
            <div className="flex flex-wrap gap-3 mt-2">
              <div className="max-w-[150px]">
                <Field small label="Stock drop trigger" value={downThrStr} onChange={setDownThrStr} suffix="%" placeholder="5"
                  hint="Sell bonds first after a drop this big" />
              </div>
              {withdrawalStrategy === 'guarded_rebalance' && (
                <div className="max-w-[150px]">
                  <Field small label="Rebalance trigger" value={upThrStr} onChange={setUpThrStr} suffix="%" placeholder="5"
                    hint="Rebalance only after gains this big" />
                </div>
              )}
              {withdrawalStrategy === 'holistic' && (
                <>
                  <div className="max-w-[160px]">
                    <Field small label={`Target after ${num(glideAgeStr, 65)}`} value={holisticPostStr} onChange={setHolisticPostStr} suffix="% stk"
                      placeholder="40" hint={`Until then: your Corpus mix (${weightedStockPct}% stocks)`} />
                  </div>
                  <div className="max-w-[130px]">
                    <Field small label="Glide age" value={glideAgeStr} onChange={setGlideAgeStr} placeholder="65"
                      hint="When the target switches" />
                  </div>
                  {!taxEnabled && (
                    <p className="text-[10px] text-amber-400/80 self-end pb-1">
                      Tax-aware planning is off — the glide still applies, but the tax-free re-location benefit needs it on.
                    </p>
                  )}
                </>
              )}
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <label className="flex items-start gap-3 bg-base-200/50 rounded-lg p-3 cursor-pointer">
            <input type="checkbox" className="toggle toggle-secondary toggle-sm mt-0.5" checked={guardrails}
              onChange={(e) => setGuardrails(e.target.checked)} />
            <div>
              <div className="text-xs font-bold">Spending guardrails</div>
              <div className="text-[11px] text-base-content/50">
                Halve discretionary spending (travel, dining, misc — or 20% of a simple budget) for the year after any &gt;10% portfolio decline.
              </div>
            </div>
          </label>
          <div className="flex items-start gap-3 bg-base-200/50 rounded-lg p-3">
            <div className="flex-1">
              <div className="text-xs font-bold">Cash buffer (bucket)</div>
              <div className="text-[11px] text-base-content/50">
                Hold N years of expenses in cash and spend it the year after a decline, so investments aren't sold low.
                Protects the fragile early years — at the cost of some long-run cash drag.
              </div>
            </div>
            <label className="block w-[92px] flex-shrink-0">
              <span className="text-[10px] text-base-content/50 block mb-0.5">Years</span>
              <select value={cashBufferStr} onChange={(e) => setCashBufferStr(e.target.value)}
                className="select select-bordered select-sm w-full bg-base-200">
                {['0', '1', '2', '3', '4', '5'].map(n => <option key={n} value={n}>{n === '0' ? 'Off' : n}</option>)}
              </select>
            </label>
          </div>
          {taxEnabled && num(tradStr) <= 0 && (
            <div className="bg-base-200/50 rounded-lg p-3 flex items-start gap-2">
              <Info className="w-3.5 h-3.5 text-base-content/40 flex-shrink-0 mt-0.5" />
              <div className="text-[11px] text-base-content/50">
                <span className="font-bold text-base-content/70">Roth conversions</span> need a Traditional
                401k / IRA balance to convert. Add one in the <span className="font-semibold">Corpus</span> section
                to unlock the conversion planner and optimizer.
              </div>
            </div>
          )}
          {taxEnabled && num(tradStr) > 0 && (
            <div className="bg-base-200/50 rounded-lg p-3">
              <div className="flex items-start justify-between gap-2 flex-wrap">
                <div>
                  <div className="text-xs font-bold">Roth conversion plan</div>
                  <div className="text-[11px] text-base-content/50">
                    Convert traditional → Roth up to a bracket ceiling — in years you pick, or let the
                    system find the low-tax years and amounts automatically.
                  </div>
                </div>
                <div className="join">
                  {([['off', 'Off'], ['custom', 'My years'], ['auto', 'Auto']] as const).map(([m, lbl]) => (
                    <button key={m} type="button" onClick={() => setRothMode(m)}
                      className={`btn btn-xs join-item ${rothMode === m ? 'btn-secondary' : 'btn-ghost'}`}>{lbl}</button>
                  ))}
                </div>
              </div>
              {rothMode === 'custom' && (
                <div className="grid grid-cols-3 gap-2 mt-2">
                  <label className="block">
                    <span className="text-[10px] text-base-content/50 block mb-0.5">Fill up to bracket</span>
                    <select value={rothCeiling} onChange={(e) => setRothCeiling(Number(e.target.value) as 12 | 22 | 24)}
                      className="select select-bordered select-sm w-full bg-base-200">
                      <option value={12}>12%</option>
                      <option value={22}>22%</option>
                      <option value={24}>24%</option>
                    </select>
                  </label>
                  <label className="block">
                    <span className="text-[10px] text-base-content/50 block mb-0.5">From age</span>
                    <input type="text" inputMode="numeric" value={rothStartStr} placeholder={String(rothDefStart)}
                      onChange={(e) => setRothStartStr(e.target.value)}
                      className="input input-bordered input-sm w-full bg-base-200" />
                  </label>
                  <label className="block">
                    <span className="text-[10px] text-base-content/50 block mb-0.5">To age</span>
                    <input type="text" inputMode="numeric" value={rothEndStr} placeholder={String(rothDefEnd)}
                      onChange={(e) => setRothEndStr(e.target.value)}
                      className="input input-bordered input-sm w-full bg-base-200" />
                  </label>
                </div>
              )}
              {rothMode === 'auto' && (
                <p className="text-[10px] text-base-content/40 mt-2">
                  Each retirement year before RMDs, the system estimates the bracket your future RMDs would
                  force, and converts up to the top of any cheaper bracket — so low-income years (little
                  interest, no SS yet) automatically absorb bigger conversions. Watch it in the "Roth conv."
                  ledger column.
                </p>
              )}
            </div>
          )}
        </div>
      </Section>

      {/* Validation + Run */}
      {errors.length > 0 && (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 space-y-1">
          {errors.map((e, i) => (
            <p key={i} className="text-xs text-rose-400 flex items-center gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />{e}
            </p>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={handleRun} disabled={computing}
          className={`btn btn-secondary gap-2 ${dirty ? 'animate-pulse' : ''}`}>
          {computing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          {computing ? `Running ${numSims.toLocaleString()} simulations…` : dirty ? 'Re-run simulation' : 'Run simulation'}
        </button>
        {dirty && !computing && (
          <span className="text-xs text-amber-400 flex items-center gap-1.5">
            <RefreshCw className="w-3.5 h-3.5" />
            Inputs changed — the results below are from the previous run.
          </span>
        )}
      </div>

      {/* ══════════════════ Results ══════════════════ */}
      {result && ranInputs && charts && (
        <div className={`space-y-6 pt-2 ${dirty ? 'opacity-60' : ''}`}>
          {/* Display-mode toggle */}
          <div className="flex items-center justify-between flex-wrap gap-2">
            <h3 className="text-base font-bold">Results</h3>
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-base-content/40">Show amounts in</span>
              <div className="join">
                <button type="button" onClick={() => setDisplayReal(true)}
                  className={`btn btn-xs join-item ${displayReal ? 'btn-secondary' : 'btn-ghost'}`}>Today's $</button>
                <button type="button" onClick={() => setDisplayReal(false)}
                  className={`btn btn-xs join-item ${!displayReal ? 'btn-secondary' : 'btn-ghost'}`}>Future $</button>
              </div>
            </div>
          </div>

          {/* Success hero */}
          <div className={`rounded-2xl p-5 border ${successTone.bg} ${successTone.border}`}>
            <div className="flex flex-col md:flex-row md:items-center gap-5">
              <div className="flex items-center gap-4">
                {success >= 75
                  ? <CheckCircle2 className={`w-10 h-10 ${successTone.text}`} />
                  : <AlertTriangle className={`w-10 h-10 ${successTone.text}`} />}
                <div>
                  <div className="text-xs uppercase tracking-wide text-base-content/50 font-semibold">Plan success rate</div>
                  <div className={`text-4xl font-extrabold leading-tight ${successTone.text}`}>{success.toFixed(1)}%</div>
                  <div className="text-[11px] text-base-content/50">
                    {result.deterministic
                      ? `deterministic projection with your fixed returns/inflation — solvent to age ${ranInputs.planAge} or not`
                      : `of ${ranInputs.numSims.toLocaleString()} simulated lifetimes stayed solvent to age ${ranInputs.planAge}`}
                  </div>
                </div>
              </div>
              <div className="flex-1 grid grid-cols-2 xl:grid-cols-4 gap-2.5 md:border-l md:border-base-300/40 md:pl-5">
                {([
                  {
                    label: 'Median ending',
                    value: fmtCompact(money(result.medianEnding.nominal, result.medianEnding.real)),
                    sub: '',
                  },
                  {
                    label: 'Corpus at retirement',
                    value: fmtCompact(money(result.corpusAtRetirementMedian.nominal, result.corpusAtRetirementMedian.real)),
                    sub: '',
                  },
                  {
                    label: 'If it fails',
                    value: result.medianDepletionAge !== null ? `age ${Math.round(result.medianDepletionAge)}` : 'never',
                    sub: result.medianDepletionAge !== null ? `${failShare.toFixed(0)}% of paths` : '',
                  },
                  ranInputs.taxEnabled
                    ? {
                        label: 'Lifetime taxes',
                        value: fmtCompact(result.medianTotalTaxes),
                        sub: '',
                      }
                    : {
                        label: 'Withdrawal rate',
                        value: result.corpusAtRetirementMedian.nominal > 0
                          ? fmtPct(result.firstYearExpensesMedian / result.corpusAtRetirementMedian.nominal)
                          : '—',
                        sub: 'first year',
                      },
                ] as const).map((m) => (
                  <div key={m.label} className="bg-base-100/40 rounded-lg px-3 py-2 min-w-0">
                    <div className="text-[10px] text-base-content/50 uppercase tracking-wide leading-tight">{m.label}</div>
                    <div className="text-lg font-bold leading-tight break-words">{m.value}</div>
                    {m.sub && <div className="text-[10px] text-base-content/40 leading-tight">{m.sub}</div>}
                  </div>
                ))}
              </div>
            </div>
            <div className="flex flex-wrap gap-2 mt-4 pt-3 border-t border-base-300/30 text-[10px]">
              <span className="badge badge-sm bg-base-300/40 border-base-300 text-base-content/60">
                History {ranInputs.windowStart}–{ranInputs.windowEnd}
                {ranInputs.returnsMode === 'manual' && ` · returns recentered to ${ranInputs.manualStockPct}%/${ranInputs.manualBondPct}%`}
                {ranInputs.inflationMode === 'manual' && ` · CPI recentered to ${ranInputs.manualInflationPct}%`}
              </span>
              <span className="badge badge-sm bg-base-300/40 border-base-300 text-base-content/60">
                Mix {result.windowStats.effectiveStockPct}/{100 - result.windowStats.effectiveStockPct} → avg {result.windowStats.portfolioMean.toFixed(1)}%/yr
              </span>
              {ranInputs.stockComposition && (
                <span className="badge badge-sm bg-violet-400/10 border-violet-400/20 text-violet-300">
                  Stock mix {STOCK_INDICES.filter(x => (ranInputs.stockComposition![x.id] ?? 0) > 0).map(x => x.short).join(' / ')} → avg {result.windowStats.stockMean.toFixed(1)}%/yr
                </span>
              )}
              {ranInputs.taxEnabled && (
                <span className="badge badge-sm bg-base-300/40 border-base-300 text-base-content/60">
                  {ranInputs.stateCode && STATE_TAX[ranInputs.stateCode]
                    ? `${STATE_NAMES[ranInputs.stateCode]} tax ${STATE_TAX[ranInputs.stateCode].rate === 0 ? '0%' : `~${STATE_TAX[ranInputs.stateCode].rate}%`}`
                    : 'no state tax'} · NIIT · IRMAA
                </span>
              )}
              {ranInputs.spouse.enabled && (
                <span className="badge badge-sm bg-sky-400/10 border-sky-400/20 text-sky-400">
                  Spouse SS + survivor step-down · to last survivor
                </span>
              )}
              {ranInputs.phases && <span className="badge badge-sm bg-emerald-400/10 border-emerald-400/20 text-emerald-400">Spending smile</span>}
              {ranInputs.guardrails && <span className="badge badge-sm bg-emerald-400/10 border-emerald-400/20 text-emerald-400">Guardrails</span>}
              {ranInputs.withdrawalStrategy !== 'sequential' && (
                <span className="badge badge-sm bg-sky-400/10 border-sky-400/20 text-sky-400">
                  {ranInputs.withdrawalStrategy === 'guarded'
                    ? `Down-market guard (−${ranInputs.downThresholdPct}%)`
                    : ranInputs.withdrawalStrategy === 'guarded_rebalance'
                    ? `Guard −${ranInputs.downThresholdPct}% + rebalance at +${ranInputs.upThresholdPct}%`
                    : `Holistic glide: ${result.windowStats.effectiveStockPct}% → ${ranInputs.holisticPostStockPct}% stocks at ${ranInputs.glideAge}`}
                </span>
              )}
              {ranInputs.roth.mode !== 'off' && (
                <span className="badge badge-sm bg-emerald-400/10 border-emerald-400/20 text-emerald-400">
                  {ranInputs.roth.mode === 'auto'
                    ? 'Roth conv. auto-optimized'
                    : `Roth conv. ${ranInputs.roth.ceiling}% bracket, ${ranInputs.roth.startAge}–${ranInputs.roth.endAge}`}
                </span>
              )}
              {ranInputs.ltc.enabled && (
                <span className="badge badge-sm bg-rose-400/10 border-rose-400/20 text-rose-400">
                  LTC stress: {fmtCompact(ranInputs.ltc.annualCost)}/yr × {ranInputs.ltc.persons}
                </span>
              )}
              {ranInputs.lumpSums.length > 0 && (
                <span className="badge badge-sm bg-base-300/40 border-base-300 text-base-content/60">
                  {ranInputs.lumpSums.length} one-time expense{ranInputs.lumpSums.length > 1 ? 's' : ''}
                </span>
              )}
              {ranInputs.sequenceReturns && ranInputs.sequenceReturns.length > 0 && (
                <span className="badge badge-sm bg-rose-400/10 border-rose-400/20 text-rose-400">
                  First {ranInputs.sequenceReturns.length} yrs fixed{planSequence ? ` (${planSequence.label})` : ''} · rest Monte Carlo
                </span>
              )}
            </div>
          </div>

          {/* Ending balance percentiles */}
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            {([
              ['Worst 10%', result.endingPercentiles.nominal.p10, result.endingPercentiles.real.p10],
              ['Median', result.endingPercentiles.nominal.p50, result.endingPercentiles.real.p50],
              ['Top 10%', result.endingPercentiles.nominal.p90, result.endingPercentiles.real.p90],
              ['Top 5%', result.endingPercentiles.nominal.p95, result.endingPercentiles.real.p95],
              ['Top 1%', result.endingPercentiles.nominal.p99, result.endingPercentiles.real.p99],
            ] as const).map(([label, nom, real]) => {
              const v = money(nom, real);
              return (
                <div key={label} className="bg-base-200 rounded-xl p-3">
                  <div className="text-[10px] text-base-content/50 uppercase tracking-wide">{label} outcome</div>
                  <div className={`text-base font-bold mt-0.5 ${v <= 0 ? 'text-rose-400' : ''}`}>
                    {v <= 0 ? 'Depleted' : fmtCompact(v)}
                  </div>
                  <div className="text-[10px] text-base-content/40">at {ranInputs.planAge} · {displayReal ? "today's $" : 'future $'}</div>
                </div>
              );
            })}
          </div>

          {/* Fan + survival charts */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="bg-base-200/30 rounded-xl p-4 border border-base-300/40">
              <h3 className="text-sm font-bold mb-1">Portfolio balance percentiles by age</h3>
              <p className="text-[10px] text-base-content/40 mb-3">
                {result.deterministic
                  ? `Single deterministic path (fixed returns & inflation — bands collapse) · ${displayReal ? "today's dollars" : 'nominal dollars'}`
                  : `10th–90th percentile bands of ${ranInputs.numSims.toLocaleString()} paths · ${displayReal ? "today's dollars" : 'nominal dollars'}`}
              </p>
              <div className="h-64"><Line data={charts.fan} options={moneyChartOpts(false)} /></div>
            </div>
            <div className="bg-base-200/30 rounded-xl p-4 border border-base-300/40">
              <h3 className="text-sm font-bold mb-1">Probability the money lasts, by age</h3>
              <p className="text-[10px] text-base-content/40 mb-3">Share of simulations still solvent at each age</p>
              <div className="h-64">
                <Line data={charts.survival} options={{
                  responsive: true, maintainAspectRatio: false,
                  plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: (ctx: TooltipItem<'line'>) => `Survives: ${(ctx.parsed.y ?? 0).toFixed(1)}%` } },
                  },
                  scales: {
                    x: { grid: { display: false }, ticks: { font: CHART_FONT, autoSkip: true, maxTicksLimit: 15, maxRotation: 0 } },
                    y: { min: 0, max: 100, grid: GRID, ticks: { font: CHART_FONT, callback: (v: number | string) => `${v}%` } },
                  },
                }} />
              </div>
            </div>
          </div>

          {/* Cashflow composition */}
          <div className="bg-base-200/30 rounded-xl p-4 border border-base-300/40">
            <h3 className="text-sm font-bold mb-1">Where each retirement year's money comes from</h3>
            <p className="text-[10px] text-base-content/40 mb-3">
              One real simulated path whose outcome landed on the median — so the bars exactly cover the line.
              Years where bars exceed the line are surpluses (income &gt; spending), reinvested into the portfolio.
              {displayReal ? " Shown in today's dollars." : ' Shown in future (nominal) dollars.'}
            </p>
            <div className="h-72">
              <Chart type="bar" data={charts.cashflow} options={moneyChartOpts(true)} />
            </div>
          </div>

          {/* Account composition (tax mode) */}
          {charts.accounts && (
            <div className="bg-base-200/30 rounded-xl p-4 border border-base-300/40">
              <h3 className="text-sm font-bold mb-1">Account balances over time (median-outcome path)</h3>
              <p className="text-[10px] text-base-content/40 mb-3">
                Withdrawal order: taxable → traditional (RMDs forced) → Roth last · {displayReal ? "today's dollars" : 'nominal'}
              </p>
              <div className="h-64"><Line data={charts.accounts} options={moneyChartOpts(true)} /></div>
            </div>
          )}

          {/* Market assumptions actually used — only sampled series get a history chart */}
          <div className="bg-base-200/30 rounded-xl p-4 border border-base-300/40">
            <h3 className="text-sm font-bold mb-1">
              {ranInputs.returnsMode === 'manual' && ranInputs.inflationMode === 'manual'
                ? 'The fixed assumptions behind these results'
                : 'The market history behind these results'}
            </h3>
            {ranInputs.returnsMode === 'manual' && ranInputs.inflationMode === 'manual' ? (
              <>
                <p className="text-[10px] text-base-content/40 mb-3">
                  No market history was sampled — you provided every series, so each year uses exactly these values
                  and the projection is deterministic.
                </p>
                <div className="grid grid-cols-3 gap-3 max-w-xl">
                  <div className="bg-base-200/50 rounded-lg p-3 text-center">
                    <div className="text-[10px] text-sky-400 uppercase tracking-wide">Stocks</div>
                    <div className="text-xl font-bold">{ranInputs.manualStockPct}%</div>
                    <div className="text-[10px] text-base-content/40">every year</div>
                  </div>
                  <div className="bg-base-200/50 rounded-lg p-3 text-center">
                    <div className="text-[10px] text-amber-400 uppercase tracking-wide">Bonds</div>
                    <div className="text-xl font-bold">{ranInputs.manualBondPct}%</div>
                    <div className="text-[10px] text-base-content/40">every year</div>
                  </div>
                  <div className="bg-base-200/50 rounded-lg p-3 text-center">
                    <div className="text-[10px] text-rose-400 uppercase tracking-wide">Inflation</div>
                    <div className="text-xl font-bold">{ranInputs.manualInflationPct}%</div>
                    <div className="text-[10px] text-base-content/40">every year</div>
                  </div>
                </div>
              </>
            ) : (
              <>
                <p className="text-[10px] text-base-content/40 mb-3">
                  {ranInputs.returnsMode === 'manual'
                    ? `Only inflation is sampled from these ${result.windowStats.yearsUsed} years (at random, with replacement) — returns are fixed at your ${ranInputs.manualStockPct}% stocks / ${ranInputs.manualBondPct}% bonds every year, so only the sampled series is charted.`
                    : ranInputs.inflationMode === 'manual'
                    ? `Only returns are sampled from these ${result.windowStats.yearsUsed} years (at random, with replacement, keeping each year's stock and bond returns together) — inflation is fixed at your ${ranInputs.manualInflationPct}% every year.`
                    : `Each simulated year draws one of these ${result.windowStats.yearsUsed} years at random (with replacement), keeping that year's stock return, bond return and inflation together.`}
                </p>
                <HistoryChart
                  start={ranInputs.windowStart} end={ranInputs.windowEnd} excluded={ranInputs.excludedYears}
                  height="h-52"
                  comp={ranInputs.stockComposition}
                  series={{
                    stocks: ranInputs.returnsMode !== 'manual',
                    bonds: ranInputs.returnsMode !== 'manual',
                    cpi: ranInputs.inflationMode !== 'manual',
                  }}
                />
              </>
            )}
            <div className="flex flex-wrap gap-2 mt-3 text-[10px]">
              <span className="badge badge-sm bg-sky-400/10 text-sky-400 border-sky-400/20">
                Stocks {ranInputs.returnsMode === 'manual' ? `fixed ${ranInputs.manualStockPct}%` : `avg ${result.windowStats.stockMean.toFixed(1)}%`}
              </span>
              <span className="badge badge-sm bg-amber-400/10 text-amber-400 border-amber-400/20">
                Bonds {ranInputs.returnsMode === 'manual' ? `fixed ${ranInputs.manualBondPct}%` : `avg ${result.windowStats.bondMean.toFixed(1)}%`}
              </span>
              <span className="badge badge-sm bg-rose-400/10 text-rose-400 border-rose-400/20">
                CPI {ranInputs.inflationMode === 'manual' ? `fixed ${ranInputs.manualInflationPct}%` : `avg ${result.windowStats.cpiMean.toFixed(1)}%`}
              </span>
              <span className="badge badge-sm bg-emerald-400/10 text-emerald-400 border-emerald-400/20">
                Your {result.windowStats.effectiveStockPct}/{100 - result.windowStats.effectiveStockPct} mix avg {result.windowStats.portfolioMean.toFixed(1)}%/yr
              </span>
            </div>
          </div>

          {/* Year-by-year ledger */}
          <div className="bg-base-200/30 rounded-xl p-4 border border-base-300/40">
            <button type="button" onClick={() => setShowLedger(v => !v)}
              className="flex items-center gap-2 text-sm font-bold w-full text-left">
              <Table className="w-4 h-4 text-secondary" />
              Year-by-year detail (median-outcome path)
              <span className="text-[10px] font-normal text-base-content/40 ml-auto">{showLedger ? 'hide' : 'show'}</span>
            </button>
            {showLedger && (
              <div className="mt-3">
                {ranInputs.taxEnabled && (
                  <div className="flex items-center justify-between flex-wrap gap-2 mb-2">
                    <div className="join">
                      {LEDGER_VIEWS.map(v => (
                        <button key={v.id} type="button" onClick={() => setLedgerView(v.id)}
                          className={`btn btn-xs join-item ${ledgerView === v.id ? 'btn-secondary' : 'btn-ghost'}`}>
                          {v.label}
                        </button>
                      ))}
                    </div>
                    <span className="text-[10px] text-base-content/40">
                      {effLedgerView === 'overview' && 'The year at a glance — returns, spending, funding, taxes, ending balance'}
                      {effLedgerView === 'sources' && 'Every dollar that funded the year: cash first, then sales, then account withdrawals'}
                      {effLedgerView === 'taxes' && 'The full tax audit — every input that builds the taxable income, then what each layer charged'}
                      {effLedgerView === 'balances' && 'End-of-year balance in each account'}
                      {effLedgerView === 'all' && 'Every column — scroll sideways; Age stays put'}
                    </span>
                  </div>
                )}
                {effLedgerView === 'taxes' && (
                  <p className="text-[10px] text-base-content/50 bg-base-200/50 rounded-lg px-3 py-2 mb-2">
                    <span className="font-semibold text-base-content/70">How to read this: </span>
                    Taxbl inc. = Income + Int. + RMD + Trad w/d + Conv. + the taxable share of Soc. Sec. − standard
                    deduction, and it picks the ordinary brackets (Marg. = your top bracket) → that's the first part of Fed.
                    <span className="font-semibold text-base-content/70"> Gains + Div. are NOT in Taxbl inc.</span> — they
                    stack on top at capital-gains rates, where the first ≈$97K (MFJ, inflation-indexed) is taxed at
                    <span className="font-semibold text-emerald-400"> 0%</span>. That's why large Gains can produce tiny Fed.
                    State (flat estimate) and NIIT tax the full base with no 0% bracket, so State can exceed Fed.
                  </p>
                )}
                <div className="overflow-x-auto">
                  <table className="table table-xs w-full">
                    <thead>
                      <tr className="text-[10px] text-base-content/50">
                        <th className="sticky left-0 z-10 bg-base-200">Age</th>
                        {visibleLedgerCols.map(c => <th key={c.l}>{c.l}</th>)}
                      </tr>
                    </thead>
                    <tbody className="text-[10px]">
                      {ledgerRows.map(r => (
                        <tr key={r.age} className={`${r.age === ranInputs.retireAge ? 'border-t-2 border-secondary/40' : ''} ${isSeqYear(r.age) ? 'bg-amber-400/5' : ''}`}>
                          <td className="sticky left-0 z-10 bg-base-200 font-semibold">
                            {r.age}
                            {isSeqYear(r.age) && <span className="ml-1 text-amber-400/80" title="Fixed sequence-of-returns year">◆</span>}
                          </td>
                          {visibleLedgerCols.map(c => (
                            <td key={c.l} className={c.cls ? c.cls(r) : undefined}>{c.cell(r)}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="text-[10px] text-base-content/40 mt-2">
                  {result.deterministic
                    ? 'Returns/CPI are your fixed values (no sampling). '
                    : `Returns/CPI columns show what that path sampled — ${ranInputs.returnsMode === 'manual' ? 'returns fixed, ' : ''}${ranInputs.inflationMode === 'manual' ? 'CPI fixed, ' : ''}the rest varies year to year. `}
                  {displayReal
                    ? 'Everything is shown in TODAY\'S dollars: balances grow at the real rate — e.g. a fixed 6% return at 3% inflation appears as ≈2.9%/yr, and a 4% bond account looks nearly flat. Switch to "Future $" (top of results) to see face values compound at the full rate. '
                    : 'Everything is shown in future (nominal) dollars. '}
                  Funding check: Income + Soc.Sec. + Div. + Int. + sales + RMD + Trad/Roth w/d
                  covers Expenses + taxes each year (any excess is reinvested into the taxable account).
                  "Gains" = LTCG realized by rebalancing + spending sales; "Taxbl inc." = ordinary income + taxable SS − standard deduction,
                  which is what dynamically picks the "Marg." bracket that year. Soc. Sec. is COLA-adjusted every year — in today's-$ view it looks
                  flat because COLA ≈ inflation cancels out (switch to Future $ to see it grow). Account columns are end-of-year balances. Retirement year highlighted.
                  {seqLen > 0 && ' Rows marked ◆ are the fixed sequence-of-returns years you baked in — their Stocks/Bonds returns match your selected sequence exactly; only later years vary.'}
                </p>
              </div>
            )}
          </div>

          {/* ── Strategy Lab ── */}
          <div className="bg-base-200/30 rounded-xl p-4 border border-base-300/40">
            <div className="flex items-center justify-between flex-wrap gap-2 mb-1">
              <h3 className="text-sm font-bold flex items-center gap-2">
                <FlaskConical className="w-4 h-4 text-secondary" />
                Strategy Lab
              </h3>
              {!sweeps && (
                <button type="button" onClick={handleSweeps} disabled={sweeping}
                  className="btn btn-xs btn-secondary gap-1.5">
                  {sweeping ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
                  {sweeping ? 'Simulating strategies…' : 'Find my optimal strategies (~5s)'}
                </button>
              )}
            </div>
            <p className="text-[10px] text-base-content/40 mb-3">
              Grouped by lever. Every comparison is simulated on the same random market paths (and the same LTC
              events) as your plan, so differences reflect the strategy — not luck. Apply anything with one click, then re-run.
            </p>

            {!sweeps && !sweeping && (
              <p className="text-xs text-base-content/50">
                Sweeps every stock/bond mix, every Social Security claiming age, spending patterns, and — in tax mode —
                a grid of Roth conversion plans (bracket ceilings × conversion windows) to find the tax-optimal one.
              </p>
            )}
            {sweeping && (
              <div className="flex items-center gap-2 text-xs text-base-content/50 py-6 justify-center">
                <Loader2 className="w-4 h-4 animate-spin" /> Sweeping allocations, claim ages, spending patterns and Roth plans…
              </div>
            )}

            {sweeps && sweepCharts && (
              <div className="space-y-6">

                {/* GROUP: F.I.R.E. — early retirement (pre-retirement only) */}
                {sweeps.fire && sweeps.fire.curve.length > 0 && (() => {
                  const fire = sweeps.fire;
                  const feasible = fire.scenarios.filter(s => s.earliestAge != null);
                  const earliest = feasible.length ? Math.min(...feasible.map(s => s.earliestAge!)) : null;
                  const fireChart = {
                    labels: fire.curve.map(c => String(c.age)),
                    datasets: [{
                      label: 'Success rate',
                      data: fire.curve.map(c => c.success * 100),
                      borderColor: '#fb923c', backgroundColor: '#fb923c22',
                      fill: true, tension: 0.3,
                      pointRadius: fire.curve.map(c => (c.age === fire.plannedAge ? 5 : 2)),
                      pointBackgroundColor: fire.curve.map(c => (c.age === fire.plannedAge ? '#fb7185' : '#fb923c')),
                    }],
                  };
                  return (
                    <LabGroup title="F.I.R.E. — retire early" hint="Financial Independence, Retire Early — how soon you could stop working">
                      <div className="rounded-lg p-3 border border-orange-400/25 bg-orange-500/5">
                        <div className="flex items-center gap-3 mb-2">
                          <Rocket className="w-7 h-7 text-orange-400 flex-shrink-0" />
                          <div>
                            {earliest != null ? (
                              <>
                                <div className="text-sm font-bold text-base-content/90">
                                  You could stop working as early as <span className="text-orange-400">age {earliest}</span>
                                  {earliest < fire.plannedAge && <> — {fire.plannedAge - earliest} {fire.plannedAge - earliest === 1 ? 'year' : 'years'} ahead of your plan</>}.
                                </div>
                                <div className="text-[10px] text-base-content/50">
                                  Earliest age each lever clears a {(fire.targetSuccess * 100).toFixed(0)}% success bar, holding everything else fixed.
                                </div>
                              </>
                            ) : (
                              <div className="text-sm font-bold text-base-content/90">
                                None of these levers reach {(fire.targetSuccess * 100).toFixed(0)}% success before your planned age {fire.plannedAge} —
                                <span className="text-base-content/60 font-normal"> save more, spend less, or extend the horizon.</span>
                              </div>
                            )}
                          </div>
                        </div>
                        <div className="h-36">
                          <Line data={fireChart} options={successChartOpts(Math.min(...fire.curve.map(c => c.success * 100)))} />
                        </div>
                        <p className="text-[10px] text-base-content/40 mt-1">
                          Success rate vs the age you stop working (current plan). Red dot = your planned age {fire.plannedAge}. Retiring earlier means fewer saving years and a longer drawdown.
                        </p>
                      </div>

                      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                        {fire.scenarios.map(sc => {
                          const feas = sc.earliestAge != null;
                          const yrsEarlier = feas ? fire.plannedAge - sc.earliestAge! : 0;
                          const isBest = feas && sc.earliestAge === earliest;
                          return (
                            <div key={sc.id} className={`rounded-lg p-3 border ${isBest ? 'bg-orange-500/5 border-orange-400/30' : 'bg-base-200/40 border-base-300/40'}`}>
                              <div className="flex items-center gap-1.5 mb-1">
                                <h4 className="text-xs font-bold">{sc.label}</h4>
                                {isBest && <span className="badge badge-xs bg-orange-400/15 text-orange-400 border-orange-400/30">earliest</span>}
                              </div>
                              <div className="mb-1">
                                {feas ? (
                                  <>
                                    <span className="text-xl font-extrabold text-base-content/90">age {sc.earliestAge}</span>
                                    <span className="text-[10px] text-base-content/50 ml-1">
                                      {yrsEarlier > 0 ? `${yrsEarlier} yr${yrsEarlier === 1 ? '' : 's'} early` : 'as planned'} · {(sc.successAtEarliest * 100).toFixed(0)}%
                                    </span>
                                  </>
                                ) : (
                                  <span className="text-sm font-bold text-base-content/40">not before {fire.plannedAge}</span>
                                )}
                              </div>
                              <p className="text-[10px] text-base-content/50 mb-2 min-h-[2.5em]">{sc.detail}</p>
                              <button type="button" onClick={() => applyFire(sc)} disabled={!feas}
                                className={`btn btn-xs w-full ${feas ? (isBest ? 'btn-secondary' : 'btn-ghost text-secondary') : 'btn-ghost text-base-content/30'}`}>
                                {feas ? `Retire at ${sc.earliestAge}` : 'Unavailable'}
                              </button>
                            </div>
                          );
                        })}
                      </div>
                      <p className="text-[10px] text-base-content/40">
                        Applying a scenario sets your retirement age{fire.scenarios.some(s => s.apply.stockPct) ? ' (and allocation)' : ''}{fire.scenarios.some(s => s.apply.expenseScale) ? ' and trims spending' : ''}, then re-runs the whole plan — the What-If, year-by-year table and every other strategy update to match.
                      </p>
                    </LabGroup>
                  );
                })()}

                {/* GROUP: Social Security */}
                {(sweepCharts.claim || sweepCharts.spouseClaim) && (
                  <LabGroup title="Social Security" hint="When each of you starts benefits">
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                      {sweepCharts.claim && (
                        <div className="bg-base-200/40 rounded-lg p-3 border border-base-300/40">
                          <div className="flex items-center justify-between mb-2 flex-wrap gap-1">
                            <h4 className="text-xs font-bold">Your claiming age</h4>
                            {sweeps.claimHelps ? (
                              <button type="button"
                                onClick={() => setSsClaimAge(sweeps.bestClaimAge)}
                                className="btn btn-xs btn-secondary">
                                Apply best: claim at {sweeps.bestClaimAge}
                              </button>
                            ) : (
                              <span className="text-[10px] text-emerald-400/80 flex items-center gap-1">
                                <CheckCircle2 className="w-3 h-3" /> Claiming at {ssClaimAge} is already best
                              </span>
                            )}
                          </div>
                          <div className="h-40">
                            <Chart type="bar" data={sweepCharts.claim} options={successChartOpts(Math.min(...sweeps.claimAge.map(p => labAdj.s(p.success) * 100)))} />
                          </div>
                          <p className="text-[10px] text-base-content/40 mt-1">Red bar = your current plan (claim at {ranInputs.ssClaimAge})</p>
                        </div>
                      )}

                      {sweepCharts.spouseClaim && (
                        <div className="bg-base-200/40 rounded-lg p-3 border border-base-300/40">
                          <div className="flex items-center justify-between mb-2 flex-wrap gap-1">
                            <h4 className="text-xs font-bold">Spouse's claiming age</h4>
                            {sweeps.spouseClaimHelps ? (
                              <button type="button"
                                onClick={() => setSpouseClaimAge(sweeps.bestSpouseClaimAge)}
                                className="btn btn-xs btn-secondary">
                                Apply best: spouse claims at {sweeps.bestSpouseClaimAge}
                              </button>
                            ) : (
                              <span className="text-[10px] text-emerald-400/80 flex items-center gap-1">
                                <CheckCircle2 className="w-3 h-3" /> Claiming at {spouseClaimAge} is already best
                              </span>
                            )}
                          </div>
                          <div className="h-40">
                            <Chart type="bar" data={sweepCharts.spouseClaim} options={successChartOpts(Math.min(...sweeps.spouseClaimAge.map(p => labAdj.s(p.success) * 100)))} />
                          </div>
                          <p className="text-[10px] text-base-content/40 mt-1">Red bar = current plan (spouse claims at {ranInputs.spouse.ssClaimAge})</p>
                        </div>
                      )}
                    </div>
                  </LabGroup>
                )}

                {/* GROUP: Stock allocation */}
                <LabGroup title="Stock allocation" hint="Stock/bond split, and the equity-index mix behind it">
                  <div className="bg-base-200/40 rounded-lg p-3 border border-base-300/40">
                    <div className="flex items-center justify-between mb-2 flex-wrap gap-1">
                      <h4 className="text-xs font-bold">Success rate vs stock allocation</h4>
                      {sweeps.allocationHelps ? (
                        <button type="button"
                          onClick={() => {
                            setAllocTaxable(sweeps.bestAllocation);
                            setAllocTraditional(sweeps.bestAllocation);
                            setAllocRoth(sweeps.bestAllocation);
                          }}
                          className="btn btn-xs btn-secondary">
                          Apply best: {sweeps.bestAllocation}% stocks
                        </button>
                      ) : (
                        <span className="text-[10px] text-emerald-400/80 flex items-center gap-1">
                          <CheckCircle2 className="w-3 h-3" /> Your mix is already near-optimal
                        </span>
                      )}
                    </div>
                    <div className="h-40">
                      <Line data={sweepCharts.alloc} options={successChartOpts(Math.min(...sweeps.allocation.map(p => labAdj.s(p.success) * 100)))} />
                    </div>
                    <p className="text-[10px] text-base-content/40 mt-1">
                      Red dot = your current plan (≈{ranInputs.stockPct}% stocks, balance-weighted). Sweep applies a uniform mix to every account.
                    </p>
                  </div>
                  {mixLabPanel}
                </LabGroup>

                {/* GROUP: Roth conversion optimizer */}
                {sweeps.rothPlans && (
                  <LabGroup title="Roth conversion optimizer" hint="Bracket ceilings × conversion windows, each fully taxed">
                  <div className="bg-base-200/40 rounded-lg p-3 border border-base-300/40">
                    <p className="text-[10px] text-base-content/40 mb-2">
                      Each plan fully simulated with taxes, RMDs, IRMAA and state tax. Recommended = best median
                      real ending balance without hurting success.
                    </p>
                    <div className="overflow-x-auto">
                      <table className="table table-xs w-full">
                        <thead>
                          <tr className="text-[10px] text-base-content/50">
                            <th>Plan</th><th>Success</th><th>Median ending (today's $)</th><th>Median lifetime taxes</th><th></th>
                          </tr>
                        </thead>
                        <tbody className="text-[11px]">
                          {sweeps.rothPlans.map((p, i) => {
                            const isCurrent = p.plan === null
                              ? ranInputs.roth.mode === 'off'
                              : p.plan.mode === 'auto'
                                ? ranInputs.roth.mode === 'auto'
                                : ranInputs.roth.mode === 'custom' && p.plan.ceiling === ranInputs.roth.ceiling
                                  && p.plan.startAge === ranInputs.roth.startAge && p.plan.endAge === ranInputs.roth.endAge;
                            return (
                              <tr key={i} className={p.recommended ? 'bg-emerald-500/5' : ''}>
                                <td className="font-medium">
                                  {p.label}
                                  {p.recommended && <span className="badge badge-xs bg-emerald-400/15 text-emerald-400 border-emerald-400/25 ml-1.5">recommended</span>}
                                  {isCurrent && <span className="badge badge-xs bg-sky-400/15 text-sky-400 border-sky-400/25 ml-1.5">current</span>}
                                </td>
                                <td>{fmtPct(labAdj.s(p.success))}</td>
                                <td>{fmtCompact(labAdj.e(p.medianEndReal))}</td>
                                <td>{fmtCompact(labAdj.t(p.medianTaxes))}</td>
                                <td className="text-right">
                                  {!isCurrent && (
                                    <button type="button" className="btn btn-xs btn-ghost text-secondary"
                                      onClick={() => {
                                        if (!p.plan) {
                                          setRothMode('off');
                                        } else if (p.plan.mode === 'auto') {
                                          setRothMode('auto');
                                        } else {
                                          setRothMode('custom');
                                          setRothCeiling(p.plan.ceiling);
                                          setRothStartStr(String(p.plan.startAge));
                                          setRothEndStr(String(p.plan.endAge));
                                        }
                                      }}>
                                      Apply
                                    </button>
                                  )}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                  </LabGroup>
                )}

                {/* GROUP: Spending */}
                {(() => {
                  const cards = renderStrategyCards(['smile', 'guardrails']);
                  return cards && (
                    <LabGroup title="Spending" hint="How much you draw, and how it flexes with markets">
                      {cards}
                    </LabGroup>
                  );
                })()}

                {/* GROUP: Portfolio balance */}
                {(() => {
                  const cards = renderStrategyCards(['down_guard', 'smart_rebalance', 'holistic', 'asset_location']);
                  return cards && (
                    <LabGroup title="Portfolio balance" hint="Which assets you sell, and when you de-risk">
                      {cards}
                    </LabGroup>
                  );
                })()}

                {sweeps.spendingCutPct !== null && (
                  <div className="flex items-center gap-2 text-xs text-amber-400 bg-amber-500/5 border border-amber-500/20 rounded-lg px-3 py-2">
                    <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
                    Cutting retirement spending by ~{sweeps.spendingCutPct}% (≈{fmtMoney(
                      (ranInputs.expenseMode === 'category' ? catTotal : ranInputs.annualExpenses) * sweeps.spendingCutPct / 100,
                    )}/yr in today's dollars) would lift this plan to roughly 90% success.
                  </div>
                )}

                <p className="text-[10px] text-base-content/40">
                  Sweeps use {sweeps.sweepSims} simulations each with identical random paths (common random numbers),
                  so differences reflect the strategy — not luck. Rates are anchored to your headline result so the
                  current plan reads {(result.successRate * 100).toFixed(1)}% here too; the deltas are what matter.
                  Re-run the Lab after changing the plan.
                </p>
              </div>
            )}
          </div>

          {/* ── What-If scenarios ── */}
          <div className="flex items-center gap-2 pt-1">
            <Sparkles className="w-4 h-4 text-secondary" />
            <h3 className="text-base font-bold">What-If scenarios</h3>
            <span className="text-[11px] text-base-content/40">— stress your plan against the big risks, on the same market paths</span>
          </div>

          {/* ── What-If: relocate to another state ── */}
          <div className="bg-base-200/30 rounded-xl p-4 border border-base-300/40">
            <h3 className="text-sm font-bold flex items-center gap-2 mb-1">
              <ArrowLeftRight className="w-4 h-4 text-secondary" />
              Relocate to another state
            </h3>
            <p className="text-[10px] text-base-content/40 mb-3">
              Compare your plan against moving to another state — it swaps the state income tax and shifts living
              costs (and long-term-care costs) to that location, on the same simulated market paths.
            </p>

            {!whatIfHome ? (
              // Relocation needs a starting location: both the cost-of-living
              // and the state-tax comparison are measured FROM the user's
              // current state. Without a ZIP there is no honest baseline.
              <div className="flex flex-wrap items-center gap-3 rounded-lg bg-base-200/50 border border-base-300/50 px-3 py-2.5">
                <MapPin className="w-4 h-4 text-secondary flex-shrink-0" />
                <span className="text-xs text-base-content/60 flex-1 min-w-[220px]">
                  Add your <span className="font-semibold">ZIP code</span> in Basics (step 1) first — a relocation
                  comparison needs your current state to measure the cost-of-living and tax difference against.
                </span>
                <button type="button" onClick={focusZip} className="btn btn-xs btn-secondary gap-1">
                  <MapPin className="w-3 h-3" /> Add ZIP
                </button>
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
                  <div>
                    <span className="text-xs font-medium text-base-content/70 mb-1 block">Relocate from → to</span>
                    <div className="flex items-center gap-2">
                      <span className="inline-flex items-center h-8 px-3 rounded-lg bg-base-200 border border-base-300/60 text-xs font-semibold whitespace-nowrap">
                        {STATE_NAMES[whatIfHome] ?? whatIfHome}
                      </span>
                      <ArrowRight className="w-4 h-4 text-base-content/30 flex-shrink-0" />
                      <select value={whatIfState}
                        onChange={(e) => { setWhatIfState(e.target.value); setWhatIfAdjTouched(false); }}
                        className="select select-bordered select-sm bg-base-200 min-w-[180px]">
                        <option value="">Select a state…</option>
                        {Object.entries(STATE_NAMES)
                          .filter(([code]) => code !== whatIfHome)
                          .sort((a, b) => a[1].localeCompare(b[1]))
                          .map(([code, name]) => (
                            <option key={code} value={code}>
                              {name}{STATE_TAX[code] && STATE_TAX[code].rate === 0 ? ' · no income tax' : ''}
                            </option>
                          ))}
                      </select>
                    </div>
                  </div>
                  {whatIfState && (
                    <label className="block w-[150px]">
                      <span className="text-xs font-medium text-base-content/70 mb-1 block">Expense change</span>
                      <div className="relative">
                        <input type="text" inputMode="decimal" value={whatIfAdjStr}
                          onChange={(e) => { setWhatIfAdjStr(e.target.value); setWhatIfAdjTouched(true); }}
                          className="input input-bordered input-sm w-full bg-base-200 pr-7" />
                        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-base-content/50 pointer-events-none">%</span>
                      </div>
                    </label>
                  )}
                  {whatIfState && (
                    <button type="button" onClick={handleWhatIf} disabled={whatIfBusy}
                      className="btn btn-sm btn-secondary gap-1.5">
                      {whatIfBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ArrowLeftRight className="w-3.5 h-3.5" />}
                      {whatIfBusy ? 'Comparing…' : 'Compare'}
                    </button>
                  )}
                </div>
                {whatIfState && (
                  <p className="text-[10px] text-base-content/40 mt-1.5">Expense change recommended from cost-of-living — editable.</p>
                )}

                {whatIfState && (
                  <div className="flex flex-wrap gap-2 mt-3 text-[10px]">
                    <span className="badge badge-sm bg-base-300/40 border-base-300 text-base-content/60">
                      Cost of living {whatIfHome} {STATE_COL[whatIfHome] ?? 100} → {whatIfState} {STATE_COL[whatIfState] ?? 100}
                    </span>
                    <span className="badge badge-sm bg-base-300/40 border-base-300 text-base-content/60">
                      Housing {STATE_HOUSING[whatIfHome] ?? 100} → {STATE_HOUSING[whatIfState] ?? 100}
                      {' '}({((housingRatio(whatIfHome, whatIfState) - 1) * 100).toFixed(0)}%)
                    </span>
                    <span className="badge badge-sm bg-base-300/40 border-base-300 text-base-content/60">
                      State tax {STATE_TAX[whatIfHome] ? (STATE_TAX[whatIfHome].rate === 0 ? '0%' : `~${STATE_TAX[whatIfHome].rate}%`) : '—'} → {STATE_TAX[whatIfState] ? (STATE_TAX[whatIfState].rate === 0 ? '0%' : `~${STATE_TAX[whatIfState].rate}%`) : '—'}
                    </span>
                    {ranInputs.ltc.enabled && (
                      <span className="badge badge-sm bg-base-300/40 border-base-300 text-base-content/60">
                        LTC cost {((ltcCostRatio(whatIfHome, whatIfState) - 1) * 100).toFixed(0)}%
                      </span>
                    )}
                    {!ranInputs.taxEnabled && (
                      <span className="badge badge-sm bg-amber-500/10 border-amber-500/25 text-amber-400">
                        Turn on Tax-aware planning to include the state income-tax difference
                      </span>
                    )}
                  </div>
                )}
              </>
            )}

            {whatIfResult && (() => {
              const tName = STATE_NAMES[whatIfState] ?? whatIfState;
              const hName = whatIfHome ? STATE_NAMES[whatIfHome] ?? whatIfHome : 'Current';
              const dSuccess = (whatIfResult.successRate - result.successRate) * 100;
              const hEnd = money(result.medianEnding.nominal, result.medianEnding.real);
              const tEnd = money(whatIfResult.medianEnding.nominal, whatIfResult.medianEnding.real);
              const dTax = whatIfResult.medianTotalTaxes - result.medianTotalTaxes;
              const better = dSuccess > 0.05 || (Math.abs(dSuccess) <= 0.05 && tEnd > hEnd);
              const rows = [
                { label: 'Success rate', h: `${(result.successRate * 100).toFixed(1)}%`, t: `${(whatIfResult.successRate * 100).toFixed(1)}%`, d: `${dSuccess >= 0 ? '+' : ''}${dSuccess.toFixed(1)}pp`, good: dSuccess >= 0 },
                { label: `Median ending (${displayReal ? "today's $" : 'future $'})`, h: fmtCompact(hEnd), t: fmtCompact(tEnd), d: `${tEnd - hEnd >= 0 ? '+' : ''}${fmtCompact(tEnd - hEnd)}`, good: tEnd - hEnd >= 0 },
                ...(ranInputs.taxEnabled ? [
                  { label: 'Lifetime state tax', h: fmtCompact(result.medianStateTaxes), t: fmtCompact(whatIfResult.medianStateTaxes), d: `${whatIfResult.medianStateTaxes - result.medianStateTaxes >= 0 ? '+' : ''}${fmtCompact(whatIfResult.medianStateTaxes - result.medianStateTaxes)}`, good: whatIfResult.medianStateTaxes <= result.medianStateTaxes },
                  { label: 'Lifetime taxes (all)', h: fmtCompact(result.medianTotalTaxes), t: fmtCompact(whatIfResult.medianTotalTaxes), d: `${dTax >= 0 ? '+' : ''}${fmtCompact(dTax)}`, good: dTax <= 0 },
                ] : []),
              ];
              return (
                <div className="mt-4">
                  {(() => {
                    const dState = whatIfResult.medianStateTaxes - result.medianStateTaxes;
                    return (
                      <div className={`rounded-lg px-3 py-2 mb-3 text-xs font-semibold border ${better ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-400' : 'bg-rose-500/10 border-rose-500/25 text-rose-400'}`}>
                        Moving to {tName}: {dSuccess >= 0 ? '+' : ''}{dSuccess.toFixed(1)}pp success
                        {' · '}{tEnd - hEnd >= 0 ? '+' : ''}{fmtCompact(tEnd - hEnd)} median ending
                        {ranInputs.taxEnabled && Math.abs(dState) > 1000 ? ` · ${dState <= 0 ? '−' : '+'}${fmtCompact(Math.abs(dState))} lifetime state tax` : ''}
                        {' — '}{better ? 'improves the plan.' : 'weakens the plan.'}
                      </div>
                    );
                  })()}
                  <div className="overflow-x-auto">
                    <table className="table table-xs w-full">
                      <thead>
                        <tr className="text-[10px] text-base-content/50">
                          <th></th><th>{hName} (now)</th><th>{tName}</th><th>Change</th>
                        </tr>
                      </thead>
                      <tbody className="text-[11px]">
                        {rows.map((r) => (
                          <tr key={r.label}>
                            <td className="font-medium">{r.label}</td>
                            <td>{r.h}</td>
                            <td className="font-semibold">{r.t}</td>
                            <td className={r.good ? 'text-emerald-400' : 'text-rose-400'}>{r.d}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="text-[10px] text-base-content/40 mt-2">
                    Same market paths for both. Expenses moved {num(whatIfAdjStr, 0) >= 0 ? '+' : ''}{num(whatIfAdjStr, 0)}%
                    (cost-of-living estimate{ranInputs.expenseMode === 'category' ? ', housing-weighted' : ''}), state income tax
                    and LTC costs re-priced to {tName}. Cost-of-living and state parameters are planning estimates.
                  </p>
                </div>
              );
            })()}
          </div>

          {/* ── What-If: sequence-of-returns risk ── */}
          <div className="bg-base-200/30 rounded-xl p-4 border border-base-300/40">
            <h3 className="text-sm font-bold flex items-center gap-2 mb-1">
              <TrendingDown className="w-4 h-4 text-secondary" />
              Sequence-of-returns risk
            </h3>
            <p className="text-[10px] text-base-content/40 mb-3">
              A bad first few years of retirement — when the portfolio is largest and you're withdrawing — does
              far more damage than the same bad years later. This forces poor returns onto the first years of
              retirement (age {ranInputs.retireAge}+); everything after follows your normal assumptions.
            </p>

            <div className="flex flex-wrap items-end gap-x-4 gap-y-3 mb-3">
              <div>
                <span className="text-xs font-medium text-base-content/70 mb-1 block">Bad-returns source</span>
                <div className="join">
                  <button type="button" onClick={() => setSeqMode('historical')} className={`btn btn-sm join-item ${seqMode === 'historical' ? 'btn-secondary' : 'btn-ghost'}`}>Worst in history</button>
                  <button type="button" onClick={() => setSeqMode('custom')} className={`btn btn-sm join-item ${seqMode === 'custom' ? 'btn-secondary' : 'btn-ghost'}`}>I'll provide</button>
                </div>
              </div>
              <label className="block">
                <span className="text-xs font-medium text-base-content/70 mb-1 block">Years</span>
                <select value={seqN} onChange={(e) => setSeqYearsStr(e.target.value)}
                  className="select select-bordered select-sm bg-base-200">
                  {[3, 4, 5, 6, 7].map(n => <option key={n} value={n}>{n} years</option>)}
                </select>
              </label>
              <button type="button" onClick={handleSeq} disabled={seqBusy}
                className="btn btn-sm btn-secondary gap-1.5">
                {seqBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <TrendingDown className="w-3.5 h-3.5" />}
                {seqBusy ? 'Stress-testing…' : 'Run stress test'}
              </button>
              {seqInPlan ? (
                <button type="button" onClick={removeSeqFromPlan} className="btn btn-sm btn-ghost gap-1.5 text-base-content/60">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /> Baked into plan · remove
                </button>
              ) : (
                <button type="button" onClick={addSeqToPlan} className="btn btn-sm btn-outline btn-secondary gap-1.5"
                  title="Fix these returns as your first years, then Monte-Carlo the rest — makes it part of the plan">
                  <Plus className="w-3.5 h-3.5" /> Bake into plan
                </button>
              )}
            </div>
            {seqInPlan && (
              <p className="text-[10px] text-emerald-400/80 -mt-1 mb-2">
                These {seqN} years are now fixed as the start of every simulation; re-run to see the effect. Remaining years stay Monte-Carlo.
              </p>
            )}

            {seqMode === 'historical' && seqWorst && (
              <div className="mb-1">
                <span className="text-[10px] text-base-content/40 block mb-1.5">
                  Worst {seqN} consecutive years in {ranInputs.windowStart}–{ranInputs.windowEnd} for your {result.windowStats.effectiveStockPct}/{100 - result.windowStats.effectiveStockPct} mix — applied as your first retirement years:
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {seqWorst.map((h, i) => {
                    const w = result.windowStats.effectiveStockPct / 100;
                    const stk = ranInputs.stockComposition ? compositeStockForYear(h[0], ranInputs.stockComposition) : h[1];
                    const blend = w * stk + (1 - w) * h[2];
                    return (
                      <span key={h[0]} className={`badge gap-1 ${blend >= 0
                        ? 'bg-emerald-400/10 text-emerald-400 border-emerald-400/25'
                        : 'bg-rose-400/10 text-rose-400 border-rose-400/25'}`}
                        title={`Historical ${h[0]} · stocks ${stk.toFixed(1)}% · bonds ${h[2].toFixed(1)}% · CPI ${h[3].toFixed(1)}%`}>
                        Yr {i + 1}: {blend >= 0 ? '+' : ''}{blend.toFixed(0)}% <span className="opacity-50">({h[0]})</span>
                      </span>
                    );
                  })}
                </div>
              </div>
            )}

            {seqMode === 'custom' && (
              <div>
                <span className="text-[10px] text-base-content/40 block mb-1.5">
                  Your returns for the first {seqN} retirement years (negatives allowed). Inflation follows your normal assumptions.
                </span>
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
                  {seqCustom.slice(0, seqN).map((r, i) => (
                    <div key={i} className="bg-base-200/50 rounded-lg p-2">
                      <div className="text-[10px] font-semibold text-base-content/60 mb-1">Retirement year {i + 1}</div>
                      <div className="grid grid-cols-2 gap-1.5">
                        <div className="relative">
                          <input type="text" inputMode="decimal" value={r.stock} placeholder="-12"
                            onChange={(e) => setSeqCustom(prev => prev.map((x, j) => j === i ? { ...x, stock: e.target.value } : x))}
                            className="input input-bordered input-xs w-full bg-base-200 pr-9" />
                          <span className="absolute right-1.5 top-1/2 -translate-y-1/2 text-[9px] text-base-content/40 pointer-events-none">stk%</span>
                        </div>
                        <div className="relative">
                          <input type="text" inputMode="decimal" value={r.bond} placeholder="2"
                            onChange={(e) => setSeqCustom(prev => prev.map((x, j) => j === i ? { ...x, bond: e.target.value } : x))}
                            className="input input-bordered input-xs w-full bg-base-200 pr-9" />
                          <span className="absolute right-1.5 top-1/2 -translate-y-1/2 text-[9px] text-base-content/40 pointer-events-none">bnd%</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {seqResult && (() => {
              const dSuccess = (seqResult.successRate - result.successRate) * 100;
              const hEnd = money(result.medianEnding.nominal, result.medianEnding.real);
              const tEnd = money(seqResult.medianEnding.nominal, seqResult.medianEnding.real);
              const rows = [
                { label: 'Success rate', h: `${(result.successRate * 100).toFixed(1)}%`, t: `${(seqResult.successRate * 100).toFixed(1)}%`, d: `${dSuccess >= 0 ? '+' : ''}${dSuccess.toFixed(1)}pp`, good: dSuccess >= 0 },
                { label: `Median ending (${displayReal ? "today's $" : 'future $'})`, h: fmtCompact(hEnd), t: fmtCompact(tEnd), d: `${tEnd - hEnd >= 0 ? '+' : ''}${fmtCompact(tEnd - hEnd)}`, good: tEnd - hEnd >= 0 },
                {
                  label: 'If it fails',
                  h: result.medianDepletionAge !== null ? `age ${Math.round(result.medianDepletionAge)}` : 'never',
                  t: seqResult.medianDepletionAge !== null ? `age ${Math.round(seqResult.medianDepletionAge)}` : 'never',
                  d: '', good: true,
                },
              ];
              return (
                <div className="mt-4">
                  <div className={`rounded-lg px-3 py-2 mb-3 text-xs font-semibold border ${dSuccess >= -0.05 ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-400' : 'bg-rose-500/10 border-rose-500/25 text-rose-400'}`}>
                    A bad {seqN}-year start: success {dSuccess >= 0 ? '+' : ''}{dSuccess.toFixed(1)}pp
                    {' · '}{tEnd - hEnd >= 0 ? '+' : ''}{fmtCompact(tEnd - hEnd)} median ending
                    {' — '}
                    {dSuccess < -5
                      ? 'your plan is materially exposed to a rough start. The mitigations below are simulated against this exact sequence.'
                      : dSuccess < -0.5
                      ? 'a real but survivable dent — see which mitigation firms it up most below.'
                      : 'your plan absorbs a bad start well.'}
                  </div>
                  <div className="overflow-x-auto">
                    <table className="table table-xs w-full">
                      <thead>
                        <tr className="text-[10px] text-base-content/50">
                          <th></th><th>Normal</th><th>Bad start</th><th>Change</th>
                        </tr>
                      </thead>
                      <tbody className="text-[11px]">
                        {rows.map((r) => (
                          <tr key={r.label}>
                            <td className="font-medium">{r.label}</td>
                            <td>{r.h}</td>
                            <td className="font-semibold">{r.t}</td>
                            <td className={r.d ? (r.good ? 'text-emerald-400' : 'text-rose-400') : 'text-base-content/40'}>{r.d || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {/* Mitigations, simulated against this exact bad sequence */}
                  {seqMitigations && (() => {
                    const baseS = seqResult.successRate, baseE = seqResult.medianEnding.real;
                    const ranked = [...seqMitigations].sort((a, b) =>
                      (b.success - a.success) || (b.endReal - a.endReal));
                    const best = ranked[0];
                    const bestHelps = best.success > baseS + 0.005 || (Math.abs(best.success - baseS) <= 0.005 && best.endReal > baseE * 1.005);
                    const applyMit = (id: string) => {
                      if (id === 'guardrails') setGuardrails(true);
                      else if (id === 'guarded') setWithdrawalStrategy('guarded');
                      else if (id === 'guarded_rebalance') setWithdrawalStrategy('guarded_rebalance');
                      else if (id === 'holistic') setWithdrawalStrategy('holistic');
                      else if (id === 'cash3') setCashBufferStr('3');
                    };
                    return (
                      <div className="mt-4">
                        <h4 className="text-xs font-bold flex items-center gap-1.5 mb-1">
                          <ShieldCheck className="w-3.5 h-3.5 text-secondary" />
                          How to mitigate — each option simulated against this exact bad start
                        </h4>
                        <div className="overflow-x-auto">
                          <table className="table table-xs w-full">
                            <thead>
                              <tr className="text-[10px] text-base-content/50">
                                <th>Mitigation</th><th>Success</th><th>vs bad start</th><th>Median ending</th><th></th>
                              </tr>
                            </thead>
                            <tbody className="text-[11px]">
                              <tr className="text-base-content/50">
                                <td className="italic">Bad start, no change</td>
                                <td>{fmtPct(baseS)}</td><td>—</td><td>{fmtCompact(baseE)}</td><td></td>
                              </tr>
                              {ranked.map((m, i) => {
                                const dS = (m.success - baseS) * 100;
                                const helps = m.success > baseS + 0.005 || (Math.abs(m.success - baseS) <= 0.005 && m.endReal > baseE * 1.005);
                                return (
                                  <tr key={m.id} className={i === 0 && bestHelps ? 'bg-emerald-500/5' : ''}>
                                    <td>
                                      <span className="font-medium">{m.label}</span>
                                      {i === 0 && bestHelps && <span className="badge badge-xs bg-emerald-400/15 text-emerald-400 border-emerald-400/25 ml-1.5">best</span>}
                                      <span className="block text-[10px] text-base-content/40">{m.detail}</span>
                                    </td>
                                    <td>{fmtPct(m.success)}</td>
                                    <td className={dS >= 0 ? 'text-emerald-400' : 'text-rose-400'}>{dS >= 0 ? '+' : ''}{dS.toFixed(1)}pp</td>
                                    <td>{fmtCompact(m.endReal)}</td>
                                    <td className="text-right">
                                      <button type="button" onClick={() => applyMit(m.id)}
                                        className={`btn btn-xs ${helps ? 'btn-outline btn-secondary' : 'btn-ghost text-base-content/40'}`}>
                                        Apply
                                      </button>
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                        <p className="text-[10px] text-base-content/40 mt-2">
                          {bestHelps
                            ? `Recommended: ${best.label} — it recovers the most against this sequence. Apply it, then re-run to lock it in.`
                            : 'None of these meaningfully beats the plan as-is against this sequence — your sequencing is already sound, or the cure (cash drag) costs more than the disease. Trimming early spending is the surer lever.'}
                          {' '}All simulated on the same market paths; the guard strategies change which asset you sell in down years, the cash buffer trades some long-run drag for early protection.
                        </p>
                      </div>
                    );
                  })()}

                  <p className="text-[10px] text-base-content/40 mt-2">
                    Same market paths after the forced window, so the difference isolates the sequence effect.
                    {seqMode === 'historical' ? ' Forced years use those historical years\' actual returns and inflation.' : ' Inflation follows your normal assumptions in the forced years.'}
                  </p>
                </div>
              );
            })()}
          </div>

          {/* Assumptions & caveats */}
          <div className="flex items-start gap-2 text-[11px] text-base-content/40">
            <Info className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
            <p>
              Bootstrap Monte Carlo over {ranInputs.windowStart}–{ranInputs.windowEnd} annual data
              (S&P 500 total return, 10Y Treasury total return, CPI-U; 2025 approximate
              {ranInputs.excludedYears.length > 0 ? `; excluded: ${ranInputs.excludedYears.join(', ')}` : ''}).
              Withdrawals at the start of each year, annual rebalancing, SS COLA = CPI floored at 0.
              Tax mode: 2026 federal brackets/deduction CPI-indexed yearly in-sim, LTCG stacking, NIIT (thresholds fixed per law),
              simplified IRMAA (current-year MAGI, not the real 2-year lookback), single-rate state tax estimate,
              RMDs per the Uniform Lifetime Table (from age {rmdAge}). LTC costs and state parameters are planning
              estimates.{ranInputs.spouse.enabled && ' Spouse: ages on the chart are your timeline; the run continues to the last survivor\'s plan-to age. Survivor benefit = larger of the two (deceased\'s planned claiming factor, a simplification); filing switches MFJ → single the year after the first death.'} Not
              tax or investment advice.
            </p>
          </div>
        </div>
      )}

      {/* First-run helper */}
      {!result && !computing && (
        <div className="flex items-center gap-2 text-sm text-base-content/50 bg-base-200/40 rounded-xl p-4 border border-base-300/50">
          <Wallet className="w-4 h-4 flex-shrink-0" />
          Fill in your numbers and hit <span className="font-semibold">Run simulation</span> — results, charts,
          a year-by-year ledger and the Strategy Lab appear here.
        </div>
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
                href={`/api/auth/google`}
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
