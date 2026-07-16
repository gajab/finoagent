// ---------------------------------------------------------------------------
// Static reference data for the Retirement Portfolio Longevity calculator.
//
// Historical series were compiled once (S&P 500 total return and 10-year
// Treasury total return per the NYU Stern / Damodaran annual dataset; CPI-U
// Dec-over-Dec from BLS) and hard-coded here so the calculator never needs an
// external service. 2025 figures are approximate full-year values.
// ---------------------------------------------------------------------------

/** [year, S&P 500 total return %, 10Y Treasury total return %, CPI inflation %] */
export type HistYear = [number, number, number, number];

export const HISTORICAL: HistYear[] = [
  [1975, 37.25, 3.61, 6.9],
  [1976, 23.68, 15.98, 4.9],
  [1977, -7.16, 1.29, 6.7],
  [1978, 6.57, -0.78, 9.0],
  [1979, 18.42, 0.67, 13.3],
  [1980, 32.41, -2.99, 12.5],
  [1981, -4.91, 8.20, 8.9],
  [1982, 21.41, 32.81, 3.8],
  [1983, 22.51, 3.20, 3.8],
  [1984, 6.27, 13.73, 3.9],
  [1985, 32.16, 25.71, 3.8],
  [1986, 18.47, 24.28, 1.1],
  [1987, 5.23, -4.96, 4.4],
  [1988, 16.81, 8.22, 4.4],
  [1989, 31.49, 17.69, 4.6],
  [1990, -3.06, 6.24, 6.1],
  [1991, 30.23, 15.00, 3.1],
  [1992, 7.49, 9.36, 2.9],
  [1993, 9.97, 14.21, 2.7],
  [1994, 1.33, -8.04, 2.7],
  [1995, 37.20, 23.48, 2.5],
  [1996, 22.68, 1.43, 3.3],
  [1997, 33.10, 9.94, 1.7],
  [1998, 28.34, 14.92, 1.6],
  [1999, 20.89, -8.25, 2.7],
  [2000, -9.03, 16.66, 3.4],
  [2001, -11.85, 5.57, 1.6],
  [2002, -21.97, 15.12, 2.4],
  [2003, 28.36, 0.38, 1.9],
  [2004, 10.74, 4.49, 3.3],
  [2005, 4.83, 2.87, 3.4],
  [2006, 15.61, 1.96, 2.5],
  [2007, 5.48, 10.21, 4.1],
  [2008, -36.55, 20.10, 0.1],
  [2009, 25.94, -11.12, 2.7],
  [2010, 14.82, 8.46, 1.5],
  [2011, 2.10, 16.04, 3.0],
  [2012, 15.89, 2.97, 1.7],
  [2013, 32.15, -9.10, 1.5],
  [2014, 13.52, 10.75, 0.8],
  [2015, 1.38, 1.28, 0.7],
  [2016, 11.77, 0.69, 2.1],
  [2017, 21.61, 2.80, 2.1],
  [2018, -4.23, -0.02, 1.9],
  [2019, 31.21, 9.64, 2.3],
  [2020, 18.02, 11.33, 1.4],
  [2021, 28.47, -4.42, 7.0],
  [2022, -18.04, -17.83, 6.5],
  [2023, 26.06, 3.88, 3.4],
  [2024, 24.88, -1.64, 2.9],
  [2025, 18.00, 7.00, 3.0], // approximate full-year 2025
];

export const HIST_MIN_WINDOW = 10;
export const HIST_START = HISTORICAL[0][0];                       // 1975
export const HIST_END = HISTORICAL[HISTORICAL.length - 1][0];     // 2025

// ---------------------------------------------------------------------------
// Optional equity-index composition. Approximate annual TOTAL returns (%),
// aligned row-for-row with HISTORICAL (index i ⇒ HISTORICAL[i][0] year), for
// [Nasdaq-100, Russell 2000, International (MSCI EAFE), Emerging markets].
// Pre-inception years use the closest proxy (NQ pre-1985 ≈ S&P/Nasdaq Comp;
// R2K pre-1979 ≈ small-cap; EM pre-1988 ≈ EAFE). These capture the major
// divergences (Nasdaq boom/bust, EM's 2003–09 run, the 2010s US lead) at a
// high level — planning estimates, not index-exact.
export const INDEX_RETURNS: number[][] = [
  /* 1975 */ [40, 55, 37, 37],
  /* 1976 */ [26, 57, 4, 4],
  /* 1977 */ [7, 25, 19, 19],
  /* 1978 */ [12, 24, 34, 34],
  /* 1979 */ [28, 43, 6, 6],
  /* 1980 */ [45, 38, 24, 24],
  /* 1981 */ [-3, 2, -1, -1],
  /* 1982 */ [19, 25, -1, -1],
  /* 1983 */ [19, 29, 24, 24],
  /* 1984 */ [-10, -7, 7, 7],
  /* 1985 */ [32, 31, 56, 56],
  /* 1986 */ [7, 5, 69, 69],
  /* 1987 */ [11, -9, 24, 24],
  /* 1988 */ [14, 25, 28, 40],
  /* 1989 */ [27, 16, 10, 65],
  /* 1990 */ [-10, -20, -23, -11],
  /* 1991 */ [65, 46, 12, 60],
  /* 1992 */ [9, 18, -12, 11],
  /* 1993 */ [11, 19, 32, 75],
  /* 1994 */ [2, -2, 8, -7],
  /* 1995 */ [43, 28, 11, -5],
  /* 1996 */ [43, 16, 6, 6],
  /* 1997 */ [21, 22, 2, -12],
  /* 1998 */ [85, -3, 20, -25],
  /* 1999 */ [102, 21, 27, 66],
  /* 2000 */ [-37, -3, -14, -31],
  /* 2001 */ [-33, 2, -21, -2],
  /* 2002 */ [-38, -20, -16, -6],
  /* 2003 */ [49, 47, 39, 56],
  /* 2004 */ [11, 18, 20, 26],
  /* 2005 */ [2, 5, 14, 34],
  /* 2006 */ [7, 18, 26, 32],
  /* 2007 */ [19, -2, 11, 39],
  /* 2008 */ [-42, -34, -43, -53],
  /* 2009 */ [55, 27, 32, 79],
  /* 2010 */ [20, 27, 8, 19],
  /* 2011 */ [4, -4, -12, -18],
  /* 2012 */ [18, 16, 17, 18],
  /* 2013 */ [37, 39, 23, -2],
  /* 2014 */ [19, 5, -5, -2],
  /* 2015 */ [10, -4, -1, -15],
  /* 2016 */ [7, 21, 1, 11],
  /* 2017 */ [33, 15, 25, 37],
  /* 2018 */ [-0.1, -11, -14, -15],
  /* 2019 */ [39, 26, 22, 18],
  /* 2020 */ [48, 20, 8, 18],
  /* 2021 */ [27, 15, 11, -3],
  /* 2022 */ [-33, -20, -14, -20],
  /* 2023 */ [55, 17, 18, 10],
  /* 2024 */ [25, 11, 4, 8],
  /* 2025 */ [20, 12, 18, 12], // approximate
];

export const STOCK_INDICES = [
  { id: 'sp500', label: 'S&P 500', short: 'S&P 500', col: -1 },
  { id: 'nasdaq100', label: 'Nasdaq-100 (large-cap growth)', short: 'Nasdaq-100', col: 0 },
  { id: 'russell2000', label: 'Russell 2000 (small-cap)', short: 'Small-cap', col: 1 },
  { id: 'intl', label: 'International — developed (EAFE)', short: 'International', col: 2 },
  { id: 'em', label: 'Emerging markets', short: 'Emerging', col: 3 },
] as const;

export type StockIndexId = (typeof STOCK_INDICES)[number]['id'];
/** Weights (%) across the equity indices; need not sum to 100 (normalized). */
export type StockComposition = Record<StockIndexId, number>;

/** That index's total return (%) for HISTORICAL row `i`. */
function indexReturnAt(i: number, id: StockIndexId): number {
  if (id === 'sp500') return HISTORICAL[i][1];
  const col = STOCK_INDICES.find(x => x.id === id)!.col;
  return INDEX_RETURNS[i][col];
}

/** Weighted composite stock return (%) for HISTORICAL row `i` under `comp`
 *  (null / empty ⇒ pure S&P 500). */
export function compositeAt(i: number, comp: StockComposition | null | undefined): number {
  if (!comp) return HISTORICAL[i][1];
  let sum = 0, w = 0;
  for (const idx of STOCK_INDICES) {
    const wt = comp[idx.id] ?? 0;
    if (wt <= 0) continue;
    w += wt;
    sum += wt * indexReturnAt(i, idx.id);
  }
  return w > 0 ? sum / w : HISTORICAL[i][1];
}

/** Composite stock returns (%) for a window, aligned 1:1 with historicalWindow. */
export function stockSeriesForWindow(
  start: number, end: number, excluded: number[], comp: StockComposition | null | undefined,
): number[] {
  const out: number[] = [];
  for (let i = 0; i < HISTORICAL.length; i++) {
    const y = HISTORICAL[i][0];
    if (y < start || y > end || excluded.includes(y)) continue;
    out.push(compositeAt(i, comp));
  }
  return out;
}

/** Composite stock return (%) for a specific calendar year (for the sequence What-If). */
export function compositeStockForYear(year: number, comp: StockComposition | null | undefined): number {
  const i = HISTORICAL.findIndex(h => h[0] === year);
  return i >= 0 ? compositeAt(i, comp) : 0;
}

/** Long-run average composite return (%) across a window — for display. */
export function compositeMean(
  start: number, end: number, excluded: number[], comp: StockComposition | null | undefined,
): number {
  const s = stockSeriesForWindow(start, end, excluded, comp);
  return s.length ? s.reduce((a, b) => a + b, 0) / s.length : 0;
}

/** Years [start, end] inclusive, minus any explicitly excluded years. */
export function historicalWindow(start: number, end: number, excluded: number[] = []): HistYear[] {
  return HISTORICAL.filter(h => h[0] >= start && h[0] <= end && !excluded.includes(h[0]));
}

/** The N consecutive years inside [start, end] (minus exclusions) with the
 *  LOWEST blended portfolio return (stock/bond weighted by `stockWeightPct`).
 *  Used by the sequence-of-returns What-If to force the worst historical start
 *  to retirement. Returned in chronological order. */
export function worstReturnWindow(
  start: number, end: number, excluded: number[], n: number, stockWeightPct: number,
  comp?: StockComposition | null,
): HistYear[] {
  const hist = historicalWindow(start, end, excluded);
  if (hist.length < n) return hist.slice();
  const stocks = stockSeriesForWindow(start, end, excluded, comp); // composite %, aligned with hist
  const w = Math.min(Math.max(stockWeightPct, 0), 100) / 100;
  const blend = (j: number) => w * stocks[j] + (1 - w) * hist[j][2];
  let bestI = 0, bestSum = Infinity;
  for (let i = 0; i + n <= hist.length; i++) {
    let s = 0;
    for (let k = 0; k < n; k++) s += blend(i + k);
    if (s < bestSum) { bestSum = s; bestI = i; }
  }
  return hist.slice(bestI, bestI + n);
}

/** The `positive` best and `negative` worst years for a series (1 = stocks,
 *  2 = bonds) inside [start, end], deduped, in chronological order. When ranking
 *  stocks with a custom `comp`, the extreme years are picked by the blended
 *  return, not raw S&P. */
export function topYears(
  start: number, end: number, series: 1 | 2, positive: number, negative: number,
  comp?: StockComposition | null,
): HistYear[] {
  const val = (h: HistYear): number =>
    series === 1 && comp ? compositeStockForYear(h[0], comp) : h[series];
  const sorted = HISTORICAL
    .filter(h => h[0] >= start && h[0] <= end)
    .slice()
    .sort((a, b) => val(b) - val(a));
  const picked = [
    ...sorted.slice(0, positive),
    ...sorted.slice(Math.max(positive, sorted.length - negative)),
  ];
  return [...new Map(picked.map(h => [h[0], h])).values()].sort((a, b) => a[0] - b[0]);
}

// ---------------------------------------------------------------------------
// Equity-mix risk analytics — for the "Stock mix Lab". All computed directly
// from the historical composite series over the chosen window, so they reflect
// exactly the same data the Monte Carlo resamples. Past performance is NOT a
// guarantee of future results — these are descriptive, not predictive.
// ---------------------------------------------------------------------------

export interface StockStats {
  mean: number;        // arithmetic mean annual return (%)
  cagr: number;        // geometric / compound annual growth (%)
  vol: number;         // annualized volatility = stdev of annual returns (%)
  best: number;        // best single year (%)
  worst: number;       // worst single year (%)
  worstStreak: number; // worst 3-consecutive-year cumulative return (%)
  maxDrawdown: number; // deepest peak-to-trough decline over the window (%, negative)
  sharpe: number;      // (cagr - riskFree) / vol
  years: number;
}

/** Descriptive risk/return statistics for a stock composition over a window. */
export function stockStats(
  start: number, end: number, excluded: number[], comp: StockComposition | null | undefined,
  riskFree = 3,
): StockStats {
  const s = stockSeriesForWindow(start, end, excluded, comp); // annual returns in %
  const n = s.length;
  if (n === 0) {
    return { mean: 0, cagr: 0, vol: 0, best: 0, worst: 0, worstStreak: 0, maxDrawdown: 0, sharpe: 0, years: 0 };
  }
  const mean = s.reduce((a, b) => a + b, 0) / n;
  const variance = s.reduce((a, b) => a + (b - mean) ** 2, 0) / n;
  const vol = Math.sqrt(variance);
  // Geometric compounding of the actual sequence.
  const growth = s.reduce((acc, r) => acc * (1 + r / 100), 1);
  const cagr = (Math.pow(growth, 1 / n) - 1) * 100;
  const best = Math.max(...s);
  const worst = Math.min(...s);
  // Worst 3-consecutive-year cumulative return.
  let worstStreak = Infinity;
  const streakN = Math.min(3, n);
  for (let i = 0; i + streakN <= n; i++) {
    let g = 1;
    for (let k = 0; k < streakN; k++) g *= 1 + s[i + k] / 100;
    worstStreak = Math.min(worstStreak, (g - 1) * 100);
  }
  if (!isFinite(worstStreak)) worstStreak = (growth - 1) * 100;
  // Max drawdown across the cumulative wealth curve.
  let wealth = 1, peak = 1, maxDd = 0;
  for (const r of s) {
    wealth *= 1 + r / 100;
    if (wealth > peak) peak = wealth;
    const dd = wealth / peak - 1;
    if (dd < maxDd) maxDd = dd;
  }
  const sharpe = vol > 0 ? (cagr - riskFree) / vol : 0;
  return { mean, cagr, vol, best, worst, worstStreak, maxDrawdown: maxDd * 100, sharpe, years: n };
}

/** Preset "starter" equity mixes for the Stock mix Lab, spanning concentrated
 *  to broadly diversified. Weights are relative (normalized at use). */
export const MIX_PRESETS: { id: string; label: string; note: string; weights: StockComposition }[] = [
  { id: 'sp500', label: 'S&P 500 only', note: 'US large-cap, concentrated',
    weights: { sp500: 100, nasdaq100: 0, russell2000: 0, intl: 0, em: 0 } },
  { id: 'growth', label: 'Growth tilt', note: 'Nasdaq-heavy — higher return, higher risk',
    weights: { sp500: 50, nasdaq100: 40, russell2000: 10, intl: 0, em: 0 } },
  { id: 'us_total', label: 'Total US', note: 'Large + small-cap US',
    weights: { sp500: 80, nasdaq100: 0, russell2000: 20, intl: 0, em: 0 } },
  { id: 'global', label: 'Global 60/30/10', note: 'US / developed intl / emerging',
    weights: { sp500: 60, nasdaq100: 0, russell2000: 0, intl: 30, em: 10 } },
  { id: 'diversified', label: 'Diversified 5-way', note: 'Spread across all five sleeves',
    weights: { sp500: 40, nasdaq100: 15, russell2000: 15, intl: 20, em: 10 } },
  { id: 'balanced', label: 'Broad balanced', note: 'US core + international ballast',
    weights: { sp500: 55, nasdaq100: 10, russell2000: 10, intl: 20, em: 5 } },
];

// ---------------------------------------------------------------------------
// Expense categories & category-level inflation.
//
// Rather than hard-coding 51 years × 7 category CPI series, each category uses
// the headline CPI of the sampled year plus its long-run average spread vs
// headline (BLS category CPIs, 1975–2025 averages, rounded). Medical care has
// historically outpaced headline CPI; energy/utilities roughly track it with
// far higher volatility (volatility is inherited from the sampled CPI path).
// ---------------------------------------------------------------------------

export const EXPENSE_CATEGORIES = [
  { id: 'housing', label: 'House', spread: 0.3, discretionary: false },
  { id: 'utilities', label: 'Utilities', spread: 0.1, discretionary: false },
  { id: 'auto', label: 'Auto / Transport', spread: 0.2, discretionary: false },
  { id: 'medical', label: 'Medical', spread: 1.5, discretionary: false },
  { id: 'travel', label: 'Travel', spread: -0.1, discretionary: true },
  { id: 'grocery', label: 'Grocery', spread: 0.0, discretionary: false },
  { id: 'restaurant', label: 'Food / Restaurant', spread: 0.4, discretionary: true },
  { id: 'misc', label: 'Miscellaneous', spread: 0.0, discretionary: true },
] as const;

export type CategoryId = (typeof EXPENSE_CATEGORIES)[number]['id'];

// ---------------------------------------------------------------------------
// 401(k) elective-deferral limits (IRS §402(g)). 2026 base ≈ $24,500 with a
// 50+ catch-up and the SECURE 2.0 age-60–63 "super catch-up". Future years are
// inflation-indexed (rounded to the nearest $500 like the IRS) — a best
// approximation so a "max out" contribution keeps pace with the real limit.
// ---------------------------------------------------------------------------
export const K401_ELECTIVE_LIMIT_2026 = 24_500;
export const K401_CATCHUP_50 = 8_000;
export const K401_CATCHUP_60_63 = 11_250;

/** Approx employee elective-deferral limit for a year `inflationFactor` above
 *  the 2026 base, including the age-appropriate catch-up. */
export function k401ElectiveLimit(inflationFactor: number, age: number): number {
  const catchup = age >= 60 && age <= 63 ? K401_CATCHUP_60_63 : age >= 50 ? K401_CATCHUP_50 : 0;
  const limit = (K401_ELECTIVE_LIMIT_2026 + catchup) * inflationFactor;
  return Math.round(limit / 500) * 500;
}

/** Share of a "simple mode" budget treated as discretionary for guardrails. */
export const SIMPLE_DISCRETIONARY_SHARE = 0.2;

// ---------------------------------------------------------------------------
// Social Security
//
// Benefit factors by claiming age relative to the age-67 Full Retirement Age
// benefit (born 1960+): -30% at 62, +24% at 70. The user supplies their FRA
// estimate from ssa.gov; COLA is applied from each simulated year's inflation
// (floored at 0 — SSA COLA is never negative).
// ---------------------------------------------------------------------------

export const SS_CLAIM_FACTORS: Record<number, number> = {
  62: 0.700, 63: 0.750, 64: 0.800, 65: 0.867, 66: 0.933,
  67: 1.000, 68: 1.080, 69: 1.160, 70: 1.240,
};

export const SS_CLAIM_AGES = Object.keys(SS_CLAIM_FACTORS).map(Number);

/** Provisional-income thresholds for taxing SS benefits (not inflation indexed by law). */
export const SS_TAX_THRESHOLDS = {
  single: { t1: 25_000, t2: 34_000 },
  mfj: { t1: 32_000, t2: 44_000 },
};

// ---------------------------------------------------------------------------
// Federal tax parameters (2026 tax year). Bracket thresholds, standard deduction and
// LTCG breakpoints are CPI-indexed in the simulation so long horizons don't
// suffer artificial bracket creep. State tax is out of scope.
// ---------------------------------------------------------------------------

export type FilingStatus = 'single' | 'mfj';

export interface TaxBracket { upTo: number; rate: number }

// 2026 tax-year figures (Rev. Proc. 2025-32, incl. OBBBA adjustments). The
// simulation indexes all of these with each path's cumulative inflation
// (`bracketScale`), so they keep rising year over year in-sim.
export const ORDINARY_BRACKETS: Record<FilingStatus, TaxBracket[]> = {
  single: [
    { upTo: 12_400, rate: 0.10 },
    { upTo: 50_400, rate: 0.12 },
    { upTo: 105_700, rate: 0.22 },
    { upTo: 201_775, rate: 0.24 },
    { upTo: 256_225, rate: 0.32 },
    { upTo: 640_600, rate: 0.35 },
    { upTo: Infinity, rate: 0.37 },
  ],
  mfj: [
    { upTo: 24_800, rate: 0.10 },
    { upTo: 100_800, rate: 0.12 },
    { upTo: 211_400, rate: 0.22 },
    { upTo: 403_550, rate: 0.24 },
    { upTo: 512_450, rate: 0.32 },
    { upTo: 768_700, rate: 0.35 },
    { upTo: Infinity, rate: 0.37 },
  ],
};

export const LTCG_BRACKETS: Record<FilingStatus, TaxBracket[]> = {
  single: [
    { upTo: 49_450, rate: 0.0 },
    { upTo: 552_850, rate: 0.15 },
    { upTo: Infinity, rate: 0.20 },
  ],
  mfj: [
    { upTo: 98_900, rate: 0.0 },
    { upTo: 622_050, rate: 0.15 },
    { upTo: Infinity, rate: 0.20 },
  ],
};

export const STANDARD_DEDUCTION: Record<FilingStatus, number> = {
  single: 16_100,
  mfj: 32_200,
};

/** Top of the bracket with the given rate — ceilings for Roth-conversion planning. */
export function bracketTopFor(filing: FilingStatus, ratePct: 12 | 22 | 24): number {
  const b = ORDINARY_BRACKETS[filing].find(x => Math.round(x.rate * 100) === ratePct);
  return b ? b.upTo : Infinity;
}

// ── NIIT: 3.8% on net investment income above MAGI thresholds (not indexed, per law) ──
export const NIIT_RATE = 0.038;
export const NIIT_THRESHOLD: Record<FilingStatus, number> = { single: 200_000, mfj: 250_000 };

// ── IRMAA: Medicare Part B + D premium surcharges by MAGI (2026 approx., per
// person/yr). Real IRMAA uses a 2-year MAGI lookback; we apply current-year
// MAGI and index the tiers with CPI — documented simplifications. Single
// thresholds; MFJ = 2×.
export const IRMAA_TIERS: { magiSingle: number; annual: number }[] = [
  { magiSingle: 109_000, annual: 0 },
  { magiSingle: 137_000, annual: 1_150 },
  { magiSingle: 171_000, annual: 2_890 },
  { magiSingle: 205_000, annual: 4_630 },
  { magiSingle: 500_000, annual: 6_370 },
  { magiSingle: Infinity, annual: 6_950 },
];

export function irmaaAnnual(magi: number, filing: FilingStatus, scale: number, persons: number): number {
  const mult = filing === 'mfj' ? 2 : 1;
  for (const t of IRMAA_TIERS) {
    if (magi <= t.magiSingle * mult * scale) return t.annual * scale * persons;
  }
  return IRMAA_TIERS[IRMAA_TIERS.length - 1].annual * scale * persons;
}

// ---------------------------------------------------------------------------
// RMDs — SECURE 2.0: first RMD at 73 (born 1951–1959) or 75 (born 1960+).
// IRS Uniform Lifetime Table divisors.
// ---------------------------------------------------------------------------

export function rmdStartAge(birthYear: number): number {
  return birthYear >= 1960 ? 75 : 73;
}

export const RMD_DIVISORS: Record<number, number> = {
  73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9, 78: 22.0, 79: 21.1,
  80: 20.2, 81: 19.4, 82: 18.5, 83: 17.7, 84: 16.8, 85: 16.0, 86: 15.2,
  87: 14.4, 88: 13.7, 89: 12.9, 90: 12.2, 91: 11.5, 92: 10.8, 93: 10.1,
  94: 9.5, 95: 8.9, 96: 8.4, 97: 7.8, 98: 7.3, 99: 6.8, 100: 6.4,
  101: 6.0, 102: 5.6, 103: 5.2, 104: 4.9, 105: 4.6, 106: 4.3, 107: 4.1,
  108: 3.9, 109: 3.7, 110: 3.5,
};

export function rmdDivisor(age: number): number {
  if (age < 73) return Infinity;
  return RMD_DIVISORS[Math.min(age, 110)] ?? 3.5;
}
