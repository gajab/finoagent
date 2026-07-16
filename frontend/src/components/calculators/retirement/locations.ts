// ---------------------------------------------------------------------------
// Location data: ZIP → state, simplified state income tax on retirement
// income, and long-term-care cost estimates. All hard-coded so the calculator
// never calls an external service.
//
// State tax figures are deliberately simplified single effective rates with
// the retirement-income features that matter most (no-tax states, states that
// exempt retirement-account distributions, SS taxation, common exclusions).
// LTC costs = national Genworth-style 2024 medians × a state cost index.
// Both are planning estimates, labeled as such in the UI.
// ---------------------------------------------------------------------------

// ── ZIP prefix (first 3 digits) → state ──
const ZIP3_RANGES: [number, number, string][] = [
  [5, 5, 'NY'], [10, 27, 'MA'], [28, 29, 'RI'], [30, 38, 'NH'], [39, 49, 'ME'],
  [50, 59, 'VT'], [60, 69, 'CT'], [70, 89, 'NJ'], [100, 149, 'NY'], [150, 196, 'PA'],
  [197, 199, 'DE'], [200, 200, 'DC'], [201, 201, 'VA'], [202, 205, 'DC'], [206, 219, 'MD'],
  [220, 246, 'VA'], [247, 268, 'WV'], [270, 289, 'NC'], [290, 299, 'SC'], [300, 319, 'GA'],
  [320, 349, 'FL'], [350, 369, 'AL'], [370, 385, 'TN'], [386, 397, 'MS'], [398, 399, 'GA'],
  [400, 427, 'KY'], [430, 459, 'OH'], [460, 479, 'IN'], [480, 499, 'MI'], [500, 528, 'IA'],
  [530, 549, 'WI'], [550, 567, 'MN'], [570, 577, 'SD'], [580, 588, 'ND'], [590, 599, 'MT'],
  [600, 629, 'IL'], [630, 658, 'MO'], [660, 679, 'KS'], [680, 693, 'NE'], [700, 714, 'LA'],
  [716, 729, 'AR'], [730, 749, 'OK'], [750, 799, 'TX'], [800, 816, 'CO'], [820, 831, 'WY'],
  [832, 838, 'ID'], [840, 847, 'UT'], [850, 865, 'AZ'], [870, 884, 'NM'], [885, 885, 'TX'],
  [889, 898, 'NV'], [900, 961, 'CA'], [967, 968, 'HI'], [970, 979, 'OR'], [980, 994, 'WA'],
  [995, 999, 'AK'],
];

export function zipToState(zip: string): string | null {
  const digits = zip.trim().slice(0, 3);
  if (!/^\d{3}$/.test(digits)) return null;
  const p = Number(digits);
  for (const [lo, hi, st] of ZIP3_RANGES) {
    if (p >= lo && p <= hi) return st;
  }
  return null;
}

export const STATE_NAMES: Record<string, string> = {
  AL: 'Alabama', AK: 'Alaska', AZ: 'Arizona', AR: 'Arkansas', CA: 'California',
  CO: 'Colorado', CT: 'Connecticut', DE: 'Delaware', DC: 'Washington DC', FL: 'Florida',
  GA: 'Georgia', HI: 'Hawaii', ID: 'Idaho', IL: 'Illinois', IN: 'Indiana',
  IA: 'Iowa', KS: 'Kansas', KY: 'Kentucky', LA: 'Louisiana', ME: 'Maine',
  MD: 'Maryland', MA: 'Massachusetts', MI: 'Michigan', MN: 'Minnesota', MS: 'Mississippi',
  MO: 'Missouri', MT: 'Montana', NE: 'Nebraska', NV: 'Nevada', NH: 'New Hampshire',
  NJ: 'New Jersey', NM: 'New Mexico', NY: 'New York', NC: 'North Carolina', ND: 'North Dakota',
  OH: 'Ohio', OK: 'Oklahoma', OR: 'Oregon', PA: 'Pennsylvania', RI: 'Rhode Island',
  SC: 'South Carolina', SD: 'South Dakota', TN: 'Tennessee', TX: 'Texas', UT: 'Utah',
  VT: 'Vermont', VA: 'Virginia', WA: 'Washington', WV: 'West Virginia', WI: 'Wisconsin',
  WY: 'Wyoming',
};

// ── Simplified state income tax on retirement income (2025-ish) ──
export interface StateTaxInfo {
  rate: number;               // effective % applied to the taxable base
  taxesSS: boolean;           // state taxes (at least some) Social Security
  retirementExempt?: boolean; // 401k/IRA/pension distributions fully exempt (IL, PA, MS, IA)
  exclusion?: number;         // per-person retirement-income exclusion, $
}

export const STATE_TAX: Record<string, StateTaxInfo> = {
  AK: { rate: 0, taxesSS: false }, FL: { rate: 0, taxesSS: false }, NV: { rate: 0, taxesSS: false },
  NH: { rate: 0, taxesSS: false }, SD: { rate: 0, taxesSS: false }, TN: { rate: 0, taxesSS: false },
  TX: { rate: 0, taxesSS: false }, WA: { rate: 0, taxesSS: false }, WY: { rate: 0, taxesSS: false },
  AL: { rate: 5.0, taxesSS: false }, AR: { rate: 3.9, taxesSS: false }, AZ: { rate: 2.5, taxesSS: false },
  CA: { rate: 7.0, taxesSS: false }, CO: { rate: 4.4, taxesSS: false }, CT: { rate: 5.5, taxesSS: true },
  DC: { rate: 7.5, taxesSS: false }, DE: { rate: 5.5, taxesSS: false, exclusion: 12_500 },
  GA: { rate: 5.39, taxesSS: false, exclusion: 65_000 }, HI: { rate: 7.5, taxesSS: false },
  IA: { rate: 3.8, taxesSS: false, retirementExempt: true }, ID: { rate: 5.7, taxesSS: false },
  IL: { rate: 4.95, taxesSS: false, retirementExempt: true }, IN: { rate: 3.0, taxesSS: false },
  KS: { rate: 5.58, taxesSS: false }, KY: { rate: 4.0, taxesSS: false }, LA: { rate: 3.0, taxesSS: false },
  MA: { rate: 5.0, taxesSS: false }, MD: { rate: 5.75, taxesSS: false, exclusion: 20_000 },
  ME: { rate: 7.15, taxesSS: false, exclusion: 30_000 }, MI: { rate: 4.25, taxesSS: false },
  MN: { rate: 7.85, taxesSS: true }, MO: { rate: 4.7, taxesSS: false },
  MS: { rate: 4.4, taxesSS: false, retirementExempt: true }, MT: { rate: 5.9, taxesSS: true },
  NC: { rate: 4.25, taxesSS: false }, ND: { rate: 2.5, taxesSS: false }, NE: { rate: 5.2, taxesSS: false },
  NJ: { rate: 6.0, taxesSS: false, exclusion: 75_000 }, NM: { rate: 4.9, taxesSS: true },
  NY: { rate: 6.0, taxesSS: false, exclusion: 20_000 }, OH: { rate: 3.5, taxesSS: false },
  OK: { rate: 4.75, taxesSS: false, exclusion: 10_000 }, OR: { rate: 8.75, taxesSS: false },
  PA: { rate: 3.07, taxesSS: false, retirementExempt: true }, RI: { rate: 5.99, taxesSS: true },
  SC: { rate: 6.2, taxesSS: false, exclusion: 15_000 }, UT: { rate: 4.55, taxesSS: true },
  VA: { rate: 5.75, taxesSS: false, exclusion: 12_000 }, VT: { rate: 7.6, taxesSS: true },
  WV: { rate: 4.82, taxesSS: false }, WI: { rate: 5.7, taxesSS: false },
};

// ── Long-term care cost estimates ──
// National 2024-style median annual costs; state index scales them.
export const LTC_NATIONAL = {
  homeHealth: 75_000,       // home health aide, ~44 hrs/wk
  assistedLiving: 64_000,
  nursingHome: 104_000,     // semi-private room
} as const;

export type LtcCareType = keyof typeof LTC_NATIONAL;

export const LTC_CARE_LABELS: Record<LtcCareType, string> = {
  homeHealth: 'Home health aide',
  assistedLiving: 'Assisted living',
  nursingHome: 'Nursing home (semi-private)',
};

const LTC_COST_INDEX: Record<string, number> = {
  AK: 2.0, HI: 1.45, MA: 1.35, CT: 1.3, NY: 1.3, DC: 1.3, NJ: 1.25, NH: 1.25, RI: 1.25,
  CA: 1.2, WA: 1.2, VT: 1.2, ME: 1.2, OR: 1.15, MN: 1.15, MD: 1.1, DE: 1.1, CO: 1.1,
  WI: 1.05, PA: 1.05, ND: 1.05, VA: 1.0, AZ: 1.0, NV: 1.0, MT: 1.0, ID: 1.0,
  IL: 0.95, FL: 0.95, OH: 0.95, MI: 0.95, IA: 0.95, NE: 0.95, SD: 0.95, WY: 0.95, UT: 0.95,
  NC: 0.9, NM: 0.9, IN: 0.9, WV: 0.9,
  TX: 0.85, GA: 0.85, SC: 0.85, TN: 0.85, KY: 0.85, MO: 0.85, KS: 0.85,
  AL: 0.8, MS: 0.75, LA: 0.75, AR: 0.75, OK: 0.75,
};

export function ltcCostEstimate(state: string | null, type: LtcCareType): number {
  const idx = (state && LTC_COST_INDEX[state]) || 1;
  return Math.round(LTC_NATIONAL[type] * idx / 1000) * 1000;
}

/** Ratio of long-term-care cost between two states (target / home). */
export function ltcCostRatio(home: string | null, target: string | null): number {
  const h = (home && LTC_COST_INDEX[home]) || 1;
  const t = (target && LTC_COST_INDEX[target]) || 1;
  return t / h;
}

// ---------------------------------------------------------------------------
// Cost of living — used by the What-If "relocate" comparison. Approximate 2024
// indices (MERIC/BEA style, US average = 100). Housing is broken out because
// it drives most of the interstate difference; everything else tracks the
// overall index. Planning estimates, labeled as such in the UI.
// ---------------------------------------------------------------------------

export const STATE_COL: Record<string, number> = {
  AL: 88, AK: 125, AZ: 108, AR: 90, CA: 138, CO: 105, CT: 113, DE: 101, DC: 145,
  FL: 103, GA: 91, HI: 186, ID: 104, IL: 92, IN: 91, IA: 90, KS: 87, KY: 92,
  LA: 91, ME: 111, MD: 116, MA: 146, MI: 91, MN: 97, MS: 86, MO: 89, MT: 103,
  NE: 91, NV: 102, NH: 110, NJ: 114, NM: 94, NY: 125, NC: 95, ND: 95, OH: 94,
  OK: 86, OR: 113, PA: 96, RI: 110, SC: 96, SD: 92, TN: 90, TX: 93, UT: 103,
  VT: 114, VA: 103, WA: 114, WV: 90, WI: 96, WY: 95,
};

export const STATE_HOUSING: Record<string, number> = {
  AL: 72, AK: 135, AZ: 115, AR: 75, CA: 240, CO: 122, CT: 122, DE: 100, DC: 260,
  FL: 110, GA: 88, HI: 320, ID: 120, IL: 82, IN: 82, IA: 78, KS: 76, KY: 80,
  LA: 84, ME: 120, MD: 130, MA: 230, MI: 82, MN: 96, MS: 68, MO: 80, MT: 112,
  NE: 82, NV: 112, NH: 125, NJ: 130, NM: 90, NY: 145, NC: 95, ND: 90, OH: 84,
  OK: 76, OR: 130, PA: 92, RI: 125, SC: 98, SD: 88, TN: 92, TX: 85, UT: 118,
  VT: 128, VA: 105, WA: 135, WV: 76, WI: 90, WY: 92,
};

const col = (s: string | null, table: Record<string, number>) => (s && table[s]) || 100;

/** Overall cost-of-living ratio moving home → target (1.10 = 10% pricier). */
export function colRatio(home: string | null, target: string | null): number {
  return col(target, STATE_COL) / col(home, STATE_COL);
}

/** Housing-only cost ratio moving home → target. */
export function housingRatio(home: string | null, target: string | null): number {
  return col(target, STATE_HOUSING) / col(home, STATE_HOUSING);
}
