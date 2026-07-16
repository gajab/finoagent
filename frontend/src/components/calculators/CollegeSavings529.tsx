import React, { useState, useMemo, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { apiBase } from '../../api';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
  type TooltipItem,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import {
  GraduationCap,
  Info,
  Coins,
  CircleDollarSign,
  AlertCircle,
  CheckCircle2,
  TrendingUp,
  Wallet,
  Percent,
  Award,
  ChevronRight,
  ShieldAlert,
  ArrowRightLeft,
  PiggyBank,
  ExternalLink,
  Sliders,
  Scale,
  Settings,
  CalendarDays,
  Save,
  UserPlus,
} from 'lucide-react';
import {
  STATE_529_DATA,
  NATIONAL_529_PLANS,
  lookupStateFromZip,
  State529Data,
  National529Plan,
} from './college529Data';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler
);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function fmtMoney(v: number): string {
  return `$${Math.round(v).toLocaleString()}`;
}

function fmtCompact(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${Math.round(v)}`;
}

// Solver to find monthly savings target to cover college costs
function solveMonthlyContribution(
  currentBalance: number,
  futureCosts: number[],
  yearsToCollege: number,
  annualReturn: number
): number {
  if (yearsToCollege <= 0) {
    const total = futureCosts.reduce((a, b) => a + b, 0);
    return total / (futureCosts.length * 12);
  }

  let low = 0;
  let high = 100000;
  const monthsToCollege = yearsToCollege * 12;
  const rMonthly = Math.pow(1 + annualReturn / 100, 1 / 12) - 1;
  const rAnnual = annualReturn / 100;

  // Binary search for exact monthly savings required
  for (let iter = 0; iter < 50; iter++) {
    const mid = (low + high) / 2;
    let bal = currentBalance;

    // Accumulation
    for (let m = 0; m < monthsToCollege; m++) {
      bal = bal * (1 + rMonthly) + mid;
    }

    // Drawdown (withdraw at start of year, remaining grows at annual rate)
    let depleted = false;
    for (let y = 0; y < futureCosts.length; y++) {
      bal -= futureCosts[y];
      if (bal < 0) {
        depleted = true;
        break;
      }
      bal *= 1 + rAnnual;
    }

    if (depleted) {
      low = mid;
    } else {
      high = mid;
    }
  }

  return high;
}

// Asset class historical returns
const ASSET_RETURNS = {
  indices: 10.0,      // S&P 500 / Major Indices
  growth: 10.5,       // Growth (Active Stock)
  aggressive: 12.0,   // Aggressive Growth
  balanced: 7.5,      // Balanced / Hybrid
  bonds: 4.5,         // Bonds / Fixed Income
  moneyMarket: 3.5,   // Money Market / Cash
};

export function CollegeSavings529() {
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

  // --- State Inputs ---
  const [kidAgeStr, setKidAgeStr] = useState('5');
  const [zipCode, setZipCode] = useState('10001'); // Defaults to New York
  const [selectedState, setSelectedState] = useState('NY');
  const [compareNationalCode, setCompareNationalCode] = useState('UT');
  const [filingStatus, setFilingStatus] = useState<'single' | 'joint'>('joint');
  const [monthlyContribStr, setMonthlyContribStr] = useState('300');
  const [currentBalanceStr, setCurrentBalanceStr] = useState('5000');
  
  // New mixing states (must sum to 100)
  const [allocIndices, setAllocIndices] = useState(50);
  const [allocGrowth, setAllocGrowth] = useState(10);
  const [allocAggressive, setAllocAggressive] = useState(10);
  const [allocBalanced, setAllocBalanced] = useState(0);
  const [allocBonds, setAllocBonds] = useState(20);
  const [allocMoneyMarket, setAllocMoneyMarket] = useState(10);

  // New Index preference selection
  const [preferredIndex, setPreferredIndex] = useState<'sp500' | 'nasdaq100' | 'russell2000' | 'total_market'>('sp500');

  // College expenses inputs
  const [expensePreset, setExpensePreset] = useState<string>('in_state_public');
  const [customAnnualCostStr, setCustomAnnualCostStr] = useState('30000');
  const [customYearsStr, setCustomYearsStr] = useState('4');

  // Rate assumptions & custom override state
  const [overrideReturn, setOverrideReturn] = useState(false);
  const [customReturnStr, setCustomReturnStr] = useState('7.0');
  const [inflationRateStr, setInflationRateStr] = useState('3.5');
  const [advisoryProfile, setAdvisoryProfile] = useState<'moderate' | 'high' | 'hnw'>('moderate');

  // --- Derived Values & Parsers ---
  const kidAge = Math.min(Math.max(parseInt(kidAgeStr, 10) || 0, 0), 18);
  const monthlyContrib = Math.max(parseFloat(monthlyContribStr) || 0, 0);
  const currentBalance = Math.max(parseFloat(currentBalanceStr) || 0, 0);
  const annualInflation = parseFloat(inflationRateStr) || 0;

  const yearsToCollege = Math.max(18 - kidAge, 0);

  const [selectedPlanIdx, setSelectedPlanIdx] = useState(0);

  // Reset local plan index when state changes to avoid out-of-bounds index errors
  useEffect(() => {
    setSelectedPlanIdx(0);
  }, [selectedState]);

  const selectedStateRules = useMemo(() => {
    return STATE_529_DATA[selectedState] ?? STATE_529_DATA.NY;
  }, [selectedState]);

  const localPlanDetails = useMemo(() => {
    return selectedStateRules.plans[selectedPlanIdx] ?? selectedStateRules.plans[0];
  }, [selectedStateRules, selectedPlanIdx]);

  const localPlan = useMemo(() => {
    return {
      ...selectedStateRules,
      ...localPlanDetails,
      notes: selectedStateRules.taxNotes + ' ' + (localPlanDetails.notes || ''),
    };
  }, [selectedStateRules, localPlanDetails]);

  // Verify Asset Allocation Sum
  const totalAllocation = allocIndices + allocGrowth + allocAggressive + allocBalanced + allocBonds + allocMoneyMarket;
  const isAllocationValid = totalAllocation === 100;

  // Calculate dynamic weighted historical return
  const weightedHistoricalReturn = useMemo(() => {
    const sum =
      allocIndices * ASSET_RETURNS.indices +
      allocGrowth * ASSET_RETURNS.growth +
      allocAggressive * ASSET_RETURNS.aggressive +
      allocBalanced * ASSET_RETURNS.balanced +
      allocBonds * ASSET_RETURNS.bonds +
      allocMoneyMarket * ASSET_RETURNS.moneyMarket;
    return sum / 100;
  }, [allocIndices, allocGrowth, allocAggressive, allocBalanced, allocBonds, allocMoneyMarket]);

  // Expected return selection based on override checkbox
  const annualReturn = useMemo(() => {
    if (overrideReturn) {
      return parseFloat(customReturnStr) || 0;
    }
    return weightedHistoricalReturn;
  }, [overrideReturn, customReturnStr, weightedHistoricalReturn]);

  // Handle normalization of sliders to sum to 100%
  const handleAutoBalance = () => {
    const sum = allocIndices + allocGrowth + allocAggressive + allocBalanced + allocBonds + allocMoneyMarket;
    if (sum === 0) {
      setAllocIndices(50);
      setAllocBonds(30);
      setAllocMoneyMarket(20);
      return;
    }
    const scale = 100 / sum;
    const newIndices = Math.round(allocIndices * scale);
    const newGrowth = Math.round(allocGrowth * scale);
    const newAggressive = Math.round(allocAggressive * scale);
    const newBalanced = Math.round(allocBalanced * scale);
    const newBonds = Math.round(allocBonds * scale);
    
    setAllocIndices(newIndices);
    setAllocGrowth(newGrowth);
    setAllocAggressive(newAggressive);
    setAllocBalanced(newBalanced);
    setAllocBonds(newBonds);
    setAllocMoneyMarket(100 - (newIndices + newGrowth + newAggressive + newBalanced + newBonds));
  };

  const handleZipChange = (val: string) => {
    setZipCode(val);
    const clean = val.trim().replace(/\D/g, '');
    if (clean.length === 5) {
      const state = lookupStateFromZip(clean);
      if (state) {
        setSelectedState(state);
      }
    }
  };

  // College Cost Settings
  const collegeYears = useMemo(() => {
    if (expensePreset === 'in_state_public') return 4;
    if (expensePreset === 'out_of_state_public') return 4;
    if (expensePreset === 'private') return 4;
    if (expensePreset === 'community') return 2;
    if (expensePreset === 'vocational') return 2;
    if (expensePreset === 'grad_masters') return 2;
    if (expensePreset === 'grad_mba') return 2;
    if (expensePreset === 'grad_law') return 3;
    if (expensePreset === 'grad_medical') return 4;
    if (expensePreset === 'k12_private') return 10;
    return Math.min(Math.max(parseInt(customYearsStr, 10) || 4, 1), 12);
  }, [expensePreset, customYearsStr]);

  const collegeAnnualCost = useMemo(() => {
    if (expensePreset === 'in_state_public') return 25850;
    if (expensePreset === 'out_of_state_public') return 45780;
    if (expensePreset === 'private') return 60920;
    if (expensePreset === 'community') return 14150;
    if (expensePreset === 'vocational') return 17500;
    if (expensePreset === 'grad_masters') return 52000;
    if (expensePreset === 'grad_mba') return 80000;
    if (expensePreset === 'grad_law') return 68000;
    if (expensePreset === 'grad_medical') return 77000;
    if (expensePreset === 'k12_private') return 15000;
    return Math.max(parseFloat(customAnnualCostStr) || 0, 0);
  }, [expensePreset, customAnnualCostStr]);

  // Future College Costs Projection
  const futureCosts = useMemo(() => {
    const inf = annualInflation / 100;
    const costs: number[] = [];
    for (let t = 0; t < collegeYears; t++) {
      const inflatedCost = collegeAnnualCost * Math.pow(1 + inf, yearsToCollege + t);
      costs.push(inflatedCost);
    }
    return costs;
  }, [collegeAnnualCost, collegeYears, yearsToCollege, annualInflation]);

  const totalFutureCost = useMemo(() => {
    return futureCosts.reduce((sum, cost) => sum + cost, 0);
  }, [futureCosts]);

  // Solver for required monthly savings
  const requiredMonthlySavings = useMemo(() => {
    return solveMonthlyContribution(currentBalance, futureCosts, yearsToCollege, annualReturn);
  }, [currentBalance, futureCosts, yearsToCollege, annualReturn]);

  // --- Financial Projections (Accumulation & Drawdown Simulation) ---
  const simulation = useMemo(() => {
    const rMonthly = Math.pow(1 + annualReturn / 100, 1 / 12) - 1;
    const rAnnual = annualReturn / 100;
    const monthsToCollege = yearsToCollege * 12;

    const series: { age: number; year: number; balance: number; contributions: number }[] = [];
    let balance = currentBalance;
    let contributions = currentBalance;

    series.push({ age: kidAge, year: 0, balance, contributions });

    // Accumulation Phase
    for (let y = 1; y <= yearsToCollege; y++) {
      for (let m = 0; m < 12; m++) {
        balance = balance * (1 + rMonthly) + monthlyContrib;
        contributions += monthlyContrib;
      }
      series.push({
        age: kidAge + y,
        year: y,
        balance,
        contributions,
      });
    }

    // Drawdown Phase
    let age = kidAge + yearsToCollege;
    for (let y = 0; y < collegeYears; y++) {
      const withdrawal = futureCosts[y];
      balance -= withdrawal;
      balance *= 1 + rAnnual;

      series.push({
        age: age + y + 1,
        year: yearsToCollege + y + 1,
        balance,
        contributions,
      });
    }

    const projectedBalanceAtAge18 = series[yearsToCollege]?.balance ?? 0;
    const finalBalance = balance;
    const totalContributed = contributions;
    const isShortfall = finalBalance < 0;

    return {
      series,
      projectedBalanceAtAge18,
      finalBalance,
      totalContributed,
      isShortfall,
    };
  }, [kidAge, yearsToCollege, collegeYears, currentBalance, monthlyContrib, annualReturn, futureCosts]);

  // --- 529 Recommendation Engine Logic ---
  const localTaxSavings = useMemo(() => {
    const annualContrib = monthlyContrib * 12;
    if (localPlan.hasDeduction) {
      const maxDeduction =
        filingStatus === 'joint'
          ? localPlan.deductionLimitJoint
          : localPlan.deductionLimitSingle;
      const deductible = Math.min(annualContrib, maxDeduction);
      return deductible * localPlan.stateTaxRate;
    } else if (localPlan.hasCredit) {
      const creditRate = (localPlan.creditRatePct ?? 0) / 100;
      const maxCredit =
        filingStatus === 'joint'
          ? localPlan.creditLimitJoint ?? 0
          : localPlan.creditLimitSingle ?? 0;
      return Math.min(annualContrib * creditRate, maxCredit);
    }
    return 0;
  }, [localPlan, monthlyContrib, filingStatus]);

  // Determine Best National Plan based on Preferred Index and Asset Mix
  const bestNationalPlan = useMemo((): National529Plan => {
    // If Nasdaq 100 is selected, recommend Utah (highly customizable) or Ohio (supports it)
    if (preferredIndex === 'nasdaq100') {
      return NATIONAL_529_PLANS.find((p) => p.stateCode === 'UT') ?? NATIONAL_529_PLANS[0];
    }
    // If Russell 2000 is selected, Utah is the only national plan that supports small caps directly
    if (preferredIndex === 'russell2000') {
      return NATIONAL_529_PLANS.find((p) => p.stateCode === 'UT') ?? NATIONAL_529_PLANS[0];
    }

    // Default to largest weighting in user's mix
    const weightings = [
      { key: 'indices', val: allocIndices },
      { key: 'growth', val: allocGrowth },
      { key: 'aggressive', val: allocAggressive },
      { key: 'balanced', val: allocBalanced },
      { key: 'bonds', val: allocBonds },
      { key: 'moneyMarket', val: allocMoneyMarket },
    ];
    const topAsset = weightings.sort((a, b) => b.val - a.val)[0].key;

    if (topAsset === 'indices' || topAsset === 'moneyMarket') {
      return NATIONAL_529_PLANS.find((p) => p.stateCode === 'NY') ?? NATIONAL_529_PLANS[1];
    }
    if (topAsset === 'growth' || topAsset === 'aggressive') {
      return NATIONAL_529_PLANS.find((p) => p.stateCode === 'OH') ?? NATIONAL_529_PLANS[3];
    }
    return NATIONAL_529_PLANS.find((p) => p.stateCode === 'UT') ?? NATIONAL_529_PLANS[0];
  }, [preferredIndex, allocIndices, allocGrowth, allocAggressive, allocBalanced, allocBonds, allocMoneyMarket]);

  // Sync compareNationalCode with bestNationalPlan but allow override
  useEffect(() => {
    setCompareNationalCode(bestNationalPlan.stateCode);
  }, [bestNationalPlan]);

  const comparedNationalPlan = useMemo(() => {
    return NATIONAL_529_PLANS.find((p) => p.stateCode === compareNationalCode) ?? bestNationalPlan;
  }, [compareNationalCode, bestNationalPlan]);

  // Build Recommendation Verdict
  const recommendationVerdict = useMemo(() => {
    const annualContrib = monthlyContrib * 12;
    const planName = localPlan.planName;
    const rating = localPlan.rating;
    const stateName = localPlan.stateName;

    // Check Index support limitations
    let limitationNote = '';
    const indexName =
      preferredIndex === 'nasdaq100'
        ? 'Nasdaq 100'
        : preferredIndex === 'russell2000'
        ? 'Russell 2000'
        : preferredIndex === 'total_market'
        ? 'Total Stock Market'
        : 'S&P 500';

    if (preferredIndex === 'nasdaq100') {
      const supportsNasdaq = localPlan.options.includes('Nasdaq 100');
      if (!supportsNasdaq) {
        limitationNote = `🚨 Limitation Alert: Your local plan (${planName}) does NOT offer a direct ${indexName} index option. `;
      }
    } else if (preferredIndex === 'russell2000') {
      const supportsRussell = localPlan.options.includes('Russell 2000');
      if (!supportsRussell) {
        limitationNote = `🚨 Limitation Alert: Your local plan (${planName}) does NOT offer a direct ${indexName} small-cap option. `;
      }
    }

    // Case 1: Tax Parity State
    if (localPlan.hasTaxParity) {
      return {
        recommendInState: false,
        useBoth: false,
        title: `Choose Out-of-State Plan (e.g. ${bestNationalPlan.planName})`,
        badge: 'Tax Parity Active',
        verdictText: `${limitationNote}Your state (${stateName}) offers tax parity, meaning you receive the state tax deduction regardless of which plan you choose. Because your local plan (${planName}, rated ${rating}) has higher fees (${localPlan.avgFeePct.toFixed(2)}%) compared to ${bestNationalPlan.planName} (${bestNationalPlan.avgFeePct.toFixed(2)}%), we recommend investing in the national plan to save on fees while still claiming your state deduction!`,
        action: `Open an account with ${bestNationalPlan.planName} and claim the state tax deduction on your ${stateName} tax return.`,
      };
    }

    // Case 2: No state tax deduction/credit
    if (!localPlan.hasDeduction && !localPlan.hasCredit) {
      return {
        recommendInState: false,
        useBoth: false,
        title: `Use National Plan: ${bestNationalPlan.planName}`,
        badge: 'Fee Optimizer',
        verdictText: `${limitationNote}${stateName} offers no state tax incentive for 529 plans. Therefore, investment quality and fees are the only criteria. ${bestNationalPlan.planName} (rated ${bestNationalPlan.rating}) offers a lower average fee of ${bestNationalPlan.avgFeePct.toFixed(2)}% compared to your local plan (${localPlan.avgFeePct.toFixed(2)}%) and supports your preferred ${indexName} index tracking.`,
        action: `Invest directly in ${bestNationalPlan.planName} to maximize growth and minimize drag from fees.`,
      };
    }

    // Case 3: In-State Tax Benefit outweighs but might overflow
    const maxDeduction =
      filingStatus === 'joint'
        ? localPlan.deductionLimitJoint
        : localPlan.deductionLimitSingle;

    if (localTaxSavings > 0) {
      // If index is not supported locally but there's a huge tax break, highlight this conflict
      const isIndexUnsupportedLocally = limitationNote !== '';

      if (isIndexUnsupportedLocally) {
        return {
          recommendInState: false,
          useBoth: true,
          title: `Consider Out-of-State for ${indexName}`,
          badge: 'Index Restriction Conflict',
          verdictText: `${limitationNote}Although you save ${fmtMoney(localTaxSavings)}/year in local taxes by using ${planName}, it lacks access to the ${indexName} index. We recommend contributing up to the local tax deduction limit (${fmtMoney(maxDeduction)}) in a close substitute (e.g. S&P 500 / Growth) locally, and directing any overflow or index-specific allocations to ${bestNationalPlan.planName} which fully supports it.`,
          action: `Max out local tax write-offs in ${planName} first, then use ${bestNationalPlan.planName} for ${indexName}.`,
        };
      }

      if (annualContrib > maxDeduction && maxDeduction < 999999) {
        return {
          recommendInState: true,
          useBoth: true,
          title: `Split Contributions: Local + National Plan`,
          badge: 'Maximum Tax Optimization',
          verdictText: `Your annual savings of ${fmtMoney(annualContrib)} exceeds your state's maximum tax deduction limit of ${fmtMoney(maxDeduction)}. To optimize, contribute ${fmtMoney(maxDeduction)} to the in-state plan (${planName}) to secure the maximum state tax deduction of ${fmtMoney(localTaxSavings)}, and invest the remaining ${fmtMoney(annualContrib - maxDeduction)} in the lower-fee ${bestNationalPlan.planName} (${bestNationalPlan.avgFeePct.toFixed(2)}%).`,
          action: `Contribute ${fmtMoney(maxDeduction)}/year to ${planName}, and any excess to ${bestNationalPlan.planName}.`,
        };
      } else {
        return {
          recommendInState: true,
          useBoth: false,
          title: `Use Local Plan: ${planName}`,
          badge: 'Local Tax Advantage',
          verdictText: `We recommend investing in your in-state plan (${planName}). The immediate state tax benefit of ${fmtMoney(localTaxSavings)}/year (approx. ${(localPlan.stateTaxRate * 100).toFixed(1)}% immediate return on contributions) easily outweighs the slightly lower fees of out-of-state plans. Keep your contributions inside the local plan.`,
          action: `Open an account with ${planName} to claim your annual tax write-off.`,
        };
      }
    }

    return {
      recommendInState: true,
      useBoth: false,
      title: 'Invest In-State',
      badge: 'State Resident Benefit',
      verdictText: `Your state plan ${planName} offers local benefits. Compare options below.`,
      action: `Check local plan details for ${planName}.`,
    };
  }, [localPlan, monthlyContrib, filingStatus, localTaxSavings, bestNationalPlan, preferredIndex]);

  // --- Chart Setup (Savings Path) ---
  const chartLabels = simulation.series.map((s) => `Age ${s.age}`);
  const chartData = {
    labels: chartLabels,
    datasets: [
      {
        label: 'Projected 529 Balance',
        data: simulation.series.map((s) => Math.max(s.balance, 0)),
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59, 130, 246, 0.08)',
        fill: true,
        tension: 0.15,
        borderWidth: 2,
        pointRadius: 3,
        pointBackgroundColor: '#3b82f6',
      },
      {
        label: 'Total Contributions',
        data: simulation.series.map((s) => s.contributions),
        borderColor: '#10b981',
        borderDash: [5, 5],
        fill: false,
        tension: 0,
        borderWidth: 1.5,
        pointRadius: 0,
      },
      {
        label: 'College Cost Target',
        data: simulation.series.map((s, idx) => {
          if (idx <= yearsToCollege) return totalFutureCost;
          let remainingCost = 0;
          const drawIdx = idx - yearsToCollege - 1;
          for (let y = drawIdx + 1; y < collegeYears; y++) {
            remainingCost += futureCosts[y] ?? 0;
          }
          return remainingCost;
        }),
        borderColor: '#f43f5e',
        borderDash: [2, 2],
        fill: false,
        tension: 0.1,
        borderWidth: 1.5,
        pointRadius: 0,
      },
    ],
  };

  const chartOpts = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'top' as const,
        labels: {
          color: 'rgba(255, 255, 255, 0.7)',
          font: { size: 11 },
        },
      },
      tooltip: {
        callbacks: {
          label: (ctx: TooltipItem<'line'>) => {
            return `${ctx.dataset.label}: ${fmtMoney(ctx.parsed.y ?? 0)}`;
          },
        },
      },
    },
    scales: {
      x: {
        grid: { display: false },
        ticks: { color: 'rgba(255, 255, 255, 0.6)', font: { size: 10 } },
      },
      y: {
        beginAtZero: true,
        grid: { color: 'rgba(255, 255, 255, 0.05)' },
        ticks: {
          color: 'rgba(255, 255, 255, 0.6)',
          font: { size: 10 },
          callback: (v: number | string) => fmtCompact(Number(v)),
        },
      },
    },
  };

  return (
    <div className="space-y-8 max-w-4xl mx-auto py-2">
      {/* Header */}
      <div className="border-b border-base-300 pb-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-extrabold flex items-center gap-2.5">
            <GraduationCap className="w-8 h-8 text-secondary" />
            529 College Savings & Selection Engine
          </h2>
          <p className="text-sm text-base-content/60 mt-1.5 leading-relaxed">
            Design your custom allocation, calculate the blended historical performance, choose indices, and select the optimal plan based on in-state tax incentives, plan fees, and restrictions.
          </p>
        </div>
        <button
          onClick={handleSaveClick}
          className="btn btn-secondary rounded-xl btn-sm font-semibold flex items-center gap-1.5 self-start sm:self-center"
        >
          <Save className="w-3.5 h-3.5" /> Save Scenario
        </button>
      </div>

      {/* SINGLE COLUMN FLOW */}
      <div className="space-y-6">

        {/* STEP 1: Student & Location Profile */}
        <div className="bg-base-200/40 rounded-2xl p-5 sm:p-6 border border-base-300/50 space-y-5">
          <h3 className="text-base font-bold text-base-content/90 flex items-center gap-2">
            <span className="bg-secondary/10 text-secondary w-6 h-6 rounded-full flex items-center justify-center text-xs font-extrabold">1</span>
            Student & Family Profile
          </h3>

          <div className="space-y-4">
            {/* Kid's Age Slider */}
            <div>
              <div className="flex justify-between text-xs font-semibold mb-1">
                <span className="text-base-content/70">Kid's Current Age:</span>
                <span className="text-secondary font-bold text-sm">{kidAge} yrs old</span>
              </div>
              <input
                type="range"
                min="0"
                max="18"
                step="1"
                value={kidAgeStr}
                onChange={(e) => setKidAgeStr(e.target.value)}
                className="range range-secondary range-sm w-full"
              />
              <div className="flex justify-between text-[10px] text-base-content/40 mt-1 px-1">
                <span>0 (Newborn)</span>
                <span>9</span>
                <span>18 (Starts College)</span>
              </div>
            </div>

            {/* ZIP, State & Local Plan Option */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <span className="text-xs font-semibold text-base-content/70 block mb-1">ZIP Code (Auto-Fills State)</span>
                <input
                  type="text"
                  maxLength={5}
                  value={zipCode}
                  onChange={(e) => handleZipChange(e.target.value)}
                  className="input input-bordered w-full bg-base-200"
                  placeholder="10001"
                />
              </div>
              <div>
                <span className="text-xs font-semibold text-base-content/70 block mb-1">Selected State</span>
                <select
                  value={selectedState}
                  onChange={(e) => setSelectedState(e.target.value)}
                  className="select select-bordered w-full bg-base-200 font-bold"
                >
                  {Object.keys(STATE_529_DATA).map((code) => (
                    <option key={code} value={code}>
                      {STATE_529_DATA[code].stateName} ({code})
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <span className="text-xs font-semibold text-base-content/70 block mb-1">Local 529 Program Option</span>
                <select
                  value={selectedPlanIdx}
                  onChange={(e) => setSelectedPlanIdx(parseInt(e.target.value, 10))}
                  className="select select-bordered w-full bg-base-200 font-bold"
                >
                  {selectedStateRules.plans.map((p, idx) => (
                    <option key={idx} value={idx}>
                      {p.planName}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-2">
            {/* Filing Status */}
            <div>
              <span className="text-xs font-semibold text-base-content/70 block mb-1.5">Tax Filing Status</span>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setFilingStatus('joint')}
                  className={`btn btn-sm flex-1 ${filingStatus === 'joint' ? 'btn-secondary text-secondary-content' : 'btn-ghost bg-base-200/40'}`}
                >
                  Joint Filer
                </button>
                <button
                  type="button"
                  onClick={() => setFilingStatus('single')}
                  className={`btn btn-sm flex-1 ${filingStatus === 'single' ? 'btn-secondary text-secondary-content' : 'btn-ghost bg-base-200/40'}`}
                >
                  Single Filer
                </button>
              </div>
            </div>

            {/* Starting Balance */}
            <label className="block">
              <span className="text-xs font-semibold text-base-content/70 block mb-1">Starting 529 Balance</span>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-base-content/50 text-sm pointer-events-none">$</span>
                <input
                  type="text"
                  value={currentBalanceStr}
                  onChange={(e) => setCurrentBalanceStr(e.target.value)}
                  className="input input-bordered w-full bg-base-200 pl-6 select-sm h-[2rem]"
                />
              </div>
            </label>

            {/* Monthly Savings */}
            <label className="block">
              <span className="text-xs font-semibold text-base-content/70 block mb-1">Monthly Contribution Target</span>
              <div className="relative">
                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-base-content/50 text-sm pointer-events-none">$</span>
                <input
                  type="text"
                  value={monthlyContribStr}
                  onChange={(e) => setMonthlyContribStr(e.target.value)}
                  className="input input-bordered w-full bg-base-200 pl-6 select-sm h-[2rem]"
                />
              </div>
            </label>
          </div>
        </div>

        {/* STEP 2: College Expenses */}
        <div className="bg-base-200/40 rounded-2xl p-5 sm:p-6 border border-base-300/50 space-y-4">
          <h3 className="text-base font-bold text-base-content/90 flex items-center gap-2">
            <span className="bg-secondary/10 text-secondary w-6 h-6 rounded-full flex items-center justify-center text-xs font-extrabold">2</span>
            College Target Expenses
          </h3>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <span className="text-xs font-semibold text-base-content/70 block mb-1.5">Select College Type (2025/2026 sticker prices)</span>
              <select
                value={expensePreset}
                onChange={(e) => setExpensePreset(e.target.value)}
                className="select select-bordered select-sm w-full bg-base-200 h-[2.5rem]"
              >
                <option value="in_state_public">Public 4-Year (In-State) - $25,850/yr</option>
                <option value="out_of_state_public">Public 4-Year (Out-of-State) - $45,780/yr</option>
                <option value="private">Private Nonprofit 4-Year - $60,920/yr</option>
                <option value="community">Public 2-Year (Community College) - $14,150/yr</option>
                <option value="vocational">Trade / Vocational School - $17,500/yr</option>
                <option value="grad_masters">Master's Degree (M.A. / M.S.) - $52,000/yr (2 yrs)</option>
                <option value="grad_mba">MBA / Business School - $80,000/yr (2 yrs)</option>
                <option value="grad_law">Law School (J.D.) - $68,000/yr (3 yrs)</option>
                <option value="grad_medical">Medical School (M.D.) - $77,000/yr (4 yrs)</option>
                <option value="k12_private">K-12 Private School - $15,000/yr (10 yrs)</option>
                <option value="custom">Custom Parameters...</option>
              </select>
            </div>

            {expensePreset === 'custom' && (
              <div className="grid grid-cols-2 gap-3 animate-fade-in">
                <label className="block">
                  <span className="text-xs font-semibold text-base-content/70 block mb-0.5">Annual Sticker Cost</span>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-base-content/50 text-xs pointer-events-none">$</span>
                    <input
                      type="text"
                      value={customAnnualCostStr}
                      onChange={(e) => setCustomAnnualCostStr(e.target.value)}
                      className="input input-bordered w-full bg-base-200 select-sm h-[2rem] pl-6 text-xs"
                    />
                  </div>
                </label>

                <label className="block">
                  <span className="text-xs font-semibold text-base-content/70 block mb-0.5">Years of Attendance</span>
                  <input
                    type="number"
                    min="1"
                    max="6"
                    value={customYearsStr}
                    onChange={(e) => setCustomYearsStr(e.target.value)}
                    className="input input-bordered w-full bg-base-200 select-sm h-[2rem] text-xs"
                  />
                </label>
              </div>
            )}
          </div>

          <div className="text-xs text-base-content/60 bg-base-200/20 p-3 rounded-xl border border-base-300/30 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <CalendarDays className="w-4 h-4 text-secondary flex-shrink-0" />
              <span>
                Saving period: <span className="text-secondary font-bold">{yearsToCollege} years</span>.
                Inflation-adjusted total cost for a <span className="font-semibold">{collegeYears}-year</span> program: <span className="text-error font-bold">{fmtMoney(totalFutureCost)}</span>.
              </span>
            </div>
          </div>
        </div>

        {/* STEP 3: Asset Mix & Index Choices */}
        <div className="bg-base-200/40 rounded-2xl p-5 sm:p-6 border border-base-300/50 space-y-6">
          <div className="flex justify-between items-center border-b border-base-300 pb-3">
            <h3 className="text-base font-bold text-base-content/90 flex items-center gap-2">
              <span className="bg-secondary/10 text-secondary w-6 h-6 rounded-full flex items-center justify-center text-xs font-extrabold">3</span>
              Asset Allocation Mix & Index Selection
            </h3>
            {/* auto balance button */}
            <button
              type="button"
              onClick={handleAutoBalance}
              className="btn btn-xs btn-outline btn-secondary flex items-center gap-1 normal-case"
            >
              <Scale className="w-3 h-3" /> Auto-Balance (100%)
            </button>
          </div>

          {/* Allocation sliders */}
          <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4">
              {/* S&P 500 / Indices */}
              <div>
                <div className="flex justify-between text-xs font-semibold mb-0.5">
                  <span className="text-base-content/85 flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full bg-blue-500" /> S&P 500 / Major Indices ({ASSET_RETURNS.indices}% return)
                  </span>
                  <span className="text-secondary font-bold">{allocIndices}%</span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={allocIndices}
                  onChange={(e) => setAllocIndices(parseInt(e.target.value, 10) || 0)}
                  className="range range-xs range-secondary"
                />
              </div>

              {/* Growth */}
              <div>
                <div className="flex justify-between text-xs font-semibold mb-0.5">
                  <span className="text-base-content/85 flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full bg-emerald-500" /> Growth (Active Stock) ({ASSET_RETURNS.growth}% return)
                  </span>
                  <span className="text-secondary font-bold">{allocGrowth}%</span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={allocGrowth}
                  onChange={(e) => setAllocGrowth(parseInt(e.target.value, 10) || 0)}
                  className="range range-xs range-secondary"
                />
              </div>

              {/* Aggressive Growth */}
              <div>
                <div className="flex justify-between text-xs font-semibold mb-0.5">
                  <span className="text-base-content/85 flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full bg-indigo-500" /> Aggressive Growth ({ASSET_RETURNS.aggressive}% return)
                  </span>
                  <span className="text-secondary font-bold">{allocAggressive}%</span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={allocAggressive}
                  onChange={(e) => setAllocAggressive(parseInt(e.target.value, 10) || 0)}
                  className="range range-xs range-secondary"
                />
              </div>

              {/* Balanced / Hybrid */}
              <div>
                <div className="flex justify-between text-xs font-semibold mb-0.5">
                  <span className="text-base-content/85 flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full bg-purple-500" /> Balanced / Hybrid ({ASSET_RETURNS.balanced}% return)
                  </span>
                  <span className="text-secondary font-bold">{allocBalanced}%</span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={allocBalanced}
                  onChange={(e) => setAllocBalanced(parseInt(e.target.value, 10) || 0)}
                  className="range range-xs range-secondary"
                />
              </div>

              {/* Bonds */}
              <div>
                <div className="flex justify-between text-xs font-semibold mb-0.5">
                  <span className="text-base-content/85 flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full bg-amber-500" /> Bonds / Fixed Income ({ASSET_RETURNS.bonds}% return)
                  </span>
                  <span className="text-secondary font-bold">{allocBonds}%</span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={allocBonds}
                  onChange={(e) => setAllocBonds(parseInt(e.target.value, 10) || 0)}
                  className="range range-xs range-secondary"
                />
              </div>

              {/* Money Market */}
              <div>
                <div className="flex justify-between text-xs font-semibold mb-0.5">
                  <span className="text-base-content/85 flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-full bg-rose-500" /> Money Market / Cash ({ASSET_RETURNS.moneyMarket}% return)
                  </span>
                  <span className="text-secondary font-bold">{allocMoneyMarket}%</span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={allocMoneyMarket}
                  onChange={(e) => setAllocMoneyMarket(parseInt(e.target.value, 10) || 0)}
                  className="range range-xs range-secondary"
                />
              </div>
            </div>
          </div>

          {/* Allocation validation warning */}
          <div className="flex items-center justify-between p-3 rounded-xl border bg-base-200/50 border-base-300">
            <div className="flex items-center gap-2 text-xs">
              <Sliders className="w-4 h-4 text-secondary" />
              <span>
                Total Allocation: <span className={`font-bold ${isAllocationValid ? 'text-success' : 'text-error'}`}>{totalAllocation}%</span>
              </span>
            </div>
            {!isAllocationValid && (
              <span className="text-[10px] text-error font-semibold flex items-center gap-1">
                <AlertCircle className="w-3 h-3" /> Adjust sliders to sum to exactly 100%.
              </span>
            )}
            {isAllocationValid && (
              <span className="text-[10px] text-success font-semibold flex items-center gap-1">
                <CheckCircle2 className="w-3 h-3" /> Weighted Return: <span className="font-bold">{weightedHistoricalReturn.toFixed(2)}%</span>
              </span>
            )}
          </div>

          {/* Index Preferences & Return Assumption Overrides */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-2">
            {/* Preferred Index Selector */}
            <div>
              <span className="text-xs font-bold text-base-content/80 block mb-1.5">Choose Preferred Major Index Category</span>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setPreferredIndex('sp500')}
                  className={`btn btn-xs normal-case h-10 flex flex-col items-center justify-center rounded-lg border border-base-300 ${
                    preferredIndex === 'sp500' ? 'btn-secondary text-secondary-content' : 'btn-ghost bg-base-200/40'
                  }`}
                >
                  <span className="font-bold text-[11px]">S&P 500</span>
                  <span className="text-[8px] opacity-75">Large Cap Core</span>
                </button>

                <button
                  type="button"
                  onClick={() => setPreferredIndex('nasdaq100')}
                  className={`btn btn-xs normal-case h-10 flex flex-col items-center justify-center rounded-lg border border-base-300 ${
                    preferredIndex === 'nasdaq100' ? 'btn-secondary text-secondary-content' : 'btn-ghost bg-base-200/40'
                  }`}
                >
                  <span className="font-bold text-[11px]">Nasdaq 100</span>
                  <span className="text-[8px] opacity-75">Growth / Tech</span>
                </button>

                <button
                  type="button"
                  onClick={() => setPreferredIndex('russell2000')}
                  className={`btn btn-xs normal-case h-10 flex flex-col items-center justify-center rounded-lg border border-base-300 ${
                    preferredIndex === 'russell2000' ? 'btn-secondary text-secondary-content' : 'btn-ghost bg-base-200/40'
                  }`}
                >
                  <span className="font-bold text-[11px]">Russell 2000</span>
                  <span className="text-[8px] opacity-75">Small Cap</span>
                </button>

                <button
                  type="button"
                  onClick={() => setPreferredIndex('total_market')}
                  className={`btn btn-xs normal-case h-10 flex flex-col items-center justify-center rounded-lg border border-base-300 ${
                    preferredIndex === 'total_market' ? 'btn-secondary text-secondary-content' : 'btn-ghost bg-base-200/40'
                  }`}
                >
                  <span className="font-bold text-[11px]">Total Market</span>
                  <span className="text-[8px] opacity-75">Broad US Index</span>
                </button>
              </div>
            </div>

            {/* Expected Return Override panel */}
            <div className="space-y-3 bg-base-200/30 p-4 rounded-xl border border-base-300/30">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={overrideReturn}
                  onChange={(e) => setOverrideReturn(e.target.checked)}
                  className="checkbox checkbox-secondary checkbox-xs"
                />
                <span className="text-xs font-bold text-base-content/85">Override expected return target</span>
              </label>

              {overrideReturn ? (
                <label className="block animate-fade-in">
                  <span className="text-xs text-base-content/60 block mb-1">Set Custom Return Assumption (% per year):</span>
                  <div className="relative">
                    <input
                      type="text"
                      value={customReturnStr}
                      onChange={(e) => setCustomReturnStr(e.target.value)}
                      className="input input-bordered w-full bg-base-200 select-sm h-[2rem]"
                    />
                    <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-base-content/40">%</span>
                  </div>
                </label>
              ) : (
                <div className="text-xs text-base-content/50 leading-normal pt-1">
                  System dynamically calculated a <span className="font-bold text-secondary">{weightedHistoricalReturn.toFixed(2)}%</span> return rate based on your custom mix. Checked on long term historical averages.
                </div>
              )}
            </div>
          </div>
        </div>

        {/* STEP 4: RECOMMENDATION ENGINE VERDICT */}
        <div className="bg-secondary/10 border-2 border-secondary/30 rounded-3xl p-6 shadow-xl space-y-4">
          <div className="flex justify-between items-center border-b border-secondary/20 pb-3">
            <h3 className="text-lg font-extrabold text-secondary flex items-center gap-2">
              <Award className="w-6 h-6" />
              529 Plan Recommendation Verdict
            </h3>
            <span className="text-xs bg-secondary/20 text-secondary px-2.5 py-1 rounded-full font-bold uppercase tracking-wide">
              {recommendationVerdict.badge}
            </span>
          </div>

          <p className="text-sm text-base-content/95 leading-relaxed font-medium">
            {recommendationVerdict.verdictText}
          </p>

          <div className="bg-base-200/80 rounded-2xl p-4 border border-base-300/50 flex items-start gap-3">
            <CheckCircle2 className="w-5 h-5 text-emerald-400 mt-0.5 flex-shrink-0" />
            <div>
              <div className="text-xs uppercase tracking-wider text-base-content/50 font-bold">Action Plan:</div>
              <p className="text-sm text-base-content/85 mt-1 font-semibold">{recommendationVerdict.action}</p>
            </div>
          </div>

          {/* Plan Comparative View (Side-by-Side Cards) */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 pt-2">
            {/* Local Plan */}
            <div className="bg-base-200/50 border border-base-300 rounded-2xl p-5 space-y-4 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex justify-between items-start border-b border-base-300 pb-2">
                  <div>
                    <span className="text-[10px] text-base-content/50 uppercase font-bold tracking-wider">Your Local State Plan ({localPlan.stateCode})</span>
                    <h4 className="font-extrabold text-base text-base-content mt-0.5 leading-snug">
                      {localPlan.planName}
                    </h4>
                  </div>
                  <span className="text-[10px] bg-base-300 text-base-content/70 px-2 py-0.5 rounded font-extrabold uppercase">
                    {localPlan.rating} Rated
                  </span>
                </div>

                <div className="space-y-1.5 text-xs">
                  <div className="flex justify-between">
                    <span className="text-base-content/50">Program Type:</span>
                    <span className="font-semibold text-base-content/75">{localPlan.type}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-base-content/50">Manager:</span>
                    <span className="font-semibold text-base-content/75">{localPlan.manager}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-base-content/50">Avg Asset Fee:</span>
                    <span className="font-semibold">{localPlan.avgFeePct.toFixed(2)}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-base-content/50">Est. Tax Refund:</span>
                    <span className={`font-semibold ${localTaxSavings > 0 ? 'text-success' : 'text-base-content/60'}`}>
                      {localTaxSavings > 0 ? `${fmtMoney(localTaxSavings)}/yr` : 'None'}
                    </span>
                  </div>
                  <div className="border-t border-base-300/50 pt-2 text-[11px] text-base-content/60 leading-relaxed min-h-[50px]">
                    <span className="font-bold text-base-content/75 block mb-0.5">Local Tax Rule:</span>
                    {localPlan.notes}
                  </div>
                </div>
              </div>

              <a
                href={localPlan.websiteUrl}
                target="_blank"
                rel="noreferrer"
                className="btn btn-sm btn-outline btn-block text-xs flex items-center gap-1.5 mt-3 normal-case rounded-xl"
              >
                Visit Local Plan site <ExternalLink className="w-3.5 h-3.5" />
              </a>
            </div>

            {/* Out-of-State Recommended Plan */}
            <div className="bg-base-200/50 border border-base-300 rounded-2xl p-5 space-y-4 flex flex-col justify-between">
              <div className="space-y-2">
                <div className="flex justify-between items-start border-b border-base-300 pb-2">
                  <div className="flex-1">
                    <span className="text-[10px] text-base-content/50 uppercase font-bold tracking-wider block mb-1">Compare National Plan</span>
                    <select
                      value={compareNationalCode}
                      onChange={(e) => setCompareNationalCode(e.target.value)}
                      className="select select-bordered select-xs w-full bg-base-200 text-secondary font-bold"
                    >
                      {NATIONAL_529_PLANS.map((p) => (
                        <option key={p.stateCode} value={p.stateCode}>
                          {p.planName} ({p.stateCode})
                        </option>
                      ))}
                    </select>
                  </div>
                  <span className="text-[10px] bg-secondary/20 text-secondary px-2 py-0.5 rounded font-extrabold uppercase ml-2 mt-5">
                    {comparedNationalPlan.rating} Rated
                  </span>
                </div>

                <div className="space-y-1.5 text-xs">
                  <div className="flex justify-between">
                    <span className="text-base-content/50">Program Type:</span>
                    <span className="font-semibold text-base-content/75">Direct-Sold Savings</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-base-content/50">Manager:</span>
                    <span className="font-semibold text-base-content/75">
                      {comparedNationalPlan.stateCode === 'UT'
                        ? 'my529 Board / Vanguard'
                        : comparedNationalPlan.stateCode === 'NY'
                        ? 'Vanguard'
                        : comparedNationalPlan.stateCode === 'NV'
                        ? 'Vanguard'
                        : 'BlackRock / T. Rowe'}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-base-content/50">Avg Asset Fee:</span>
                    <span className="font-semibold text-secondary">{comparedNationalPlan.avgFeePct.toFixed(2)}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-base-content/50">Tax Deduction:</span>
                    <span className="font-semibold text-base-content/60">
                      {localPlan.hasTaxParity && localTaxSavings > 0 ? `${fmtMoney(localTaxSavings)}/yr` : 'None (for out-of-state)'}
                    </span>
                  </div>
                  <div className="border-t border-base-300/50 pt-2 text-[11px] text-base-content/60 leading-relaxed min-h-[50px]">
                    <span className="font-bold text-secondary block mb-0.5">Best for: {comparedNationalPlan.bestFor}</span>
                    {comparedNationalPlan.optionsDescription}
                  </div>
                </div>
              </div>

              <a
                href={comparedNationalPlan.websiteUrl}
                target="_blank"
                rel="noreferrer"
                className="btn btn-sm btn-secondary btn-block text-xs flex items-center gap-1.5 mt-3 normal-case rounded-xl text-secondary-content"
              >
                Visit Recommended Plan <ExternalLink className="w-3.5 h-3.5" />
              </a>
            </div>
          </div>
        </div>

        {/* STEP 5: PROJECTIONS & STATS */}
        <div className="bg-base-200/40 rounded-2xl p-5 sm:p-6 border border-base-300/50 space-y-6">
          <h3 className="text-base font-bold text-base-content/90 flex items-center gap-2 border-b border-base-300 pb-3">
            <span className="bg-secondary/10 text-secondary w-6 h-6 rounded-full flex items-center justify-center text-xs font-extrabold">4</span>
            Savings & Drawdown Projections
          </h3>

          {/* Financial summary stats */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-base-200 rounded-2xl p-4 border border-base-300/40">
              <div className="text-[10px] uppercase font-bold text-base-content/50 flex items-center gap-1">
                <Wallet className="w-3.5 h-3.5 text-secondary" /> Savings at Age 18
              </div>
              <div className="text-lg font-black mt-1 text-secondary">
                {fmtMoney(simulation.projectedBalanceAtAge18)}
              </div>
              <span className="text-[9px] text-base-content/40 block mt-0.5">Accumulation Cap</span>
            </div>

            <div className="bg-base-200 rounded-2xl p-4 border border-base-300/40">
              <div className="text-[10px] uppercase font-bold text-base-content/50 flex items-center gap-1">
                <Coins className="w-3.5 h-3.5 text-error" /> College Outlay
              </div>
              <div className="text-lg font-black mt-1 text-error">
                {fmtMoney(totalFutureCost)}
              </div>
              <span className="text-[9px] text-base-content/40 block mt-0.5">Nominal Cost</span>
            </div>

            <div className="bg-base-200 rounded-2xl p-4 border border-base-300/40">
              <div className="text-[10px] uppercase font-bold text-base-content/50 flex items-center gap-1">
                <CircleDollarSign className="w-3.5 h-3.5 text-success" /> Monthly Target
              </div>
              <div className="text-lg font-black mt-1 text-success">
                {fmtMoney(requiredMonthlySavings)}
              </div>
              <span className="text-[9px] text-base-content/40 block mt-0.5">To Cover Costs</span>
            </div>

            <div className="bg-base-200 rounded-2xl p-4 border border-base-300/40">
              <div className="text-[10px] uppercase font-bold text-base-content/50 flex items-center gap-1">
                <PiggyBank className="w-3.5 h-3.5 text-base-content/70" /> Final Runway
              </div>
              <div
                className={`text-lg font-black mt-1 ${
                  simulation.finalBalance >= 0 ? 'text-success' : 'text-error'
                }`}
              >
                {simulation.finalBalance >= 0
                  ? `+${fmtMoney(simulation.finalBalance)}`
                  : `-${fmtMoney(Math.abs(simulation.finalBalance))}`}
              </div>
              <span className="text-[9px] text-base-content/40 block mt-0.5">
                {simulation.finalBalance >= 0 ? 'Surplus' : 'Deficit'}
              </span>
            </div>
          </div>

          {/* Line Chart */}
          <div className="bg-base-200/20 rounded-2xl p-4 border border-base-300/30">
            <div className="h-[18rem] w-full">
              <Line data={chartData} options={chartOpts} />
            </div>
          </div>
        </div>

        {/* WEALTH ADVISORY & STRATEGIES DASHBOARD */}
        <div className="bg-base-200/40 rounded-2xl p-5 sm:p-6 border border-base-300/50 space-y-5">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-base-300 pb-4">
            <div>
              <h3 className="text-base font-bold text-base-content/90 flex items-center gap-2">
                <Award className="w-5 h-5 text-secondary animate-pulse" />
                Wealth Planning & Financial Aid Advisory
              </h3>
              <p className="text-xs text-base-content/50 mt-0.5">
                Strategic college funding guidelines customized by household financial profile
              </p>
            </div>
            
            {/* Tab Switcher */}
            <div className="flex bg-base-350 p-1 rounded-xl gap-1 self-start sm:self-center border border-base-300">
              <button
                type="button"
                onClick={() => setAdvisoryProfile('moderate')}
                className={`btn btn-xs normal-case rounded-lg border-none px-3 ${advisoryProfile === 'moderate' ? 'bg-secondary text-secondary-content' : 'btn-ghost text-base-content/60'}`}
              >
                Moderate (&lt;$120k)
              </button>
              <button
                type="button"
                onClick={() => setAdvisoryProfile('high')}
                className={`btn btn-xs normal-case rounded-lg border-none px-3 ${advisoryProfile === 'high' ? 'bg-secondary text-secondary-content' : 'btn-ghost text-base-content/60'}`}
              >
                High Earner ($120k-$400k)
              </button>
              <button
                type="button"
                onClick={() => setAdvisoryProfile('hnw')}
                className={`btn btn-xs normal-case rounded-lg border-none px-3 ${advisoryProfile === 'hnw' ? 'bg-secondary text-secondary-content' : 'btn-ghost text-base-content/60'}`}
              >
                High Net Worth (&gt;$400k)
              </button>
            </div>
          </div>

          {/* Tab Content */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {advisoryProfile === 'moderate' && (
              <>
                <div className="bg-base-200/60 border border-base-300/60 rounded-xl p-4 space-y-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-secondary">
                    <span className="bg-secondary/15 text-secondary px-2 py-0.5 rounded text-[10px] uppercase font-black">Aid Impact</span>
                    FAFSA Asset Assessment Shielding
                  </div>
                  <p className="text-[11px] text-base-content/60 leading-relaxed">
                    <strong>Asset Assessment Rule:</strong> Parent-owned 529 plans are assessed for financial aid at a maximum rate of <strong>5.64%</strong>. In contrast, student-owned personal savings, UTMA, or UGMA custodial accounts are assessed at a flat <strong>20.00%</strong> rate.
                  </p>
                  <p className="text-[11px] text-secondary font-semibold">
                    💡 Action: Avoid UGMA/UTMA custodial accounts. Save in a parent-owned 529 plan to minimize the impact on need-based aid.
                  </p>
                </div>

                <div className="bg-base-200/60 border border-base-300/60 rounded-xl p-4 space-y-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-secondary">
                    <span className="bg-success/15 text-success px-2 py-0.5 rounded text-[10px] uppercase font-black">Loophole</span>
                    The Grandparent 529 Advantage
                  </div>
                  <p className="text-[11px] text-base-content/60 leading-relaxed">
                    <strong>FAFSA Simplification Update:</strong> Under simplified FAFSA rules, grandparent-owned 529 plans are completely excluded from federal financial aid calculations. Distributions from grandparent-owned plans to pay for college costs do not count as student income.
                  </p>
                  <p className="text-[11px] text-success font-semibold">
                    💡 Action: Have grandparents set up accounts directly. It keeps the asset invisible for federal need-based calculations.
                  </p>
                </div>

                <div className="bg-base-200/60 border border-base-300/60 rounded-xl p-4 space-y-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-secondary">
                    <span className="bg-warning/15 text-warning-content px-2 py-0.5 rounded text-[10px] uppercase font-black">Tax Strategy</span>
                    AOTC Tax Credit Coordination
                  </div>
                  <p className="text-[11px] text-base-content/60 leading-relaxed">
                    <strong>No Double-Dipping:</strong> The American Opportunity Tax Credit (AOTC) provides up to <strong>$2,500/year</strong> in tax credits. However, you cannot claim the AOTC and pay with 529 funds for the same tuition dollars.
                  </p>
                  <p className="text-[11px] text-warning-content/95 font-semibold">
                    💡 Action: Pay $4,000 of tuition out-of-pocket (cash/loans) to claim the full AOTC, and use 529 withdrawals only for remaining costs (housing/books).
                  </p>
                </div>

                <div className="bg-base-200/60 border border-base-300/60 rounded-xl p-4 space-y-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-secondary">
                    <span className="bg-info/15 text-info px-2 py-0.5 rounded text-[10px] uppercase font-black">Govt Benefits</span>
                    State Matching Grants
                  </div>
                  <p className="text-[11px] text-base-content/60 leading-relaxed">
                    <strong>Free Money:</strong> States like Colorado, Maryland, Louisiana, and Kansas offer matching contribution grants (ranging from $100 to $1,000+) for residents under certain income limits.
                  </p>
                  <p className="text-[11px] text-info font-semibold">
                    💡 Action: Check your home state matching grant program details before prioritizing out-of-state direct-sold plans.
                  </p>
                </div>
              </>
            )}

            {advisoryProfile === 'high' && (
              <>
                <div className="bg-base-200/60 border border-base-300/60 rounded-xl p-4 space-y-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-secondary">
                    <span className="bg-secondary/15 text-secondary px-2 py-0.5 rounded text-[10px] uppercase font-black">Merit Aid</span>
                    FAFSA vs CSS Profile Target Strategy
                  </div>
                  <p className="text-[11px] text-base-content/60 leading-relaxed">
                    <strong>Institutional Aid:</strong> High earners won't qualify for need-based federal aid, but can get merit scholarships. However, CSS Profile colleges (private/elite) assess home equity and grandparent-owned 529 plans, whereas FAFSA-only schools ignore them.
                  </p>
                  <p className="text-[11px] text-secondary font-semibold">
                    💡 Action: Target FAFSA-only public flagship universities to shield grandparent-held assets and primary home equity.
                  </p>
                </div>

                <div className="bg-base-200/60 border border-base-300/60 rounded-xl p-4 space-y-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-secondary">
                    <span className="bg-success/15 text-success px-2 py-0.5 rounded text-[10px] uppercase font-black">Fees</span>
                    Split-Contribution Optimization
                  </div>
                  <p className="text-[11px] text-base-content/60 leading-relaxed">
                    <strong>Tax Credit Caps:</strong> High earners in states like New York, Illinois, or Indiana get great tax breaks but face strict contribution limits (e.g. $10,000/year cap for NY).
                  </p>
                  <p className="text-[11px] text-success font-semibold">
                    💡 Action: Contribute up to your state's tax break cap locally, then route any excess savings to low-fee national plans (like Utah or NY Saves).
                  </p>
                </div>

                <div className="bg-base-200/60 border border-base-300/60 rounded-xl p-4 space-y-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-secondary">
                    <span className="bg-warning/15 text-warning-content px-2 py-0.5 rounded text-[10px] uppercase font-black">Protection</span>
                    Statutory Asset Shielding
                  </div>
                  <p className="text-[11px] text-base-content/60 leading-relaxed">
                    <strong>Creditor Shelter:</strong> In states like Pennsylvania, New York, and Texas, 529 plans are fully shielded from creditors. This protects educational savings from business or personal lawsuit liabilities.
                  </p>
                  <p className="text-[11px] text-warning-content/95 font-semibold">
                    💡 Action: High earners with business/professional liability should hold college funds inside 529s rather than standard brokerage accounts.
                  </p>
                </div>

                <div className="bg-base-200/60 border border-base-300/60 rounded-xl p-4 space-y-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-secondary">
                    <span className="bg-info/15 text-info px-2 py-0.5 rounded text-[10px] uppercase font-black">Exit Route</span>
                    Roth IRA Rollover Exit Strategy
                  </div>
                  <p className="text-[11px] text-base-content/60 leading-relaxed">
                    <strong>Secure 2.0 Protection:</strong> If the child receives scholarships or doesn't go to college, up to <strong>$35,000</strong> lifetime can be rolled over to the beneficiary's Roth IRA.
                  </p>
                  <p className="text-[11px] text-info font-semibold">
                    💡 Action: Avoid withdrawing the funds in a panic if plans change; roll it over to kickstart your child's tax-free retirement.
                  </p>
                </div>
              </>
            )}

            {advisoryProfile === 'hnw' && (
              <>
                <div className="bg-base-200/60 border border-base-300/60 rounded-xl p-4 space-y-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-secondary">
                    <span className="bg-secondary/15 text-secondary px-2 py-0.5 rounded text-[10px] uppercase font-black">Estate Tax</span>
                    5-Year Superfunding (2026 Exclusion)
                  </div>
                  <p className="text-[11px] text-base-content/60 leading-relaxed">
                    <strong>Accelerated Gifting:</strong> The 2026 gift exclusion is <strong>$19,000/yr</strong>. Superfunding allows contributing 5 years of gifts at once without gift tax: up to <strong>$95,000</strong> per individual or <strong>$190,000</strong> for married couples.
                  </p>
                  <p className="text-[11px] text-secondary font-semibold">
                    💡 Action: Make a lump-sum $190,000 contribution. It removes assets from your taxable estate immediately while maximizing compound tax-deferred growth.
                  </p>
                </div>

                <div className="bg-base-200/60 border border-base-300/60 rounded-xl p-4 space-y-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-secondary">
                    <span className="bg-success/15 text-success px-2 py-0.5 rounded text-[10px] uppercase font-black">Dynasty 529</span>
                    Generation-Skipping Wealth Transfers
                  </div>
                  <p className="text-[11px] text-base-content/60 leading-relaxed">
                    <strong>Dynasty Education Funds:</strong> You can change the beneficiary of a 529 plan to any family member. This allows HNW families to build tax-sheltered education trust pools that transfer down generations.
                  </p>
                  <p className="text-[11px] text-success font-semibold">
                    💡 Action: Build a "Dynasty 529" to fund educations of grandchildren and future generations, removing wealth from your estate without trust administrative costs.
                  </p>
                </div>

                <div className="bg-base-200/60 border border-base-300/60 rounded-xl p-4 space-y-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-secondary">
                    <span className="bg-warning/15 text-warning-content px-2 py-0.5 rounded text-[10px] uppercase font-black">Prepaid</span>
                    Private College 529 Inflation Hedge
                  </div>
                  <p className="text-[11px] text-base-content/60 leading-relaxed">
                    <strong>Locking in Tuition:</strong> The Private College 529 Plan lets you buy tuition certificates at today's rates, redeemable at over 300 member private universities (including Stanford, MIT, and Princeton).
                  </p>
                  <p className="text-[11px] text-warning-content/95 font-semibold">
                    💡 Action: If you target elite private schools, allocate a portion of college funds here to hedge against private school tuition inflation.
                  </p>
                </div>

                <div className="bg-base-200/60 border border-base-300/60 rounded-xl p-4 space-y-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-secondary">
                    <span className="bg-info/15 text-info px-2 py-0.5 rounded text-[10px] uppercase font-black">Estate Control</span>
                    Asset Exclusion with Account Control
                  </div>
                  <p className="text-[11px] text-base-content/60 leading-relaxed">
                    <strong>Unique Trust Alternative:</strong> Unlike standard trusts where you must surrender control, a 529 plan removes assets from your taxable estate while letting you maintain control (change beneficiaries, control investments, or reclaim funds).
                  </p>
                  <p className="text-[11px] text-info font-semibold">
                    💡 Action: Leverage 529 plans for estate tax management before setting up more complex, restrictive trust vehicles.
                  </p>
                </div>
              </>
            )}
          </div>
        </div>

        {/* STEP 6: RULES & Updates */}
        <div className="bg-base-200/40 rounded-2xl p-6 border border-base-300/50 space-y-4">
          <h3 className="text-sm font-bold text-base-content/85 flex items-center gap-2 border-b border-base-300 pb-2">
            <Info className="w-4 h-4 text-secondary" />
            Key 529 Planning Provisions & SECURE 2.0 Updates
          </h3>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 text-xs text-base-content/65 leading-relaxed">
            <div className="space-y-3">
              <div className="flex gap-2">
                <ArrowRightLeft className="w-4 h-4 text-secondary flex-shrink-0 mt-0.5" />
                <div>
                  <span className="font-bold text-base-content/80 block">Roth IRA Rollover Provisions</span>
                  Under SECURE 2.0, beneficiaries can roll over unused 529 funds to a Roth IRA tax-free (lifetime cap of **$35,000**). The 529 account must be active for 15+ years, and funds must have been in the account for 5+ years.
                </div>
              </div>

              <div className="flex gap-2">
                <Coins className="w-4 h-4 text-secondary flex-shrink-0 mt-0.5" />
                <div>
                  <span className="font-bold text-base-content/80 block">2026 Federal Gift Tax Exemptions</span>
                  Contribute up to **$19,000** annually per individual (**$38,000** joint) without gift-tax reporting. Alternatively, superfund accounts with 5 years of gifts at once: up to **$95,000** single or **$190,000** joint.
                </div>
              </div>
            </div>

            <div className="space-y-3">
              <div className="flex gap-2">
                <GraduationCap className="w-4 h-4 text-secondary flex-shrink-0 mt-0.5" />
                <div>
                  <span className="font-bold text-base-content/80 block">K-12 Elementary & Secondary Tuition</span>
                  Withdraw up to **$10,000** per year per student tax-free to cover tuition at elementary and secondary private, public, or religious schools.
                </div>
              </div>

              <div className="flex gap-2">
                <ShieldAlert className="w-4 h-4 text-secondary flex-shrink-0 mt-0.5" />
                <div>
                  <span className="font-bold text-base-content/80 block">Trade Schools, Loans & Apprenticeships</span>
                  Qualified 529 withdrawals can be used for accredited trade schools, vocational programs, and certified apprenticeships. Up to a **$10,000** lifetime cap can be used to pay off student loans.
                </div>
              </div>
            </div>
          </div>
        </div>

      </div>

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
