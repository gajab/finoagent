// ---------------------------------------------------------------------------
// Retirement Portfolio Longevity — Monte Carlo engine.
//
// Historical mode bootstrap-samples whole years from the chosen window, so
// stock returns, bond returns and inflation stay jointly correlated. Manual
// overrides are DETERMINISTIC: a user-provided return/inflation is applied as
// that fixed value every year (no resampled volatility). If both returns and
// inflation are manual and LTC is off, the run collapses to a single path.
//
// Tax-mode accounting (per year):
//   1. Rebalance the taxable account to its target stock/bond mix — selling
//      appreciated stock realizes LTCG.
//   2. The taxable account throws off cash: qualified dividends (stock sleeve,
//      taxed at capital-gains rates) and bond coupons (taxed as ordinary).
//   3. Spending waterfall: outside income + SS + dividends + interest + RMD →
//      sell taxable stock (LTCG on the gain slice) → sell taxable bonds (no
//      gain by construction) → traditional (ordinary income) → Roth.
//   4. Federal bracket tax + LTCG stacking + NIIT + state tax + IRMAA are
//      solved with a fixed-point loop (tax depends on withdrawals and back).
//   5. Traditional & Roth grow at their own allocation's blended return.
// ---------------------------------------------------------------------------

import {
  EXPENSE_CATEGORIES, CategoryId, HistYear, historicalWindow, stockSeriesForWindow,
  SIMPLE_DISCRETIONARY_SHARE, SS_CLAIM_FACTORS, SS_TAX_THRESHOLDS,
  ORDINARY_BRACKETS, LTCG_BRACKETS, STANDARD_DEDUCTION, bracketTopFor,
  NIIT_RATE, NIIT_THRESHOLD, irmaaAnnual,
  FilingStatus, TaxBracket, HIST_MIN_WINDOW, StockComposition,
  rmdStartAge, rmdDivisor, k401ElectiveLimit,
} from './data';
import { STATE_TAX } from './locations';

// ---------------------------------------------------------------------------
// Input / output types
// ---------------------------------------------------------------------------

export interface IncomeSource {
  id: string;
  name: string;
  annual: number;
  startAge: number;
  endAge: number;
  growthPct: number;
}

export interface LumpSum {
  id: string;
  name: string;
  amount: number;      // today's dollars
  age: number;
}

export interface SpendingPhases {
  gogoEndAge: number;
  slowgoEndAge: number;
  gogoMult: number;
  slowgoMult: number;
  nogoMult: number;
}

export interface LtcConfig {
  enabled: boolean;
  annualCost: number;
  persons: 1 | 2;
  probabilityPct: number;  // the ONLY stochastic element; 100% ⇒ certain event
  onsetAge: number;        // exact — care starts at this age (each person's own timeline)
  durationYears: number;   // exact years of care
}

export interface RothPlan {
  mode: 'off' | 'custom' | 'auto';
  // custom mode: fill up to `ceiling` in [startAge, endAge].
  // auto mode: each year the engine estimates the bracket future RMDs will
  // force and converts up to the top of any cheaper bracket — low-income
  // years automatically absorb bigger conversions.
  ceiling: 12 | 22 | 24;
  startAge: number;
  endAge: number;
}

/** How yearly cash needs are pulled from the portfolio. */
export type WithdrawalStrategy =
  | 'sequential'          // interest → dividends → sell stock → sell bonds; rebalance yearly
  | 'guarded'             // + after a stock year ≤ −down%, sell bonds before stocks
  | 'guarded_rebalance'   // + rebalance the taxable account only after stock years ≥ +up%
  | 'holistic';           // + household-level target (pre/post glide age) maintained by
                          //   re-locating stock inside 401k/Roth — rebalancing is tax-free

/** A forced return (percent) for one of the first retirement years — the
 *  sequence-of-returns What-If. `infl` null ⇒ keep the normal inflation path. */
export interface SequenceReturn { stock: number; bond: number; infl: number | null }

export interface SpouseConfig {
  enabled: boolean;
  age: number;
  planAge: number;
  ssFraAnnual: number;
  ssClaimAge: number;
  survivorExpensePct: number;
}

/** Stock share (%) of each account — asset location. */
export interface AccountAllocation {
  taxable: number;
  traditional: number;
  roth: number;
}

export interface SimInputs {
  currentAge: number;
  retireAge: number;
  planAge: number;
  corpus: number;
  expenseMode: 'simple' | 'category';
  annualExpenses: number;
  categoryExpenses: Record<CategoryId, number>;
  categorySpreads: Record<CategoryId, number>;
  phases: SpendingPhases | null;
  medicareFraction: number;  // medical cost from 65 on, as a fraction of TODAY'S
                             // (pre-Medicare) medical cost — 0.5 = half, inflation-adjusted
  lumpSums: LumpSum[];
  ltc: LtcConfig;
  stockPct: number;                      // single-pot mix (no-tax mode)
  accountStockPct: AccountAllocation;    // per-account mix (tax mode)
  stockDividendPct: number;              // qualified dividend yield, % of stock sleeve
  bondInterestPct: number;               // bond coupon yield, % of bond sleeve
  taxableStockGainPct: number;           // % of the taxable STOCK value that is unrealized gain at start
  taxableBondGainPct: number;            // % of the taxable BOND value that is unrealized gain at start
  // Optional equity-index composition; null ⇒ pure S&P 500. In historical mode
  // the yearly stock return becomes the weighted composite of these indices.
  stockComposition?: StockComposition | null;
  incomes: IncomeSource[];
  ssFraAnnual: number;
  ssClaimAge: number;
  spouse: SpouseConfig;
  returnsMode: 'historical' | 'manual';
  inflationMode: 'historical' | 'manual';
  windowStart: number;
  windowEnd: number;
  excludedYears: number[];
  manualStockPct: number;
  manualBondPct: number;
  manualInflationPct: number;
  numSims: number;
  taxEnabled: boolean;
  filing: FilingStatus;
  stateCode: string;
  taxableBal: number;
  traditionalBal: number;
  rothBal: number;
  guardrails: boolean;
  roth: RothPlan;
  withdrawalStrategy: WithdrawalStrategy;
  downThresholdPct: number;   // stock drop that triggers bonds-first selling (default 5)
  upThresholdPct: number;     // stock gain that permits rebalancing in guarded_rebalance (default 5)
  holisticPostStockPct: number; // holistic: household stock target from glideAge on (default 40)
  glideAge: number;             // holistic: age the target switches (default 65)
  // Cash buffer / bucket (sequence-risk mitigation): years of expenses held in
  // a zero-volatility cash reserve, spent first in the year after a market
  // decline so investments aren't sold low. 0 = off.
  cashBufferYears?: number;
  // What-If sequence-of-returns: forces the first N retirement years' returns.
  sequenceReturns?: SequenceReturn[] | null;
  // Future savings during the working years (age < retireAge). Amounts are in
  // today's dollars and inflated to nominal each year; contributions grow with
  // the portfolio. null = not saving (default).
  savings?: {
    untilAge: number;      // save through the year before this age (≤ retireAge)
    taxable: number;       // $/yr into the taxable brokerage
    roth: number;          // $/yr into a Roth IRA
    k401Employee: number;  // $/yr employee 401(k) deferral (when not maxing)
    k401Max: boolean;      // employee defers the IRS elective max instead
    k401EmployerMode?: 'amount' | 'match'; // employer contribution style (default 'amount')
    k401Employer: number;  // $/yr employer contribution when mode = 'amount'
    k401EmployerMatchPct?: number; // % of the employee contribution when mode = 'match'
  } | null;
}

export interface Pctls { p10: number; p25: number; p50: number; p75: number; p90: number }

export interface AgePercentiles extends Pctls { age: number }

/** One simulated year on a single path — everything needed for the ledger. */
export interface YearRecord {
  age: number;
  stockR: number;
  bondR: number;
  infl: number;
  defl: number;         // cumulative inflation at the START of the year
  expenses: number;     // TOTAL outflow incl. lump/LTC/IRMAA
  lump: number; ltcCost: number; irmaa: number;
  dividends: number;    // taxable-account qualified dividends (cash)
  interest: number;     // taxable-account bond coupons (cash)
  sellStock: number;    // taxable stock sold for spending
  sellBond: number;     // taxable bonds sold for spending
  tradExtra: number;    // traditional withdrawn beyond the RMD
  rothW: number;        // Roth withdrawn
  cashSpend: number;    // cash-buffer drawn for spending (tax-free)
  cashBal: number;      // cash-buffer balance, end of year
  contrib: number;      // future-savings contributed this year (accumulation)
  gains: number;        // realized LTCG (rebalancing + spending sales, excl. dividends)
  taxableIncome: number; // ordinary taxable income after deduction — picks the bracket
  fedTax: number; stateTax: number; niit: number;
  penalty: number;      // 10% early-withdrawal penalty (traditional/Roth before 59½)
  taxes: number;        // fed + state + NIIT + penalty
  ss: number; income: number;
  withdrawal: number;   // portfolio-funded spending (cash + sales + trad + Roth)
  rmd: number; conversion: number;
  marginalRate: number;
  stockShare: number;   // household stock fraction at year-end (verifies glide targets)
  balance: number; taxable: number; traditional: number; roth: number;
}

export interface MCResult {
  successRate: number;
  survivalByAge: { age: number; prob: number }[];
  percentilesNominal: AgePercentiles[];
  percentilesReal: AgePercentiles[];
  endingPercentiles: {
    nominal: { p10: number; p50: number; p90: number; p95: number; p99: number };
    real: { p10: number; p50: number; p90: number; p95: number; p99: number };
  };
  medianEnding: { nominal: number; real: number };
  medianDepletionAge: number | null;
  medianTotalTaxes: number;
  medianStateTaxes: number;
  corpusAtRetirementMedian: { nominal: number; real: number };
  firstYearExpensesMedian: number;
  representative: YearRecord[];
  windowStats: {
    stockMean: number; bondMean: number; cpiMean: number; portfolioMean: number;
    yearsUsed: number;
    effectiveStockPct: number;   // balance-weighted stock share across accounts
  };
  numSims: number;
  deterministic: boolean;   // true when both series are manual and LTC is off
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const idx = (sorted.length - 1) * p;
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

function bracketTax(taxable: number, brackets: TaxBracket[], scale: number): number {
  let tax = 0, prev = 0;
  for (const b of brackets) {
    const top = b.upTo === Infinity ? Infinity : b.upTo * scale;
    if (taxable <= prev) break;
    tax += (Math.min(taxable, top) - prev) * b.rate;
    prev = top;
  }
  return tax;
}

function marginalRateOf(taxable: number, brackets: TaxBracket[], scale: number): number {
  if (taxable <= 0) return 0;
  for (const b of brackets) {
    const top = b.upTo === Infinity ? Infinity : b.upTo * scale;
    if (taxable <= top) return b.rate;
  }
  return brackets[brackets.length - 1].rate;
}

/** LTCG (and qualified dividends) stacked on top of ordinary taxable income. */
function ltcgTax(gain: number, ordTaxable: number, brackets: TaxBracket[], scale: number): number {
  let tax = 0, prev = 0;
  const lo = ordTaxable, hi = ordTaxable + gain;
  for (const b of brackets) {
    const top = b.upTo === Infinity ? Infinity : b.upTo * scale;
    const from = Math.max(lo, prev), to = Math.min(hi, top);
    if (to > from) tax += (to - from) * b.rate;
    prev = top;
    if (top >= hi) break;
  }
  return tax;
}

function taxableSS(ssGross: number, otherAgi: number, filing: FilingStatus): number {
  if (ssGross <= 0) return 0;
  const { t1, t2 } = SS_TAX_THRESHOLDS[filing];
  const prov = otherAgi + 0.5 * ssGross;
  if (prov <= t1) return 0;
  if (prov <= t2) return Math.min(0.5 * (prov - t1), 0.5 * ssGross);
  return Math.min(0.85 * ssGross, 0.85 * (prov - t2) + Math.min(0.5 * (t2 - t1), 0.5 * ssGross));
}

/** Medical costs (incl. LTC) inflate ~1.5pp above headline CPI. */
const MED_SPREAD = 0.015;

/** Simulation horizon: until the LAST person's plan-to age. */
export function horizonYears(inp: SimInputs): number {
  return Math.max(
    1,
    inp.planAge - inp.currentAge,
    inp.spouse.enabled ? inp.spouse.planAge - inp.spouse.age : 0,
  );
}

function isDeterministic(inp: SimInputs): boolean {
  // LTC at 100% probability is a certain event (exact onset & duration), so
  // it adds no randomness — the plan is fully deterministic with manual series.
  const ltcRandom = inp.ltc.enabled && inp.ltc.probabilityPct < 100;
  return inp.returnsMode === 'manual' && inp.inflationMode === 'manual' && !ltcRandom;
}

// ---------------------------------------------------------------------------
// Stochastic long-term-care events
// ---------------------------------------------------------------------------

/** Persons-in-care count per simulated year index. Onset age and duration are
 *  EXACT (user-provided); only WHETHER care happens is random — and at 100%
 *  probability nothing is, so the event needs no simulation at all. */
function sampleLtcCare(inp: SimInputs, simIndex: number, seed: number, years: number): Uint8Array | null {
  if (!inp.ltc.enabled) return null;
  const care = new Uint8Array(years);
  const certain = inp.ltc.probabilityPct >= 100;
  const rng = certain ? null : mulberry32(((seed ^ 0x9e3779b9) + Math.imul(simIndex + 1, 2654435761)) >>> 0);
  const dur = Math.max(1, Math.round(inp.ltc.durationYears));
  for (let p = 0; p < inp.ltc.persons; p++) {
    const spouseP = p === 1 && inp.spouse.enabled;
    const baseAge = spouseP ? inp.spouse.age : inp.currentAge;
    const deathT = spouseP ? inp.spouse.planAge - inp.spouse.age : inp.planAge - inp.currentAge;
    if (rng && rng() >= inp.ltc.probabilityPct / 100) continue;
    const t0 = inp.ltc.onsetAge - baseAge;
    for (let k = Math.max(0, t0); k < Math.min(years, t0 + dur, deathT); k++) care[k]++;
  }
  return care;
}

// ---------------------------------------------------------------------------
// Single path simulation
// ---------------------------------------------------------------------------

interface PathResult {
  balances: Float64Array;
  deflators: Float64Array;
  depletionAge: number | null;
  totalTaxes: number;
  totalStateTaxes: number;
  records: YearRecord[] | null;
  corpusAtRetirement: number;
  corpusAtRetirementDefl: number;
  firstYearExpenses: number;
}

/** Planning-mean returns/inflation (decimals) used for forward projections
 *  inside a path (e.g. auto-Roth's estimate of the future RMD bracket). */
interface PlanMeans { stock: number; bond: number; cpi: number }

function simulatePath(
  inp: SimInputs, hist: HistYear[], stockSeries: number[], yearIdx: Uint16Array,
  ltcCare: Uint8Array | null, plan: PlanMeans, collect: boolean,
): PathResult {
  const years = horizonYears(inp);
  const birthYear = new Date().getFullYear() - inp.currentAge;
  const rmdAge = rmdStartAge(birthYear);
  const claimFactor = SS_CLAIM_FACTORS[inp.ssClaimAge] ?? 1;
  const spouseFactor = SS_CLAIM_FACTORS[inp.spouse.ssClaimAge] ?? 1;
  const stateInfo = inp.taxEnabled && inp.stateCode ? STATE_TAX[inp.stateCode] : undefined;

  // Allocations: single pot (no-tax) vs per account (asset location).
  const wGlobal = inp.stockPct / 100;
  const wTax = inp.taxEnabled ? inp.accountStockPct.taxable / 100 : wGlobal;
  const wTrad = inp.taxEnabled ? inp.accountStockPct.traditional / 100 : wGlobal;
  const wRothA = inp.taxEnabled ? inp.accountStockPct.roth / 100 : wGlobal;
  const divY = Math.max(0, inp.stockDividendPct) / 100;
  const couponY = Math.max(0, inp.bondInterestPct) / 100;

  // Accounts. Tax mode: taxable account split into stock/bond sleeves.
  // No-tax mode: everything lives in tvS as a single blended pot.
  let tvS = inp.taxEnabled ? inp.taxableBal * wTax : inp.corpus;
  // Cost basis seeded from the user's embedded unrealized gains at start —
  // e.g. 40% gain ⇒ basis is 60% of today's value. Growth accrues on top.
  let tvSb = tvS * (1 - Math.min(Math.max(inp.taxableStockGainPct, 0), 95) / 100);
  let tvB = inp.taxEnabled ? inp.taxableBal * (1 - wTax) : 0;
  let tvBb = tvB * (1 - Math.min(Math.max(inp.taxableBondGainPct, 0), 95) / 100);
  let trad = inp.taxEnabled ? inp.traditionalBal : 0;
  let roth = inp.taxEnabled ? inp.rothBal : 0;
  // Cash buffer: a zero-volatility reserve, funded from bonds (or the pot in
  // no-tax mode), spent first in the year after a decline, refilled in others.
  const bufferYears = Math.max(0, Math.min(inp.cashBufferYears ?? 0, 10));
  let cash = 0;
  const totalBal = () => tvS + tvB + trad + roth + cash;

  // Holistic targets: pre-glide = the household mix the user set up in the
  // Corpus section (balance-weighted); post-glide = their after-65 target.
  const initTotal = totalBal();
  const holisticPre = initTotal > 0 ? (tvS + wTrad * trad + wRothA * roth) / initTotal : wGlobal;
  const holisticPost = Math.min(Math.max(inp.holisticPostStockPct, 0), 100) / 100;

  const cats = EXPENSE_CATEGORIES.map(c => ({
    id: c.id,
    base: inp.expenseMode === 'category' ? (inp.categoryExpenses[c.id] || 0)
      : c.id === 'misc' ? inp.annualExpenses : 0,
    spread: inp.expenseMode === 'category' ? (inp.categorySpreads[c.id] ?? c.spread) / 100 : 0,
    discretionary: inp.expenseMode === 'category' ? c.discretionary : false,
    factor: 1,
  }));
  const simpleDiscretionary = inp.expenseMode === 'simple' ? SIMPLE_DISCRETIONARY_SHARE : 0;

  const phaseMult = (age: number): number => {
    if (!inp.phases) return 1;
    if (age < inp.phases.gogoEndAge) return inp.phases.gogoMult;
    if (age < inp.phases.slowgoEndAge) return inp.phases.slowgoMult;
    return inp.phases.nogoMult;
  };

  let cumInfl = 1;
  let cumCola = 1;
  let bracketScale = 1;
  let medFactor = 1;
  let prevReturn = 0;
  let prevStockR = 0;     // prior year's stock return — drives the guard strategies
  let totalTaxes = 0;
  let totalStateTaxes = 0;
  let depletionAge: number | null = null;
  let corpusAtRetirement = 0;
  let corpusAtRetirementDefl = 1;
  let firstYearExpenses = 0;

  const balances = new Float64Array(years + 1);
  const deflators = new Float64Array(years + 1);
  balances[0] = totalBal();
  deflators[0] = 1;
  const records: YearRecord[] | null = collect ? [] : null;

  for (let t = 0; t < years; t++) {
    const age = inp.currentAge + t;
    const h = hist[yearIdx[t]];
    // Manual overrides are deterministic: the user's number, every year.
    let stockR = inp.returnsMode === 'manual' ? inp.manualStockPct / 100 : stockSeries[yearIdx[t]] / 100;
    let bondR = inp.returnsMode === 'manual' ? inp.manualBondPct / 100 : h[2] / 100;
    let infl = inp.inflationMode === 'manual' ? inp.manualInflationPct / 100 : h[3] / 100;
    // Sequence-of-returns What-If: force the first N retirement years' returns.
    if (inp.sequenceReturns && age >= inp.retireAge) {
      const ri = age - inp.retireAge;
      if (ri < inp.sequenceReturns.length) {
        const s = inp.sequenceReturns[ri];
        stockR = s.stock / 100;
        bondR = s.bond / 100;
        if (s.infl != null) infl = s.infl / 100;
      }
    }
    const retired = age >= inp.retireAge;
    const deflStart = cumInfl;

    // ── Who's alive this year ──
    const pAlive = !inp.spouse.enabled || age < inp.planAge;
    const sAge = inp.spouse.enabled ? inp.spouse.age + t : 0;
    const sAlive = inp.spouse.enabled && sAge < inp.spouse.planAge;
    const firstDeath = inp.spouse.enabled && (!pAlive || !sAlive);
    const filingNow: FilingStatus = inp.filing === 'mfj' && firstDeath ? 'single' : inp.filing;
    const exclusionMult = filingNow === 'mfj' ? 2 : 1;

    if (age === inp.retireAge) { corpusAtRetirement = totalBal(); corpusAtRetirementDefl = deflStart; }

    // ── Regular expenses ──
    let regular = 0;
    let discretionary = 0;
    if (retired) {
      const pm = phaseMult(age);
      for (const c of cats) {
        let amt = c.base * c.factor;
        if (inp.expenseMode === 'category' && c.id === 'medical') {
          // The entered medical amount is TODAY'S (pre-Medicare) cost; once
          // Medicare starts at 65 it becomes the user's fraction of that
          // (inflation-adjusted via the category factor as usual).
          if (age >= 65) amt *= inp.medicareFraction;
        } else {
          amt *= pm;
        }
        regular += amt;
        if (c.discretionary) discretionary += amt;
      }
      if (inp.expenseMode === 'simple') discretionary = regular * simpleDiscretionary;
      if (firstDeath) {
        const f = Math.min(Math.max(inp.spouse.survivorExpensePct, 30), 100) / 100;
        regular *= f; discretionary *= f;
      }
      if (inp.guardrails && prevReturn < -0.10) regular -= discretionary * 0.5;
      if (firstYearExpenses === 0 && regular > 0) firstYearExpenses = regular;
    }

    // ── One-time expenses & long-term care ──
    let lump = 0;
    for (const ls of inp.lumpSums) if (ls.age === age) lump += ls.amount * cumInfl;
    const ltcCost = ltcCare && ltcCare[t] > 0 ? ltcCare[t] * inp.ltc.annualCost * medFactor : 0;

    // ── External income ──
    let income = 0;
    for (const src of inp.incomes) {
      if (age >= src.startAge && age <= src.endAge) {
        income += src.annual * Math.pow(1 + src.growthPct / 100, age - src.startAge);
      }
    }

    // ── Social Security (household; survivor keeps the larger benefit) ──
    const pOwn = pAlive && age >= inp.ssClaimAge ? inp.ssFraAnnual * claimFactor * cumCola : 0;
    let ssGross: number;
    if (!inp.spouse.enabled) {
      ssGross = pOwn;
    } else {
      const sOwn = sAlive && sAge >= inp.spouse.ssClaimAge ? inp.spouse.ssFraAnnual * spouseFactor * cumCola : 0;
      if (pAlive && sAlive) ssGross = pOwn + sOwn;
      else if (sAlive) ssGross = Math.max(sOwn, inp.ssFraAnnual * claimFactor * cumCola);
      else ssGross = Math.max(pOwn, inp.spouse.ssFraAnnual * spouseFactor * cumCola);
    }

    // Holistic household target for this year (glide at glideAge).
    const holisticTarget = age >= inp.glideAge ? holisticPost : holisticPre;

    // ── Start-of-year rebalance of the taxable account to its target mix.
    //    Selling appreciated stock to rebalance realizes LTCG this year.
    //    Strategy 4 only rebalances after a strong stock year (≥ +up%).
    //    Holistic mode NEVER rebalances the taxable account — the household
    //    mix is restored tax-free inside the 401k/Roth instead. ──
    const allowRebalance = inp.withdrawalStrategy === 'holistic'
      ? false
      : inp.withdrawalStrategy !== 'guarded_rebalance' || prevStockR >= inp.upThresholdPct / 100;
    let rebalGain = 0;
    if (inp.taxEnabled && inp.withdrawalStrategy === 'holistic') {
      const totalNow = tvS + tvB + trad + roth;
      // Under-target and even a 100%-stock 401k+Roth can't reach it → buy
      // stock in taxable with bond proceeds (realizing any bond gains).
      const short = holisticTarget * totalNow - (tvS + trad + roth);
      if (short > 1e-9) {
        const buy = Math.min(short, tvB);
        const gfB = tvB > 0 ? Math.max(0, tvB - tvBb) / tvB : 0;
        rebalGain += buy * gfB;
        tvBb -= tvB > 0 ? buy * (tvBb / tvB) : 0;
        tvB -= buy; tvS += buy; tvSb += buy;
      }
      // Over-target even with an all-bonds 401k+Roth → the only fix is
      // trimming taxable stock (LTCG). Per strategy 4, only after an up year.
      if (prevStockR >= inp.upThresholdPct / 100) {
        const excess = tvS - holisticTarget * totalNow;
        if (excess > 1e-9) {
          const gf = tvS > 0 ? Math.max(0, tvS - tvSb) / tvS : 0;
          rebalGain += excess * gf;
          tvSb -= tvS > 0 ? excess * (tvSb / tvS) : 0;
          tvS -= excess; tvB += excess; tvBb += excess; // bonds bought at market
        }
      }
    } else if (inp.taxEnabled && allowRebalance) {
      const totalTaxable = tvS + tvB;
      const targetS = totalTaxable * wTax;
      if (tvS > targetS + 1e-9) {
        const sell = tvS - targetS;
        const gf = tvS > 0 ? Math.max(0, tvS - tvSb) / tvS : 0;
        rebalGain = sell * gf;
        tvSb -= tvS > 0 ? sell * (tvSb / tvS) : 0;
        tvS = targetS; tvB = totalTaxable - targetS;
        tvBb += sell; // bonds bought at market
      } else if (tvS < targetS - 1e-9) {
        const buy = targetS - tvS;
        const gfB = tvB > 0 ? Math.max(0, tvB - tvBb) / tvB : 0;
        rebalGain = buy * gfB; // selling bonds realizes their gains too
        tvBb -= tvB > 0 ? buy * (tvBb / tvB) : 0;
        tvB -= buy; tvS += buy; tvSb += buy;
      }
    }

    // ── Taxable-account cash: qualified dividends + bond coupons ──
    const dividends = inp.taxEnabled ? tvS * divY : 0;
    // Cash earns the money-market coupon (paid out, taxable), no price change.
    const interest = inp.taxEnabled ? (tvB + cash) * couponY : 0;

    // ── Roth conversion plan ──
    let conversion = 0;
    if (inp.taxEnabled && inp.roth.mode !== 'off' && trad > 0) {
      const rmdEstNow = age >= rmdAge ? trad / rmdDivisor(age) : 0;
      const ordinaryEst = income + interest + rmdEstNow + 0.85 * ssGross;
      let top = 0;
      if (inp.roth.mode === 'custom' && age >= inp.roth.startAge && age <= inp.roth.endAge) {
        top = bracketTopFor(filingNow, inp.roth.ceiling) * bracketScale
          + STANDARD_DEDUCTION[filingNow] * bracketScale;
      } else if (inp.roth.mode === 'auto' && retired && age < rmdAge) {
        // Estimate the marginal bracket future RMDs will force: project the
        // traditional balance to RMD age at planning-mean growth, add the
        // (COLA-projected) SS, and read the bracket at that time. Convert
        // this year up to the top of the highest bracket that is CHEAPER —
        // low-income years (little interest/RMD) get big conversions "free".
        const yearsToRmd = rmdAge - age;
        const wTradBlend = wTrad * plan.stock + (1 - wTrad) * plan.bond;
        const tradAtRmd = trad * Math.pow(1 + wTradBlend, yearsToRmd);
        const rmdAtStart = tradAtRmd / rmdDivisor(rmdAge);
        const colaGrow = Math.pow(1 + Math.max(plan.cpi, 0), yearsToRmd);
        let ssAtRmd = inp.ssClaimAge <= rmdAge ? inp.ssFraAnnual * claimFactor * cumCola * colaGrow : 0;
        if (inp.spouse.enabled && inp.spouse.ssClaimAge <= rmdAge) {
          ssAtRmd += inp.spouse.ssFraAnnual * spouseFactor * cumCola * colaGrow;
        }
        const scaleAtRmd = bracketScale * Math.pow(1 + plan.cpi, yearsToRmd);
        const futureOrd = rmdAtStart + 0.85 * ssAtRmd + income;
        const futureTaxable = Math.max(0, futureOrd - STANDARD_DEDUCTION[filingNow] * scaleAtRmd);
        const futureRate = marginalRateOf(futureTaxable, ORDINARY_BRACKETS[filingNow], scaleAtRmd);
        let targetTop = 0;
        for (const b of ORDINARY_BRACKETS[filingNow]) {
          if (b.upTo !== Infinity && b.rate < futureRate - 1e-9) targetTop = b.upTo;
        }
        if (targetTop > 0) {
          top = targetTop * bracketScale + STANDARD_DEDUCTION[filingNow] * bracketScale;
        }
      }
      if (top > 0) {
        conversion = Math.max(0, Math.min(top - ordinaryEst, trad));
        trad -= conversion;
        roth += conversion;
      }
    }

    // ── Cash buffer: a front-loaded reserve. Funded ONCE at retirement from
    //    bonds (or the pot in no-tax mode) and spent in the year after a
    //    decline so investments aren't sold low. It's refilled only modestly
    //    in strong-stock years — never by piling permanently into cash, which
    //    is pure drag — so protection concentrates in the fragile early years.
    const downFollow = prevStockR < -inp.downThresholdPct / 100;
    if (bufferYears > 0 && retired) {
      const target = bufferYears * regular; // buffer covers living expenses
      // Fund fully at retirement; afterwards top up ≤1 yr of expenses per
      // strong year (stocks up > up-threshold), and only while under target.
      const firstYear = age === inp.retireAge;
      const strongYear = prevStockR > inp.upThresholdPct / 100;
      const want = firstYear ? target - cash
        : (strongYear && cash < target ? Math.min(target - cash, regular) : 0);
      if (want > 0) {
        if (inp.taxEnabled) {
          const pull = Math.min(want, tvB); // basis-preserving bond→cash reallocation
          tvBb -= tvB > 0 ? pull * (tvBb / tvB) : 0;
          tvB -= pull; cash += pull;
        } else {
          const pull = Math.min(want, tvS);
          tvS -= pull; cash += pull;
        }
      }
    }
    const useCash = bufferYears > 0 && downFollow && cash > 0;

    // ── Withdrawals + taxes (fixed point) ──
    const tvS0 = tvS, tvSb0 = tvSb, tvB0 = tvB, tvBb0 = tvBb, trad0 = trad, roth0 = roth, cash0 = cash;
    const rmd = age >= rmdAge && trad0 > 0 ? trad0 / rmdDivisor(age) : 0;
    const gf0 = tvS0 > 0 ? Math.max(0, tvS0 - tvSb0) / tvS0 : 0;
    const gfB0 = tvB0 > 0 ? Math.max(0, tvB0 - tvBb0) / tvB0 : 0;
    const baseExpenses = regular + lump + ltcCost;

    let tax = 0, fedTax = 0, stateTax = 0, niit = 0, irmaa = 0, penalty = 0, marginalRate = 0;
    let sellStock = 0, sellBond = 0, wTradExtra = 0, wRoth = 0, cashSpend = 0, shortfall = 0;

    // Down-market guard (strategies 3 & 4): after a stock year worse than
    // −down%, sell bonds before stocks so equities aren't dumped at a low.
    const bondsFirst = inp.withdrawalStrategy !== 'sequential'
      && prevStockR <= -inp.downThresholdPct / 100;

    const computeWithdrawals = () => {
      // Cash first: outside income, SS, dividends, coupons, forced RMD.
      const inflow = income + ssGross + dividends + interest + rmd;
      let rem = Math.max(0, baseExpenses + irmaa + tax - inflow);
      // After a decline, spend from the cash buffer before selling anything.
      cashSpend = useCash ? Math.min(rem, cash0) : 0; rem -= cashSpend;
      if (bondsFirst) {
        sellBond = Math.min(rem, tvB0); rem -= sellBond;
        sellStock = Math.min(rem, tvS0); rem -= sellStock;
      } else {
        sellStock = Math.min(rem, tvS0); rem -= sellStock;
        sellBond = Math.min(rem, tvB0); rem -= sellBond;
      }
      wTradExtra = Math.min(rem, trad0 - rmd); rem -= wTradExtra;
      wRoth = Math.min(rem, roth0); rem -= wRoth;
      shortfall = rem;
    };

    // Iterate to convergence (tax → need → withdrawal → tax). Typical years
    // settle in ~5 passes; the penalty/state/IRMAA feedback in extreme years
    // needs more, so run until the tax moves < 1¢ (capped at 60 passes).
    for (let iter = 0; iter < 60; iter++) {
      const prevTax = tax;
      computeWithdrawals();
      if (!inp.taxEnabled) break;
      const ordinary = income + interest + rmd + wTradExtra + conversion;
      const saleGain = sellStock * gf0 + sellBond * gfB0;
      // Qualified dividends are taxed at capital-gains rates → same stack.
      const gains = rebalGain + saleGain + dividends;
      const ssTaxablePart = taxableSS(ssGross, ordinary + gains, filingNow);
      const sd = STANDARD_DEDUCTION[filingNow] * bracketScale;
      const totalOrd = ordinary + ssTaxablePart;
      const ordTaxable = Math.max(0, totalOrd - sd);
      const sdLeftover = Math.max(0, sd - totalOrd);
      const gainTaxable = Math.max(0, gains - sdLeftover);

      fedTax = bracketTax(ordTaxable, ORDINARY_BRACKETS[filingNow], bracketScale)
        + ltcgTax(gainTaxable, ordTaxable, LTCG_BRACKETS[filingNow], bracketScale);
      marginalRate = marginalRateOf(ordTaxable, ORDINARY_BRACKETS[filingNow], bracketScale);

      const magi = totalOrd + gains;
      const nii = interest + dividends + rebalGain + saleGain;
      niit = NIIT_RATE * Math.min(Math.max(nii, 0), Math.max(0, magi - NIIT_THRESHOLD[filingNow]));

      stateTax = 0;
      if (stateInfo && stateInfo.rate > 0) {
        const retirementIncome = rmd + wTradExtra + conversion + income;
        const exclusion = (stateInfo.exclusion ?? 0) * exclusionMult * bracketScale;
        const stateBase =
          (stateInfo.retirementExempt ? 0 : Math.max(0, retirementIncome - exclusion))
          + interest + dividends + rebalGain + saleGain
          + (stateInfo.taxesSS ? ssTaxablePart : 0);
        stateTax = (stateInfo.rate / 100) * Math.max(0, stateBase);
      }

      const persons65 = inp.spouse.enabled
        ? (pAlive && age >= 65 ? 1 : 0) + (sAlive && sAge >= 65 ? 1 : 0)
        : age >= 65 ? (inp.filing === 'mfj' ? 2 : 1) : 0;
      irmaa = persons65 > 0 ? irmaaAnnual(magi, filingNow, bracketScale, persons65) : 0;
      // 10% early-withdrawal penalty on retirement-account distributions
      // before 59½ (simplified: applied to traditional-beyond-RMD and Roth).
      penalty = age < 59.5 ? 0.10 * (wTradExtra + wRoth) : 0;
      tax = fedTax + stateTax + niit + penalty;
      if (Math.abs(tax - prevTax) < 0.005) break;
    }
    // Final pass with the settled tax + IRMAA so recorded flows are exact.
    computeWithdrawals();
    totalTaxes += tax;
    totalStateTaxes += stateTax;
    const expenses = baseExpenses + irmaa;

    // Audit values for the ledger, from the settled withdrawals: realized
    // gains and the ordinary taxable income that dynamically picks the bracket.
    const gainsFinal = rebalGain + sellStock * gf0 + sellBond * gfB0;
    let taxableIncomeRec = 0;
    if (inp.taxEnabled) {
      const ordinaryF = income + interest + rmd + wTradExtra + conversion;
      const ssTaxF = taxableSS(ssGross, ordinaryF + gainsFinal + dividends, filingNow);
      taxableIncomeRec = Math.max(0, ordinaryF + ssTaxF - STANDARD_DEDUCTION[filingNow] * bracketScale);
      marginalRate = marginalRateOf(taxableIncomeRec, ORDINARY_BRACKETS[filingNow], bracketScale);
    }

    // ── Apply cash flows ──
    if (sellStock > 0) {
      tvSb -= tvS0 > 0 ? sellStock * (tvSb0 / tvS0) : 0;
      tvS -= sellStock;
    }
    if (sellBond > 0) {
      tvBb -= tvB0 > 0 ? sellBond * (tvBb0 / tvB0) : 0;
      tvB -= sellBond;
    }
    if (cashSpend > 0) cash = cash0 - cashSpend;
    trad = trad0 - rmd - wTradExtra;
    roth = roth0 - wRoth;
    // Surplus cash (unspent dividends/coupons/RMD/income) is reinvested at
    // YEAR-END — see below, after growth. Reinvesting before growth would let
    // distributions earn the same year's return they were paid out of,
    // silently compounding above the stated total return.
    const surplus = Math.max(0, (income + ssGross + dividends + interest + rmd) - (expenses + tax));

    if (shortfall > 1e-6) {
      const totalNeed = expenses + tax - income - ssGross;
      const covered = totalNeed - shortfall;
      const frac = totalNeed > 0 ? Math.max(0, Math.min(1, covered / totalNeed)) : 0;
      // Report the failure against a LIVING person's age. With a younger
      // spouse the household horizon runs past the primary's plan age, so
      // `age` (the primary's) can exceed their plan age even though they've
      // "died" — report the surviving spouse's age instead.
      const refAge = pAlive ? age : (sAlive ? sAge : age);
      depletionAge = refAge + frac;
      balances.fill(0, t + 1);
      for (let k = t; k <= years - 1; k++) deflators[k + 1] = cumInfl * (1 + infl);
      records?.push({
        age, stockR, bondR, infl, defl: deflStart,
        expenses, lump, ltcCost, irmaa, dividends, interest,
        sellStock, sellBond, tradExtra: wTradExtra, rothW: wRoth, cashSpend, cashBal: 0, contrib: 0,
        gains: gainsFinal, taxableIncome: taxableIncomeRec,
        fedTax, stateTax, niit, penalty, taxes: tax, ss: ssGross, income, withdrawal: covered,
        rmd, conversion, marginalRate, stockShare: 0, balance: 0, taxable: 0, traditional: 0, roth: 0,
      });
      break;
    }

    // ── Asset placement inside tax-advantaged accounts. Holistic mode
    //    re-locates stock (Roth first, then 401k) so the HOUSEHOLD hits its
    //    target: sell stock in taxable for spending → buy it back in the
    //    401k, tax-free. Other modes keep each account at its own mix. ──
    let tradStockAmt = wTrad * trad;
    let rothStockAmt = wRothA * roth;
    if (inp.taxEnabled && inp.withdrawalStrategy === 'holistic') {
      const totalNow = tvS + tvB + trad + roth;
      const needStock = Math.min(Math.max(holisticTarget * totalNow - tvS, 0), trad + roth);
      rothStockAmt = Math.min(roth, needStock);
      tradStockAmt = Math.min(trad, needStock - rothStockAmt);
    }

    // ── Growth (end of year). Distributed cash (dividends/coupons) comes out
    //    of the sleeves' total return; traditional & Roth compound fully. ──
    let yearReturn: number;
    let stockShare: number;
    if (inp.taxEnabled) {
      const start0 = tvS + tvB + trad + roth;
      const wEff = start0 > 0 ? (tvS + tradStockAmt + rothStockAmt) / start0 : wTax;
      yearReturn = wEff * stockR + (1 - wEff) * bondR;
      tvS *= 1 + (stockR - divY);
      tvB *= 1 + (bondR - couponY);
      tvS = Math.max(tvS, 0); tvB = Math.max(tvB, 0);
      tvSb = Math.min(tvSb, tvS);
      tvBb = Math.min(tvBb, tvB);
      const tradStockEnd = tradStockAmt * (1 + stockR);
      const rothStockEnd = rothStockAmt * (1 + stockR);
      trad = Math.max(tradStockEnd + (trad - tradStockAmt) * (1 + bondR), 0);
      roth = Math.max(rothStockEnd + (roth - rothStockAmt) * (1 + bondR), 0);
      const endTotal = tvS + tvB + trad + roth;
      stockShare = endTotal > 0 ? (tvS + tradStockEnd + rothStockEnd) / endTotal : 0;
    } else {
      const wPot = inp.withdrawalStrategy === 'holistic' && age >= inp.glideAge ? holisticPost : wGlobal;
      yearReturn = wPot * stockR + (1 - wPot) * bondR;
      tvS *= 1 + yearReturn;
      stockShare = wPot;
    }
    // ── Year-end reinvestment of surplus cash, at the target mix ──
    if (surplus > 0) {
      if (inp.taxEnabled) {
        const reinvestW = inp.withdrawalStrategy === 'holistic' ? holisticTarget : wTax;
        tvS += surplus * reinvestW; tvSb += surplus * reinvestW;
        tvB += surplus * (1 - reinvestW); tvBb += surplus * (1 - reinvestW);
      } else {
        tvS += surplus;
      }
    }
    // ── Future savings: during the working years, add contributions that then
    //    compound with the portfolio. Amounts are today's-dollars inflated to
    //    nominal; the 401(k) "max" is the IRS elective limit for that year. ──
    let contrib = 0;
    if (inp.savings && !retired && age < inp.savings.untilAge) {
      const cTaxable = Math.max(0, inp.savings.taxable) * cumInfl;
      const cRoth = Math.max(0, inp.savings.roth) * cumInfl;
      const cEmployee = inp.savings.k401Max
        ? k401ElectiveLimit(cumInfl, age)
        : Math.max(0, inp.savings.k401Employee) * cumInfl;
      // Employer contribution: a fixed today's-dollars amount, or a % match of
      // the employee deferral (which itself may be the indexed max).
      const cEmployer = inp.savings.k401EmployerMode === 'match'
        ? cEmployee * (Math.max(0, inp.savings.k401EmployerMatchPct ?? 0) / 100)
        : Math.max(0, inp.savings.k401Employer) * cumInfl;
      const cTrad = cEmployee + cEmployer;
      contrib = cTaxable + cRoth + cTrad;
      if (inp.taxEnabled) {
        tvS += cTaxable * wTax; tvSb += cTaxable * wTax;
        tvB += cTaxable * (1 - wTax); tvBb += cTaxable * (1 - wTax);
        roth += cRoth;
        trad += cTrad;
      } else {
        tvS += contrib; // single blended pot in no-tax mode
      }
    }

    prevReturn = yearReturn;
    prevStockR = stockR;

    // ── Advance cumulative factors ──
    for (const c of cats) c.factor *= 1 + infl + c.spread;
    cumInfl *= 1 + infl;
    cumCola *= 1 + Math.max(infl, 0);
    bracketScale *= 1 + infl;
    medFactor *= 1 + infl + MED_SPREAD;

    const total = totalBal();
    balances[t + 1] = total;
    deflators[t + 1] = cumInfl;
    records?.push({
      age, stockR, bondR, infl, defl: deflStart,
      expenses, lump, ltcCost, irmaa, dividends, interest,
      sellStock, sellBond, tradExtra: wTradExtra, rothW: wRoth, cashSpend, cashBal: cash, contrib,
      gains: gainsFinal, taxableIncome: taxableIncomeRec,
      fedTax, stateTax, niit, penalty, taxes: tax, ss: ssGross, income,
      withdrawal: Math.max(0, expenses + tax - income - ssGross),
      rmd, conversion, marginalRate, stockShare,
      balance: total, taxable: tvS + tvB, traditional: trad, roth,
    });
  }

  if (corpusAtRetirement === 0) {
    const i = Math.max(0, Math.min(years, inp.retireAge - inp.currentAge));
    corpusAtRetirement = balances[i];
    corpusAtRetirementDefl = deflators[i] || 1;
  }

  return {
    balances, deflators, depletionAge, totalTaxes, totalStateTaxes, records,
    corpusAtRetirement, corpusAtRetirementDefl, firstYearExpenses,
  };
}

// ---------------------------------------------------------------------------
// Shared run scaffolding
// ---------------------------------------------------------------------------

const BASE_SEED = 0x5eed1234;

function windowMeans(inp: SimInputs) {
  const hist = historicalWindow(inp.windowStart, inp.windowEnd, inp.excludedYears);
  if (hist.length < HIST_MIN_WINDOW) {
    throw new Error(`The historical window needs at least ${HIST_MIN_WINDOW} usable years (currently ${hist.length}).`);
  }
  const n = hist.length;
  // Composite stock returns (%) for the window, aligned 1:1 with `hist`.
  const stockSeries = stockSeriesForWindow(inp.windowStart, inp.windowEnd, inp.excludedYears, inp.stockComposition);
  return {
    hist,
    stockSeries,
    stockMean: stockSeries.reduce((s, v) => s + v, 0) / n,
    bondMean: hist.reduce((s, h) => s + h[2], 0) / n,
    cpiMean: hist.reduce((s, h) => s + h[3], 0) / n,
  };
}

/** Effective initial stock share across accounts (for display stats). */
function effectiveStockShare(inp: SimInputs): number {
  if (!inp.taxEnabled) return inp.stockPct / 100;
  const total = inp.taxableBal + inp.traditionalBal + inp.rothBal;
  if (total <= 0) return inp.stockPct / 100;
  return (
    inp.taxableBal * inp.accountStockPct.taxable
    + inp.traditionalBal * inp.accountStockPct.traditional
    + inp.rothBal * inp.accountStockPct.roth
  ) / (total * 100);
}

// ---------------------------------------------------------------------------
// Full Monte Carlo run
// ---------------------------------------------------------------------------

export function runMonteCarlo(inp: SimInputs, numSims?: number, seed = BASE_SEED): MCResult {
  const deterministic = isDeterministic(inp);
  const sims = deterministic ? 1 : Math.max(100, Math.min(numSims ?? inp.numSims, 10_000));
  const years = horizonYears(inp);
  const { hist, stockSeries, stockMean, bondMean, cpiMean } = windowMeans(inp);
  const wEff = effectiveStockShare(inp);
  const portfolioMean = inp.returnsMode === 'manual'
    ? wEff * inp.manualStockPct + (1 - wEff) * inp.manualBondPct
    : wEff * stockMean + (1 - wEff) * bondMean;

  const plan: PlanMeans = {
    stock: (inp.returnsMode === 'manual' ? inp.manualStockPct : stockMean) / 100,
    bond: (inp.returnsMode === 'manual' ? inp.manualBondPct : bondMean) / 100,
    cpi: (inp.inflationMode === 'manual' ? inp.manualInflationPct : cpiMean) / 100,
  };
  const rng = mulberry32(seed);
  const yearIdx = new Uint16Array(years);

  const balNom: number[][] = Array.from({ length: years + 1 }, () => []);
  const balReal: number[][] = Array.from({ length: years + 1 }, () => []);
  const stride = Math.max(1, Math.floor(sims / 1200));
  const collected: { records: YearRecord[]; ending: number }[] = [];
  const endingsNom: number[] = [], endingsReal: number[] = [];
  const depletions: number[] = [], taxTotals: number[] = [], stateTaxTotals: number[] = [];
  const corpusRetNom: number[] = [], corpusRetReal: number[] = [], firstYearExp: number[] = [];
  let successes = 0;

  for (let s = 0; s < sims; s++) {
    for (let t = 0; t < years; t++) yearIdx[t] = Math.floor(rng() * hist.length);
    const ltcCare = sampleLtcCare(inp, s, seed, years);
    const collect = s % stride === 0;
    const path = simulatePath(inp, hist, stockSeries, yearIdx, ltcCare, plan, collect);
    for (let t = 0; t <= years; t++) {
      balNom[t].push(path.balances[t]);
      balReal[t].push(path.balances[t] / (path.deflators[t] || 1));
    }
    const endN = path.balances[years];
    endingsNom.push(endN);
    endingsReal.push(endN / (path.deflators[years] || 1));
    if (path.depletionAge === null) successes++;
    else depletions.push(path.depletionAge);
    taxTotals.push(path.totalTaxes);
    stateTaxTotals.push(path.totalStateTaxes);
    corpusRetNom.push(path.corpusAtRetirement);
    corpusRetReal.push(path.corpusAtRetirement / path.corpusAtRetirementDefl);
    firstYearExp.push(path.firstYearExpenses);
    if (collect && path.records) collected.push({ records: path.records, ending: endN });
  }

  const pctlsOf = (arr: number[][]): AgePercentiles[] =>
    arr.map((vals, t) => {
      const sorted = vals.slice().sort((a, b) => a - b);
      return {
        age: inp.currentAge + t,
        p10: percentile(sorted, 0.10), p25: percentile(sorted, 0.25),
        p50: percentile(sorted, 0.50), p75: percentile(sorted, 0.75),
        p90: percentile(sorted, 0.90),
      };
    });

  const survivalByAge = balNom.map((vals, t) => ({
    age: inp.currentAge + t,
    prob: vals.filter(v => v > 0).length / vals.length,
  }));

  const endPctls = (arr: number[]) => {
    const sorted = arr.slice().sort((a, b) => a - b);
    return {
      p10: percentile(sorted, 0.10), p50: percentile(sorted, 0.50),
      p90: percentile(sorted, 0.90), p95: percentile(sorted, 0.95),
      p99: percentile(sorted, 0.99),
    };
  };

  const sortedDepl = depletions.slice().sort((a, b) => a - b);
  const sortedTax = taxTotals.slice().sort((a, b) => a - b);
  const sortedStateTax = stateTaxTotals.slice().sort((a, b) => a - b);
  const sortedRetN = corpusRetNom.slice().sort((a, b) => a - b);
  const sortedRetR = corpusRetReal.slice().sort((a, b) => a - b);
  const sortedFy = firstYearExp.slice().sort((a, b) => a - b);
  const endingNominal = endPctls(endingsNom);
  const endingReal = endPctls(endingsReal);

  let representative: YearRecord[] = [];
  let bestDiff = Infinity;
  for (const c of collected) {
    const d = Math.abs(c.ending - endingNominal.p50);
    if (d < bestDiff) { bestDiff = d; representative = c.records; }
  }

  return {
    successRate: successes / sims,
    survivalByAge,
    percentilesNominal: pctlsOf(balNom),
    percentilesReal: pctlsOf(balReal),
    endingPercentiles: { nominal: endingNominal, real: endingReal },
    medianEnding: { nominal: endingNominal.p50, real: endingReal.p50 },
    medianDepletionAge: sortedDepl.length ? percentile(sortedDepl, 0.5) : null,
    medianTotalTaxes: percentile(sortedTax, 0.5),
    medianStateTaxes: percentile(sortedStateTax, 0.5),
    corpusAtRetirementMedian: { nominal: percentile(sortedRetN, 0.5), real: percentile(sortedRetR, 0.5) },
    firstYearExpensesMedian: percentile(sortedFy, 0.5),
    representative,
    windowStats: {
      stockMean, bondMean, cpiMean, portfolioMean,
      yearsUsed: hist.length,
      effectiveStockPct: Math.round(wEff * 100),
    },
    numSims: sims,
    deterministic,
  };
}

// ---------------------------------------------------------------------------
// Quick run — success/ending/taxes only, for strategy sweeps.
// ---------------------------------------------------------------------------

export interface QuickResult { success: number; medianEndReal: number; medianTaxes: number }

function runQuick(inp: SimInputs, sims: number, seed = BASE_SEED): QuickResult {
  const years = horizonYears(inp);
  const { hist, stockSeries, stockMean, bondMean, cpiMean } = windowMeans(inp);
  const plan: PlanMeans = {
    stock: (inp.returnsMode === 'manual' ? inp.manualStockPct : stockMean) / 100,
    bond: (inp.returnsMode === 'manual' ? inp.manualBondPct : bondMean) / 100,
    cpi: (inp.inflationMode === 'manual' ? inp.manualInflationPct : cpiMean) / 100,
  };
  const nSims = isDeterministic(inp) ? 1 : sims;
  const rng = mulberry32(seed);
  const yearIdx = new Uint16Array(years);
  const endingsReal: number[] = [], taxTotals: number[] = [];
  let successes = 0;
  for (let s = 0; s < nSims; s++) {
    for (let t = 0; t < years; t++) yearIdx[t] = Math.floor(rng() * hist.length);
    const ltcCare = sampleLtcCare(inp, s, seed, years);
    const path = simulatePath(inp, hist, stockSeries, yearIdx, ltcCare, plan, false);
    endingsReal.push(path.balances[years] / (path.deflators[years] || 1));
    taxTotals.push(path.totalTaxes);
    if (path.depletionAge === null) successes++;
  }
  return {
    success: successes / nSims,
    medianEndReal: percentile(endingsReal.sort((a, b) => a - b), 0.5),
    medianTaxes: percentile(taxTotals.sort((a, b) => a - b), 0.5),
  };
}

// ---------------------------------------------------------------------------
// Strategy Lab
// ---------------------------------------------------------------------------

export interface SweepPoint { x: number; success: number; end: number }

/** Is outcome `a` genuinely better than `b`? Success dominates (needs > 0.5pp);
 *  on a near-tie, the higher inflation-adjusted ending balance wins (needs a
 *  ~0.5% edge). Used so recommendations never push a strictly-worse change. */
export function outcomeBetter(
  a: { success: number; end: number },
  b: { success: number; end: number },
): boolean {
  const ds = a.success - b.success;
  if (ds > 0.005) return true;
  if (ds < -0.005) return false;
  return a.end > b.end * 1.005 + 1;
}

export interface StrategyComparison {
  id: 'guardrails' | 'smile' | 'asset_location' | 'down_guard' | 'smart_rebalance' | 'holistic';
  label: string;
  detail: string;
  off: QuickResult;
  on: QuickResult;
  currentlyOn: boolean;
  applyAllocation?: AccountAllocation;   // asset-location: the mix to apply
}

export interface RothPlanOption {
  label: string;
  plan: RothPlan | null;
  success: number;
  medianEndReal: number;
  medianTaxes: number;
  recommended: boolean;
}

export interface SweepResults {
  baseline: { success: number; end: number; tax: number };  // the current plan's outcome (sweep resolution)
  allocation: SweepPoint[];
  bestAllocation: number;
  allocationHelps: boolean;                     // best mix beats the current plan
  claimAge: SweepPoint[];
  bestClaimAge: number;
  claimHelps: boolean;
  spouseClaimAge: SweepPoint[];
  bestSpouseClaimAge: number;
  spouseClaimHelps: boolean;
  strategies: StrategyComparison[];
  rothPlans: RothPlanOption[] | null;
  spendingCutPct: number | null;
  fire: FireResult | null;                      // early-retirement analysis (pre-retirement only)
  sweepSims: number;
}

/** One "path to early retirement": the earliest age the plan clears the target
 *  success bar under some lever (portfolio, spending), plus what Apply sets. */
export interface FireScenario {
  id: 'plan' | 'aggressive' | 'lean' | 'both';
  label: string;
  detail: string;
  earliestAge: number | null;         // null ⇒ not reachable within the swept range
  successAtEarliest: number;
  apply: { retireAge: number; stockPct?: number; expenseScale?: number };
}

export interface FireResult {
  targetSuccess: number;              // the success bar used (e.g. 0.9)
  plannedAge: number;                 // the user's current retirement age
  curve: { age: number; success: number }[]; // current-plan success vs retire age
  scenarios: FireScenario[];
  sweepSims: number;
}

const SWEEP_SIMS = 600;
const FIRE_SIMS = 500;
const FIRE_TARGET = 0.90;

export const SMILE_DEFAULT: SpendingPhases = {
  gogoEndAge: 75, slowgoEndAge: 85, gogoMult: 1.0, slowgoMult: 0.85, nogoMult: 0.75,
};

/** Conventional asset location: sink bonds into traditional first (interest
 *  hides from annual tax), then taxable; Roth holds stock (tax-free growth). */
export function assetLocationSuggestion(inp: SimInputs): AccountAllocation | null {
  const total = inp.taxableBal + inp.traditionalBal + inp.rothBal;
  if (!inp.taxEnabled || total <= 0) return null;
  const stockDollars = (
    inp.taxableBal * inp.accountStockPct.taxable
    + inp.traditionalBal * inp.accountStockPct.traditional
    + inp.rothBal * inp.accountStockPct.roth
  ) / 100;
  let bondDollars = total - stockDollars;
  const bondFill = (bal: number) => {
    const put = Math.min(bondDollars, bal);
    bondDollars -= put;
    return bal > 0 ? Math.round(((bal - put) / bal) * 20) * 5 : 0; // stock share, steps of 5%
  };
  const traditional = bondFill(inp.traditionalBal);
  const taxable = bondFill(inp.taxableBal);
  const roth = bondFill(inp.rothBal);
  return { taxable, traditional, roth };
}

/** Early-retirement (F.I.R.E.) analysis: how early could this person stop
 *  working and still clear the success bar, under different levers? Only
 *  meaningful while still accumulating (currentAge < retireAge). */
export function runFire(inp: SimInputs, target = FIRE_TARGET): FireResult | null {
  if (inp.currentAge >= inp.retireAge) return null;
  const planned = inp.retireAge;
  // Sweep the last ≤ 25 candidate ages up to the planned age; earlier than that
  // is rarely feasible and keeps the sweep bounded.
  const lo = Math.max(inp.currentAge + 1, planned - 25);
  const fireQuick = (age: number, extra: Partial<SimInputs> = {}) =>
    runQuick({
      ...inp,
      retireAge: age,
      // Stop contributing once retired.
      savings: inp.savings ? { ...inp.savings, untilAge: Math.min(inp.savings.untilAge, age) } : null,
      ...extra,
    }, FIRE_SIMS).success;

  // Base curve for the current plan.
  const curve: { age: number; success: number }[] = [];
  for (let age = lo; age <= planned; age++) curve.push({ age, success: fireQuick(age) });

  // Success rises monotonically with a later retirement age, so the earliest
  // feasible age is the first that clears the bar (scan ascending, stop early).
  const earliestIn = (extra: Partial<SimInputs>): { age: number | null; success: number } => {
    for (let age = lo; age <= planned; age++) {
      const success = fireQuick(age, extra);
      if (success >= target) return { age, success };
    }
    return { age: null, success: 0 };
  };
  // The base plan reuses the curve we already computed.
  const basePlanPt = curve.find(p => p.success >= target);
  const basePlan = { age: basePlanPt?.age ?? null, success: basePlanPt?.success ?? 0 };

  const aggMix: Partial<SimInputs> = {
    stockPct: 90, accountStockPct: { taxable: 90, traditional: 90, roth: 90 },
  };
  const leanMod: Partial<SimInputs> = {
    annualExpenses: inp.annualExpenses * 0.85,
    categoryExpenses: Object.fromEntries(
      Object.entries(inp.categoryExpenses).map(([k, v]) => [k, v * 0.85]),
    ) as Record<CategoryId, number>,
  };
  const bothMod: Partial<SimInputs> = { ...aggMix, ...leanMod };

  const agg = earliestIn(aggMix);
  const lean = earliestIn(leanMod);
  const both = earliestIn(bothMod);

  const scenarios: FireScenario[] = [
    {
      id: 'plan', label: 'As planned',
      detail: 'Your current portfolio and spending, retiring as early as the numbers allow.',
      earliestAge: basePlan.age, successAtEarliest: basePlan.success,
      apply: { retireAge: basePlan.age ?? planned },
    },
    {
      id: 'aggressive', label: 'Growth portfolio',
      detail: 'Tilt to a 90% stock allocation across accounts — more growth, more volatility to stomach.',
      earliestAge: agg.age, successAtEarliest: agg.success,
      apply: { retireAge: agg.age ?? planned, stockPct: 90 },
    },
    {
      id: 'lean', label: 'Leaner spending',
      detail: 'Trim the retirement budget 15% — the single biggest lever on how early you can stop.',
      earliestAge: lean.age, successAtEarliest: lean.success,
      apply: { retireAge: lean.age ?? planned, expenseScale: 0.85 },
    },
    {
      id: 'both', label: 'Growth + lean',
      detail: '90% stocks and a 15% leaner budget together — the most aggressive path to an early exit.',
      earliestAge: both.age, successAtEarliest: both.success,
      apply: { retireAge: both.age ?? planned, stockPct: 90, expenseScale: 0.85 },
    },
  ];

  return { targetSuccess: target, plannedAge: planned, curve, scenarios, sweepSims: FIRE_SIMS };
}

export function runSweeps(inp: SimInputs): SweepResults {
  const quick = (mod: Partial<SimInputs>) => runQuick({ ...inp, ...mod }, SWEEP_SIMS);
  const baseQ = quick({});
  const baseline = { success: baseQ.success, end: baseQ.medianEndReal, tax: baseQ.medianTaxes };
  const bestPoint = (pts: SweepPoint[]) =>
    pts.reduce((b, p) => (outcomeBetter(p, b) ? p : b));

  // Allocation curve — uniform stock share across all accounts.
  const allocation: SweepPoint[] = [];
  for (let s = 0; s <= 100; s += 10) {
    const q = quick({ stockPct: s, accountStockPct: { taxable: s, traditional: s, roth: s } });
    allocation.push({ x: s, success: q.success, end: q.medianEndReal });
  }
  // Allocation is a SAFETY lever: recommend the mix that maximizes success, and
  // among those within 0.5pp of the top, prefer the LOWEST equity (least risk
  // for the same safety) rather than piling on stocks just to lift the median.
  const maxAllocSuccess = Math.max(...allocation.map(p => p.success));
  const bestAllocPoint = allocation
    .filter(p => p.success >= maxAllocSuccess - 0.005)
    .reduce((lo, p) => (p.x < lo.x ? p : lo));
  const bestAllocation = bestAllocPoint.x;
  // Only recommended when it actually improves the odds the money lasts.
  const allocationHelps = bestAllocPoint.success > baseline.success + 0.005;

  // Social Security claiming-age curves.
  const claimAge: SweepPoint[] = [];
  if (inp.ssFraAnnual > 0) {
    for (const age of Object.keys(SS_CLAIM_FACTORS).map(Number)) {
      const q = age === inp.ssClaimAge ? baseQ : quick({ ssClaimAge: age });
      claimAge.push({ x: age, success: q.success, end: q.medianEndReal });
    }
  }
  const bestClaimPoint = claimAge.length ? bestPoint(claimAge) : null;
  const curClaim = claimAge.find(p => p.x === inp.ssClaimAge);
  const bestClaimAge = bestClaimPoint ? bestClaimPoint.x : inp.ssClaimAge;
  const claimHelps = !!(bestClaimPoint && curClaim
    && bestClaimPoint.x !== inp.ssClaimAge && outcomeBetter(bestClaimPoint, curClaim));

  const spouseClaimAge: SweepPoint[] = [];
  if (inp.spouse.enabled && inp.spouse.ssFraAnnual > 0) {
    for (const age of Object.keys(SS_CLAIM_FACTORS).map(Number)) {
      const q = age === inp.spouse.ssClaimAge ? baseQ : quick({ spouse: { ...inp.spouse, ssClaimAge: age } });
      spouseClaimAge.push({ x: age, success: q.success, end: q.medianEndReal });
    }
  }
  const bestSpousePoint = spouseClaimAge.length ? bestPoint(spouseClaimAge) : null;
  const curSpouseClaim = spouseClaimAge.find(p => p.x === inp.spouse.ssClaimAge);
  const bestSpouseClaimAge = bestSpousePoint ? bestSpousePoint.x : inp.spouse.ssClaimAge;
  const spouseClaimHelps = !!(bestSpousePoint && curSpouseClaim
    && bestSpousePoint.x !== inp.spouse.ssClaimAge && outcomeBetter(bestSpousePoint, curSpouseClaim));

  // On/off strategies.
  const strategies: StrategyComparison[] = [];
  strategies.push({
    id: 'guardrails',
    label: 'Spending guardrails',
    detail: 'Halve discretionary spending (travel, dining, misc) for the year after any >10% portfolio decline.',
    off: inp.guardrails ? quick({ guardrails: false }) : baseQ,
    on: inp.guardrails ? baseQ : quick({ guardrails: true }),
    currentlyOn: inp.guardrails,
  });
  strategies.push({
    id: 'smile',
    label: 'Retirement spending smile',
    detail: `Real-world spending declines with age: full budget to ${SMILE_DEFAULT.gogoEndAge}, ${Math.round(SMILE_DEFAULT.slowgoMult * 100)}% in the slow-go years, ${Math.round(SMILE_DEFAULT.nogoMult * 100)}% after ${SMILE_DEFAULT.slowgoEndAge} — medical costs keep rising throughout.`,
    off: inp.phases ? quick({ phases: null }) : baseQ,
    on: inp.phases ? baseQ : quick({ phases: SMILE_DEFAULT }),
    currentlyOn: inp.phases !== null,
  });

  // Withdrawal-strategy comparisons (user strategies 3 & 4), each fully simulated.
  const stratQ = (ws: WithdrawalStrategy) =>
    inp.withdrawalStrategy === ws ? baseQ : quick({ withdrawalStrategy: ws });
  const seqQ = stratQ('sequential');
  strategies.push({
    id: 'down_guard',
    label: 'Down-market guard',
    detail: `After any stock year worse than −${inp.downThresholdPct}%, that year's spending sells bonds before stocks so equities aren't dumped at a low.`,
    off: seqQ,
    on: stratQ('guarded'),
    currentlyOn: inp.withdrawalStrategy === 'guarded',
  });
  strategies.push({
    id: 'smart_rebalance',
    label: 'Guard + up-year rebalancing',
    detail: `Down-market guard, plus the taxable account only rebalances back to its target mix after stock years better than +${inp.upThresholdPct}% (those rebalancing sales realize LTCG).`,
    off: seqQ,
    on: stratQ('guarded_rebalance'),
    currentlyOn: inp.withdrawalStrategy === 'guarded_rebalance',
  });
  if (inp.taxEnabled) {
    strategies.push({
      id: 'holistic',
      label: 'Holistic household glide',
      detail: `Down-market guard, plus the WHOLE portfolio holds your household target (your mix until ${inp.glideAge}, ${inp.holisticPostStockPct}% stocks after) by re-locating stock inside the 401k/Roth — rebalancing is tax-free, no LTCG.`,
      off: seqQ,
      on: stratQ('holistic'),
      currentlyOn: inp.withdrawalStrategy === 'holistic',
    });
  }

  // Asset location: same overall stock share, relocated (bonds → traditional,
  // stock → Roth/taxable). Only surfaced when the simulation confirms it helps.
  const located = assetLocationSuggestion(inp);
  if (located && (
    Math.abs(located.taxable - inp.accountStockPct.taxable) >= 5
    || Math.abs(located.traditional - inp.accountStockPct.traditional) >= 5
    || Math.abs(located.roth - inp.accountStockPct.roth) >= 5
  )) {
    const onQ = quick({ accountStockPct: located });
    const helps = onQ.success >= baseQ.success - 0.005 && (
      onQ.medianEndReal > baseQ.medianEndReal * 1.005
      || (onQ.medianTaxes < baseQ.medianTaxes * 0.98 && onQ.medianEndReal >= baseQ.medianEndReal * 0.995)
    );
    if (helps) {
      strategies.push({
        id: 'asset_location',
        label: 'Asset location (same overall mix)',
        detail: `Keep your overall stock share but relocate it: Roth ${located.roth}% / taxable ${located.taxable}% / traditional ${located.traditional}% stocks. Bonds shelter in the 401k where interest isn't taxed annually.`,
        off: baseQ,
        on: onQ,
        currentlyOn: false,
        applyAllocation: located,
      });
    }
  }

  // Roth conversion optimizer.
  let rothPlans: RothPlanOption[] | null = null;
  if (inp.taxEnabled && inp.traditionalBal > 0) {
    const birthYear = new Date().getFullYear() - inp.currentAge;
    const rmdA = rmdStartAge(birthYear);
    const start = Math.max(inp.retireAge, inp.currentAge);
    const endOptions = [...new Set([
      Math.min(inp.ssClaimAge, rmdA) - 1,
      rmdA - 1,
    ])].filter(e => e >= start);
    const offQ = inp.roth.mode !== 'off' ? quick({ roth: { ...inp.roth, mode: 'off' } }) : baseQ;
    rothPlans = [{
      label: 'No conversions', plan: null,
      success: offQ.success, medianEndReal: offQ.medianEndReal, medianTaxes: offQ.medianTaxes,
      recommended: false,
    }];
    // Auto mode: the engine decides year-by-year based on each year's income
    // and the bracket future RMDs would force.
    const autoPlan: RothPlan = { mode: 'auto', ceiling: 22, startAge: start, endAge: rmdA - 1 };
    const autoQ = quick({ roth: autoPlan });
    rothPlans.push({
      label: 'Auto-optimized (system picks years & amounts)',
      plan: autoPlan, success: autoQ.success, medianEndReal: autoQ.medianEndReal, medianTaxes: autoQ.medianTaxes,
      recommended: false,
    });
    for (const ceiling of [12, 22, 24] as const) {
      for (const endAge of endOptions) {
        const plan: RothPlan = { mode: 'custom', ceiling, startAge: start, endAge };
        const q = quick({ roth: plan });
        rothPlans.push({
          label: `Fill ${ceiling}% bracket, age ${start}–${endAge}`,
          plan, success: q.success, medianEndReal: q.medianEndReal, medianTaxes: q.medianTaxes,
          recommended: false,
        });
      }
    }
    // Recommend the plan with the genuinely best outcome (success first, then
    // ending balance, then lower lifetime taxes on a tie). "No conversions" is
    // the baseline, so a conversion plan only wins if it actually beats it.
    let best = rothPlans[0];
    for (const p of rothPlans) {
      const better = outcomeBetter(
        { success: p.success, end: p.medianEndReal },
        { success: best.success, end: best.medianEndReal },
      );
      const tieLowerTax = Math.abs(p.success - best.success) <= 0.005
        && Math.abs(p.medianEndReal - best.medianEndReal) <= best.medianEndReal * 0.005 + 1
        && p.medianTaxes < best.medianTaxes - 1;
      if (better || tieLowerTax) best = p;
    }
    best.recommended = true;
  }

  // Spending cut needed for ~90% success.
  let spendingCutPct: number | null = null;
  if (baseQ.success < 0.90) {
    let lo = 0.5, hi = 1.0;
    for (let i = 0; i < 7; i++) {
      const mid = (lo + hi) / 2;
      const scaled: Partial<SimInputs> = {
        annualExpenses: inp.annualExpenses * mid,
        categoryExpenses: Object.fromEntries(
          Object.entries(inp.categoryExpenses).map(([k, v]) => [k, v * mid]),
        ) as Record<CategoryId, number>,
      };
      if (quick(scaled).success >= 0.90) lo = mid; else hi = mid;
    }
    const cut = Math.round((1 - lo) * 100);
    spendingCutPct = cut > 0 && cut < 50 ? cut : null;
  }

  return {
    baseline,
    allocation, bestAllocation, allocationHelps,
    claimAge, bestClaimAge, claimHelps,
    spouseClaimAge, bestSpouseClaimAge, spouseClaimHelps,
    strategies, rothPlans, spendingCutPct, fire: runFire(inp), sweepSims: SWEEP_SIMS,
  };
}
