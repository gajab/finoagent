// ===== User & Auth Types =====

export interface User {
  id: string;
  email: string;
  name: string;
  picture: string;
  whatsapp_number?: string | null;
  whatsapp_thread_id?: string | null;
  is_premium: boolean;
}

export interface ApiKeyInfo {
  key_name: string;
  masked_value: string;
  updated_at: string;
}

export interface AllowedUser {
  id: number;
  email: string;
  is_premium: boolean;
  created_at: string | null;
}

// ===== Stock Data Types =====

export interface NewsItem {
  title: string;
  publisher: string;
  link: string;
  published: string;
  summary: string;
}

export interface NewsSummary {
  totalArticles: number;
  themes: string[];
  sentiment: string;
  topHeadlines: string[];
  sourceBreakdown: Record<string, number>;
  digest: string; // LLM-generated narrative summary of the top news
}

export interface AnalystRating {
  period: string;
  strongBuy: number;
  buy: number;
  hold: number;
  sell: number;
  strongSell: number;
}

export interface FinancialData {
  years: string[];
  eps: (number | null)[];
  revenue: (number | null)[];
  freeCashFlow: (number | null)[];
}

export interface VolumeAnalysis {
  phase: string;
  priceTrend: string;
  priceChangePct: number;
  volumeTrend: string;
  volumeChangePct: number;
  avgRecentVolume: number;
  avgOlderVolume: number;
  bigMoneyAnalysis: string;
}

export interface MACDData {
  macdLine: number;
  signalLine: number;
  histogram: number;
  signal: string;
  crossover: string;
  macdValues: number[];
  signalValues: number[];
  histogramValues: number[];
  timestamps: string[];
}

export interface BollingerBandsData {
  upper: number;
  middle: number;
  lower: number;
  bandwidthPct: number;
  percentB: number;
  position: string;
}

export interface MovingAveragesData {
  sma50?: number;
  priceVsSma50?: string;
  sma200?: number;
  priceVsSma200?: string;
  goldenDeathCross?: string;
}

export interface EMACrossoverData {
  ema12: number;
  ema26: number;
  signal: string;
}

export interface TechnicalData {
  timestamps: string[];
  prices: number[];
  volumes: number[];
  highs: number[];
  lows: number[];
  rsiValues: number[];
  rsiTimestamps: string[];
  currentRSI: number | null;
  rsiSignal: string;
  supportLevel: number;
  resistanceLevel: number;
  volumeAnalysis: VolumeAnalysis;
  analysisSummary: string;
  error?: string;
  // Momentum indicators (daily data)
  macd?: MACDData;
  bollingerBands?: BollingerBandsData;
  movingAverages?: MovingAveragesData;
  emaCrossover?: EMACrossoverData;
  // Institutional market-structure / order-flow read
  institutional?: InstitutionalTA | null;
}

export interface VolumeProfileBin { price: number; volume: number; pct: number }
export interface VolumeProfile { poc: number; vah: number; val: number; value_area_pct: number; bins: VolumeProfileBin[] }
export interface OrderBlock { type: 'bullish' | 'bearish'; top: number; bottom: number; price: number; strength: number; mitigated: boolean; index: number }
export interface FairValueGap { type: 'bullish' | 'bearish'; bottom: number; top: number; mid: number; filled: boolean; index: number }
export interface LiquiditySweep { type: 'buyside' | 'sellside'; level: number; swept_to: number; reversed: boolean; index: number }
export interface Displacement { index: number; direction: 'up' | 'down'; magnitude: number }
export interface MarketStructure {
  trend: 'up' | 'down' | 'range' | string;
  bos: { type: 'bullish' | 'bearish'; level: number } | null;
  change_of_character: boolean;
  recent_swing_high: number | null;
  recent_swing_low: number | null;
}
export interface TARegime {
  state: string;
  mode: 'mean_reversion' | 'trend' | 'range' | string;
  bias: 'bullish' | 'bearish' | 'neutral' | string;
  rationale: string;
  favored_income: string[];
  favored_income_labels: string[];
  signals: {
    rsi: number | null;
    vs_value_area: 'above' | 'below' | 'inside' | null;
    at_demand: boolean;
    at_supply: boolean;
    trend: string | null;
    bos: string | null;
    recent_sweep: string | null;
  };
}
export interface InstitutionalTA {
  price: number;
  atr: number;
  volume_profile: VolumeProfile | null;
  order_blocks: OrderBlock[];
  fair_value_gaps: FairValueGap[];
  liquidity_sweeps: LiquiditySweep[];
  displacement: Displacement[];
  market_structure: MarketStructure;
  regime: TARegime;
}

// ── Microstructure & multi-timeframe volume profile ──
export interface LowVolumeNode { price: number | null; pct: number | null }
export interface VolumeProfileTF {
  label: string;
  period: string;
  interval: string;
  poc: number;
  vah: number;
  val: number;
  value_area_pct: number;
  lvns: LowVolumeNode[];
  bins: VolumeProfileBin[];
}
export interface NakedPOC {
  price: number;
  date: string;
  age_days: number | null;
  distance_pct: number | null;
  side: 'above' | 'below' | null;
}
export interface AnchoredVWAP {
  label: string;
  anchor_date: string | null;
  value: number;
  distance_pct: number | null;
  side: 'above' | 'below' | null;
}
export interface MicrostructureData {
  price: number | null;
  as_of: string;
  timeframe_profiles: {
    macro: VolumeProfileTF | null;
    swing: VolumeProfileTF | null;
    micro: VolumeProfileTF | null;
  };
  naked_pocs: NakedPOC[];
  avwap: {
    ytd: AnchoredVWAP | null;
    earnings: AnchoredVWAP | null;
    high_52w: AnchoredVWAP | null;
    low_52w: AnchoredVWAP | null;
  };
  price_series: { timestamps: string[]; closes: number[] } | null;
}
export interface MicrostructureResponse {
  ticker: string;
  microstructure: MicrostructureData;
  cached?: boolean;
}
export interface MicroChatMessage { role: 'user' | 'assistant'; content: string }

// ── Multi-timeframe market structure & liquidity ──
export interface StructureEvent { type: 'BOS' | 'CHOCH'; direction: 'bullish' | 'bearish'; level: number; index: number }
export interface TFStructure {
  trend: 'up' | 'down' | 'range' | string;
  last_event: StructureEvent | null;
  events: StructureEvent[];
  recent_swing_high: number | null;
  recent_swing_low: number | null;
}
export interface LiquidityPool {
  type: 'BSL' | 'SSL';
  price: number;
  swept: boolean;
  equal_count: number;
  side?: 'above' | 'below';
  distance_pct?: number;
  strength?: 'strong' | 'normal';
  index: number;
}
export interface TimeframeBlock {
  label: string;
  trend: string;
  atr: number | null;
  structure: TFStructure;
  order_blocks: OrderBlock[];
  fair_value_gaps: FairValueGap[];
  liquidity_pools: LiquidityPool[];
}
export interface Confluence {
  bias: 'bullish' | 'bearish';
  timeframes: string[];
  zone: [number, number];
  score: number;
  components: { tf: string; kind: string }[];
  summary: string;
}
export interface MarketStructureData {
  price: number | null;
  as_of: string;
  bias: { overall: 'bullish' | 'bearish' | 'mixed' | string; aligned: boolean; score: number; note: string };
  timeframes: { daily: TimeframeBlock | null; h4: TimeframeBlock | null; h1: TimeframeBlock | null };
  confluence: Confluence[];
  price_series: { timestamps: string[]; closes: number[] } | null;
}
export interface MarketStructureResponse { ticker: string; market_structure: MarketStructureData; cached?: boolean }

// ── Market regime & statistical extremes ──
export interface RegimeTF {
  label: string;
  hurst: number | null;
  efficiency_ratio: number | null;
  regime: string;
  confidence: string;
  playbook: string;
  favored: string[];
}
export interface RegimeZScore { window: number; vwap: number | null; z: number; state: string; distance_pct: number | null }
export interface RegimeData {
  price: number | null;
  as_of: string;
  regime: { overall: string; confidence: string; hurst_daily: number | null; er_daily: number | null; aligned: boolean; playbook: string; favored: string[] };
  timeframes: { daily: RegimeTF | null; h4: RegimeTF | null };
  zscore: RegimeZScore | null;
  price_series: { timestamps: string[]; closes: number[] } | null;
}
export interface RegimeResponse { ticker: string; regime: RegimeData; cached?: boolean }

// ── Dealer positioning (option mechanics overlay) ──
export interface GexWall { strike: number | null; gex_millions: number | null }
export interface ExpectedMove { dte: number; iv_atm_pct: number | null; move: number | null; move_pct: number | null; upper: number | null; lower: number | null }
export interface GammaLevel { strike: number | null; gex_millions?: number | null; distance_pct: number | null; kind: string }
export interface DealerGammaLevels {
  call_resistance: GammaLevel | null;
  put_support: GammaLevel | null;
  hvl: GammaLevel | null;
  gamma_flip: { level: number | null; distance_pct: number | null; side: string; note: string } | null;
  regime: string | null;
}
export interface GexProfilePoint { strike: number | null; gex_millions: number | null; cum_millions: number | null }
export interface DealerPositioningData {
  price: number | null;
  as_of: string;
  net_gex: { value: number | null; value_millions: number | null; sign: 'long' | 'short' | string; label: string };
  gamma_flip: { level: number | null; distance_pct: number | null; side: 'above' | 'below' | string; note: string } | null;
  gamma_levels?: DealerGammaLevels;
  gex_profile?: GexProfilePoint[];
  walls: { call_wall: GexWall | null; put_wall: GexWall | null; by_strike: { strike: number | null; gex_millions: number | null }[] };
  expected_move: { em_30d: ExpectedMove | null; em_45d: ExpectedMove | null };
  expirations_used: string[];
  price_series: { timestamps: string[]; closes: number[] } | null;
}
export interface DealerPositioningResponse { ticker: string; dealer_positioning: DealerPositioningData; cached?: boolean }

// ── Trade-setup engine (fusion of all four TA families) ──
export interface SetupBias { direction: string; strength: string; score: number; rationale: string; regime: string; confirmations?: { signal: string; reads: string; detail: string }[] }
export interface SetupContext {
  bias: SetupBias;
  regime: { label: string | null; confidence: string | null; hurst: number | null; note: string | null; favored: string[] | null };
  expected_move: { pct_30d: number | null; upper: number | null; lower: number | null; iv: number | null } | null;
  dealer: { gamma: string | null; flip: number | null; note: string | null } | null;
  trend_alignment: { daily: string | null; h4: string | null; h1: string | null } | null;
}
export interface ConfluenceZone {
  center: number; low: number; high: number; kind: string; score: number;
  n_sources: number; has_magnet?: boolean;
  sources: { label: string; price: number; weight: number }[];
  distance_pct: number | null;
}
export interface SetupTarget { level: number | null; label: string; rr: number | null }
export interface EquityPlan {
  side: string; entry: number | null; stop: number | null;
  targets: { level: number | null; gain_per_share: number | null }[];
  risk_per_share: number | null; reward_per_share_t1: number | null; risk_reward: number | null;
  suggested_shares: number | null; risk_budget: number | null; sizing_basis?: string;
  dollar_risk: number | null; dollar_reward_t1: number | null; note: string;
}
export interface OptionLeg { action: string; right: string; strike: number; price: number | null }
export interface OptionsPlan {
  available: boolean; structure: string; kind?: string; note?: string;
  expiry?: { date: string; dte: number | null } | null;
  priced_from?: string;
  legs?: OptionLeg[];
  net_cost?: number | null; net_cost_label?: string;
  max_profit?: number | null; max_loss?: number | null;
  breakevens?: number[];
  payoff?: { price: number; pnl: number }[];
  pop_pct?: number | null; ev?: number | null; why?: string; preferred?: boolean;
}
export interface OptionsAlt {
  structure: string; kind?: string; net_cost?: number | null; net_cost_label?: string;
  max_profit?: number | null; max_loss?: number | null; breakevens?: number[];
  pop_pct?: number | null; ev?: number | null; legs?: OptionLeg[];
}
export interface TradeSetup {
  rank?: number;
  type: string;
  direction: 'long' | 'short' | 'neutral' | string;
  regime_fit: string;
  confidence: 'high' | 'medium' | 'low' | string;
  score: number;
  entry: { low: number | null; high: number | null; level: number | null; label: string };
  stop: { level: number | null; label: string };
  targets: SetupTarget[];
  risk_reward: number | null;
  sizing: { risk_per_share: number | null; target_move_pct: number | null; within_expected_move: boolean; note: string | null };
  options: { structure: string; detail: string; bias: string; strikes?: Record<string, number | null>; expiry?: { date: string; dte: number | null } | null };
  equity_plan?: EquityPlan | null;
  options_plan?: OptionsPlan | null;
  options_alternatives?: OptionsAlt[];
  what_to_watch?: string[];
  edge?: {
    equity?: { pop_pct: number | null; ev_per_share: number | null; payoff_ratio: number | null; kelly_pct: number | null; half_kelly_risk_pct: number | null };
    options?: { pop_pct: number | null; ev: number | null; basis: string };
  };
  event_risk?: { type: string; date: string; in_days: number; warning: string } | null;
  entry_style?: { type: string; label: string; note: string; distance_pct: number };
  horizon?: { label: string; style?: string; est_days?: number; atrs_to_t1?: number; note: string };
  from_current?: { to_entry_pct: number | null; to_t1_pct: number | null };
  thesis: string;
  evidence: string[];
}
export interface SetupVerification {
  verdict?: string | null; confidence?: string; summary?: string;
  verification?: string[]; pnl_check?: string; adjustments?: string[];
  alternate?: { name?: string; kind?: string; direction?: string; entry?: string; stop?: string; target?: string; structure?: string; why?: string };
  risks?: string[]; enrichment_read?: { sentiment?: string; fundamental?: string; analyst?: string };
  raw?: string;
}
export interface VerifyResponse { verification: SetupVerification; enrichments_used: string[] }
export interface TradeSetupsData {
  price: number | null;
  as_of: string;
  context: SetupContext;
  confluence_zones: ConfluenceZone[];
  setups: TradeSetup[];
  dossier?: Record<string, unknown>;
  price_series: { timestamps: string[]; closes: number[] } | null;
  meta: { sources_ok: Record<string, boolean> };
}
export interface TradeSetupsResponse { ticker: string; trade_setups: TradeSetupsData; cached?: boolean }

// ===== Chart patterns =====
export interface PatternPoint { idx: number; date: string; price: number; label: string }
export interface PatternLineEnd { idx: number; date: string; price: number }
export interface PatternLine { from: PatternLineEnd; to: PatternLineEnd; label: string; kind: string }
export interface PatternEducation { what: string; where: string; how_to_spot: string; confirms: string; invalidates: string }
export interface ChartPattern {
  type: string; name: string; category: 'reversal' | 'continuation' | string;
  direction: 'bullish' | 'bearish' | 'neutral' | string;
  status: 'forming' | 'broken_out' | string;
  confidence: number;
  points: PatternPoint[]; lines: PatternLine[];
  breakout: { level: number; side: 'up' | 'down' | string } | null;
  target: { price: number; method: string; pct: number | null } | null;
  stop: number | null; as_of_idx: number; education: PatternEducation;
}
export interface FibLevel { ratio: number; price: number }
export interface FibonacciData {
  direction: 'up' | 'down' | string;
  swing: { from: PatternPoint; to: PatternPoint };
  levels: FibLevel[]; in_zone: { low: FibLevel; high: FibLevel } | null; education: PatternEducation;
}
export interface PatternSeries {
  timestamps: string[]; open: (number | null)[]; high: (number | null)[];
  low: (number | null)[]; close: (number | null)[]; volume: (number | null)[]; offset: number;
}
export interface ChartPatternsData {
  price: number; as_of: string; atr: number; series: PatternSeries;
  patterns: ChartPattern[]; fibonacci: FibonacciData | null; meta: { n_pivots: number; n_bars: number };
}
export interface ChartPatternsResponse { ticker: string; chart_patterns: ChartPatternsData; cached?: boolean }

// ===== Trade Tracking & Management =====
export type TrackVerdict = 'execute' | 'wait' | 'invalid' | 'hold' | 'scale_out' | 'tighten' | 'exit' | string;
export type TrackStatus = 'watching' | 'in_progress' | 'closed' | 'invalidated' | string;
export interface TrackCheck {
  key: string; label: string;
  status: 'pass' | 'fail' | 'warn' | 'na' | string;
  detail: string; value?: unknown; weight?: number;
  role?: 'gate' | 'critical' | 'confirm' | 'trigger' | 'caution' | 'favorable' | string;
}
export interface TrackChochEvent { type?: string; direction?: string; level?: number | null; index?: number }
export interface TrackChoch { trend?: string; last_event?: TrackChochEvent | null; recent_swing_high?: number | null; recent_swing_low?: number | null }
export interface TrackVwap {
  vwap: number | null; sigma: number | null; z: number | null; session_bars?: number;
  upper_1: number | null; upper_2: number | null; upper_3: number | null;
  lower_1: number | null; lower_2: number | null; lower_3: number | null;
}
export interface TrackMarket {
  as_of: string; spot: number | null;
  vwap: TrackVwap | null; rsi_1h: number | null;
  choch_5m?: TrackChoch | null; choch_15m?: TrackChoch | null; choch_1h?: TrackChoch | null; trend_daily?: TrackChoch | null;
  cvd?: { last: number | null; slope_10: number | null; divergence: string | null } | null;
  volume?: { ratio: number | null; last: number | null; avg: number | null } | null;
  atr_15m?: number | null; atr_daily?: number | null;
  gamma?: Record<string, unknown> | null;
}
export interface TrackPosition {
  spot: number | null; entry: number | null; stop: number | null; targets: (number | null)[]; ref?: number | null;
  dist_to_entry_pct?: number | null; open_pnl_pct?: number | null; open_pnl?: number | null;
  r_multiple?: number | null; dist_to_t1_pct?: number | null;
}
export interface TrackEval {
  ok: boolean; error?: string; detail?: string;
  as_of: string; mode?: 'entry' | 'exit' | string; spot?: number | null;
  verdict?: TrackVerdict; verdict_label?: string; headline?: string;
  confidence_pct?: number | null; reasons?: string[]; need?: string[];
  checks?: TrackCheck[]; position?: TrackPosition; market?: TrackMarket;
  payload?: Record<string, unknown>;
}
export interface TrackedTrade {
  id: number; ticker: string; direction: string; instrument: string;
  setup_type?: string | null; status: TrackStatus; title?: string | null;
  entry_low: number | null; entry_high: number | null; entry_level: number | null; stop_level: number | null;
  target_levels: number[];
  setup_snapshot?: Record<string, unknown>; context_snapshot?: Record<string, unknown> | null;
  executed_at?: string | null; executed_price?: number | null; executed_qty?: number | null; execution_note?: string | null;
  closed_at?: string | null; exit_price?: number | null; exit_note?: string | null; realized_pnl?: number | null;
  last_eval?: TrackEval | null; last_verdict?: string | null; last_eval_at?: string | null;
  user_notes?: string | null; created_at: string; updated_at: string;
}
export interface TrackedTradesResponse {
  groups: { watching: TrackedTrade[]; in_progress: TrackedTrade[]; closed: TrackedTrade[]; invalidated: TrackedTrade[] };
  counts: Record<string, number>; total: number;
}
export interface TrackRefreshResponse { trade: TrackedTrade; evaluation: TrackEval }
export interface TrackAdvice {
  advice: {
    agree_with_verdict?: boolean; assessment?: string; recommended_action?: string;
    key_risks?: string[]; what_to_watch?: string[]; confidence?: string; raw?: string;
  };
  verdict_reviewed?: string;
}
export interface TrackInput {
  ticker: string; direction: string; instrument?: string; setup_type?: string | null; title?: string | null;
  entry_low?: number | null; entry_high?: number | null; entry_level?: number | null; stop_level?: number | null;
  target_levels?: number[]; setup_snapshot?: Record<string, unknown>; context_snapshot?: Record<string, unknown> | null;
  evaluate_now?: boolean;
}

export interface QuarterlyEarningsHistory {
  quarter: string;
  epsActual: number | null;
  epsEstimate: number | null;
  surprisePct: number | null;
}

export interface ForwardGuidance {
  forwardEps: number | null;
  trailingEps: number | null;
  epsGrowthPct: number | null;
  epsCurrentYear: number | null;
  epsCurrentYearGrowthPct: number | null;
  epsForwardGrowthPct: number | null;
  revenueGrowthPct: number | null;
  earningsGrowthPct: number | null;
  targetMeanPrice: number | null;
  targetHighPrice: number | null;
  targetLowPrice: number | null;
  targetMedianPrice: number | null;
  numberOfAnalysts: number | null;
  recommendation: string | null;
  recommendationScore: number | null;
  nextEarningsDate: string | null;
  nextEarningsDateIsEstimated?: boolean;
  lastEarningsDate?: string | null;
  revenuePerShare: number | null;
  profitMargin: number | null;
  operatingMargin: number | null;
  currentPrice: number | null;
  targetUpsidePct: number | null;
}

export interface EarningsData {
  available: boolean;
  lastQuarter: string;
  reportedEPS: number | null;
  estimatedEPS: number | null;
  epsSurprise: number | null;
  epsSurprisePct: number | null;
  revenue: number | null;
  revenueFormatted: string;
  netIncome: number | null;
  netIncomeFormatted: string;
  quarterlyHistory: QuarterlyEarningsHistory[];
  trailingPE: number | null;
  forwardPE: number | null;
  pegRatio: number | null;
  sector: string;
  industry: string;
  nextEarningsDate?: string;
  guidance?: ForwardGuidance;
}

export interface EarningsInsightSource {
  form: string | null;
  date: string | null;
  url: string | null;
}

export interface EarningsInsight {
  ticker: string;
  quarter: string | null;
  summary: string | null;
  sources: EarningsInsightSource[];
  generated_at: string | null;
}

export interface OptionTrade {
  expiration: string;
  dte: number;
  strike: number;
  bid: number;
  ask: number;
  mid: number;
  iv: number;
  delta: number;
  probOTM: number;
  annualizedReturn: number;
  totalReturnIfCalled?: number;
  otmPct: number;
  openInterest: number;
  volume: number;
  capitalRequired?: number;
  premiumPer100: number;
}

export interface OptionsData {
  available: boolean;
  error?: string;
  currentPrice: number;
  expirationCount: number;
  nearestExpiration: string;
  farthestExpiration: string;
  iv: {
    current: number | null;
    high: number | null;
    low: number | null;
  };
  hv30: number | null;
  hv60: number | null;
  putCallRatio: {
    openInterest: number;
    volume: number;
    totalPutOI: number;
    totalCallOI: number;
    totalPutVol: number;
    totalCallVol: number;
  };
  cashSecuredPuts: OptionTrade[];
  coveredCalls: OptionTrade[];
  criteria: {
    minAnnualizedReturn: number;
    minProbOTM: number;
    minDTE: number;
    maxDTE: number;
  };
}

export interface StockData {
  ticker: string;
  companyName: string;
  price: number;
  change: number;
  changePercent: number;
  marketCap: string;
  fiftyTwoWeekHigh: number | null;
  fiftyTwoWeekLow: number | null;
  dividendYield: number;
  sector: string;
  industry: string;
  description: string;
  news: NewsItem[];
  newsSummary: string;
  analystRatings: AnalystRating[];
  financials: FinancialData;
  technical: TechnicalData;
  earnings: EarningsData;
  options: OptionsData;
  // Asset type & fund/ETF-specific fields (present for all; non-null for ETF/MF)
  quoteType: string;
  aumB: number | null;
  fundYieldPct: number | null;
  ytdReturnPct: number | null;
  threeYrReturnPct: number | null;
  fiveYrReturnPct: number | null;
  fundTurnoverPct: number | null;
  fundCategory: string;
  fundFamily: string;
  netExpenseRatioPct: number | null;
  morningstarRiskRating: number | null;
  morningstarOverallRating: number | null;
}

// ===== Fund / ETF detail types =====

export interface FundHolding {
  ticker: string;
  name: string;
  weight_pct: number;
}

export interface FundReturns {
  '7d_pct': number | null;
  '1m_pct': number | null;
  '3m_pct': number | null;
  '1yr_pct': number | null;
  '3yr_ann': number | null;
  '5yr_ann': number | null;
  '10yr_ann': number | null;
  ytd_pct: number | null;
}

export interface FundBenchmark {
  ticker: string;
  name: string;
  '7d_pct': number | null;
  '1m_pct': number | null;
  '3m_pct': number | null;
  '1yr_pct': number | null;
  '3yr_ann': number | null;
  '5yr_ann': number | null;
  '10yr_ann': number | null;
  ytd_pct: number | null;
  expense_ratio_pct: number | null;
  aum_b: number | null;
}

export interface FundOps {
  expense_ratio_pct: number | null;
  category_avg_expense_ratio_pct: number | null;
  turnover_pct: number | null;
  category_avg_turnover_pct: number | null;
  aum_m: number | null;
}

export interface FundDetails {
  ticker: string;
  company_name: string;
  fund_family: string;
  legal_type: string;
  category: string;
  top_holdings: FundHolding[];
  sector_weightings: Record<string, number>;
  asset_classes: Record<string, number>;
  fund_ops: FundOps;
  returns: FundReturns;
  benchmark_comparison: FundBenchmark[];
}

export interface FundManagerBrief {
  ticker: string;
  analysis: string | null;
  created_at: string | null;
}

// ===== Portfolio Types =====

export type AssetType = 'STOCK' | 'ETF' | 'BOND' | 'MUTUAL_FUND' | 'CASH' | 'OPTION' | 'CRYPTO' | 'OTHER';
export type TransactionType = 'BUY' | 'SELL' | 'OPTION_BUY' | 'OPTION_SELL' | 'TRANSFER_IN' | 'TRANSFER_OUT';

export interface PortfolioHolding {
  id: number;
  ticker: string;
  asset_type: string;
  shares: number;
  cost_basis: number;
  purchase_date: string;
  created_at: string;
}

export interface Portfolio {
  id: number;
  name: string;
  holdings: PortfolioHolding[];
  created_at: string;
}

export interface PortfolioHoldingWithPrice {
  id: number;
  ticker: string;
  shares: number;
  cost_basis: number;
  purchase_date: string;
  current_price: number | null;
  market_value: number | null;
  total_cost: number;
  unrealized_gain_loss: number | null;
  unrealized_gain_loss_pct: number | null;
}

export interface PortfolioSummary {
  id: number;
  name: string;
  holdings: PortfolioHoldingWithPrice[];
  total_market_value: number | null;
  total_cost: number;
  total_unrealized_gain_loss: number | null;
  total_unrealized_gain_loss_pct: number | null;
}

export interface PortfolioTransaction {
  id: number;
  holding_id: number;
  ticker: string;
  transaction_type: TransactionType;
  shares: number;
  price_per_share: number;
  fees: number;
  date: string;
  notes: string | null;
  created_at: string;
}

export interface TransactionInput {
  ticker: string;
  asset_type?: AssetType;
  transaction_type: TransactionType;
  shares: number;
  price_per_share: number;
  fees?: number;
  date: string;
  notes?: string;
}

export interface EnhancedHolding {
  id: number;
  ticker: string;
  company_name: string | null;
  sector: string | null;
  industry: string | null;
  asset_type: AssetType;
  shares: number;
  cost_basis: number;
  total_cost: number;
  purchase_date: string;
  first_buy_date: string | null;
  current_price: number | null;
  market_value: number | null;
  day_change: number | null;
  day_change_pct: number | null;
  day_pnl: number | null;
  unrealized_gain_loss: number | null;
  unrealized_gain_loss_pct: number | null;
  realized_gain_loss: number;
  total_gain_loss: number | null;
  total_return_pct: number | null;
  annualized_return_pct: number | null;
  weight_pct: number | null;
  transaction_count: number;
}

export interface EnhancedPortfolioSummary {
  id: number;
  name: string;
  holdings: EnhancedHolding[];
  total_market_value: number | null;
  total_cost: number;
  total_unrealized_gain_loss: number | null;
  total_unrealized_gain_loss_pct: number | null;
  total_realized_gain_loss: number;
  total_gain_loss: number | null;
  total_day_pnl: number | null;
  total_day_pnl_pct: number | null;
  portfolio_annualized_return: number | null;
}

export interface HoldingInput {
  ticker: string;
  asset_type?: AssetType;
  shares: number;
  cost_basis: number;
  purchase_date: string;
}

// ===== Portfolio Enrichment Types =====

export interface DividendData {
  ticker: string;
  company_name?: string;
  sector?: string;
  shares: number;
  market_value: number | null;
  dividend_yield_pct: number | null;
  annual_dividend_rate: number | null;
  last_dividend_per_share: number | null;
  ex_dividend_date: string | null;
  payout_ratio_pct: number | null;
  five_yr_avg_yield_pct: number | null;
  is_dividend_payer: boolean;
  annual_income: number;
  error?: string;
}

export interface FundamentalData {
  ticker: string;
  company_name?: string;
  sector?: string;
  industry?: string;
  quote_type: string;
  // ── Equity metrics ───────────────────────────────────────────────────────
  trailing_pe: number | null;
  forward_pe: number | null;
  peg_ratio: number | null;
  eps_ttm: number | null;
  eps_forward: number | null;
  earnings_growth_yoy_pct: number | null;
  revenue_growth_yoy_pct: number | null;
  revenue_b: number | null;
  free_cashflow_b: number | null;
  operating_margin_pct: number | null;
  net_margin_pct: number | null;
  ebitda_margin_pct: number | null;
  roe_pct: number | null;
  debt_to_equity: number | null;
  price_to_book: number | null;
  market_cap_b: number | null;
  // ── Fund / ETF metrics ────────────────────────────────────────────────────
  aum_b?: number | null;
  fund_yield_pct?: number | null;
  ytd_return_pct?: number | null;
  three_yr_return_pct?: number | null;
  five_yr_return_pct?: number | null;
  fund_turnover_pct?: number | null;
  fund_category?: string;
  fund_family?: string;
  net_expense_ratio_pct?: number | null;
  morningstar_risk_rating?: number | null;
  morningstar_overall_rating?: number | null;
  fund_inception_date?: string | null;
  error?: string;
}

export interface PortfolioTechnicalData {
  ticker: string;
  company_name?: string;
  sector?: string;
  quote_type?: string;
  beta: number | null;
  ma_50d: number | null;
  ma_200d: number | null;
  vs_50d_pct: number | null;
  vs_200d_pct: number | null;
  week52_high: number | null;
  week52_low: number | null;
  week52_change_pct: number | null;
  range_position_pct: number | null;
  analyst_target_mean: number | null;
  analyst_target_high: number | null;
  analyst_target_low: number | null;
  analyst_upside_pct: number | null;
  analyst_count: number | null;
  recommendation: string;
  recommendation_mean: number | null;
  avg_volume_3m: number | null;
  avg_volume_10d: number | null;
  // Fund performance (populated for ETF/MF; often null for equities)
  ytd_return_pct?: number | null;
  three_yr_return_pct?: number | null;
  five_yr_return_pct?: number | null;
  // Upcoming earnings date
  earnings_date?: string | null;
  error?: string;
}

// ===== Portfolio Events =====

export interface PortfolioEvent {
  ticker: string;
  company_name: string;
  type: 'earnings' | 'ex_div';
  date: string;
  days_until: number;
  label: string;
  estimated_income?: number | null;
}

// ===== Daily Briefing (the "Today" hero) =====

export interface BriefHeadline {
  total_market_value: number;
  day_change: number;
  day_change_pct: number | null;
  total_unrealized: number;
  total_unrealized_pct: number | null;
  annual_income: number;
  portfolio_yield_pct: number | null;
  positions: number;
}

export interface BriefMoverNews {
  title: string;
  publisher: string;
  url: string;
  published: string;
}

export interface BriefMover {
  ticker: string;
  company: string;
  day_change_pct: number | null;
  day_pnl: number | null;
  news?: BriefMoverNews | null;
}

export interface BriefUpcoming {
  ticker: string;
  type: 'earnings' | 'ex_div';
  date: string;
  days_until: number;
  estimated_income?: number | null;
}

export interface BriefContributor {
  ticker: string;
  company: string;
  amount: number;
  pct_move: number | null;
}

export interface BriefCrossing {
  ticker: string;
  company: string;
  direction: 'into_gain' | 'into_loss';
  price: number;
  cost_basis: number;
}

export interface BriefSectorDrift {
  sector: string;
  drift_pp: number;
  now_weight: number;
}

export interface BriefDelta {
  baseline_date: string;
  since_label: string;
  market_change: number;
  market_change_pct: number | null;
  top_contributors: BriefContributor[];
  crossings: BriefCrossing[];
  sector_drift: BriefSectorDrift | null;
  positions_added: string[];
  positions_removed: string[];
}

export interface BriefBenchmark {
  symbol: string;
  day_change_pct: number;
  vs_portfolio_pp: number;
}

export interface PortfolioBrief {
  empty: boolean;
  generated_at?: string;
  narrative?: string | null;
  headline?: BriefHeadline;
  delta?: BriefDelta | null;
  benchmark?: BriefBenchmark | null;
  movers?: { gainers: BriefMover[]; losers: BriefMover[] };
  upcoming?: BriefUpcoming[];
  income_next_30d?: number;
}

// ===== Agent Types =====

export type AgentStatus = 'active' | 'paused' | 'stopped';
export type AgentRunStatus = 'running' | 'completed' | 'failed';
export type ScheduleType = 'manual' | 'one_time' | 'recurring';

export interface AgentRun {
  id: number;
  agent_id: number;
  status: AgentRunStatus;
  started_at: string;
  completed_at: string | null;
  output: string | null;
  error: string | null;
  created_at: string;
}

export interface Agent {
  id: number;
  user_id: number;
  name: string;
  description: string | null;
  instruction: string;
  is_shared: boolean;
  cloned_from_id: number | null;
  status: AgentStatus;
  schedule_type: ScheduleType;
  schedule_cron: string | null;
  scheduled_at: string | null;
  send_email_on_run: boolean;
  email_report_to: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  created_at: string;
  updated_at: string;
  latest_run: AgentRun | null;
  creator_name: string | null;
  creator_picture: string | null;
  tracked_company_id: number | null;
}

export interface AgentCreateInput {
  name: string;
  description?: string;
  instruction: string;
  is_shared?: boolean;
  schedule_type?: ScheduleType;
  schedule_cron?: string;
  scheduled_at?: string;
  send_email_on_run?: boolean;
  email_report_to?: string;
  tracked_company_id?: number | null;
}

export interface AgentUpdateInput {
  name?: string;
  description?: string;
  instruction?: string;
  is_shared?: boolean;
  schedule_type?: ScheduleType;
  schedule_cron?: string;
  scheduled_at?: string;
  send_email_on_run?: boolean;
  email_report_to?: string;
}

// ===== Price Prediction Types =====

export interface PricePredictionData {
  ticker: string;
  current_price: number;
  horizon_days: number;
  data_points: number;
  linear_trend: {
    slope_per_day: number;
    daily_change_pct: number;
    r_squared: number;
    projected_price: number;
    projected_change_pct: number;
    trend_direction: string;
    trend_strength: string;
  };
  mean_reversion: {
    sma50: number;
    z_score: number;
    deviation_pct: number;
    signal: string;
  };
  momentum: {
    roc_14d_pct: number;
    roc_30d_pct: number | null;
    signal: string;
  };
  volatility: {
    daily_volatility_pct: number;
    annualized_volatility_pct: number;
    projected_95_range: {
      low: number;
      high: number;
    };
  };
  overall_signal: {
    signal: string;
    confidence_pct: number;
    score: number;
  };
  disclaimer: string;
  error?: string;
}

// ===== Stock Notes / Chat Types =====

export interface StockNote {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
}

export interface AskResponse {
  answer: string;
  notes: StockNote[];
}

// ===== Bulk Import Types =====

export interface ParsedHolding {
  ticker: string;
  shares: number;
  cost_basis: number;
  purchase_date: string;
}

export interface BulkParseResponse {
  holdings: ParsedHolding[];
}

export interface ParsedTransaction {
  ticker: string;
  asset_type: string;
  transaction_type: TransactionType;
  shares: number;
  price_per_share: number;
  fees: number;
  date: string;
  notes: string;
}

export interface BulkTransactionParseResponse {
  transactions: ParsedTransaction[];
}

// ===== API Types =====

export interface LLMMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface LLMResponse {
  content: string;
  model: string;
}

export interface SearchResult {
  title: string;
  link: string;
  snippet: string;
  position: number;
}

export interface SearchResponse {
  results: SearchResult[];
  query: string;
}

// ===== DCF Analysis Types =====

export interface DCFHistoryItem {
  year: string;
  value: number | null;
}

export interface DCFValuation {
  projected_fcfs: number[];
  present_values: number[];
  total_pv_fcf: number;
  terminal_value: number;
  pv_terminal_value: number;
  enterprise_value: number;
  net_debt: number;
  equity_value: number;
  fair_value_per_share: number;
  // Two-stage / CAP engine additions
  fair_value_low?: number;
  fair_value_high?: number;
  market_implied_growth?: number | null;   // reverse-DCF: implied FCF growth % over 10y
  stage1_growth?: number;                   // percent
  cap_years?: number;
  terminal_growth?: number;                 // percent
  wacc?: number;                            // percent
  exit_multiple?: number;
  terminal_pct?: number | null;             // terminal value as % of EV
  gordon_terminal?: number;
  exit_terminal?: number;
  base_fcf?: number;
  upside_pct?: number | null;
  flags?: string[];
  error?: string;
}

export interface DCFDefaults {
  fcf_growth_rate: number;
  discount_rate: number;
  terminal_growth_rate: number;
  projection_years: number;
  starting_fcf: number;
  net_debt: number;
}

export interface DCFSuggestions {
  fcf_growth_rate: string;
  discount_rate: string;
  terminal_growth_rate: string;
}

export interface PEGVariant {
  method: string;
  pe_used: number;
  growth_used: number;
  growth_source: string;
  peg: number;
  interpretation: string;
}

export interface PEGYDetails {
  pe_used: number;
  growth_used: number;
  dividend_yield_pct: number;
  denominator: number;
  pegy: number;
  interpretation: string;
}

export interface PEGAnalysis {
  trailing_pe: number | null;
  forward_pe: number | null;
  trailing_eps: number | null;
  forward_eps: number | null;
  eps_growth_rate: number | null;
  dividend_yield_pct: number;
  yfinance_peg: number | null;
  primary_peg: number | null;
  primary_peg_source: string | null;
  eps_growth_caveat?: boolean;
  peg_variants: PEGVariant[];
  pegy: number | null;
  pegy_details: PEGYDetails | null;
}

export interface DCFAnalysisData {
  ticker: string;
  company_name: string;
  sector: string;
  industry: string;
  current_price: number;
  shares_outstanding: number;
  market_cap: number;
  beta: number;
  trailing_pe: number | null;
  forward_pe: number | null;
  fcf_history: DCFHistoryItem[];
  revenue_history: DCFHistoryItem[];
  net_income_history: DCFHistoryItem[];
  total_debt: number;
  cash_and_equivalents: number;
  net_debt: number;
  defaults: DCFDefaults;
  suggestions: DCFSuggestions;
  reasoning: string[];
  valuation: DCFValuation;
  peg_analysis: PEGAnalysis;
}

// ===== Guru Analysis Types =====

export interface GuruInfo {
  id: string;
  name: string;
  title: string;
  philosophy: string;
  avatar_url: string;
}

export interface GuruAnalysisEntry {
  id: number;
  guru_id: string;
  analysis: string;
  created_at: string;
  guru_name?: string;
}

export interface GuruAnalysisResponse {
  ticker: string;
  gurus: GuruInfo[];
  analyses: Record<string, GuruAnalysisEntry>;
}

// ===== Financial Health Types =====

export interface RevenueTrendItem {
  year: string;
  revenue: number | null;
  gross_profit: number | null;
  operating_income: number | null;
  net_income: number | null;
  ebitda: number | null;
  interest_expense: number | null;
  gross_margin: number | null;
  operating_margin: number | null;
  net_margin: number | null;
  ebitda_margin: number | null;
}

export interface CashFlowTrendItem {
  year: string;
  operating_cf: number | null;
  capex: number | null;
  free_cash_flow: number | null;
}

export interface DebtDataItem {
  year: string;
  total_debt: number | null;
  long_term_debt: number | null;
  short_term_debt: number | null;
  cash: number | null;
  net_debt: number;
  total_assets: number | null;
  total_equity: number | null;
  debt_to_assets_pct: number | null;
  debt_to_equity: number | null;
  debt_to_ebitda: number | null;
  interest_coverage: number | null;
  interest_expense: number | null;
}

export interface LiquidityData {
  year: string;
  current_assets: number | null;
  current_liabilities: number | null;
  inventory: number | null;
  cash: number | null;
  receivables: number | null;
  current_ratio: number | null;
  quick_ratio: number | null;
  cash_ratio: number | null;
}

export interface QuickStats {
  return_on_equity: number | null;
  return_on_assets: number | null;
  profit_margin: number | null;
  operating_margin: number | null;
  gross_margin: number | null;
  revenue_per_share: number | null;
  book_value: number | null;
  price_to_book: number | null;
  dividend_yield: number;
  payout_ratio: number | null;
  trailing_pe: number | null;
  forward_pe: number | null;
  peg_ratio: number | null;
  ev_to_ebitda: number | null;
  ev_to_revenue: number | null;
}

export interface HealthScoreComponent {
  name: string;
  score: number;
  detail: string;
}

export interface HealthScore {
  overall: number | null;
  components: HealthScoreComponent[];
}

export interface TrailingPayout {
  year: number;
  total_payout: number;
}

export interface UniversalDividendMetrics {
  dividend_yield: number;
  payout_ratio: number | null;
  trailing_payouts: TrailingPayout[];
}

export interface IndustryMetricHistory {
  year: string;
  value: string;
}

export interface IndustryMetric {
  name: string;
  value: string;
  desc: string;
  history?: IndustryMetricHistory[];
  target?: string;
}

export interface IndustryMetrics {
  type: string;
  metrics: IndustryMetric[];
}

export interface TTMSnapshot {
  period: string;              // "TTM"
  through: string;             // e.g. "Jun 2025" — the last quarter included
  revenue: number | null;
  gross_profit: number | null;
  operating_income: number | null;
  net_income: number | null;
  ebitda: number | null;
  eps: number | null;
  operating_cf: number | null;
  free_cash_flow: number | null;
  gross_margin: number | null;
  operating_margin: number | null;
  net_margin: number | null;
  ebitda_margin: number | null;
}

export interface ReturnsBucket {
  ttm: number | null;
  current: number | null;
  y1: number | null;
  y3: number | null;
  y5: number | null;
}

export interface ReturnsOnCapital {
  series: { year: string; roe: number | null; roa: number | null; roic: number | null }[];
  summary: {
    roe: ReturnsBucket;
    roa: ReturnsBucket;
    roic: ReturnsBucket;
  };
}

export interface FinancialHealthData {
  ticker: string;
  company_name: string;
  sector: string;
  industry: string;
  currency: string;
  ttm?: TTMSnapshot | null;
  returns_on_capital?: ReturnsOnCapital | null;
  revenue_trend: RevenueTrendItem[];
  revenue_growth_rates: (number | null)[];
  eps_history: { year: string; net_income: number | null; eps: number | null }[];
  eps_positive_years: number;
  eps_total_years: number;
  earnings_growth_rates: (number | null)[];
  cash_flow_trend: CashFlowTrendItem[];
  fcf_growth_rates: (number | null)[];
  fcf_stability: { mean: number; std: number; cv: number | null } | null;
  debt_data: DebtDataItem[];
  liquidity: LiquidityData | null;
  quick_stats: QuickStats;
  health_score: HealthScore;
  universal_dividend_metrics?: UniversalDividendMetrics | null;
  industry_metrics?: IndustryMetrics | null;
}

// ===== Macro Liquidity Dashboard =====

export interface MacroDataPoint {
  date: string;
  value: number;
}

export type MacroSignal = 'bullish' | 'bearish' | 'neutral';

export interface MacroIndicator {
  value: number | null;
  unit: string;
  change_5d: number | null;
  change_20d: number | null;
  change_mode: 'pct' | 'abs';
  signal: MacroSignal;
  series: MacroDataPoint[];
}

export interface MacroRegime {
  score: number;
  label: string;
  bullish: number;
  bearish: number;
  neutral: number;
}

export interface MacroRegimeComponent {
  id: string;
  label: string;
  detail: string | null;
  z: number | null;
  signal: 'accelerating' | 'decelerating' | 'stable' | 'n/a';
}

export interface MacroRegimeQuadrant {
  growth_score: number;      // -100 (decelerating) .. +100 (accelerating)
  inflation_score: number;   // -100 (disinflation) .. +100 (accelerating)
  quadrant: string;          // Reflation | Goldilocks | Stagflation | Slowdown | Transition / Mixed
  summary: string;
  tilt: string[];
  growth_components: MacroRegimeComponent[];
  inflation_components: MacroRegimeComponent[];
}

export interface MacroLiquidityResponse {
  indicators: Record<string, MacroIndicator>;
  spy: {
    price: number | null;
    change_5d: number | null;
    change_20d: number | null;
    series_price: MacroDataPoint[];
    series_obv: MacroDataPoint[];
  };
  components: {
    walcl_b: number | null;
    tga_b: number | null;
    rrp_b: number | null;
  };
  regime: MacroRegime;
  macro_regime?: MacroRegimeQuadrant;
  generated_at: string;
}

// ===== Business / Economic Cycle Types =====

export type CyclePhase = 'early' | 'mid' | 'late' | 'recession';
export type CycleMomentum = 'accelerating' | 'stable' | 'decelerating';
export type CycleConfidence = 'high' | 'medium' | 'low';

export interface GdpPoint {
  year: number;
  value: number;
}

export type PhaseSource = 'oecd_cli' | 'imf_gdp_fallback' | 'default';

export interface RegionCycle {
  id: string;
  name: string;
  flag: string;
  proxy_note: string | null;
  phase: CyclePhase;
  phase_label: string;
  momentum: CycleMomentum;
  confidence: CycleConfidence;
  phase_source: PhaseSource;
  months_in_phase: number | null;
  // OECD CLI
  cli_current: number | null;
  cli_3m_change: number | null;
  cli_series: number[];              // last 12 months, oldest→newest
  // IMF GDP
  gdp_current_year: number | null;
  gdp_next_year: number | null;
  gdp_year: number;
  gdp_series: GdpPoint[];
  // LLM enrichment (optional)
  key_signals: string[];
  outlook_headline: string;
}

export interface CycleAssetPerformer {
  category: string;
  stars: number;
  note: string;
}

export interface CyclePhaseMatrix {
  label: string;
  color: string;
  description: string;
  performers: CycleAssetPerformer[];
}

export interface BusinessCycleResponse {
  regions: RegionCycle[];
  asset_matrix: Record<CyclePhase, CyclePhaseMatrix>;
  as_of: string;
  generated_with_llm: boolean;
  oecd_available: boolean;
  data_sources: {
    cycle_phase: string;
    gdp: string;
    signals: string;
  };
}

// ===== Cycle Sector Technical Analysis =====

export interface TaSignal {
  type: 'bullish' | 'bearish' | 'neutral';
  text: string;
}

export interface TaChart {
  dates: string[];
  price: (number | null)[];
  sma20: (number | null)[];
  sma50: (number | null)[];
  sma200_ref: number | null;
  rsi: (number | null)[];
  macd: (number | null)[];
  macd_signal: (number | null)[];
  macd_hist: (number | null)[];
}

export interface SectorTA {
  category: string;
  ticker: string;
  stars: number;
  note: string;
  price: number;
  sma20: number | null;
  sma50: number | null;
  sma200: number | null;
  vs_sma200_pct: number | null;
  hi52: number | null;
  lo52: number | null;
  rsi: number | null;
  macd_current: number | null;
  signal_current: number | null;
  macd_histogram: number | null;
  macd_bullish: boolean | null;
  vol_ratio: number | null;
  signals: TaSignal[];
  overall: 'bullish' | 'neutral' | 'bearish';
  chart: TaChart;
  pct_1m: number | null;
  pct_3m: number | null;
  pct_6m: number | null;
  pct_ytd: number | null;
}

export interface CycleSectorTaResponse {
  phase: string;
  phase_label: string;
  phase_color: string;
  sectors: SectorTA[];
  as_of: string;
}

// ===== LLM Analysis Types (Qualitative & Macro) =====

export interface LLMAnalysisResponse {
  ticker: string;
  analysis: string | null;
  created_at: string | null;
}

// ===== Strategy Types =====

export interface BoxSpreadLeg {
  action: 'BUY' | 'SELL';
  type: 'CALL' | 'PUT';
  strike: number;
  bid: number;
  ask: number;
  mid: number;
  last?: number;
  iv: number | null;
  oi: number;
  vol: number;
  // Greeks (available when using IBKR quote source)
  delta?: number;
  gamma?: number;
  theta?: number;
  vega?: number;
  conid?: number;
}

export interface BoxSpreadRisk {
  category: string;
  description: string;
  severity: string;
  mitigation: string;
}

export interface BoxSpreadResult {
  expiration: string;
  dte: number;
  lower_strike: number;
  upper_strike: number;
  box_width: number;
  intent: 'lend' | 'borrow';
  legs: BoxSpreadLeg[];
  // Lending fields
  box_cost_mid?: number;
  box_cost_worst?: number;
  box_value_at_expiration: number;
  profit_per_contract?: number;
  return_pct?: number;
  annualized_return_pct?: number;
  num_contracts: number;
  total_cost?: number;
  total_value_at_expiration?: number;
  total_profit?: number;
  // Borrowing fields
  box_proceeds_mid?: number;
  box_proceeds_worst?: number;
  interest_cost_per_contract?: number;
  interest_rate_pct?: number;
  annualized_rate_pct?: number;
  total_proceeds?: number;
  total_owed_at_expiration?: number;
  total_interest_cost?: number;
  // Common
  implied_annual_rate: number;
  min_open_interest: number;
  // Fill-optimization (added by the scanner / engine)
  smart_annual_rate?: number;        // return at the realistic, likely-to-fill smart price
  mid_annual_rate?: number;          // optimistic mid-price rate (reference)
  smart_price?: SmartPriceResponse;  // per-leg recommended fill prices
  fill_probability?: number;         // 0–100, from liquidity indicators
  fill_factors?: BoxFillFactors;
  // No-arbitrage fair value (the price that actually fills)
  fair_value_net?: number;           // (K2-K1) * discount factor, per share
  recommended_limit_net?: number;    // fair value ± 1 tick toward the dealer — the fill price
  theoretical_annual_rate?: number;  // gross rate you transact at (≈ risk-free), same for all boxes
  edge_vs_fair?: number;             // signed gap of quoted net vs fair (>0 = favorable)
  // Net-of-execution-cost rate — the real per-box differentiator
  net_achievable_rate?: number;      // rate after slippage + commission (lend: <gross; borrow: >gross)
  friction_cost?: number;            // estimated $ to enter (slippage + commission)
  cost_drag_bps?: number;            // bps lost to friction vs the gross rate
}

export interface BoxFillFactors {
  spread_tightness: number;
  liquidity: number;
  turnover: number;
  min_leg_oi_vol: number;
  crossed: boolean;
}

export interface BoxOpportunity extends BoxSpreadResult {
  ticker: string;
  current_price: number;
}

export interface BoxMarketTiming {
  now_et: string;
  market_open: boolean;
  session: string;
  good_to_trade: boolean;
  recommendation: string;
  notes: string[];
}

export interface BoxScanResponse {
  generated_at: string;
  intent: 'lend' | 'borrow';
  params: {
    duration_days: number;
    target_annual_return: number;
    amount: number;
    max_contracts: number;
    per_ticker: number;
    quote_source: string;
  };
  universe: string[];
  risk_free_rate: number;
  risk_free_source?: string;
  achievable_rate: number;
  target_feasible: boolean;
  feasibility_note?: string;
  market_timing: BoxMarketTiming;
  opportunities: BoxOpportunity[];
  skipped: { ticker: string; reason: string }[];
}

export interface BoxSpreadResponse {
  ticker: string;
  current_price: number;
  intent: string;
  target_amount: number;
  target_duration_days: number;
  target_annual_return: number;
  quote_source?: string;
  spreads: BoxSpreadResult[];
  risks: BoxSpreadRisk[];
  available_expirations: string[];
  error?: string;
}

// ===== Hedging Strategy =====

export interface HedgeLeg {
  action: 'BUY' | 'SELL';
  type: 'CALL' | 'PUT';
  strike: number;
  expiration: string;
  contracts: number;
  bid: number;
  ask: number;
  mid: number;
  iv: number | null;
  oi: number;
  vol: number;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
}

export interface HedgeGreeks {
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
}

// A single strike's quote on the chain (used for client-side strike editing).
export interface HedgeQuote {
  strike: number;
  type: 'CALL' | 'PUT';
  bid: number;
  ask: number;
  mid: number;
  iv: number | null;
  oi: number;
  vol: number;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
}

export interface HedgeStructure {
  id: string;
  name: string;
  style: string;
  plain: string;                    // one-line plain-English summary
  legs: HedgeLeg[];
  net_cost: number;                 // +debit / -credit (total $)
  net_premium_per_share: number;
  cost_pct_of_notional: number;
  annualized_cost_pct: number;
  is_credit: boolean;
  within_budget: boolean;
  budget: number;
  protection_floor: number | null;
  protection_floor_pct: number | null;
  buffer_bottom: number | null;
  buffer_bottom_pct: number | null;
  upside_cap: number | null;
  upside_cap_pct: number | null;
  giveup_from: number | null;
  giveup_from_pct: number | null;
  participate_above: number | null;
  participate_above_pct: number | null;
  max_loss: number;
  max_loss_pct: number | null;
  upside_breakeven: number;
  upside_breakeven_pct: number;
  net_greeks: HedgeGreeks;
  intrinsic_value: number;
  time_value: number;
  theta_per_day: number;
  covered_shares: number;
  uncovered_shares: number;
  tail_open_below: number | null;
  notes: string[];
  probs?: HedgeProbabilities | null;   // market-implied (Breeden-Litzenberger RND)
  smile_risk?: SmileRisk | null;       // Vanna-Volga VIX-spike exposure
}

// Per-structure market-implied probabilities from the risk-neutral density.
export interface HedgeProbabilities {
  p_breach_floor_pct?: number;
  floor_strike?: number;
  p_hit_cap_pct?: number;
  cap_strike?: number;
  p_in_band_pct?: number;
  p_below_buffer_pct?: number;
}

// Vanna-Volga smile risk — exposure to a VIX spike (margin risk on short-vol legs).
export interface SmileRisk {
  net_vega: number;
  net_vanna: number;
  net_volga: number;
  vix_shock_pts: number;
  pnl_on_vol_spike: number;
  short_vol: boolean;
}

// Heston stochastic-vol fit (κ pinned, v0 = ATM-variance; single expiry).
export interface HestonParams {
  v0: number;
  kappa: number;
  theta: number;
  sigma_v: number;
  rho: number;
  vol_of_vol: number;
  long_run_vol_pct: number;
  spot_vol_corr: number;
  feller_ok: boolean;
  rmse_vol_pts: number;
}

// Downsampled RND curve on a %-of-spot grid — charting + client-side prob recompute.
export interface RndCurve {
  pct: number[];           // % move from spot at expiry
  cdf_pct: number[];       // P(S_T ≤ level) in %
  pdf_per_pct: number[];   // probability mass (%) per 1% move
}

// Response-level RND summary (SVI smile → Breeden-Litzenberger).
export interface RndSummary {
  forward: number;
  expected_move_pct: number;
  arb_free: boolean;
  svi_rmse_vol_pts: number;
  p_down_5_pct: number;
  p_down_10_pct: number;
  p_down_20_pct: number;
  floor_for_10pct_breach_pct: number;
  floor_for_5pct_breach_pct: number;
  curve?: RndCurve | null;
  heston?: HestonParams | null;
}

// Daily history + light technicals for the hedge-context price chart.
export interface HedgePriceHistory {
  available: boolean;
  error?: string;
  ticker?: string;
  spot?: number;
  lookback_days?: number;
  dates?: string[];
  close?: (number | null)[];
  volume?: number[];
  sma20?: (number | null)[];
  sma50?: (number | null)[];
  supports?: { level: number; touches: number }[];
  resistances?: { level: number; touches: number }[];
  avg_volume?: number;
  last_volume_ratio?: number | null;
}

export interface MarketSignal {
  label: string;
  value: string;
  tone: 'good' | 'bad' | 'neutral';
  read: string;
}

export interface MarketConditions {
  available: boolean;
  spot?: number;
  rnd?: RndSummary | null;
  next_earnings?: { date: string; days: number; inside_horizon: boolean } | null;
  beta: number | null;
  score?: number;
  verdict?: 'Favorable' | 'Fair' | 'Expensive';
  headline?: string;
  recommended_ids?: string[];
  signals?: MarketSignal[];
  ret_1w_pct?: number | null;
  ret_1m_pct?: number | null;
  vs_ma50_pct?: number | null;
  vs_ma200_pct?: number | null;
  drawdown_52w_pct?: number | null;
  rv10_pct?: number | null;
  rv20_pct?: number | null;
  rv60_pct?: number | null;
  rv_pctile?: number | null;
  atm_iv_pct?: number | null;
  iv_rank?: number | null;
  vrp_pts?: number | null;
  vix?: number | null;
  vix_pctile?: number | null;
  vix3m?: number | null;
  term_structure_ratio?: number | null;
  skew_pts?: number | null;
  pc_oi_ratio?: number | null;
  pc_vol_ratio?: number | null;
}

export interface IndexScenario {
  move_pct: number;
  stock_pl: number;
  hedge_pl: number;
  net_pl: number;
}

export interface IndexOverlay {
  benchmark: string;
  beta: number;
  index_spot: number;
  expiration: string;
  dte: number;
  strike: number;
  strike_pct: number;
  contracts: number;
  hedge_notional: number;
  cost: number;
  cost_pct_of_notional: number | null;
  leg: HedgeLeg;
  scenarios: IndexScenario[];
  is_broad_index: boolean;
}

export interface HedgingResponse {
  ticker: string;
  current_price: number;
  shares: number;
  notional: number;
  beta: number | null;
  benchmark: string;
  hedge_ratio: number;
  contracts: number;
  covered_shares: number;
  horizon_days: number;
  expiration: string;
  dte: number;
  protection_pct: number;
  upside_pct: number;
  downside_buffer: number;
  upside_giveup: number;
  downside_cap: number;
  max_cost_pct: number;
  quote_source: string;
  atm_iv: number | null;
  hedges: HedgeStructure[];
  chain: { puts: HedgeQuote[]; calls: HedgeQuote[] };
  market: MarketConditions | null;
  rnd: RndSummary | null;
  index_overlay: IndexOverlay | null;
  risks: BoxSpreadRisk[];
  available_expirations: string[];
  error?: string;
}

// Smart price recommendation
export interface SmartPriceLeg {
  strike: number;
  action: string;
  type?: string;
  price: number;
  confidence_pct: number;
  reasoning: string;
}

export interface SmartPriceResponse {
  recommended_legs: SmartPriceLeg[];
  total_net_per_contract: number;
  annualized_return_pct?: number;
  annualized_rate_pct?: number;
}

// Order preview
export interface OrderPreviewLeg {
  strike: number;
  type: string;
  action: string;
  conid?: number;
  resolved: boolean;
  limit_price?: number;
  estimated_commission?: number | null;
  margin_impact?: number | null;
  warnings?: string[];
  error?: string;
}

export interface OrderPreviewResponse {
  preview_success: boolean;
  legs: OrderPreviewLeg[];
  total_estimated_commission: number;
  margin_impact?: string | number | null;
  warnings: string[];
  account_id: string;
}

export interface StockInfoBrief {
  ticker: string;
  name: string;
  price: number | null;
  sector: string;
  industry: string;
  market_cap: number | null;
  beta: number | null;
  pe_ratio: number | null;
  forward_pe: number | null;
  dividend_yield: number;
  '52w_high': number | null;
  '52w_low': number | null;
}

export interface SpreadData {
  timestamps: string[];
  values: number[];
  mean: number;
  std: number;
  current: number;
  z_score: number;
}

export interface PriceHistoryData {
  timestamps: string[];
  prices: number[];
  normalized: number[];
}

// ===== Quant Analytics Types =====

export interface FactorScores {
  value: number;
  momentum: number;
  quality: number;
  defensive: number;
  composite: number;
}

export interface RiskDecomposition {
  beta: number;
  alpha_annual: number;
  alpha_t_stat: number;
  r_squared: number;
  idiosyncratic_vol: number;
  downside_beta: number;
  tracking_error: number;
  information_ratio: number;
}

export interface TailRisk {
  var_95: number;
  cvar_95: number;
  sortino: number;
  calmar: number;
  skewness: number;
  kurtosis: number;
  tail_ratio: number;
}

export interface QuantSignals {
  rsi_14: number;
  bollinger_pct: number;
  above_sma_50: boolean | null;
  above_sma_200: boolean | null;
  momentum_12_1: number;
  spread_regime?: string;
  entry_signal: string;
  entry_reasons: string[];
}

export interface PositionSizing {
  kelly_full: number;
  kelly_half: number;
  risk_parity_pct: number;
  inverse_vol_pct: number;
  recommended_pct: number;
  recommended_dollars: number;
}

export interface QuantAnalytics {
  factor_scores: FactorScores;
  risk_decomposition: RiskDecomposition;
  tail_risk: TailRisk;
  signals: QuantSignals;
  position_sizing: PositionSizing;
}

export interface PairAnalytics {
  relative_value: number;
  relative_momentum: number;
  relative_quality: number;
  relative_composite: number;
  spread_z_score: number | null;
  long_entry_signal: string | null;
  short_entry_signal: string | null;
}

// ===== Long/Short Strategy Types =====

export interface LongShortStrategy {
  hedge_ticker: string;
  hedge_name: string;
  hedge_price: number;
  correlation: number | null;
  hedge_ratio: number;
  shares_long: number;
  long_value: number;
  shares_short: number;
  short_value: number;
  net_exposure: number;
  gross_exposure: number;
  price_history_1: PriceHistoryData | null;
  price_history_2: PriceHistoryData | null;
  spread: SpreadData | null;
}

export interface SingleStockLongShortResponse {
  type: 'single_stock';
  ticker: string;
  stock_info: StockInfoBrief;
  investment_amount: number;
  strategies: LongShortStrategy[];
  stock_metrics: {
    annualized_volatility: number | null;
    max_drawdown_1y: number | null;
    sharpe_ratio_1y: number | null;
  };
  sector_etfs_available: string[];
  quant_analytics?: QuantAnalytics | null;
  error?: string;
}

export interface PairCandidate {
  ticker: string;
  name: string;
  price: number | null;
  sector: string;
  industry: string;
  market_cap: number | null;
  correlation: number;
  hedge_ratio: number;
}

export interface PairSuggestionsResponse {
  ticker: string;
  sector: string;
  industry: string;
  candidates: PairCandidate[];
  message?: string;
}

export interface PairTradeResponse {
  type: 'pair_trade';
  long_ticker: string;
  short_ticker: string;
  long_info: StockInfoBrief;
  short_info: StockInfoBrief;
  correlation: number | null;
  hedge_ratio: number;
  investment_amount: number;
  shares_long: number;
  long_value: number;
  shares_short: number;
  short_value: number;
  net_exposure: number;
  gross_exposure: number;
  price_history: PriceHistoryData | null;
  price_history_2: PriceHistoryData | null;
  spread: SpreadData | null;
  long_metrics: { annualized_volatility: number | null; max_drawdown_1y: number | null };
  short_metrics: { annualized_volatility: number | null; max_drawdown_1y: number | null };
  quant_analytics_long?: QuantAnalytics | null;
  quant_analytics_short?: QuantAnalytics | null;
  pair_analytics?: PairAnalytics | null;
  error?: string;
}

// ===== 130/30 Enhanced Equity Types =====

export interface PortfolioPosition {
  ticker: string;
  name: string;
  price: number | null;
  sector: string;
  side: 'long' | 'short';
  shares: number;
  dollar_value: number;
  weight_pct: number;
  beta: number;
  factor_scores: FactorScores;
  quant_analytics?: QuantAnalytics | null;
}

export interface MarketScenario {
  market_move: number;
  long_pnl: number;
  short_pnl: number;
  net_pnl: number;
  return_pct: number;
  portfolio_value: number;
}

export interface StressScenario {
  name: string;
  description: string;
  net_pnl: number;
  return_pct: number;
}

export interface LeverageComparison {
  ratio: string;
  best_case: number;
  best_case_pct: number;
  worst_case: number;
  worst_case_pct: number;
  expected: number;
  expected_pct: number;
}

export interface PositionImpact {
  ticker: string;
  side: 'long' | 'short';
  weight: number;
  dollar_value: number;
  if_down_20_pnl: number;
  if_up_20_pnl: number;
  portfolio_impact_pct: number;
}

export interface ScenarioAnalysis {
  market_scenarios: MarketScenario[];
  stress_scenarios: StressScenario[];
  leverage_comparison: LeverageComparison[];
  position_impact: PositionImpact[];
  max_profit: number;
  max_loss: number;
  breakeven_market_move: number | null;
}

export interface TaxProjectionYear {
  year: number;
  long_only_losses: number;
  ls_losses: number;
  long_only_tax_savings: number;
  ls_tax_savings: number;
  cumulative_long_only: number;
  cumulative_ls: number;
}

export interface TaxProjections {
  yearly: TaxProjectionYear[];
  avg_annual_tax_alpha_pct: number;
  total_10yr_savings: number;
  loss_multiplier_vs_long_only: number;
  leverage_comparison: Record<string, number>;
}

export interface PortfolioRiskMetrics {
  net_beta: number;
  gross_exposure: number;
  net_exposure: number;
  portfolio_volatility: number;
  estimated_tracking_error: number;
  sharpe_ratio: number;
  long_count: number;
  short_count: number;
  long_value: number;
  short_value: number;
  sector_breakdown: SectorBreakdown[];
  top5_concentration: number;
  hhi: number;
}

export interface SectorBreakdown {
  sector: string;
  long_weight: number;
  short_weight: number;
  net_weight: number;
}

export interface FactorExposure {
  long_value: number;
  long_momentum: number;
  long_quality: number;
  long_defensive: number;
  long_composite: number;
  short_value: number;
  short_momentum: number;
  short_quality: number;
  short_defensive: number;
  short_composite: number;
  portfolio_value: number;
  portfolio_momentum: number;
  portfolio_quality: number;
  portfolio_defensive: number;
  portfolio_composite: number;
}

export interface Portfolio130_30Response {
  type: '130_30';
  long_positions: PortfolioPosition[];
  short_positions: PortfolioPosition[];
  risk_metrics: PortfolioRiskMetrics;
  factor_exposure: FactorExposure;
  scenario_analysis: ScenarioAnalysis;
  tax_projections: TaxProjections;
  parameters: {
    investment_amount: number;
    leverage_ratio: string;
    long_target: number;
    short_target: number;
    actual_long: number;
    actual_short: number;
    tax_rate_st: number;
    tax_rate_lt: number;
  };
  llm_insights: string | null;
  error?: string;
}

// ===== Exit Analysis Types =====

export interface ExitPosition {
  current_price: number;
  market_value: number;
  total_cost: number;
  unrealized_gain_loss: number;
  unrealized_gain_loss_pct: number;
  holding_period_days: number;
  tax_type: string;
  purchase_date: string;
}

export interface ExitPillarChartData {
  years: string[];
  revenue: number[];
  gross_margin: number[];
  operating_margin: number[];
  net_margin: number[];
  fcf: number[];
}

export interface ExitFundamentalData {
  revenue_growth_rates: number[];
  revenue_direction: string;
  gross_margin_trend: number[];
  operating_margin_trend: number[];
  net_margin_trend: number[];
  margin_direction: string;
  eps_trend: { year: string; value: number }[];
  eps_consistency: number;
  fcf_trend: { year: string; value: number }[];
  fcf_direction: string;
  debt_to_equity: number | null;
  interest_coverage: number | null;
  current_ratio: number | null;
  earnings_surprises: { quarter: string; surprise_pct: number }[];
}

export interface ExitMacroData {
  beta: number | null;
  beta_interpretation: string;
  sector: string;
  sector_cyclicality: string;
  dividend_yield: number;
  dividend_yield_vs_rates: string;
  hv30: number | null;
  hv60: number | null;
  vol_regime: string;
}

export interface ExitStructuralData {
  rd_as_pct_revenue: number | null;
  rd_trend: { year: string; value: number }[] | null;
  rd_direction: string | null;
  capex_trend: { year: string; value: number }[];
  capex_to_revenue_pct: number;
  revenue_growth_3y_cagr: number;
  industry: string;
  industry_growth_signal: string;
}

export interface ExitStructuralChartData {
  years: string[];
  rd_spend: number[];
  capex: number[];
  revenue_growth: number[];
}

export interface ExitGeopoliticalData {
  news_sentiment: string;
  negative_news_count: number;
  total_news_count: number;
  regulatory_themes_detected: string[];
  sector_regulatory_risk: string;
  recent_headlines: { title: string; sentiment: string; publisher?: string; link?: string; published?: string }[];
}

export interface ExitSectorRotationData {
  sector: string;
  sector_etf: string;
  sector_vs_spy_1m: number | null;
  sector_vs_spy_3m: number | null;
  sector_vs_spy_6m: number | null;
  sector_return_1m: number | null;
  sector_return_3m: number | null;
  sector_return_6m: number | null;
  spy_return_1m: number | null;
  spy_return_3m: number | null;
  spy_return_6m: number | null;
  sector_volume_trend: string;
  relative_strength_signal: string;
  rotation_signal: string;
  sector_rs_series: number[];
  sector_etf_prices: number[];
  spy_prices: number[];
  price_timestamps: string[];
}

export interface ExitValuationData {
  trailing_pe: number | null;
  forward_pe: number | null;
  pe_expansion: number | null;
  peg_ratio: number | null;
  ev_to_ebitda: number | null;
  price_to_book: number | null;
  fifty_two_week_high: number | null;
  fifty_two_week_low: number | null;
  price_vs_52w_pct: number | null;
  analyst_target_mean: number | null;
  analyst_target_low: number | null;
  analyst_target_high: number | null;
  target_upside_pct: number | null;
  num_analysts: number | null;
  recommendation: string | null;
  recommendation_score: number | null;
}

export interface ExitSentimentData {
  short_pct_of_float: number | null;
  short_ratio: number | null;
  insider_ownership_pct: number | null;
  institutional_ownership_pct: number | null;
  analyst_consensus: string | null;
  target_upside_pct: number | null;
  sentiment_label: string;
}

export interface ExitSwingSignals {
  rsi_score: number; rsi_value: number; rsi_signal: string;
  macd_score: number; macd_value: number; macd_signal: string; macd_histogram: number;
  bollinger_score: number; bollinger_pct_b: number; bollinger_position: string;
  ema_crossover_score: number; ema_signal: string;
  volume_divergence_score: number; volume_phase: string;
  composite_score: number;
}

export interface ExitLongtermSignals {
  sma_cross_score: number; sma_cross_label: string;
  sma50: number | null; sma200: number | null;
  fundamental_health_score: number; valuation_score: number;
  analyst_consensus_score: number; analyst_recommendation: string;
  composite_score: number;
}

export interface ExitOptionsProtection {
  available: boolean;
  current_price?: number;
  contracts_needed?: number;
  protective_put?: {
    strike: number; expiration: string; dte: number;
    mid_price: number; total_cost: number; protection_floor: number;
    max_loss_per_share: number; annualized_cost_pct: number;
  };
  zero_cost_collar?: {
    put_strike: number; call_strike: number; expiration: string; dte: number;
    put_mid: number; call_mid: number; net_credit_debit: number;
    protection_floor: number; upside_cap: number;
  };
  covered_call?: {
    strike: number; expiration: string; dte: number;
    mid_price: number; total_premium: number;
    annualized_return_pct: number; upside_cap_pct: number;
  };
}

export interface ExitPriceChartEvent {
  date: string;
  price: number | null;
  type: string;
  label: string;
  description: string;
}

export interface ExitTrendData {
  change_pct: number;
  high: number;
  low: number;
  volatility: number | null;
  direction: string;
}

export interface ExitPriceChart {
  timestamps: string[];
  prices: number[];
  volumes: number[];
  cost_basis_line: number | null;
  trend_30d: ExitTrendData | null;
  trend_60d: ExitTrendData | null;
  trend_90d: ExitTrendData | null;
  events: ExitPriceChartEvent[];
  sma50: (number | null)[];
  sma200: (number | null)[];
}

export interface ExitRiskData {
  beta: number | null; beta_label: string;
  annualized_volatility: number | null;
  max_drawdown_1y: number | null;
  sharpe_ratio_1y: number | null;
  sortino_ratio_1y: number | null;
  var_95_daily: number | null; var_95_monthly: number | null;
  cvar_95_daily: number | null; cvar_95_monthly: number | null;
  position_var_95_daily: number | null; position_var_95_monthly: number | null;
  position_cvar_95_daily: number | null; position_cvar_95_monthly: number | null;
  upside_volatility: number | null; downside_volatility: number | null;
  skewness: number | null; kurtosis: number | null;
  calmar_ratio: number | null; max_drawdown_duration_days: number | null;
  ulcer_index: number | null; tail_risk_ratio: number | null;
  win_rate: number | null; avg_win_pct: number | null; avg_loss_pct: number | null;
  gain_to_pain_ratio: number | null;
}

export interface ExitLiquidityData {
  avg_daily_volume_20d: number | null;
  avg_daily_dollar_volume: number | null;
  days_to_liquidate: number | null;
  liquidity_rating: string;
}

export interface ExitSignalDriver {
  pillar: string;
  score: number;
  severity: string;
}

export interface ExitSignalSummary {
  overall_narrative: string;
  key_drivers: ExitSignalDriver[];
  strengths: { pillar: string; score: number }[];
  detail_narratives: string[];
  quantitative: {
    pillar_avg: number;
    pillar_label?: string;
    tech_avg: number;
    tech_label?: string;
    risk_score?: number;
    risk_label?: string;
    pillar_weight: number;
    tech_weight: number;
    risk_weight?: number;
    swing_score?: number;
    longterm_score?: number;
    highest_pillar: { name: string; score: number };
    lowest_pillar: { name: string; score: number };
    pillar_scores: Record<string, number>;
  };
}

export interface ExitDimensionScore {
  score: number;
  label: string;
  weight: number;
}

export interface ExitAnalysisData {
  success: boolean;
  ticker: string;
  position: ExitPosition;
  overall_score: number;
  overall_label: string;
  dimension_scores?: {
    pillars: ExitDimensionScore;
    technical: ExitDimensionScore;
    risk: ExitDimensionScore;
  };
  signal_summary?: ExitSignalSummary;
  pillars: {
    fundamental: { score: number; data: ExitFundamentalData; chart_data: ExitPillarChartData };
    macro: { score: number; data: ExitMacroData };
    structural: { score: number; data: ExitStructuralData; chart_data: ExitStructuralChartData };
    geopolitical: { score: number; data: ExitGeopoliticalData };
    valuation: { score: number; data: ExitValuationData };
    sentiment: { score: number; data: ExitSentimentData };
    sector_rotation: { score: number; data: ExitSectorRotationData };
  };
  technical_signals: {
    swing: ExitSwingSignals;
    longterm: ExitLongtermSignals;
  };
  price_chart: ExitPriceChart;
  options_protection: ExitOptionsProtection;
  risk: ExitRiskData;
  liquidity: ExitLiquidityData;
  error?: string;
}

// ===== AI Impact Types =====
export interface AIImpactDimension {
  name: string;
  weight: number;
  score: number;
  explanation: string;
}

export interface AIImpactData {
  ticker: string;
  analysis: {
    compositeScore: number;
    dimensions: AIImpactDimension[];
  } | null;
  created_at: string | null;
}

// ===== AI Fortress Analysis Types =====
export interface AIFortressArchitecture {
  name: string;
  present: boolean;
  score: number;
  explanation: string;
}

export interface AIFortressData {
  ticker: string;
  analysis: {
    summary: string;
    fortresses: AIFortressArchitecture[];
  } | null;
  created_at: string | null;
}

// ===== AI Business Stress Test Types =====
export interface AIStressTestAngle {
  name: string;
  rating: string;
  defense: string;
}

export interface AIStressTestData {
  ticker: string;
  analysis: {
    overallRisk: string;
    angles: AIStressTestAngle[];
  } | null;
  created_at: string | null;
}

// ===== RUPEE Framework Types (Marwari "Sethji" value analysis) =====
export interface RupeeSection {
  key: string;
  title: string;
  reality: string;
  verdict: string;
}

export interface RupeeData {
  ticker: string;
  analysis: {
    company: string;
    sections: RupeeSection[];
    stance: string;
    verdict: string;
  } | null;
  created_at: string | null;
}

// ===== Broker Types =====

export interface BrokerConnection {
  id: number;
  broker_type: string;
  account_id: string | null;
  is_active: boolean;
  last_connected_at: string | null;
  has_credentials: boolean;
  has_keys: boolean;
}

export interface BrokerKeyGenResponse {
  signature_public_key: string;
  encryption_public_key: string;
  dh_params: string;
  dh_prime_hex: string;
  keys_stored: boolean;
  message: string;
}

export interface BrokerStatus {
  connected: boolean;
  authenticated: boolean;
  account_id: string | null;
  server_name: string | null;
  error: string | null;
}

export interface BrokerOrderLeg {
  ticker: string;
  action: 'Buy' | 'Sell';
  type: 'Call' | 'Put';
  strike: number;
  qty: number;
  expiration: string;
  limit_price: number;
}

export interface BrokerOrderResult {
  success: boolean;
  order_id: string | null;
  message: string | null;
  error: string | null;
}

export interface BrokerStrategyResult {
  overall_success: boolean;
  legs: BrokerOrderResult[];
  order_record_id: number;
}

// ---------------------------------------------------------------------------
// Market overview dashboard
// ---------------------------------------------------------------------------

export interface IndexReturns {
  '1d'?: number | null;
  '7d'?: number | null;
  '15d'?: number | null;
  '1m'?: number | null;
  '3m'?: number | null;
  ytd?: number | null;
  '1y'?: number | null;
  '3y'?: number | null;
  '5y'?: number | null;
}

export interface IndexData {
  symbol: string;
  name: string;
  short: string;
  group?: string;
  price: number | null;
  returns: IndexReturns;
  sparkline: number[];
  volume: number | null;
  avg_volume_20d: number | null;
  volume_ratio: number | null;
  high_52w: number | null;
  low_52w: number | null;
  error?: string;
}

export interface MarketOverviewResponse {
  indices: IndexData[];
  narrative: string | null;
  generated_at: string;
}

export interface SectorQuantAnalytics {
  volatility_ann?: number | null;
  sharpe?: number | null;
  beta_vs_spx?: number | null;
  max_drawdown?: number | null;
  trend_persistence?: number | null;
  rs_vs_spx?: number | null;
  rs_zscore?: number | null;
  rotation_stage?: string | null;
  momentum_status?: string | null;
}

export interface IndustryData {
  symbol: string;
  name: string;
  price: number | null;
  returns: IndexReturns;
  volume_ratio: number | null;
  analytics?: SectorQuantAnalytics;
  error?: string;
}

export interface SectorData {
  symbol: string;
  name: string;
  price: number | null;
  returns: IndexReturns;
  sparkline: number[];
  volume: number | null;
  avg_volume_20d: number | null;
  volume_ratio: number | null;
  industries?: IndustryData[];
  analytics?: SectorQuantAnalytics;
  error?: string;
}

export interface SectorQuantSummary {
  avg_volatility: number | null;
  avg_sharpe: number | null;
  avg_beta: number | null;
  avg_trend_persistence: number | null;
  beating_spx_count: number;
  total_with_rs: number;
  dispersion: number | null;
}

export interface SectorRotationItem {
  symbol: string;
  name: string;
  return: number;
  short_return?: number | null;
  r_7d?: number | null;
  r_1m?: number | null;
  r_3m?: number | null;
  one_year?: number | null;
  volume_ratio: number;
  signal?: string;
  flow_score?: number;
  momentum?: number;
  pace?: number;
  vol_tilt?: number;
}

export interface SectorRotation {
  timeframe: string;
  leaders: SectorRotationItem[];
  laggards: SectorRotationItem[];
  inflows: SectorRotationItem[];
  outflows: SectorRotationItem[];
  rotating_in: SectorRotationItem[];
  rotating_out: SectorRotationItem[];
  breadth_pct: number;
}

export interface SectorDashboardResponse {
  sectors: SectorData[];
  rotation: SectorRotation;
  summary?: SectorQuantSummary;
  intelligence: string | null;
  generated_at: string;
}

export interface SectorChatTurn {
  role: 'user' | 'assistant';
  content: string;
}

// ===== Sector Calendar Returns =====
export interface SectorYearReturn {
  symbol: string;
  name: string;
  return: number;   // percent, e.g. 46.3
}

export interface SectorCalendarYear {
  year: number;
  sectors: SectorYearReturn[];   // sorted best → worst
  in_progress: boolean;
}

export interface SectorCalendarResponse {
  years: SectorCalendarYear[];
  generated_at: string;
}

// ===== Style Calendar Returns =====
export interface StyleYearReturn {
  symbol: string;
  name: string;
  category: string;
  return: number;
}

export interface StyleCalendarYear {
  year: number;
  styles: StyleYearReturn[];   // sorted best → worst
  in_progress: boolean;
}

export interface StyleCalendarResponse {
  years: StyleCalendarYear[];
  generated_at: string;
}

export interface SectorChatResponse {
  answer: string;
  timeframe: string;
  generated_at: string;
}

export interface Mover {
  ticker: string;
  sector: string;
  return: number;
  price: number;
  volume_ratio: number | null;
}

export interface MoversResponse {
  timeframe: string;
  gainers: Mover[];
  losers: Mover[];
  explanations: string | null;
  generated_at: string;
}

// ---------------------------------------------------------------------------
// Pick & Shovel theme research
// ---------------------------------------------------------------------------

export interface PickShovelCompany {
  ticker: string;
  name: string;
  exchange?: string | null;
  thesis: string;
  catalysts: string[];
  revenue_exposure?: string | null;
  earnings_signal?: string | null;
  risk?: string | null;
  // Tier-specific depth fields
  supply_chain_role?: string | null;      // Tier 2 — where in supply chain
  why_overlooked?: string | null;         // Tier 2 — why investors miss this
  hidden_link?: string | null;            // Tier 3 — non-obvious connection
  discovery_insight?: string | null;      // Tier 3 — why analysts miss this
  why_this_not_another?: string | null;   // Tier 1 — competitive moat
  // Dig Deeper extra fields
  connection_type?: string | null;        // supplier | client | bottleneck | testing | infra | data | finance
  connection_to?: string | null;          // which anchor company this relates to
  why_nobody_covers_this?: string | null; // why absent from thematic ETFs
  // live financial data
  price?: number | null;
  price_chg_1d?: number | null;
  pe_ratio?: number | null;
  forward_pe?: number | null;
  market_cap?: string | null;
  week52_high?: number | null;
  week52_low?: number | null;
  sector?: string | null;
  // verification pass results
  verified?: boolean | null;              // null = not yet checked; true = passed; false = flagged
  verification_note?: string | null;      // evidence citation (if verified) or fail reason (if flagged)
  yfinance_name?: string | null;          // actual company name from yfinance for cross-check
  // v2 grounded synthesis — why this pick was chosen, tracing to the research
  provenance?: string | null;
  // v2 component-level suppliers
  supply_kind?: string | null;   // 'documented' (named in filings) | 'inferred'
  unlisted?: boolean;            // true = private/unlisted lead (no tradable ticker)
}

export interface PickShovelResponse {
  theme: string;
  theme_title: string;
  theme_summary: string;
  supply_chain_map?: string | null;
  key_trends: string[];
  direct_plays: PickShovelCompany[];
  enablers: PickShovelCompany[];
  deep_picks: PickShovelCompany[];
  generated_at: string;
  from_cache?: boolean;
  // v2 grounded synthesis
  selection_notes?: string;
  seeded_from_research?: boolean;
}

/** One "Dig Deeper" layer returned by POST /api/market/pick-shovel/deeper */
export interface DigDeeperLayer {
  depth_level: number;
  depth_label: string;
  depth_rationale: string;
  companies: PickShovelCompany[];
  generated_at: string;
  from_cache?: boolean;
}

export interface DigDeeperParams {
  theme: string;
  already_shown: string[];
  anchor_companies: { ticker: string; name: string; supply_chain_role?: string | null; hidden_link?: string | null; thesis?: string | null }[];
  depth_level: number;
}

// ---------------------------------------------------------------------------
// Pick & Shovel — collaborative interpret / refine loop
// ---------------------------------------------------------------------------

/** The AI's evolving understanding of the user's thesis (not a generic form). */
export interface ResearchBrief {
  thesis: string;
  interpretation: string;
  scope_notes: string[];          // angles/boundaries the AI inferred FROM the thesis
  exclusions: string[];           // only what the user implied avoiding
  preferences_learned: string[];  // grows from keep/drop signals
}

export interface ClarifyingOption {
  label: string;
  value: string;
}

/** A thesis-specific question the AI asks ONLY when genuinely in doubt. */
export interface ClarifyingQuestion {
  id: string;
  question: string;
  options: ClarifyingOption[];
  allow_multiple: boolean;
  allow_custom: boolean;
}

/** Answer to a clarifying question, sent back on the next interpret call. */
export interface ClarifyingAnswer {
  question: string;
  answer: string;
}

export interface InterpretParams {
  thesis: string;
  brief?: ResearchBrief | null;
  user_reply?: string | null;
  answers?: ClarifyingAnswer[];
}

export interface InterpretResponse {
  brief: ResearchBrief;
  interpretation: string;
  clarifying_questions: ClarifyingQuestion[];
  ready: boolean;
  generated_at: string;
}

export type PickShovelTier = 'direct' | 'enabler' | 'deep';

/** One dropped card carried into refine (ticker + tier + optional reason). */
export interface DroppedCard {
  ticker: string;
  name?: string;
  tier: PickShovelTier;
  reason?: string | null;
}

export interface RefineParams {
  brief: ResearchBrief | null;
  kept: { ticker: string; name: string; tier: PickShovelTier; thesis?: string | null }[];
  dropped: DroppedCard[];
  already_shown: string[];
}

export interface RefineResponse {
  brief: ResearchBrief;
  learning_note: string;
  new_companies: {
    direct?: PickShovelCompany[];
    enabler?: PickShovelCompany[];
    deep?: PickShovelCompany[];
  };
  follow_up_question: ClarifyingQuestion | null;
  generated_at: string;
}

// ---------------------------------------------------------------------------
// Pick & Shovel v2 — structured, filing-grounded research pipeline
// ---------------------------------------------------------------------------

export interface ThemeComponent {
  id: string;
  name: string;
  category: string;
  description: string;
  why_essential: string;
}

export interface DecomposeResponse {
  theme: string;
  theme_title: string;
  summary: string;
  components: ThemeComponent[];
  generated_at: string;
  from_cache?: boolean;
}

export interface EtfHolding {
  ticker: string;
  name: string;
  weight?: number | null;                       // unified (deduped) = AVG across ETFs holding it
  weight_pct?: number | null;                   // single-ETF holdings
  etf_weights?: Record<string, number | null>;  // weight of this holding in each ETF (top-10)
  in_etfs?: string[];
}

export interface EtfInfo {
  ticker: string;
  name: string;
  rationale?: string;
  holding_count?: number;
}

export interface EtfDiscoveryResponse {
  theme: string;
  etfs: EtfInfo[];
  holdings: EtfHolding[];
  generated_at: string;
  from_cache?: boolean;
}

export interface EtfHoldingsResponse {
  ticker: string;
  name: string;
  holdings: EtfHolding[];
  generated_at: string;
}

export type UserInputKind = 'text' | 'link' | 'image';
export interface UserInput {
  type: UserInputKind;
  value: string;   // text content, URL, or base64 data URL
}

export interface IngestSource {
  type: UserInputKind;
  ok: boolean;
  label: string;
  url?: string;
}

export interface IngestResponse {
  suggested_companies: { ticker: string; name: string; why?: string }[];
  suggested_components: ThemeComponent[];
  notes: string;
  sources: IngestSource[];
  generated_at: string;
}

export interface V2Company {
  ticker: string;
  name: string;
  sector?: string | null;
  price?: number | null;
  price_chg_1d?: number | null;
  pe_ratio?: number | null;
  forward_pe?: number | null;
  week52_high?: number | null;
  week52_low?: number | null;
  market_cap?: string | null;
  website?: string | null;
  why?: string | null;
  source?: 'etf' | 'user' | 'ingest' | 'component';
}

export interface ValidateResponse {
  companies: V2Company[];
  generated_at: string;
}

export interface ComponentMatch {
  component_id: string;
  component_name: string;
  companies: { ticker: string; how: string }[];
}

export interface MatchResponse {
  matches: ComponentMatch[];
  unmatched_components: string[];
  new_components?: ThemeComponent[];   // components the matcher created (incl. "Other relevant")
  generated_at: string;
}

export interface DeepDiveRelation {
  name: string;
  ticker?: string | null;
  evidence?: string;
}

export interface HiddenOpportunity {
  insight: string;
  beneficiary?: string;
  why_nonobvious?: string;
}

export interface DeepDiveSource {
  type: string;   // "10-K" | "10-Q" | "8-K" | "8-K EX-99" | "Investor material"
  date?: string;
  url: string;
}

export interface SocialChatter {
  summary: string;
  sentiment: string;   // bullish | bearish | mixed | quiet
  themes: string[];
  mentioned: { name: string; ticker?: string | null }[];
  found: boolean;
  url?: string;
}

export interface CompanyDeepDive {
  ticker: string;
  name: string;
  what_it_does: string;
  segments: string[];
  clients: DeepDiveRelation[];
  suppliers: DeepDiveRelation[];
  hidden_opportunities: HiddenOpportunity[];
  social?: SocialChatter;
  sources: DeepDiveSource[];
  documents_found: boolean;
  model_only: boolean;
  generated_at: string;
  from_cache?: boolean;
}

export interface SummaryGroup {
  title: string;
  kind: string;   // core | suppliers | clients | opportunity
  summary?: string;
  items: { label: string; note?: string }[];
}

export interface RecommendedDeepDive {
  ticker: string;
  name: string;
  why?: string;
}

/** Flat supplier list returned by the per-component "Find Pick & Shovel". */
export interface ComponentPicks {
  theme: string;
  title: string;
  summary: string;
  selection_notes: string;
  suppliers: PickShovelCompany[];   // each tagged supply_kind = documented | inferred
  generated_at: string;
}

export interface ResearchSummary {
  headline: string;
  groups: SummaryGroup[];
  alpha: { insight: string; beneficiaries?: string[]; why?: string }[];
  gaps: string[];
  recommended_deep_dives: RecommendedDeepDive[];
  generated_at: string;
}

// ---------------------------------------------------------------------------
// Tracked Companies
// ---------------------------------------------------------------------------

export interface TrackedCompanyFinancials {
  price?: number | null;
  price_chg_1d?: number | null;
  pe_ratio?: number | null;
  forward_pe?: number | null;
  market_cap?: string | null;
  week52_high?: number | null;
  week52_low?: number | null;
  sector?: string | null;
  refreshed_at?: string | null;
}

export interface TrackedCompanyLLM {
  thesis?: string | null;
  catalysts?: string[] | null;
  revenue_exposure?: string | null;
  earnings_signal?: string | null;
  risk?: string | null;
  supply_chain_role?: string | null;
  hidden_link?: string | null;
  discovery_insight?: string | null;
  why_overlooked?: string | null;
  connection_type?: string | null;
  connection_to?: string | null;
  why_nobody_covers_this?: string | null;
}

export interface TrackedCompany {
  id: number;
  ticker: string;
  name: string;
  theme_raw: string;
  theme_slug: string;
  theme_summary?: string | null;
  exchange?: string | null;
  sector?: string | null;
  source_tier?: string | null;
  depth_level?: number | null;
  user_notes?: string | null;
  llm_data: TrackedCompanyLLM;
  financial_data: TrackedCompanyFinancials;
  last_price_refresh?: string | null;
  created_at: string;
  updated_at: string;
  already_tracked?: boolean;
  status: 'active' | 'on_hold';
  agent_count?: number;
  active_agent_count?: number;
}

export interface TrackedGroup {
  theme_slug: string;
  theme_raw: string;
  theme_summary?: string | null;
  companies: TrackedCompany[];
}

export interface TrackedListResponse {
  groups: TrackedGroup[];
  total: number;
}

export interface AgentScaffold {
  name: string;
  description: string;
  instruction: string;
  schedule_type: string;
  schedule_cron: string | null;
  tracked_company_id?: number;
  mini_agent_type?: string;
}

export interface AgentFromTrackingResponse {
  agent_scaffold: AgentScaffold;
  company: TrackedCompany;
}

export interface MiniAgentTemplate {
  key: string;
  label: string;
  description: string;
  schedule_type: string;
  schedule_cron: string | null;
}

// ===== Debt Radar (entry-timing tracker for debt instruments) =====

export interface DebtSignalMetric {
  label: string;
  value: string | null;
  hint: string | null;
}

export interface DebtSignal {
  key: string;
  family: string;
  score: number | null;
  verdict: string;            // Favorable | Neutral | Unfavorable | N/A
  headline: string;
  metrics: DebtSignalMetric[];
  confidence: string;         // high | medium | low
  weight: number;
}

export interface DebtHolding {
  ticker: string;
  name: string;
  weight_pct: number;
}

export interface DebtClassification {
  instrument_class?: string;
  class_label?: string;
  rate_type?: string;         // floating | fixed
  tier?: string | null;
  name?: string;
  category?: string | null;
  fund_family?: string | null;
  summary?: string;
  asset_classes?: Record<string, number>;
  top_holdings?: DebtHolding[];
  holdings_note?: string | null;
}

export interface DebtLive {
  price: number | null;
  nav: number | null;
  premium_discount_pct: number | null;
  bid: number | null;
  ask: number | null;
  bid_ask_bps: number | null;
  distribution_yield_pct: number | null;
  aum: number | null;
  aum_fmt: string | null;
  expense_ratio_pct: number | null;
  move: number | null;
  move_pct: number | null;
  vix: number | null;
  vix_pct: number | null;
}

export interface DebtTechnical {
  price?: number;
  sma50?: number | null;
  sma200?: number | null;
  pct_above_200dma?: number | null;
  golden_cross?: boolean;
  mom_12_1_pct?: number | null;
  rsi14?: number | null;
}

export interface DebtMacroSnapshot {
  sofr: number | null;
  t1y: number | null;
  t2y: number | null;
  curve_10y2y: number | null;
  curve_10y3m: number | null;
  real_10y: number | null;
  breakeven_10y: number | null;
  oas_aaa_bps: number | null;
  oas_bbb_bps: number | null;
  oas_hy_bps: number | null;
  nfci: number | null;
  sahm: number | null;
}

export interface DebtSpreadPoint {
  date: string;
  value: number;
}

export interface DebtEntryResponse {
  ticker: string;
  as_of: string;
  is_debt: boolean;
  reject_reason: string | null;
  resolved_from?: string | null;   // CUSIP the ticker was resolved from, if any
  classification: DebtClassification;
  live?: DebtLive;
  technical?: DebtTechnical;
  spread_history?: DebtSpreadPoint[];
  composite?: { score: number | null; verdict: string };
  signals?: DebtSignal[];
  macro_snapshot?: DebtMacroSnapshot;
}

export interface DebtExplainResponse {
  ticker: string;
  explanation: string;
}

export interface DebtPricePoint {
  t: string;   // date or HH:MM (1D)
  c: number;   // close
}

export interface DebtRange {
  key: string;
  label: string;
  change_pct: number | null;
  change_abs: number | null;
  series: DebtPricePoint[];
  start_date?: string;
  partial?: boolean;   // fund younger than the requested window
}

export interface DebtDividendPoint {
  date: string;
  amount: number;
}

export interface DebtDividends {
  history: DebtDividendPoint[];
  ttm_total: number | null;
  ttm_yield_pct: number | null;
  frequency: string | null;       // Monthly | Quarterly | ...
  last_amount: number | null;
  last_date: string | null;
  next_ex_date: string | null;
  next_estimated: boolean;
}

export interface DebtHistoryResponse {
  ticker: string;
  currency: string | null;
  current_price: number | null;
  ranges: DebtRange[];
  dividends: DebtDividends;
}

// ===== Derivative Income =====

export interface DerivativeIncomeFlag {
  level: 'good' | 'warn' | 'info';
  text: string;
  scope?: 'common' | 'ticker' | string;
}

export interface DerivativeIncomeLeg {
  action: 'BUY' | 'SELL';
  type: 'CALL' | 'PUT';
  strike: number;
  expiration: string;
  bid: number;
  ask: number;
  mid: number;
  iv: number | null;
  oi: number;
  vol: number;
  prob_reach_pct?: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
}

export interface DerivativeIncomeGreeks {
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
}

export interface DerivativeIncomeOpportunity {
  structure: 'covered_call' | 'cash_secured_put' | 'collar' | 'put_credit_spread' | 'call_credit_spread' | 'iron_condor' | 'jade_lizard' | string;
  label: string;
  expiration: string;
  dte: number;
  short_strike: number;
  short_strike_pct?: number | null;
  long_strike?: number;
  floor_strike?: number;
  width?: number;
  // multi-leg extras (iron condor / jade lizard)
  put_short?: number;
  put_long?: number;
  call_short?: number;
  call_long?: number;
  band_low?: number;
  band_high?: number;
  short_delta: number | null;
  prob_keep_pct: number;
  prob_assign_pct: number;
  prob_in_band_pct?: number | null;
  prob_method: 'RND' | 'BS' | string;
  premium: number;
  premium_per_share: number;
  collateral: number;
  capital_basis?: 'reg_t_margin' | 'covered_stock' | string;   // short put = Reg-T naked margin (BPR)
  notional_capital?: number | null;                            // full cash-secured notional (the dollar RISK)
  premium_annualized_pct: number;
  total_annualized_pct: number;
  sofr_pct?: number;
  sofr_excess_pct: number;
  beats_sofr: boolean;
  static_return_pct: number;
  if_assigned_return_pct?: number;
  breakeven: number;
  cushion_pct: number;
  floor_pct?: number;
  cap_pct?: number;
  max_profit: number | null;
  max_loss: number | null;
  expected_pnl: number | null;
  greeks: DerivativeIncomeGreeks;
  theta_per_day: number;
  vol_spike_pnl?: number | null;
  atm_iv_pct: number | null;
  iv_hv_ratio: number | null;
  premium_richness: string;
  liquidity: { oi: number; volume: number; spread_pct: number | null };
  exercise_style: string;
  confidence?: { score: number; label: 'High' | 'Medium' | 'Low' | string; reasons: string[] };
  flags: DerivativeIncomeFlag[];
  legs: DerivativeIncomeLeg[];
  // portfolio-mode extras
  contracts?: number;
  total_premium?: number;
}

export interface DerivativeIncomeHeston {
  vol_of_vol?: number;
  spot_vol_corr?: number;
  long_run_vol_pct?: number;
  feller_ok?: boolean;
  rmse_vol_pts?: number;
}

export interface DerivativeIncomeQuant {
  rnd_available: boolean;
  svi_rmse_vol_pts: number | null;
  arb_free: boolean | null;
  n_quotes: number | null;
  heston: DerivativeIncomeHeston | null;
  expected_move_pct: number | null;
}

export interface DerivativeIncomeVolStats {
  iv_atm_pct: number | null;
  hv_current_pct: number | null;    // = HV30 (kept for back-compat)
  hv10_pct?: number | null;         // realized vol, 10 trading-day window
  hv20_pct?: number | null;         // realized vol, 20 trading-day window
  hv30_pct?: number | null;         // realized vol, 30 trading-day window (desk baseline for IV/HV)
  har_rv_pct?: number | null;       // HAR-RV forward (~1mo) realized-vol forecast
  iv_vs_har_pts?: number | null;    // implied − HAR forecast (vol pts); + = seller edge
  iv_rank: number | null;
  iv_percentile: number | null;
  vol_rank: number | null;
  vol_percentile: number | null;
  skew_pts: number | null;
  basis: string;
  har_basis?: string;
}

export interface DerivativeIncomeContext {
  spot: number;
  shares_per_contract: number;
  notional_per_contract: number;
  week52: { high: number; low: number; position_pct: number } | null;
  exercise_style: string;
  european: boolean;
  sofr_pct: number;
  sofr_source: string;
  hv30_pct: number | null;
  hv20_pct: number | null;
  next_earnings: string | null;
  vol_stats?: DerivativeIncomeVolStats;
}

export interface DerivativeIncomeExpirySummary {
  expiration: string;
  dte: number;
  monthly: boolean;
  atm_iv_pct: number | null;
  hv30_pct: number | null;
  iv_hv_ratio: number | null;
  premium_richness: string;
  rnd_available: boolean;
  earnings_before_expiry: string | null;
  macro_events: string[];
  quant?: DerivativeIncomeQuant;
  n_opportunities: number;
}

export interface DerivativeIncomeResult {
  ticker: string;
  spot: number;
  context?: DerivativeIncomeContext;
  events?: DerivativeIncomeFlag[];
  as_of: string;
  quote_source: string;
  exercise_style: string;
  european: boolean;
  min_prob_pct: number;
  min_income: number;
  sofr_pct: number;
  sofr_source: string;
  hv30_pct: number | null;
  hv20_pct: number | null;
  next_earnings: string | null;
  expiry_mode: string;
  target_dte: number | null;
  expiry_summaries: DerivativeIncomeExpirySummary[];
  opportunities: DerivativeIncomeOpportunity[];
  best_by_structure: DerivativeIncomeOpportunity[];
  n_opportunities: number;
  note?: string;
  error?: string;
}

export interface DerivativeIncomePortfolioRow {
  ticker: string;
  shares: number;
  cost_basis?: number | null;
  price: number;
  market_value: number;
  best_opportunity: DerivativeIncomeOpportunity | null;
  spot?: number;
  exercise_style?: string;
  next_earnings?: string | null;
  iv_hv_ratio?: number | null;
  note?: string | null;
}

export interface DerivativeIncomePortfolioResult {
  offset: number;
  limit: number;
  total_holdings: number;
  next_offset: number | null;
  has_more: boolean;
  min_prob_pct: number;
  min_income: number;
  expiry_mode: string;
  common_events?: DerivativeIncomeFlag[];
  results: DerivativeIncomePortfolioRow[];
  error?: string;
}

// ===== Desk Review (ticker-level, ranks all trades + Quant→Risk→PM cascade) =====

export interface DeskMetrics {
  trader: {
    net_delta?: number; net_gamma?: number; net_vega?: number; net_theta?: number;
    net_vanna?: number; net_charm?: number; net_volga?: number; avg_iv_pct?: number | null;
  };
  pm: {
    omega?: number | null; sortino?: number | null; calmar?: number | null; pop?: number | null;
    expected_value?: number | null; expected_return_pct?: number | null; kelly_fraction?: number | null;
  };
  risk: { var_95?: number | null; cvar_95?: number | null; max_loss?: number | null; max_profit?: number | null; capital?: number | null };
  quant: {
    score?: number | null; verdict?: string | null; reasons?: string[];
    subscores?: { edge: number; pop: number; sortino: number; tail: number; carry: number };
  };
}

export interface DeskRankedTrade extends DerivativeIncomeOpportunity {
  desk_metrics: DeskMetrics;
  desk_score: number;
  ta_note?: string;
  algo_grade?: string;          // A–F after the full deterministic pre-vet
  approval_odds?: string;       // high | medium | low | auto_reject
  grade_demerits?: string[];    // deterministic marks against the trade
  grade_merits?: string[];
  grade_blocking?: string[];    // hard fails (structurally broken, etc.)
  base_quality?: number;        // the algorithmic_quant base score BEFORE regime/factor adjustments
  grade_adjustments?: { label: string; points: number }[];  // signed option-math contributions → desk_score
  ta_factors?: { label: string; points: number }[];         // signed technical/regime contributions → desk_score
  qp?: {                        // Q-vs-P: implied (risk-neutral) vs physical (realized) read
    implied_vol_pct?: number | null; realized_vol_pct?: number | null; weight_vol_pct?: number | null;
    iv_hv_ratio?: number | null;                     // < 1 = negative VRP (implied under-prices risk)
    implied_move_pct?: number | null;                // ±1σ implied (Q) move to expiry
    physical_move_pct?: number | null;               // ±1σ physical (P) move to expiry
    dual_move_pct?: number | null;                   // the wider of the two
    short_sigmas?: number | null;                    // nearest short strike in DUAL-σ units
    short_dist_pct?: number | null;                  // nearest short strike distance from spot (%)
    physical_wider?: boolean; exposed_physical?: boolean;
    keep_standard_pct?: number | null;               // headline Win% — standard risk-neutral PoP
    keep_drift_pct?: number | null;                  // drift-adjusted Win% (P-measure overlay; display only)
    drift_mu_pct?: number | null;                    // trend velocity: annualized 21-day EMA slope μ (%/yr), applied over DTE as μ×T
    atr_vol_pct?: number | null;                     // ATR-implied (gap-aware) annualized vol
    gap_aware?: boolean;                             // true when ATR-vol > close-to-close HV (gaps present)
  };
  // Strike de-dup: this row is the best-in-band representative; near-adjacent same-structure strikes
  // with a near-identical score are folded in here rather than flooding the ranking.
  nearby_strikes?: { strike: number | null; premium_per_share?: number | null;
                     premium_annualized_pct?: number | null; short_strike_pct?: number | null;
                     desk_score?: number | null }[];
  nearby_count?: number;                             // how many adjacent strikes this row stands in for
  nearby_range?: [number, number];                   // [min, max] strike span it represents
  risk_triggers?: RiskTrigger[];                     // WATCH→DEFEND→EXIT price ladder (TA + geometry)
  event_adjusted_yield_pct?: number | null;          // annualized yield with the earnings/event premium stripped
  event_premium_share?: number | null;               // fraction of the premium that is event (not harvestable) premium
}

// One rung of the tail-risk management plan — a price level + the corrective action to take there.
export interface RiskTrigger {
  side: 'down' | 'up';                               // which exposed side (short puts vs short calls)
  tier: 'watch' | 'defend' | 'exit' | 'cap';         // escalation: monitor → adjust → cut · cap = covered-call upside
  price: number;
  pct_from_spot: number;
  atr_units?: number | null;                         // distance in daily-ATR units (imminence)
  sigma?: number | null;                             // distance in expected-move (σ) units
  action: string;                                    // the corrective action at this level
  basis: string;                                     // the technical level / σ band it's anchored to
  why?: string;                                      // the TA reasoning for THIS level
}

// On-demand live monitoring plan — real advanced-TA structures (order blocks, POCs, liquidity pools,
// gamma flip/walls, swings, VWAP) mapped onto the trade's short strikes. Fetched when the panel opens.
export interface MonitorPlan {
  triggers: RiskTrigger[];                           // watch → defend → exit(/cap), each a REAL named structure
  strike_rationale: string[];                        // why the strikes were chosen (the TA cushion behind them)
  gamma_note?: string | null;                        // dealer gamma-flip backdrop
  regime?: string | null;
  levels_found?: number;
  error?: string;
}

export interface DeskReviewResult {
  ticker: string;
  spot: number;
  sofr_pct: number;
  as_of?: string;
  ta_summary: {
    state?: string; mode?: string; bias?: string; rsi?: number | null; rsi_signal?: string;
    support?: number | null; resistance?: number | null; poc?: number | null;
    value_area?: (number | null)[] | null; trend?: string; bos?: string | null;
  };
  events?: DeskReviewEvent[];
  /** Which technical read scores the trade (e.g. "Medium Term (6mo / 1d)"). */
  ta_timeframe?: string | null;
  /** Set when a requested data source (e.g. IBKR) was unavailable and the scan fell back to Yahoo. */
  data_source_note?: string | null;
  /** Dealer gamma-exposure proxy — long gamma = vol-suppressed (good for selling), short = vol-expansion. */
  gex?: {
    gex_bn?: number; regime?: 'long' | 'short' | string; flip_level?: number | null;
    spot?: number; n_strikes?: number; proxy?: boolean;
  } | null;
  ranked: DeskRankedTrade[];
  algo_top_pick: DeskRankedTrade | null;
  n_trades: number;
  note?: string;
  error?: string;
  // Chrome passthrough (single-ticker one-call render): the same context / expiries / event flags
  // the /derivative-income scan returns, so the header + volatility + events render from this payload.
  context?: DerivativeIncomeContext | null;
  expiry_summaries?: DerivativeIncomeExpirySummary[];
  flag_events?: DerivativeIncomeFlag[];
}

export interface DeskReviewEvent {
  kind: 'earnings' | 'dividend' | 'macro';
  level: 'warn' | 'info' | 'good';
  text: string;
}

export interface DeskAgent {
  role: string;
  title: string;
  verdict: string;
  action_needed: boolean;
  content: string;
  model: string;
  input_context: string;
  system_prompt: string;
}

export interface DeskAgentsResult {
  ticker: string;
  n_trades: number;
  quant: DeskAgent;
  risk: DeskAgent;
  rebuttal: DeskAgent;
  pm: DeskAgent;
  final_recommendation: {
    verdict: string; action_needed: boolean; decision?: string | null;
    rationale?: string | null; consistency?: string | null;
    winning_argument?: string | null; sizing?: string | null; desk_mandate?: string | null;
    quant_choice?: string | null; quant_agrees_with_algo?: string | null;
    rebuttal_stance?: string | null; final_pick?: string | null;
    algo_top_pick?: string | null; risk_verdict?: string | null;
    chosen_index?: number | null;   // index into DeskReviewResult.ranked — the trade to Explore
  };
  error?: string;
}

// ─── Portfolio Optimization (min-vol + HRP) ──────────────────────────────

export interface OptimizeTrade {
  ticker: string;
  action: 'BUY' | 'SELL' | 'HOLD';
  price: number | null;
  current_shares: number | null;
  target_shares: number;
  delta_shares: number | null;
  current_weight: number | null;   // percent
  target_weight: number | null;    // percent
  current_value: number | null;
  target_value: number | null;
}

export interface OptimizeMethodResult {
  method: 'min_volatility' | 'hrp';
  label: string;
  weights: Record<string, number>;   // ticker -> percent
  expected_return: number | null;    // percent
  volatility: number | null;         // percent
  sharpe: number | null;
  trades: OptimizeTrade[];
  leftover_cash: number;
  turnover?: number | null;          // percent of book traded vs. current
  constrained?: boolean;             // whether caps/turnover cost were applied
  adjustments?: string[];            // notes when constraints were relaxed/clamped
}

export interface FrontierPoint {
  volatility: number | null;         // percent
  expected_return: number | null;    // percent
}

export interface OptimizeCurrent {
  weights: Record<string, number>;   // ticker -> percent
  expected_return: number | null;
  volatility: number | null;
  sharpe: number | null;
}

export interface PortfolioOptimization {
  available: boolean;
  reason?: string;
  as_of?: string;
  lookback?: string;
  risk_free_rate?: number;           // percent
  total_value?: number;
  tickers?: string[];
  excluded: { ticker: string; reason: string }[];
  current?: OptimizeCurrent;
  targets?: Partial<Record<'min_volatility' | 'hrp', OptimizeMethodResult>>;
  frontier?: FrontierPoint[];
  markers?: { max_sharpe?: FrontierPoint; min_volatility?: FrontierPoint };
  settings?: {
    max_weight: number | null;         // percent
    sector_max: number | null;         // percent
    transaction_cost_bps: number;
    l2_gamma: number;
    sectors_available: boolean;
  };
}

// ─── Multi-Agent Debate (iterative Bull ↔ Bear, refereed by Judge) ───────

export interface DebateFiling {
  form: string | null;
  date: string | null;
  url: string | null;
}

export interface DebateAnchors {
  price: number | null;
  forward_pe: number | null; trailing_pe: number | null; peg: number | null;
  forward_eps: number | null; trailing_eps: number | null;
  consensus_eps_growth_pct: number | null; revenue_growth_pct: number | null;
  dcf_fair_value: number | null; dcf_upside_pct: number | null;
  dcf_low: number | null; dcf_high: number | null; dcf_implied_growth: number | null;
  analyst_mean: number | null; analyst_high: number | null; analyst_low: number | null;
  analyst_mean_upside_pct: number | null; analyst_high_upside_pct: number | null; analyst_low_upside_pct: number | null;
  analyst_count: number | null; market_cap: number | null;
}

// --- Structural valuation: driver-claims priced through a P&L (valuation_engine) ---
export type ClaimDriver = 'revenue' | 'margin' | 'buyback' | 'other' | 'multiple';
export type EvidenceTier = 'E1' | 'E2' | 'E3' | 'E4' | 'E5';
export type ClaimVerdict = 'keep' | 'haircut' | 'reject' | 'unreviewed';

export interface StructuralClaim {
  id: string;
  driver: ClaimDriver;
  magnitude: number;             // native unit: rev frac; margin bps; share frac; $; P/E pts
  tier: EvidenceTier;            // final tier (after Judge adjudication)
  cite?: string;                 // the specific figure + form the claim rests on
  source_url?: string | null;    // EDGAR link inferred from the cited form
  label: string;
  source?: 'bull' | 'bear';
  unanswered?: boolean;
  persistence_nudge?: number;
  verdict?: ClaimVerdict;        // Judge's ruling
  rejected?: boolean;
  judge_reason?: string;         // why the Judge kept/haircut/rejected it
}

export interface BearRebuttal { target: string; counter: string; severity?: string; }

export interface ClaimAdjudication {
  id: string; verdict: ClaimVerdict; tier_final?: string; unanswered?: boolean; reason?: string;
}

export interface StructuralWaterfallStep {
  label: string; driver: string; tier: string; survival: number; eps_delta: number;
}

export interface MultipleBreakdown {
  quality_base: number; op_margin: number; sustainable_growth_pct: number | null;
  own_hist_pe: number | null; warranted: number; formula: string;
}

export interface ConfidenceFactors {
  dispersion: number; coverage?: number; evidence_quality: number;
  band_agreement: number; n_surviving: number; formula: string;
}

export interface StructuralValuation {
  base_eps: number;
  eps: number;                   // forward EPS after surviving claims
  growth_pct: number;            // near-term (survival-adjusted) revenue growth
  sustainable_growth_pct: number;// durable growth feeding the multiple
  multiple: number;              // warranted forward P/E
  target: number;
  range: [number, number];       // [bear, bull]
  scenarios: { bear: number; base: number; bull: number };
  confidence: number;            // 0..1, code-derived
  confidence_factors?: ConfidenceFactors;
  multiple_breakdown?: MultipleBreakdown;
  price?: number;
  upside_pct?: number;
  waterfall: StructuralWaterfallStep[];
  claims: StructuralClaim[];
}

export interface DebateRound {
  round: number;
  user_input?: string;   // present on rounds the user triggered by answering open questions
  bull: { argument: string; new_argument: boolean; claims: StructuralClaim[] };
  bear: { argument: string; new_argument: boolean; rebuttals: BearRebuttal[] };
  judge: {
    adjudication: ClaimAdjudication[];
    rationale: string;
    open_questions?: string[];
    new_information?: boolean;
    should_continue?: boolean;
    parse_error?: boolean;
    raw?: string;
  };
  valuation?: StructuralValuation | null;   // code-priced result for this round
  upside_pct?: number | null;
  confidence?: number | null;
}

export interface StructuralBase {
  base_eps: number; price: number; op_margin: number; inc_margin: number;
  leverage_source: string; leverage_r2: number;
  own_hist_pe: number | null; trailing_growth: number;
}

export interface AgentDebateResult {
  available: boolean;
  saved?: boolean;
  ticker: string;
  company_name?: string;
  model?: string;
  generated_at?: string;
  evidence?: {
    documents_found: boolean;
    filings: DebateFiling[];
    macro: Record<string, number>;
  };
  prompts?: { bull: string; bear: string; judge: string };
  anchors?: DebateAnchors;
  structural_base?: StructuralBase | null;
  structural_valuation?: StructuralValuation | null;
  rounds?: DebateRound[];
  conclusion?: {
    view_return: number | null;
    base_confidence: number | null;
    view_source?: 'structural' | 'none';
    target_price?: number | null;
    rationale: string;
    open_questions?: string[];
    rounds: number;
    stop_reason: string;
    parse_error: boolean;
  };
  settings?: { max_rounds: number; confidence_target: number };
}
