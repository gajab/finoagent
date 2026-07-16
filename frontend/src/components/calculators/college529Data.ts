// 529 Plan State Data & ZIP Code Mapping - 2026 Updates

export interface PlanDetails {
  planName: string;
  type: 'Direct-Sold Savings' | 'Advisor-Sold Savings' | 'Prepaid Tuition';
  manager: string;
  rating: 'Gold' | 'Silver' | 'Bronze' | 'Neutral' | 'None';
  avgFeePct: number;
  options: string[];
  notes: string;
  websiteUrl: string;
}

export interface State529Rules {
  stateCode: string;
  stateName: string;
  hasDeduction: boolean;
  hasCredit: boolean;
  deductionLimitSingle: number;
  deductionLimitJoint: number;
  creditRatePct?: number;
  creditLimitSingle?: number;
  creditLimitJoint?: number;
  hasTaxParity: boolean;
  noIncomeTax: boolean;
  stateTaxRate: number;
  taxNotes: string;
  plans: PlanDetails[];
}

export type State529Data = State529Rules;

export const STATE_529_DATA: Record<string, State529Rules> = {

  AL: {

    stateCode: 'AL',

    stateName: 'Alabama',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 5000,

    deductionLimitJoint: 10000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.05,

    taxNotes: 'Deduction up to $5,000 ($10,000 for married couples filing jointly) per beneficiary. Requires in-state plan.',

    plans: [

      {

        planName: 'CollegeEducation529 (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.alabama529.org',

      },

      {

        planName: 'CollegeEducation529 (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.alabama529.org',

      },

    ],

  },

  AK: {

    stateCode: 'AK',

    stateName: 'Alaska',

    hasDeduction: false,

    hasCredit: false,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    hasTaxParity: false,

    noIncomeTax: true,

    stateTaxRate: 0.0,

    taxNotes: 'No state income tax. Contributions grow tax-deferred and withdrawals are tax-free at federal level.',

    plans: [

      {

        planName: 'Alaska 529 (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.alaska529plan.com',

      },

      {

        planName: 'Alaska 529 (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.alaska529plan.com',

      },

    ],

  },

  AZ: {

    stateCode: 'AZ',

    stateName: 'Arizona',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 2000,

    deductionLimitJoint: 4000,

    hasTaxParity: true,

    noIncomeTax: false,

    stateTaxRate: 0.025,

    taxNotes: 'TAX PARITY STATE. Deduct up to $2,000 ($4,000 for joint filers) for contributions to ANY state’s 529 plan.',

    plans: [

      {

        planName: 'AZ529 (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.az529.gov',

      },

      {

        planName: 'AZ529 (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.az529.gov',

      },

    ],

  },

  AR: {

    stateCode: 'AR',

    stateName: 'Arkansas',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 5000,

    deductionLimitJoint: 10000,

    hasTaxParity: true,

    noIncomeTax: false,

    stateTaxRate: 0.044,

    taxNotes: 'TAX PARITY STATE. Deduct up to $5,000 ($10,000 for joint filers) for contributions to any state’s 529 plan.',

    plans: [

      {

        planName: 'GIFT College Investing Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.16,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.arkansas529.org',

      },

      {

        planName: 'GIFT College Investing Plan (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.arkansas529.org',

      },

    ],

  },

  CA: {

    stateCode: 'CA',

    stateName: 'California',

    hasDeduction: false,

    hasCredit: false,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.08,

    taxNotes: 'California offers no state tax deduction or credit for 529 contributions. Gains are tax-free for qualified withdrawals.',

    plans: [

      {

        planName: 'ScholarShare 529 (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'TIAA-CREF',

        rating: 'Silver',

        avgFeePct: 0.13,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market', 'Total Stock Market'],

        notes: 'California’s direct-sold savings plan features very low fees and diverse Vanguard/TIAA index funds.',

        websiteUrl: 'https://www.scholarshare529.com',

      },

    ],

  },

  CO: {

    stateCode: 'CO',

    stateName: 'Colorado',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 26200,

    deductionLimitJoint: 39200,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.044,

    taxNotes: 'High deduction limits. Colorado taxpayers can deduct up to $26,200 (single) or $39,200 (joint) per beneficiary for 2026.',

    plans: [

      {

        planName: 'CollegeInvest (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.collegeinvest.org',

      },

      {

        planName: 'CollegeInvest (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.collegeinvest.org',

      },

    ],

  },

  CT: {

    stateCode: 'CT',

    stateName: 'Connecticut',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 5000,

    deductionLimitJoint: 10000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.05,

    taxNotes: 'Deduction up to $5,000 ($10,000 for joint filers) per tax return for contributions to CHET.',

    plans: [

      {

        planName: 'CHET Direct (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.aboutchet.com',

      },

      {

        planName: 'CHET Direct (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.aboutchet.com',

      },

    ],

  },

  DE: {

    stateCode: 'DE',

    stateName: 'Delaware',

    hasDeduction: false,

    hasCredit: false,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.05,

    taxNotes: 'Delaware offers no state income tax deduction or credit for 529 plan contributions.',

    plans: [

      {

        planName: 'Delaware College Investment Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'None',

        avgFeePct: 0.20,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market', 'Nasdaq 100', 'Total Stock Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.fidelity.com/529-plans/delaware',

      },

      {

        planName: 'Delaware College Investment Plan (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.fidelity.com/529-plans/delaware',

      },

    ],

  },

  DC: {

    stateCode: 'DC',

    stateName: 'District of Columbia',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 4000,

    deductionLimitJoint: 8000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.065,

    taxNotes: 'Deduction up to $4,000 ($8,000 for married couples filing jointly) for DC plan contributions.',

    plans: [

      {

        planName: 'DC College Savings Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.dc529.com',

      },

      {

        planName: 'DC College Savings Plan (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.dc529.com',

      },

    ],

  },

  FL: {

    stateCode: 'FL',

    stateName: 'Florida',

    hasDeduction: false,

    hasCredit: false,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    hasTaxParity: false,

    noIncomeTax: true,

    stateTaxRate: 0.0,

    taxNotes: 'No state income tax. Florida Prepaid or Florida 529 plans grow tax-free for qualified expenses.',

    plans: [

      {

        planName: 'Florida 529 Savings Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'Florida Prepaid College Board',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Direct-sold savings plan offering flexible portfolios.',

        websiteUrl: 'https://www.myfloridaprepaid.com/savings-plan/',

      },

      {

        planName: 'Florida Prepaid College Plan (Prepaid)',

        type: 'Prepaid Tuition',

        manager: 'Florida Prepaid College Board',

        rating: 'None',

        avgFeePct: 0.0,

        options: ['Prepaid Tuition Contract'],

        notes: 'Popular prepaid tuition plan to lock in Florida public university tuition rates.',

        websiteUrl: 'https://www.myfloridaprepaid.com',

      },

    ],

  },

  GA: {

    stateCode: 'GA',

    stateName: 'Georgia',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 4000,

    deductionLimitJoint: 8000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.0549,

    taxNotes: 'Deduction up to $4,000 ($8,000 for joint filers) per beneficiary per year for GA residents.',

    plans: [

      {

        planName: 'Path2College 529 Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.path2college529.com',

      },

      {

        planName: 'Path2College 529 Plan (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.path2college529.com',

      },

    ],

  },

  HI: {

    stateCode: 'HI',

    stateName: 'Hawaii',

    hasDeduction: false,

    hasCredit: false,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.06,

    taxNotes: 'Hawaii does not offer state income tax deductions or credits for 529 plan contributions.',

    plans: [

      {

        planName: 'HI529 (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'None',

        avgFeePct: 0.20,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.hi529.com',

      },

      {

        planName: 'HI529 (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.hi529.com',

      },

    ],

  },

  ID: {

    stateCode: 'ID',

    stateName: 'Idaho',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 6000,

    deductionLimitJoint: 12000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.058,

    taxNotes: 'Deduction up to $6,000 ($12,000 for joint filers) for contributions to Idaho’s plan.',

    plans: [

      {

        planName: 'Ideal Idaho College Savings (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.idsaves.org',

      },

      {

        planName: 'Ideal Idaho College Savings (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.idsaves.org',

      },

    ],

  },

  IL: {

    stateCode: 'IL',

    stateName: 'Illinois',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 10000,

    deductionLimitJoint: 20000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.0495,

    taxNotes: 'Highly rated Gold plan. Deduction up to $10,000 ($20,000 for joint filers) per tax return.',

    plans: [

      {

        planName: 'Bright Start College Savings (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'Union Bank & Trust',

        rating: 'Gold',

        avgFeePct: 0.12,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market', 'Total Stock Market'],

        notes: 'Gold-rated Direct plan with outstanding investment selections.',

        websiteUrl: 'https://www.brightstartsavings.com',

      },

      {

        planName: 'Bright Directions College Savings (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Union Bank & Trust',

        rating: 'Gold',

        avgFeePct: 0.45,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Highly rated advisor-sold plan, but subject to advisor fees.',

        websiteUrl: 'https://www.brightdirections.com',

      },

    ],

  },

  IN: {

    stateCode: 'IN',

    stateName: 'Indiana',

    hasDeduction: false,

    hasCredit: true,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    creditRatePct: 20,

    creditLimitSingle: 1500,

    creditLimitJoint: 1500,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.0315,

    taxNotes: 'Generous 20% state tax credit. Claim up to $1,500 credit on up to $7,500 in contributions.',

    plans: [

      {

        planName: 'Indiana529 Direct (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.in529.com',

      },

      {

        planName: 'Indiana529 Direct (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.in529.com',

      },

    ],

  },

  IA: {

    stateCode: 'IA',

    stateName: 'Iowa',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 4000,

    deductionLimitJoint: 4000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.057,

    taxNotes: 'Deduction up to $4,000 per beneficiary for Iowa taxpayers contributing to College Savings Iowa.',

    plans: [

      {

        planName: 'College Savings Iowa (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Silver',

        avgFeePct: 0.14,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.collegesavingsiowa.com',

      },

      {

        planName: 'College Savings Iowa (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.collegesavingsiowa.com',

      },

    ],

  },

  KS: {

    stateCode: 'KS',

    stateName: 'Kansas',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 3000,

    deductionLimitJoint: 6000,

    hasTaxParity: true,

    noIncomeTax: false,

    stateTaxRate: 0.057,

    taxNotes: 'TAX PARITY STATE. Deduct up to $3,000 ($6,000 for joint filers) for contributions to any state’s plan.',

    plans: [

      {

        planName: 'Learning Quest (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.learningquest.com',

      },

      {

        planName: 'Learning Quest (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.learningquest.com',

      },

    ],

  },

  KY: {

    stateCode: 'KY',

    stateName: 'Kentucky',

    hasDeduction: false,

    hasCredit: false,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.045,

    taxNotes: 'Kentucky offers no state income tax deduction or credit for 529 plan contributions.',

    plans: [

      {

        planName: 'KY Saves 529 (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'None',

        avgFeePct: 0.18,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.kysaves.com',

      },

      {

        planName: 'KY Saves 529 (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.kysaves.com',

      },

    ],

  },

  LA: {

    stateCode: 'LA',

    stateName: 'Louisiana',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 2400,

    deductionLimitJoint: 4800,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.042,

    taxNotes: 'Deduction up to $2,400 per account ($4,800 for joint filers) with roll-over of excess.',

    plans: [

      {

        planName: 'START Saving Program (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.startsaving.la.gov',

      },

      {

        planName: 'START Saving Program (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.startsaving.la.gov',

      },

    ],

  },

  ME: {

    stateCode: 'ME',

    stateName: 'Maine',

    hasDeduction: false,

    hasCredit: false,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    hasTaxParity: true,

    noIncomeTax: false,

    stateTaxRate: 0.0715,

    taxNotes: 'TAX PARITY STATE. No income tax deduction, but offers state matching grants and incentives for residents.',

    plans: [

      {

        planName: 'NextGen 529 (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.nextgen529.com',

      },

      {

        planName: 'NextGen 529 (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.nextgen529.com',

      },

    ],

  },

  MD: {

    stateCode: 'MD',

    stateName: 'Maryland',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 2500,

    deductionLimitJoint: 5000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.08,

    taxNotes: 'Subtraction up to $2,500 per beneficiary ($5,000 for joint filers). Excess carries forward for 10 years.',

    plans: [

      {

        planName: 'Maryland College Investment Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'T. Rowe Price',

        rating: 'Silver',

        avgFeePct: 0.13,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market', 'Total Stock Market'],

        notes: 'Direct savings plan managed by T. Rowe Price.',

        websiteUrl: 'https://maryland529.com/college-investment-plan/',

      },

      {

        planName: 'Maryland Prepaid College Trust (Prepaid)',

        type: 'Prepaid Tuition',

        manager: 'State of Maryland',

        rating: 'None',

        avgFeePct: 0.0,

        options: ['Prepaid Tuition Contract'],

        notes: 'Maryland’s prepaid tuition trust to lock in tuition rates.',

        websiteUrl: 'https://maryland529.com/prepaid-college-trust/',

      },

    ],

  },

  MA: {

    stateCode: 'MA',

    stateName: 'Massachusetts',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 1000,

    deductionLimitJoint: 2000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.05,

    taxNotes: 'Deduction up to $1,000 ($2,000 joint) per year. Covers MEFA’s U.Fund (savings) and U.Plan (prepaid tuition).',

    plans: [

      {

        planName: 'U.Fund College Investing Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'Fidelity',

        rating: 'Silver',

        avgFeePct: 0.14,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market', 'Nasdaq 100', 'Total Stock Market'],

        notes: 'Direct-sold Fidelity savings plan for Massachusetts residents.',

        websiteUrl: 'https://www.fidelity.com/529-plans/massachusetts',

      },

      {

        planName: 'U.Plan Prepaid Tuition Program',

        type: 'Prepaid Tuition',

        manager: 'MEFA',

        rating: 'None',

        avgFeePct: 0.0,

        options: ['Prepaid Tuition Certificate'],

        notes: 'Allows parents to purchase tuition certificates that lock in costs at MA colleges.',

        websiteUrl: 'https://www.mefa.org/saved/uplan-prepaid-tuition-program',

      },

      {

        planName: 'Fidelity Advisor 529 Plan (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Fidelity',

        rating: 'Neutral',

        avgFeePct: 0.55,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold savings plan managed by Fidelity.',

        websiteUrl: 'https://www.fidelity.com/529-plans/fidelity-advisor-529-plan',

      },

    ],

  },

  MI: {

    stateCode: 'MI',

    stateName: 'Michigan',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 5000,

    deductionLimitJoint: 10000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.0425,

    taxNotes: 'Deduct up to $5,000 ($10,000 for joint filers) per tax return for MESP contributions.',

    plans: [

      {

        planName: 'Michigan Education Savings Program (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Silver',

        avgFeePct: 0.12,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.misaves.com',

      },

      {

        planName: 'Michigan Education Savings Program (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.misaves.com',

      },

    ],

  },

  MN: {

    stateCode: 'MN',

    stateName: 'Minnesota',

    hasDeduction: true,

    hasCredit: true,

    deductionLimitSingle: 1500,

    deductionLimitJoint: 3000,

    creditRatePct: 50,

    creditLimitSingle: 500,

    creditLimitJoint: 500,

    hasTaxParity: true,

    noIncomeTax: false,

    stateTaxRate: 0.068,

    taxNotes: 'TAX PARITY STATE. Choose either a deduction (up to $1,500 single / $3,000 joint) OR a 50% credit up to $500.',

    plans: [

      {

        planName: 'Minnesota College Savings Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.mnsaves.org',

      },

      {

        planName: 'Minnesota College Savings Plan (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.mnsaves.org',

      },

    ],

  },

  MS: {

    stateCode: 'MS',

    stateName: 'Mississippi',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 10000,

    deductionLimitJoint: 20000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.05,

    taxNotes: 'Deduction up to $10,000 ($20,000 for joint filers) for contributions to the MACS plan.',

    plans: [

      {

        planName: 'MACS (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.16,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.ms529.com',

      },

      {

        planName: 'MACS (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.ms529.com',

      },

    ],

  },

  MO: {

    stateCode: 'MO',

    stateName: 'Missouri',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 8000,

    deductionLimitJoint: 16000,

    hasTaxParity: true,

    noIncomeTax: false,

    stateTaxRate: 0.048,

    taxNotes: 'TAX PARITY STATE. Deduct up to $8,000 ($16,000 for joint filers) for contributions to any state’s plan.',

    plans: [

      {

        planName: 'MOST (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Silver',

        avgFeePct: 0.13,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.most529.com',

      },

      {

        planName: 'MOST (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.most529.com',

      },

    ],

  },

  MT: {

    stateCode: 'MT',

    stateName: 'Montana',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 3000,

    deductionLimitJoint: 6000,

    hasTaxParity: true,

    noIncomeTax: false,

    stateTaxRate: 0.047,

    taxNotes: 'TAX PARITY STATE. Deduct up to $3,000 ($6,000 for joint filers) per tax return for any state’s 529 plan.',

    plans: [

      {

        planName: 'Achieve Montana (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://achievemontana.com',

      },

      {

        planName: 'Achieve Montana (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://achievemontana.com',

      },

    ],

  },

  NE: {

    stateCode: 'NE',

    stateName: 'Nebraska',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 10000,

    deductionLimitJoint: 10000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.0584,

    taxNotes: 'Deduction up to $10,000 per tax return ($5,000 for married filing separately) for Nebraska plan.',

    plans: [

      {

        planName: 'NEST 529 College Savings (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Silver',

        avgFeePct: 0.13,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.nest529.com',

      },

      {

        planName: 'NEST 529 College Savings (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.nest529.com',

      },

    ],

  },

  NV: {

    stateCode: 'NV',

    stateName: 'Nevada',

    hasDeduction: false,

    hasCredit: false,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    hasTaxParity: false,

    noIncomeTax: true,

    stateTaxRate: 0.0,

    taxNotes: 'No state income tax. Nevada hosts Vanguard’s national 529, highly regarded for low index fund fees.',

    plans: [

      {

        planName: 'SSgA Upromise / Vanguard 529 (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Silver',

        avgFeePct: 0.13,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.vanguard.com/us/whatweoffer/college/529vanguard',

      },

      {

        planName: 'SSgA Upromise / Vanguard 529 (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.vanguard.com/us/whatweoffer/college/529vanguard',

      },

    ],

  },

  NH: {

    stateCode: 'NH',

    stateName: 'New Hampshire',

    hasDeduction: false,

    hasCredit: false,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    hasTaxParity: false,

    noIncomeTax: true,

    stateTaxRate: 0.0,

    taxNotes: 'No state income tax. NH hosts the Fidelity-managed Unique plan, open to all investors.',

    plans: [

      {

        planName: 'Unique College Investing Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Silver',

        avgFeePct: 0.14,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market', 'Nasdaq 100', 'Total Stock Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.fidelity.com/529-plans/new-hampshire',

      },

      {

        planName: 'Unique College Investing Plan (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.fidelity.com/529-plans/new-hampshire',

      },

    ],

  },

  NJ: {

    stateCode: 'NJ',

    stateName: 'New Jersey',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 10000,

    deductionLimitJoint: 10000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.0637,

    taxNotes: 'Deduct up to $10,000 per year for contributions to NJBEST if household income is $200,000 or less.',

    plans: [

      {

        planName: 'NJBEST (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.njbest.com',

      },

      {

        planName: 'NJBEST (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.njbest.com',

      },

    ],

  },

  NM: {

    stateCode: 'NM',

    stateName: 'New Mexico',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 999999, // unlimited,

    deductionLimitJoint: 999999,  // unlimited,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.049,

    taxNotes: 'UNLIMITED state tax deduction for New Mexico residents contributing to New Mexico’s plan.',

    plans: [

      {

        planName: 'The Education Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.16,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.theeducationplan.com',

      },

      {

        planName: 'The Education Plan (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.theeducationplan.com',

      },

    ],

  },

  NY: {

    stateCode: 'NY',

    stateName: 'New York',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 5000,

    deductionLimitJoint: 10000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.06,

    taxNotes: 'One of the lowest fees in the nation (0.11%). Deduction up to $5,000 ($10,000 for joint filers) per year.',

    plans: [

      {

        planName: "New York's 529 Direct Plan",

        type: 'Direct-Sold Savings',

        manager: 'Vanguard',

        rating: 'Silver',

        avgFeePct: 0.11,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market', 'Total Stock Market'],

        notes: 'Top-tier low fee direct plan in the US, using Vanguard index funds.',

        websiteUrl: 'https://www.nysaves.org',

      },

      {

        planName: "New York's 529 Advisor-Guided Plan",

        type: 'Advisor-Sold Savings',

        manager: 'JP Morgan',

        rating: 'Neutral',

        avgFeePct: 0.55,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor plan carrying additional management fees and advisor commissions.',

        websiteUrl: 'https://www.ny529advisor.com',

      },

    ],

  },

  NC: {

    stateCode: 'NC',

    stateName: 'North Carolina',

    hasDeduction: false,

    hasCredit: false,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.045,

    taxNotes: 'North Carolina offers no state tax deduction or credit for 529 plan contributions.',

    plans: [

      {

        planName: 'NC 529 Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.16,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.cfnc.org/save-for-college',

      },

      {

        planName: 'NC 529 Plan (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.cfnc.org/save-for-college',

      },

    ],

  },

  ND: {

    stateCode: 'ND',

    stateName: 'North Dakota',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 5000,

    deductionLimitJoint: 10000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.025,

    taxNotes: 'Deduction up to $5,000 ($10,000 for married filing jointly) for contributions to North Dakota plan.',

    plans: [

      {

        planName: 'College SAVE (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.collegesave4u.com',

      },

      {

        planName: 'College SAVE (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.collegesave4u.com',

      },

    ],

  },

  OH: {

    stateCode: 'OH',

    stateName: 'Ohio',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 4000,

    deductionLimitJoint: 4000,

    hasTaxParity: true,

    noIncomeTax: false,

    stateTaxRate: 0.035,

    taxNotes: 'TAX PARITY STATE. Deduct up to $4,000 per beneficiary per year with unlimited carryforward. Covers out-of-state plans.',

    plans: [

      {

        planName: 'Ohio CollegeAdvantage Direct',

        type: 'Direct-Sold Savings',

        manager: 'OTTA',

        rating: 'Silver',

        avgFeePct: 0.13,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market', 'Total Stock Market'],

        notes: 'Low-fee direct savings plan with excellent index choices.',

        websiteUrl: 'https://www.collegeadvantage.com',

      },

      {

        planName: 'BlackRock CollegeAdvantage (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'BlackRock',

        rating: 'Neutral',

        avgFeePct: 0.55,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan managed by BlackRock.',

        websiteUrl: 'https://www.blackrock.com/us/individual/products/529-plans',

      },

    ],

  },

  OK: {

    stateCode: 'OK',

    stateName: 'Oklahoma',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 10000,

    deductionLimitJoint: 20000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.0475,

    taxNotes: 'Deduction up to $10,000 ($20,000 for joint filers) per tax return for OK residents.',

    plans: [

      {

        planName: 'Oklahoma College Savings Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.16,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.ok4saving.org',

      },

      {

        planName: 'Oklahoma College Savings Plan (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.ok4saving.org',

      },

    ],

  },

  OR: {

    stateCode: 'OR',

    stateName: 'Oregon',

    hasDeduction: false,

    hasCredit: true,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    creditRatePct: 5, // varies by income, using average representative credit,

    creditLimitSingle: 150,

    creditLimitJoint: 300,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.07,

    taxNotes: 'Oregon offers a state tax credit up to $150 ($300 for joint filers) based on adjusted gross income.',

    plans: [

      {

        planName: 'Oregon College Savings Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.oregoncollegesavings.com',

      },

      {

        planName: 'Oregon College Savings Plan (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.oregoncollegesavings.com',

      },

    ],

  },

  PA: {

    stateCode: 'PA',

    stateName: 'Pennsylvania',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 19000,

    deductionLimitJoint: 38000,

    hasTaxParity: true,

    noIncomeTax: false,

    stateTaxRate: 0.0307,

    taxNotes: 'TAX PARITY STATE. Deduct up to federal gift tax exclusion ($19,000 single / $38,000 joint) for ANY plan.',

    plans: [

      {

        planName: 'PA 529 Investment Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'Vanguard',

        rating: 'Silver',

        avgFeePct: 0.13,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market', 'Total Stock Market'],

        notes: 'Vanguard-managed direct plan with low fees.',

        websiteUrl: 'https://www.pa529.com/select-a-plan/investment-plan/',

      },

      {

        planName: 'PA 529 Guaranteed Savings Plan (Prepaid)',

        type: 'Prepaid Tuition',

        manager: 'State of Pennsylvania',

        rating: 'None',

        avgFeePct: 0.0,

        options: ['Prepaid Tuition Credit'],

        notes: 'PA’s prepaid tuition program where credits grow at the rate of college tuition inflation.',

        websiteUrl: 'https://www.pa529.com/select-a-plan/guaranteed-savings-plan/',

      },

    ],

  },

  RI: {

    stateCode: 'RI',

    stateName: 'Rhode Island',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 500,

    deductionLimitJoint: 1000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.0375,

    taxNotes: 'Low deduction limits: up to $500 ($1,000 for joint filers) for Rhode Island residents.',

    plans: [

      {

        planName: 'CollegeBound 529 (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.16,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.collegebound529.com',

      },

      {

        planName: 'CollegeBound 529 (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.collegebound529.com',

      },

    ],

  },

  SC: {

    stateCode: 'SC',

    stateName: 'South Carolina',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 999999, // unlimited,

    deductionLimitJoint: 999999,  // unlimited,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.07,

    taxNotes: 'UNLIMITED state tax deduction for South Carolina residents contributing to the Future Scholar plan.',

    plans: [

      {

        planName: 'Future Scholar 529 (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Silver',

        avgFeePct: 0.13,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.futurescholar.com',

      },

      {

        planName: 'Future Scholar 529 (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.futurescholar.com',

      },

    ],

  },

  SD: {

    stateCode: 'SD',

    stateName: 'South Dakota',

    hasDeduction: false,

    hasCredit: false,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    hasTaxParity: false,

    noIncomeTax: true,

    stateTaxRate: 0.0,

    taxNotes: 'No state income tax. South Dakota uses CollegeAccess 529 with relatively higher management fees.',

    plans: [

      {

        planName: 'CollegeAccess 529 (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.22,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.collegeaccess529.com',

      },

      {

        planName: 'CollegeAccess 529 (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.collegeaccess529.com',

      },

    ],

  },

  TN: {

    stateCode: 'TN',

    stateName: 'Tennessee',

    hasDeduction: false,

    hasCredit: false,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    hasTaxParity: false,

    noIncomeTax: true,

    stateTaxRate: 0.0,

    taxNotes: 'No state income tax. Tennessee residents can save in the TNStars plan which features low cost options.',

    plans: [

      {

        planName: 'TNStars College Savings (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.tnstars.com',

      },

      {

        planName: 'TNStars College Savings (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.tnstars.com',

      },

    ],

  },

  TX: {

    stateCode: 'TX',

    stateName: 'Texas',

    hasDeduction: false,

    hasCredit: false,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    hasTaxParity: false,

    noIncomeTax: true,

    stateTaxRate: 0.0,

    taxNotes: 'No state income tax. Texas offers prepaid and savings plans but does not offer state-level deductions.',

    plans: [

      {

        planName: 'Texas College Savings Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'Orion',

        rating: 'Neutral',

        avgFeePct: 0.18,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Direct savings plan managed by Orion.',

        websiteUrl: 'https://www.texas529.com',

      },

      {

        planName: 'LoneStar 529 (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Orion',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales loads and advisor fee drag.',

        websiteUrl: 'https://www.lonestar529.com',

      },

      {

        planName: 'Texas Tuition Promise Fund (Prepaid)',

        type: 'Prepaid Tuition',

        manager: 'State of Texas',

        rating: 'None',

        avgFeePct: 0.0,

        options: ['Prepaid Tuition Contract'],

        notes: 'Locks in undergraduate tuition at today’s rates for Texas public colleges.',

        websiteUrl: 'https://www.texastuitionpromisefund.com',

      },

    ],

  },

  UT: {

    stateCode: 'UT',

    stateName: 'Utah',

    hasDeduction: false,

    hasCredit: true,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    creditRatePct: 5,

    creditLimitSingle: 118, // 5% of max contribution limits,

    creditLimitJoint: 236,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.0465,

    taxNotes: 'Gold Rated Plan. 5% tax credit up to $118 single / $236 joint per beneficiary for Utah residents.',

    plans: [

      {

        planName: 'my529 (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Gold',

        avgFeePct: 0.12,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market', 'Nasdaq 100', 'Russell 2000', 'Total Stock Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://my529.org',

      },

      {

        planName: 'my529 (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://my529.org',

      },

    ],

  },

  VT: {

    stateCode: 'VT',

    stateName: 'Vermont',

    hasDeduction: false,

    hasCredit: true,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    creditRatePct: 10,

    creditLimitSingle: 250, // 10% of $2,500 contribution,

    creditLimitJoint: 500,  // 10% of $5,000 contribution,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.06,

    taxNotes: 'Vermont offers a 10% tax credit up to a maximum credit of $250 (single) or $500 (joint) per beneficiary.',

    plans: [

      {

        planName: 'VHEIP (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.17,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.vheip.org',

      },

      {

        planName: 'VHEIP (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.vheip.org',

      },

    ],

  },

  VA: {

    stateCode: 'VA',

    stateName: 'Virginia',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 4000,

    deductionLimitJoint: 4000,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.0575,

    taxNotes: 'Low fees (0.11%). Deduction up to $4,000 per account per year, with unlimited carryforward of excess contributions.',

    plans: [

      {

        planName: 'Invest529 (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'Virginia529',

        rating: 'Gold',

        avgFeePct: 0.11,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market', 'Total Stock Market'],

        notes: 'Gold-rated direct savings plan with some of the lowest fees in the US.',

        websiteUrl: 'https://www.virginia529.com/invest/',

      },

      {

        planName: 'CollegeAmerica (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'American Funds',

        rating: 'Bronze',

        avgFeePct: 0.5,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'The largest advisor-sold plan in the nation. Managed by Capital Group.',

        websiteUrl: 'https://www.capitalgroup.com/advisor/investments/529-collegeamerica.html',

      },

    ],

  },

  WA: {

    stateCode: 'WA',

    stateName: 'Washington',

    hasDeduction: false,

    hasCredit: false,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    hasTaxParity: false,

    noIncomeTax: true,

    stateTaxRate: 0.0,

    taxNotes: 'No state income tax. Offers the GET prepaid plan or the DreamAhead savings plan.',

    plans: [

      {

        planName: 'DreamAhead College Investment Plan (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'BNY Mellon',

        rating: 'Bronze',

        avgFeePct: 0.15,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Washington direct savings plan.',

        websiteUrl: 'https://dreamahead.wa.gov',

      },

      {

        planName: 'GET (Guaranteed Education Tuition) (Prepaid)',

        type: 'Prepaid Tuition',

        manager: 'State of Washington',

        rating: 'None',

        avgFeePct: 0.0,

        options: ['Prepaid Tuition Unit'],

        notes: 'Prepaid tuition plan where 100 units equals 1 year of state public university tuition.',

        websiteUrl: 'https://529.wa.gov',

      },

    ],

  },

  WV: {

    stateCode: 'WV',

    stateName: 'West Virginia',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 999999, // unlimited,

    deductionLimitJoint: 999999,  // unlimited,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.0512,

    taxNotes: 'West Virginia offers an UNLIMITED deduction on in-state contributions to the SMART529 plan.',

    plans: [

      {

        planName: 'SMART529 (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.16,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.smart529.com',

      },

      {

        planName: 'SMART529 (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.smart529.com',

      },

    ],

  },

  WI: {

    stateCode: 'WI',

    stateName: 'Wisconsin',

    hasDeduction: true,

    hasCredit: false,

    deductionLimitSingle: 4300,

    deductionLimitJoint: 4300,

    hasTaxParity: false,

    noIncomeTax: false,

    stateTaxRate: 0.053,

    taxNotes: 'Wisconsin taxpayers can deduct up to $4,300 per beneficiary for contributions to Edvest.',

    plans: [

      {

        planName: 'Edvest (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'Silver',

        avgFeePct: 0.12,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://www.edvest.com',

      },

      {

        planName: 'Edvest (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://www.edvest.com',

      },

    ],

  },

  WY: {

    stateCode: 'WY',

    stateName: 'Wyoming',

    hasDeduction: false,

    hasCredit: false,

    deductionLimitSingle: 0,

    deductionLimitJoint: 0,

    hasTaxParity: false,

    noIncomeTax: true,

    stateTaxRate: 0.0,

    taxNotes: 'Wyoming has no state 529 plan and no state income tax. Residents are encouraged to use top national plans.',

    plans: [

      {

        planName: 'None (Out-of-state Recommended) (Direct)',

        type: 'Direct-Sold Savings',

        manager: 'State Program Manager',

        rating: 'None',

        avgFeePct: 0.00,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Primary direct-sold savings plan for state residents.',

        websiteUrl: 'https://my529.org',

      },

      {

        planName: 'None (Out-of-state Recommended) (Advisor)',

        type: 'Advisor-Sold Savings',

        manager: 'Advisor Program Manager',

        rating: 'Neutral',

        avgFeePct: 0.65,

        options: ['S&P 500 Index', 'Growth', 'Aggressive Growth', 'Balanced', 'Bonds', 'Money Market'],

        notes: 'Advisor-sold plan with typical sales commissions and advisor fee drag.',

        websiteUrl: 'https://my529.org',

      },

    ],

  },
};

// ZIP code prefix to state mappings
export const ZIP_STATE_RANGES = [
  { state: 'AL', min: 350, max: 369 },
  { state: 'AK', min: 995, max: 999 },
  { state: 'AZ', min: 850, max: 865 },
  { state: 'AR', min: 716, max: 729 },
  { state: 'CA', min: 900, max: 961 },
  { state: 'CO', min: 800, max: 816 },
  { state: 'CT', min: 60, max: 69 },
  { state: 'DE', min: 197, max: 199 },
  { state: 'DC', min: 200, max: 205 },
  { state: 'FL', min: 320, max: 349 },
  { state: 'GA', min: 300, max: 319 },
  { state: 'HI', min: 967, max: 968 },
  { state: 'ID', min: 832, max: 838 },
  { state: 'IL', min: 600, max: 629 },
  { state: 'IN', min: 460, max: 479 },
  { state: 'IA', min: 500, max: 528 },
  { state: 'KS', min: 660, max: 679 },
  { state: 'KY', min: 400, max: 427 },
  { state: 'LA', min: 700, max: 714 },
  { state: 'ME', min: 39, max: 49 },
  { state: 'MD', min: 206, max: 219 },
  { state: 'MA', min: 10, max: 27 },
  { state: 'MI', min: 480, max: 499 },
  { state: 'MN', min: 550, max: 567 },
  { state: 'MS', min: 386, max: 397 },
  { state: 'MO', min: 630, max: 658 },
  { state: 'MT', min: 590, max: 599 },
  { state: 'NE', min: 680, max: 693 },
  { state: 'NV', min: 889, max: 898 },
  { state: 'NH', min: 30, max: 38 },
  { state: 'NJ', min: 70, max: 89 },
  { state: 'NM', min: 870, max: 884 },
  { state: 'NY', min: 100, max: 149 },
  { state: 'NC', min: 270, max: 289 },
  { state: 'ND', min: 580, max: 588 },
  { state: 'OH', min: 430, max: 459 },
  { state: 'OK', min: 730, max: 749 },
  { state: 'OR', min: 970, max: 979 },
  { state: 'PA', min: 150, max: 196 },
  { state: 'RI', min: 28, max: 29 },
  { state: 'SC', min: 290, max: 299 },
  { state: 'SD', min: 570, max: 577 },
  { state: 'TN', min: 370, max: 385 },
  { state: 'TX', min: 750, max: 799 },
  { state: 'UT', min: 840, max: 847 },
  { state: 'VT', min: 50, max: 59 },
  { state: 'VA', min: 220, max: 246 },
  { state: 'WA', min: 980, max: 994 },
  { state: 'WV', min: 247, max: 268 },
  { state: 'WI', min: 530, max: 549 },
  { state: 'WY', min: 820, max: 831 },
];

/**
 * Resolves a 5-digit ZIP code to a 2-letter state code.
 */
export function lookupStateFromZip(zip: string): string | null {
  const cleanZip = zip.trim().replace(/\D/g, '');
  if (cleanZip.length < 5) return null;
  const prefix = parseInt(cleanZip.substring(0, 3), 10);
  if (isNaN(prefix)) return null;

  // Exact single 3-digit edge cases
  if (prefix === 5) return 'NY';
  if (prefix === 55) return 'MA';
  if (prefix === 733) return 'TX';
  if (prefix === 201) return 'VA';
  if (prefix === 398 || prefix === 399) return 'GA';

  // Search range
  const matched = ZIP_STATE_RANGES.find(r => prefix >= r.min && prefix <= r.max);
  return matched ? matched.state : null;
}

export interface National529Plan {
  planName: string;
  stateName: string;
  stateCode: string;
  rating: 'Gold' | 'Silver' | 'Bronze';
  avgFeePct: number;
  bestFor: string;
  optionsDescription: string;
  feeNotes: string;
  websiteUrl: string; // Direct link
}

export const NATIONAL_529_PLANS: National529Plan[] = [
  {
    planName: 'my529 (Direct)',
    stateName: 'Utah',
    stateCode: 'UT',
    rating: 'Gold',
    avgFeePct: 0.12,
    bestFor: 'Custom Asset Allocation & Index Diversity',
    optionsDescription: 'Highly customizable. Choose exact percentages of Vanguard & Dimensional index funds. Offers S&P 500, Nasdaq 100, Russell 2000, Total Stock Market, and Money Market options.',
    feeNotes: '0.10% - 0.14% asset-based fee. Drop in admin fees effective July 2026.',
    websiteUrl: 'https://my529.org',
  },
  {
    planName: "New York's 529 Program (Direct)",
    stateName: 'New York',
    stateCode: 'NY',
    rating: 'Silver',
    avgFeePct: 0.11,
    bestFor: 'Ultra-low cost Vanguard index funds',
    optionsDescription: 'Simple Vanguard pre-mixed portfolios (S&P 500, Total Stock Market, Growth, Balanced, Bonds, Money Market). Does not support Nasdaq 100 directly.',
    feeNotes: 'Flat 0.11% fee for all portfolios (one of the absolute lowest in the US).',
    websiteUrl: 'https://www.nysaves.org',
  },
  {
    planName: 'Vanguard 529 College Savings Plan (Direct)',
    stateName: 'Nevada',
    stateCode: 'NV',
    rating: 'Silver',
    avgFeePct: 0.13,
    bestFor: 'Vanguard Brand Loyalists',
    optionsDescription: 'Direct access to Vanguard mutual funds across S&P 500, Total Stock Market, Growth, Balanced, Bonds, and Cash.',
    feeNotes: '0.12% - 0.15% average fees depending on selected portfolio.',
    websiteUrl: 'https://www.vanguard.com/us/whatweoffer/college/529vanguard',
  },
  {
    planName: 'Ohio CollegeAdvantage 529 (Direct)',
    stateName: 'Ohio',
    stateCode: 'OH',
    rating: 'Silver',
    avgFeePct: 0.13,
    bestFor: 'Active Management & Passive Indexing Hybrid',
    optionsDescription: 'Diversified portfolios utilizing Vanguard, Dimensional, and T. Rowe Price funds (S&P 500, Nasdaq 100, Growth, Balanced, Bonds, Money Market).',
    feeNotes: '0.11% - 0.18% direct-sold fee. Excellent track record.',
    websiteUrl: 'https://www.collegeadvantage.com',
  },
];
