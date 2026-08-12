import React, { useEffect, useState } from 'react';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement,
  Title, Tooltip, Legend, Filler,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import {
  Droplets, Loader2, RefreshCw, Sparkles, AlertCircle, ChevronDown,
  Landmark, Gauge, Percent, ShieldAlert, Activity, Globe2, Info,
  Compass, TrendingUp, TrendingDown, Minus,
} from 'lucide-react';
import { fetchLiquidityDashboard, fetchLiquidityNarrative } from '../api';
import type {
  MacroLiquidityResponse, MacroDataPoint, MacroIndicator, MacroSignal,
  MacroRegimeQuadrant, MacroRegimeComponent,
} from '../types';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend, Filler);

// ===========================================================================
// Static indicator metadata — name + educational brief (what / how to read /
// market impact). The backend supplies only the live values, signals & series.
// ===========================================================================

interface Brief { what: string; howToRead: string; impact: string; }
interface IndMeta { name: string; brief: Brief; }

const INDICATOR_META: Record<string, IndMeta> = {
  net_liquidity: {
    name: 'Net Fed Liquidity',
    brief: {
      what: "Proxy for the cash actually circulating in the financial system: the Fed's balance sheet (WALCL) minus the Treasury's checking account (TGA) and cash parked at the overnight reverse-repo facility (RRP). The Fed buying assets adds cash; Treasury debt issuance filling the TGA, or banks parking cash in RRP, drains it.",
      howToRead: 'Rising = liquidity being added to markets. Falling = liquidity draining. Watch the slope, not just the level — and flag divergences where SPY rises while net liquidity falls (often unsustainable).',
      impact: 'The single most-watched liquidity gauge for equities since 2020. Expanding liquidity tends to lift risk assets and compress spreads; contraction is a headwind that often precedes volatility.',
    },
  },
  reserves: {
    name: 'Bank Reserves',
    brief: {
      what: 'Total reserves commercial banks hold at the Fed (WRESBAL) — the cleanest measure of banking-system liquidity, and a more direct read than net liquidity on whether the plumbing is flush or scarce.',
      howToRead: "Above ~$3T is generally 'abundant'. Reserves falling toward the 'ample minimum' is the warning sign — the level where funding markets seized in Sept 2019. Falling reserves = quantitative tightening (QT) biting.",
      impact: 'Scarce reserves force banks to compete for funding, spiking repo rates and forcing risk reduction. The Fed slows or stops QT when reserves approach scarcity.',
    },
  },
  sofr: {
    name: 'SOFR (Funding Rate)',
    brief: {
      what: 'Secured Overnight Financing Rate — what banks pay to borrow cash overnight against Treasuries. The LIBOR replacement and the heartbeat of money-market funding.',
      howToRead: "Should sit just below the Fed's policy rate. Sudden spikes above the Fed Funds upper bound signal a funding squeeze / reserve scarcity. Watch quarter- and month-ends for collateral pressure.",
      impact: 'A clean, persistent SOFR spike is an early warning of liquidity stress that can force Fed intervention (standing repo facility). Usually quiet — when it isn\'t, pay attention.',
    },
  },
  nfci: {
    name: 'Chicago Fed NFCI',
    brief: {
      what: "A weekly composite of 105 measures across money, debt, equity markets and the banking system — the Fed's own broad gauge of US financial conditions (Risk, Credit and Leverage subcomponents).",
      howToRead: 'Zero = average conditions. Negative = looser than average (easy money, calm markets). Positive = tighter than average (stress). Direction matters: a negative-but-rising print means conditions are tightening at the margin.',
      impact: 'Tightening financial conditions slow the economy with a lag and pressure risk assets. One of the most reliable cross-checks on whether the liquidity backdrop is genuinely supportive.',
    },
  },
  anfci: {
    name: 'Adjusted NFCI',
    brief: {
      what: "The NFCI stripped of the part explained by the current state of the economy — i.e. financial conditions relative to where growth and inflation 'should' put them.",
      howToRead: 'Same scale as NFCI (negative = loose). A positive ANFCI alongside a negative NFCI warns that conditions are looser than the economy can sustain — a setup for future tightening.',
      impact: 'Better than raw NFCI at flagging when policy is too loose or too tight for conditions. Watched as a forward-looking stress signal.',
    },
  },
  dxy: {
    name: 'US Dollar Index (DXY)',
    brief: {
      what: "The dollar's value against a basket of major currencies (euro, yen, pound, etc.). The world's reserve-currency thermometer and a master valve for global liquidity.",
      howToRead: 'Rising dollar = tightening global financial conditions (dollar debt harder to service, capital flees risk). Falling dollar = easing, risk-on. Trades broadly inverse to equities and commodities.',
      impact: 'A sharply rising DXY is a classic headwind for stocks, EM and commodities; a falling dollar usually accompanies equity rallies and lifts multinationals\' earnings.',
    },
  },
  curve_2s10s: {
    name: '2s10s Yield Curve',
    brief: {
      what: 'The 10-year Treasury yield minus the 2-year. The most famous recession indicator — how much more (or less) investors demand to lend long vs. short.',
      howToRead: 'Positive = normal upward-sloping curve. Negative (inverted) = market expects rate cuts / slowdown. Critically, recessions historically arrive as the curve RE-steepens out of inversion, not at the moment of inversion.',
      impact: 'Inversion has preceded every modern US recession (with long, variable lags). A bull-steepener driven by falling front-end yields signals the market pricing imminent Fed easing — often into a slowdown.',
    },
  },
  curve_3m10s: {
    name: '3m10s Yield Curve',
    brief: {
      what: "10-year yield minus the 3-month T-bill. The New York Fed's preferred recession-model input — many consider it more reliable than 2s10s.",
      howToRead: 'Same logic: positive = normal, negative = inverted/recession warning. Because the 3-month tracks the policy rate closely, this curve reflects current Fed stance vs. growth expectations directly.',
      impact: "Drives the NY Fed's recession-probability model. Sustained inversion is among the highest-conviction macro warning signals.",
    },
  },
  real_yield_10y: {
    name: '10Y Real Yield (TIPS)',
    brief: {
      what: "The yield on 10-year inflation-protected Treasuries — the 'real' (after-inflation) risk-free rate and the true discount rate for all long-duration assets.",
      howToRead: 'Rising real yields = tighter conditions, a discount-rate headwind that compresses equity multiples (especially growth/tech) and pressures gold. Falling real yields = a tailwind for risk and duration.',
      impact: 'The master valuation variable. The 2022 equity drawdown was largely a re-rating to higher real yields. Watch the level (>2% is restrictive) and the speed of change.',
    },
  },
  inflation_5y5y: {
    name: '5y5y Forward Inflation',
    brief: {
      what: "The market's expected average inflation over the five years starting five years from now (from inflation swaps/breakevens). The Fed's favored gauge of whether inflation expectations are 'anchored'.",
      howToRead: 'Anchored around ~2.0–2.5% is healthy. A sustained move above ~2.6% warns expectations are un-anchoring (forces hawkish policy); a slide below ~1.8% signals deflation/growth fears.',
      impact: "Un-anchored expectations are the Fed's nightmare and force tighter-for-longer policy. A stable 5y5y gives the Fed room to cut into weakness.",
    },
  },
  hy_oas: {
    name: 'High-Yield Credit Spread',
    brief: {
      what: 'The ICE BofA US High-Yield Option-Adjusted Spread — the extra yield investors demand to hold junk-rated corporate bonds over Treasuries. The market\'s premier credit fear gauge.',
      howToRead: 'Below ~3.5% = complacent/risk-on; 3.5–5% = normal; above ~5% = stress building; above ~7–8% = recessionary/crisis pricing. Rising fast matters more than the absolute level.',
      impact: 'Credit leads equities. Widening HY spreads signal investors pricing default risk and pulling back — a reliable, often-early bearish signal for stocks. Tight, stable spreads underpin risk-on regimes.',
    },
  },
  ig_oas: {
    name: 'Investment-Grade Spread',
    brief: {
      what: 'The same option-adjusted spread for investment-grade (BBB and above) corporate bonds — the funding cost for blue-chip America.',
      howToRead: 'Below ~1.0% = easy credit; above ~1.5% = meaningful stress for high-quality borrowers. IG widening is a higher bar than HY — when even IG cracks, stress is broad.',
      impact: 'IG spreads gate corporate borrowing costs and buybacks. Widening IG is a more systemic warning than HY alone; it means even safe credit is repricing.',
    },
  },
  hy_ig_spread: {
    name: 'HY − IG Differential',
    brief: {
      what: 'High-yield spread minus investment-grade spread — the compensation specifically for taking junk vs. quality credit risk. A pure read on credit risk appetite.',
      howToRead: 'Widening = investors fleeing the riskiest credit toward quality (risk-off within credit). Compressing = reaching for yield, risk-on. Cuts through the all-in level to show relative risk appetite.',
      impact: 'A clean gauge of how much the market rewards/punishes risk-taking in credit. Early widening here, even with tame headline spreads, can foreshadow broader risk-off.',
    },
  },
  vix: {
    name: 'VIX (Equity Vol)',
    brief: {
      what: "The CBOE Volatility Index — expected 30-day S&P 500 volatility implied by options pricing. The original 'fear gauge'.",
      howToRead: 'Below 15 = complacency/calm; 15–20 = normal; 20–30 = elevated/nervous; above 30 = fear; spikes above 40 = panic (often contrarian buys). Low and falling supports risk.',
      impact: 'Spiking VIX accompanies selloffs and triggers systematic de-risking (vol-target funds, risk parity). Extreme spikes are frequently exhaustion signals that mark short-term bottoms.',
    },
  },
  move: {
    name: 'MOVE (Bond Vol)',
    brief: {
      what: "The 'VIX of bonds' — implied volatility of US Treasury options. How turbulent the rates market expects to be, and the best single gauge of bond-market stress.",
      howToRead: 'Below ~90 = calm; 90–120 = normal; above ~120 = bond-market stress. Treasuries are the system\'s collateral, so rates vol drives margin requirements and liquidity everywhere.',
      impact: 'Elevated MOVE tightens conditions by raising hedging costs and collateral haircuts. Bond vol often leads equity vol — MOVE rising while VIX stays low is a classic warning divergence.',
    },
  },
  vix_term: {
    name: 'VIX Term Structure',
    brief: {
      what: 'The ratio of spot VIX to 3-month VIX (VIX ÷ VIX3M). Captures the shape of the volatility curve — whether near-term fear exceeds longer-term expectations.',
      howToRead: 'Below 1.0 = contango (normal: near-term calm, healthy risk appetite). Above 1.0 = backwardation (acute near-term fear) — a genuine stress signal and, at extremes, a contrarian buy.',
      impact: 'Backwardation forces vol-sellers to cover and signals real dislocation. A steep, persistent contango (well below 1) is one of the most reliable risk-on green lights.',
    },
  },
  copper_gold: {
    name: 'Copper/Gold Ratio',
    brief: {
      what: "Copper (the industrial 'growth' metal) divided by gold (the 'fear' metal), ×1000. A cross-asset proxy for global growth expectations and a known leading indicator for the 10-year yield.",
      howToRead: 'Rising = growth optimism / reflation (copper bid, gold lagging) — typically rises with bond yields and cyclicals. Falling = growth scare / flight to safety. Watch for divergence vs. the 10Y yield.',
      impact: "Quant desks use copper/gold to confirm or fade the bond market's growth signal. A rising ratio supports cyclicals and risk-on; a falling ratio warns of slowdown before it shows in equities.",
    },
  },
  gold: {
    name: 'Gold',
    brief: {
      what: 'Spot gold — the premier monetary hedge against currency debasement, real-rate moves and geopolitical/financial tail risk.',
      howToRead: 'Context-dependent: gold rising with falling real yields = monetary-easing trade; rising DESPITE higher real yields = debasement/safe-haven demand (a notable regime). Falling = real-yield headwind or risk-on rotation.',
      impact: 'A persistent bid in gold against rising real yields signals deep distrust of fiat/fiscal sustainability — a structurally important macro tell, not just a hedge.',
    },
  },
  oil: {
    name: 'Crude Oil (WTI)',
    brief: {
      what: 'WTI crude — the marginal cost of energy and a direct input to headline inflation and corporate margins.',
      howToRead: 'Rising oil = inflationary impulse and a tax on consumers (can force hawkish policy); falling oil = disinflationary relief, but a collapse is a demand/growth warning. Context decides good vs. bad.',
      impact: 'Oil spikes have preceded most recessions by squeezing consumers and forcing tightening. Falling oil eases inflation and gives the Fed room — unless it signals demand destruction.',
    },
  },
  usdjpy: {
    name: 'USD/JPY (Carry)',
    brief: {
      what: "The dollar-yen rate — the world's primary carry-trade and funding-currency pair, and a sensitive barometer of risk appetite and rate differentials.",
      howToRead: 'Rising (yen weakening) generally tracks risk-on and wide US-Japan rate differentials. But a sharp yen STRENGTHENING (USD/JPY falling fast) can signal a carry-trade unwind — a violent risk-off event (cf. Aug 2024).',
      impact: 'The yen funds a huge share of global leverage. A disorderly yen rally forces deleveraging across risk assets worldwide — one of the most important and underwatched tail risks.',
    },
  },
};

// Cross-asset series shown for context only (no directional risk signal).
const INFO_ONLY = new Set(['gold', 'oil', 'usdjpy']);

interface Pillar { id: string; title: string; icon: JSX.Element; ids: string[]; }
const PILLARS: Pillar[] = [
  { id: 'funding',    title: 'Fed Liquidity & Funding', icon: <Landmark className="w-4 h-4" />,   ids: ['net_liquidity', 'reserves', 'sofr'] },
  { id: 'conditions', title: 'Financial Conditions',    icon: <Gauge className="w-4 h-4" />,      ids: ['nfci', 'anfci', 'dxy'] },
  { id: 'rates',      title: 'Rates & Yield Curve',     icon: <Percent className="w-4 h-4" />,    ids: ['curve_2s10s', 'curve_3m10s', 'real_yield_10y', 'inflation_5y5y'] },
  { id: 'credit',     title: 'Credit Risk',             icon: <ShieldAlert className="w-4 h-4" />,ids: ['hy_oas', 'ig_oas', 'hy_ig_spread'] },
  { id: 'vol',        title: 'Volatility & Fear',       icon: <Activity className="w-4 h-4" />,   ids: ['vix', 'move', 'vix_term'] },
  { id: 'crossasset', title: 'Cross-Asset Signals',     icon: <Globe2 className="w-4 h-4" />,     ids: ['copper_gold', 'gold', 'oil', 'usdjpy'] },
];

// ===========================================================================
// Formatting helpers
// ===========================================================================

const fmtValue = (v: number | null, unit: string): string => {
  if (v == null) return '—';
  switch (unit) {
    case '$B':    return `$${Math.round(v).toLocaleString()}B`;
    case '$T':    return `$${v.toFixed(2)}T`;
    case '%':     return `${v.toFixed(2)}%`;
    case 'bps':   return `${Math.round(v)} bps`;
    case 'index': return v.toFixed(Math.abs(v) < 5 ? 3 : 2);
    case 'level': return v.toFixed(2);
    case 'ratio': return v.toFixed(3);
    case 'x1000': return v.toFixed(2);
    case '$':     return `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
    case 'fx':    return `¥${v.toFixed(2)}`;
    default:      return v.toFixed(2);
  }
};

const fmtChange = (ch: number | null, mode: string, unit: string): string => {
  if (ch == null) return '—';
  const sign = ch > 0 ? '+' : '';
  if (mode === 'pct') return `${sign}${ch.toFixed(2)}%`;
  if (unit === 'bps') return `${sign}${Math.round(ch)} bps`;
  if (unit === '%')   return `${sign}${ch.toFixed(2)} pp`;
  return `${sign}${ch.toFixed(2)}`;
};

const signalColor = (s: MacroSignal): string =>
  s === 'bullish' ? 'text-success' : s === 'bearish' ? 'text-error' : 'text-base-content/50';

const signalLabel = (s: MacroSignal, info: boolean): string =>
  info ? 'Context' : s === 'bullish' ? 'Risk-On' : s === 'bearish' ? 'Risk-Off' : 'Neutral';

const signalBadgeClass = (s: MacroSignal, info: boolean): string => {
  if (info) return 'border-white/15 text-base-content/40';
  if (s === 'bullish') return 'border-success/40 text-success bg-success/10';
  if (s === 'bearish') return 'border-error/40 text-error bg-error/10';
  return 'border-white/15 text-base-content/50';
};

// Change text is kept tone-neutral on purpose: whether a move is "good" is
// indicator-specific (falling VIX is bullish, falling liquidity is bearish), so
// the signal badge carries direction rather than the raw delta's sign.
const changeTone = (ch: number | null): string =>
  ch == null ? 'text-base-content/40' : 'text-base-content/70';

// ===========================================================================
// Inline SVG sparkline
// ===========================================================================

function Sparkline({ pts, signal, info }: { pts: MacroDataPoint[]; signal: MacroSignal; info: boolean }) {
  if (!pts || pts.length < 2) return <div className="h-8 w-full" />;
  const vals = pts.map(p => p.value);
  const min = Math.min(...vals), max = Math.max(...vals);
  const range = max - min || 1;
  const W = 200, H = 32;
  const step = W / (vals.length - 1);
  const d = vals
    .map((v, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(1)},${(H - ((v - min) / range) * H).toFixed(1)}`)
    .join(' ');
  const stroke = info ? 'rgba(148,163,184,0.7)'
    : signal === 'bullish' ? 'rgba(34,197,94,0.9)'
    : signal === 'bearish' ? 'rgba(239,68,68,0.9)'
    : 'rgba(148,163,184,0.7)';
  return (
    <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="overflow-visible">
      <path d={d} fill="none" stroke={stroke} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// ===========================================================================
// Indicator card (expandable brief)
// ===========================================================================

function IndicatorCard({ id, data }: { id: string; data: MacroIndicator }) {
  const [open, setOpen] = useState(false);
  const meta = INDICATOR_META[id];
  const info = INFO_ONLY.has(id);
  if (!meta) return null;

  return (
    <div className="rounded-2xl border border-white/[0.06] bg-base-100/60 overflow-hidden">
      <div className="p-4">
        <div className="flex items-start justify-between gap-2 mb-1">
          <div className="text-xs text-base-content/55 font-medium leading-snug flex-1">{meta.name}</div>
          <span className={`shrink-0 text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${signalBadgeClass(data.signal, info)}`}>
            {signalLabel(data.signal, info)}
          </span>
        </div>

        <div className="text-xl font-bold tabular-nums mb-2">{fmtValue(data.value, data.unit)}</div>

        <div className="h-8 w-full mb-2">
          <Sparkline pts={data.series} signal={data.signal} info={info} />
        </div>

        <div className="flex items-center gap-2">
          {([['5D', data.change_5d], ['20D', data.change_20d]] as [string, number | null][]).map(([label, val]) => (
            <div key={label} className={`flex items-center gap-1.5 rounded-lg bg-white/[0.04] px-2 py-1 text-[11px] ${changeTone(val)}`}>
              <span className="text-[9px] font-bold uppercase tracking-wide text-base-content/35">{label}</span>
              <span className="tabular-nums font-semibold">{fmtChange(val, data.change_mode, data.unit)}</span>
            </div>
          ))}
        </div>

        <button
          onClick={() => setOpen(o => !o)}
          className="mt-3 flex items-center gap-1 text-[11px] text-base-content/50 hover:text-primary transition-colors"
        >
          <Info className="w-3 h-3" />
          {open ? 'Hide brief' : 'How to read this'}
          <ChevronDown className={`w-3 h-3 transition-transform ${open ? 'rotate-180' : ''}`} />
        </button>
      </div>

      {open && (
        <div className="px-4 pb-4 pt-1 space-y-2 text-[11px] leading-relaxed border-t border-white/[0.05] bg-base-100/40">
          <div>
            <span className="font-semibold text-base-content/70">What it is — </span>
            <span className="text-base-content/60">{meta.brief.what}</span>
          </div>
          <div>
            <span className="font-semibold text-base-content/70">How to read — </span>
            <span className="text-base-content/60">{meta.brief.howToRead}</span>
          </div>
          <div>
            <span className="font-semibold text-base-content/70">Market impact — </span>
            <span className="text-base-content/60">{meta.brief.impact}</span>
          </div>
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// Regime gauge
// ===========================================================================

function RegimeGauge({ regime }: { regime: MacroLiquidityResponse['regime'] }) {
  const pct = Math.max(0, Math.min(100, (regime.score + 100) / 2));
  const labelColor = regime.score > 20 ? 'text-success' : regime.score < -20 ? 'text-error' : 'text-warning';
  return (
    <div className="rounded-2xl border border-white/[0.07] bg-base-100/50 p-4">
      <div className="flex items-start justify-between flex-wrap gap-2 mb-4">
        <div>
          <div className="flex items-center gap-1.5 mb-0.5">
            <Gauge className="w-3.5 h-3.5 text-base-content/40" />
            <span className="text-[10px] font-bold uppercase tracking-widest text-base-content/40">Composite Liquidity Regime</span>
          </div>
          <div className={`text-2xl font-bold leading-none ${labelColor}`}>{regime.label}</div>
        </div>
        <div className="text-right">
          <div className="text-[10px] text-base-content/40 mb-1 uppercase tracking-wide">Signal breakdown</div>
          <div className="flex items-center gap-3 text-xs">
            <span className="flex items-center gap-1 text-success/80"><span className="w-1.5 h-1.5 rounded-full bg-success inline-block" />{regime.bullish} risk-on</span>
            <span className="flex items-center gap-1 text-error/80"><span className="w-1.5 h-1.5 rounded-full bg-error inline-block" />{regime.bearish} risk-off</span>
            <span className="flex items-center gap-1 text-base-content/40"><span className="w-1.5 h-1.5 rounded-full bg-base-content/25 inline-block" />{regime.neutral} neutral</span>
          </div>
        </div>
      </div>
      <div className="relative h-2 rounded-full overflow-hidden"
        style={{ background: 'linear-gradient(90deg, rgba(239,68,68,0.5) 0%, rgba(251,191,36,0.5) 50%, rgba(34,197,94,0.5) 100%)' }}>
        <div
          className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-3 h-4 bg-white rounded-full shadow-lg ring-2 ring-white/30 transition-all"
          style={{ left: `${pct}%` }}
        />
      </div>
      <div className="flex justify-between text-[9px] text-base-content/30 mt-1.5 font-medium uppercase tracking-wider">
        <span>Risk-Off</span><span>Neutral</span><span>Risk-On</span>
      </div>
    </div>
  );
}

// ===========================================================================
// Chart.js charts
// ===========================================================================

const thinLabels = (pts: MacroDataPoint[], n = 12): string[] => {
  if (!pts || pts.length === 0) return [];
  if (pts.length <= n) return pts.map(p => p.date);
  const step = Math.ceil(pts.length / n);
  return pts.map((p, i) => (i % step === 0 || i === pts.length - 1 ? p.date.slice(0, 7) : ''));
};

interface ChartDataset { label: string; pts: MacroDataPoint[]; color: string; axis?: 'y' | 'y1'; fill?: boolean; }

function MultiLineChart({
  title, datasets, dualAxis, zeroLine,
}: { title: string; datasets: ChartDataset[]; dualAxis?: boolean; zeroLine?: boolean }) {
  const base = datasets[0]?.pts ?? [];
  const labels = thinLabels(base, 14);
  const data = {
    labels,
    datasets: datasets.map(ds => ({
      label: ds.label,
      data: ds.pts.map(p => p.value),
      yAxisID: ds.axis ?? 'y',
      borderColor: ds.color,
      backgroundColor: ds.fill ? `${ds.color}18` : 'transparent',
      fill: !!ds.fill,
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.3,
    })),
  };
  const scales: any = {
    x: {
      grid: { color: 'rgba(255,255,255,0.04)' },
      ticks: { color: 'rgba(255,255,255,0.45)', font: { size: 10 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
    },
    y: {
      position: 'left' as const,
      grid: { color: zeroLine ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.04)' },
      ticks: { color: datasets[0]?.color ?? 'rgba(255,255,255,0.6)', font: { size: 10 } },
    },
  };
  if (dualAxis) {
    scales.y1 = {
      position: 'right' as const,
      grid: { drawOnChartArea: false },
      ticks: { color: datasets[1]?.color ?? 'rgba(255,255,255,0.6)', font: { size: 10 } },
    };
  }
  const opts: any = {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: datasets.length > 1, labels: { color: 'rgba(255,255,255,0.6)', font: { size: 11 }, boxWidth: 14 } },
      tooltip: { callbacks: { label: (ctx: any) => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(2)}` } },
    },
    scales,
  };
  return (
    <div className="rounded-2xl border border-white/[0.06] bg-base-100/60 p-4">
      <div className="text-xs font-semibold text-base-content/60 uppercase tracking-wide mb-3">{title}</div>
      <div className="h-60"><Line data={data} options={opts} /></div>
    </div>
  );
}

// ===========================================================================
// Main panel
// ===========================================================================

// ===========================================================================
// Growth × Inflation regime quadrant
// ===========================================================================

function RegimeComponentRow({ c }: { c: MacroRegimeComponent }) {
  const Icon = c.signal === 'accelerating' ? TrendingUp
    : c.signal === 'decelerating' ? TrendingDown : Minus;
  return (
    <div className="flex items-center justify-between gap-2 text-[11px]">
      <span className="text-base-content/70 truncate">{c.label}</span>
      <span className="flex items-center gap-1.5 shrink-0">
        <span className="tabular-nums text-base-content/55">{c.detail ?? '—'}</span>
        <Icon className="w-3 h-3 text-base-content/45" />
      </span>
    </div>
  );
}

function MacroRegimeQuadrantCard({ regime }: { regime: MacroRegimeQuadrant }) {
  const { growth_score: g, inflation_score: i, quadrant, summary, tilt } = regime;
  const x = Math.max(2, Math.min(98, (i + 100) / 2));
  const y = Math.max(2, Math.min(98, (100 - g) / 2));

  const cells = [
    { key: 'Goldilocks',  label: 'Goldilocks' },
    { key: 'Reflation',   label: 'Reflation' },
    { key: 'Slowdown',    label: 'Slowdown' },
    { key: 'Stagflation', label: 'Stagflation' },
  ];
  const activeKey = quadrant.startsWith('Transition') ? null : quadrant;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-base-100/50 p-4">
      <div className="flex items-start justify-between flex-wrap gap-2 mb-4">
        <div>
          <div className="flex items-center gap-1.5 mb-0.5">
            <Compass className="w-3.5 h-3.5 text-base-content/40" />
            <span className="text-[10px] font-bold uppercase tracking-widest text-base-content/40">Growth × Inflation Regime</span>
          </div>
          <div className="text-2xl font-bold leading-none">{quadrant}</div>
        </div>
        <div className="text-right text-[10px] text-base-content/40 tabular-nums space-y-0.5 mt-1">
          <div>Growth <span className={g > 0 ? 'text-success' : 'text-error'}>{g > 0 ? '+' : ''}{g}</span></div>
          <div>Inflation <span className={i > 0 ? 'text-error' : 'text-success'}>{i > 0 ? '+' : ''}{i}</span></div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[240px_1fr] gap-5">
        {/* Quadrant plot */}
        <div>
          <div className="relative w-full max-w-[240px] aspect-square mx-auto rounded-xl border border-white/10 overflow-hidden grid grid-cols-2 grid-rows-2">
            {cells.map(cell => (
              <div
                key={cell.key}
                className={`flex items-center justify-center text-center px-1 text-[10px] uppercase tracking-wide border border-white/[0.04]
                  ${activeKey === cell.key ? 'bg-primary/15 text-primary font-bold' : 'text-base-content/30'}`}
              >
                {cell.label}
              </div>
            ))}
            <div className="absolute inset-y-0 left-1/2 w-px bg-white/10 -translate-x-1/2" />
            <div className="absolute inset-x-0 top-1/2 h-px bg-white/10 -translate-y-1/2" />
            <div
              className="absolute w-3 h-3 rounded-full bg-primary ring-4 ring-primary/25 -translate-x-1/2 -translate-y-1/2"
              style={{ left: `${x}%`, top: `${y}%` }}
            />
          </div>
          <div className="flex justify-between text-[9px] text-base-content/40 mt-1.5 uppercase tracking-wide">
            <span>← disinflation</span><span>inflation →</span>
          </div>
          <div className="text-center text-[9px] text-base-content/40 uppercase tracking-wide">top = growth accelerating</div>
        </div>

        {/* Summary + tilt + component breakdown */}
        <div className="space-y-3">
          <p className="text-sm text-base-content/85 leading-relaxed">{summary}</p>
          <div>
            <div className="text-[10px] font-bold uppercase tracking-wide text-base-content/50 mb-1.5">Favored tilt</div>
            <div className="flex flex-wrap gap-1.5">
              {tilt.map(t => (
                <span key={t} className="text-[10px] px-2 py-0.5 rounded-full border border-primary/30 bg-primary/10 text-primary">{t}</span>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 pt-1">
            <div className="text-[10px] font-bold uppercase tracking-wide text-base-content/50">Growth momentum</div>
            <div className="text-[10px] font-bold uppercase tracking-wide text-base-content/50">Inflation momentum</div>
            <div className="space-y-1">{regime.growth_components.map(c => <RegimeComponentRow key={c.id} c={c} />)}</div>
            <div className="space-y-1">{regime.inflation_components.map(c => <RegimeComponentRow key={c.id} c={c} />)}</div>
          </div>
        </div>
      </div>

      <p className="text-[10px] text-base-content/40 mt-3 leading-relaxed">
        <Info className="w-3 h-3 inline -mt-0.5 mr-1" />
        Each axis is the average momentum (z-score of recent change vs its own 4-year history) across its indicators. The quadrant maps to the asset and sector mix that has historically led in that regime — it drives the sector-rotation tilt, not a timing signal.
      </p>
    </div>
  );
}

export default function LiquidityPanel({ view }: { view?: 'liquidity' | 'regime' }) {
  const [data, setData] = useState<MacroLiquidityResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [narrative, setNarrative] = useState<string | null>(null);
  const [loadingNarrative, setLoadingNarrative] = useState(false);

  const isLiquidity = !view || view === 'liquidity';
  const isRegime = !view || view === 'regime';
  const panelTitle = view === 'liquidity' ? 'Liquidity & Funding'
    : view === 'regime' ? 'Macro Regime & Risk Signals'
    : 'Macro Liquidity & Risk Regime';
  const panelSubtitle = view === 'liquidity'
    ? 'Net Fed Liquidity (WALCL − TGA − RRP), bank reserves and the SOFR funding rate — the plumbing that drives risk-asset expansions and contractions. Data via FRED & Yahoo Finance.'
    : view === 'regime'
    ? 'Growth × Inflation regime classification, financial conditions, yield curve, credit risk, volatility and cross-asset signals. Each card shows a live risk-on / risk-off read. Data via FRED & Yahoo Finance.'
    : 'An institutional cross-section of liquidity, financial conditions, rates, credit, volatility and cross-asset signals. Each card carries a live signal framed risk-on / risk-off for equities. Data via FRED & Yahoo Finance.';
  const PanelIcon = view === 'regime' ? Compass : Droplets;

  const load = async () => {
    setLoading(true); setError(null);
    try { setData(await fetchLiquidityDashboard()); }
    catch (e: any) { setError(e?.message || 'Failed to load liquidity data'); }
    finally { setLoading(false); }
  };

  const getAI = async () => {
    setLoadingNarrative(true);
    try { const r = await fetchLiquidityNarrative(); setNarrative(r.narrative || null); }
    catch (e: any) { setNarrative(`Error: ${e?.message || 'Failed'}`); }
    finally { setLoadingNarrative(false); }
  };

  useEffect(() => { load(); }, []);

  const ind = data?.indicators;
  const comp = data?.components;

  // Detail chart datasets (guarded on presence)
  const get = (id: string) => ind?.[id]?.series ?? [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <PanelIcon className="w-5 h-5 text-primary" />
          <h2 className="text-lg font-semibold">{panelTitle}</h2>
        </div>
        <button onClick={load} disabled={loading} className="btn btn-sm btn-ghost gap-1.5">
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>

      <p className="text-xs text-base-content/50">{panelSubtitle}</p>

      {error && (
        <div className="alert alert-error rounded-2xl">
          <AlertCircle className="w-4 h-4" />
          <span className="text-sm">{error}</span>
          <button onClick={load} className="btn btn-xs btn-ghost gap-1"><RefreshCw className="w-3 h-3" /> Retry</button>
        </div>
      )}

      {loading && !data ? (
        <div className="space-y-3">
          <div className="rounded-2xl border border-white/[0.06] bg-base-100/40 animate-pulse h-20" />
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="rounded-2xl border border-white/[0.06] bg-base-100/40 animate-pulse h-40" />
            ))}
          </div>
        </div>
      ) : data && ind && Object.keys(ind).length > 0 ? (
        <div className="space-y-7">
          {/* Composite regime gauge — liquidity view */}
          {isLiquidity && <RegimeGauge regime={data.regime} />}

          {/* Growth × Inflation regime quadrant — regime view */}
          {isRegime && data.macro_regime && <MacroRegimeQuadrantCard regime={data.macro_regime} />}

          {/* Hero: SPY vs Net Liquidity — liquidity view */}
          {isLiquidity && data.spy.series_price.length > 0 && get('net_liquidity').length > 0 && (
            <MultiLineChart
              title="SPY Price vs Net Fed Liquidity (2Y) — rising liquidity = equity tailwind"
              dualAxis
              datasets={[
                { label: 'SPY ($)', pts: data.spy.series_price, color: 'rgba(99,102,241,1)', axis: 'y' },
                { label: 'Net Liquidity ($B)', pts: get('net_liquidity'), color: 'rgba(34,197,94,1)', axis: 'y1', fill: true },
              ]}
            />
          )}

          {/* Pillars — filter by view: funding only in liquidity, everything else in regime */}
          {PILLARS.filter(p =>
            view === 'liquidity' ? p.id === 'funding'
            : view === 'regime' ? p.id !== 'funding'
            : true
          ).map(pillar => (
            <section key={pillar.id} className="space-y-3">
              <div className="flex items-center gap-2 pb-2 border-b border-white/[0.04]">
                <span className="text-primary/70">{pillar.icon}</span>
                <h3 className="text-xs font-bold uppercase tracking-widest text-base-content/50">{pillar.title}</h3>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                {pillar.ids.map(id => ind[id] ? <IndicatorCard key={id} id={id} data={ind[id]} /> : null)}
              </div>

              {/* Pillar-specific detail charts */}
              {pillar.id === 'rates' && get('curve_2s10s').length > 0 && (
                <MultiLineChart
                  title="Yield Curve Spreads (bps) — below zero = inverted = recession warning"
                  zeroLine
                  datasets={[
                    { label: '2s10s (bps)', pts: get('curve_2s10s'), color: 'rgba(99,102,241,1)' },
                    { label: '3m10s (bps)', pts: get('curve_3m10s'), color: 'rgba(251,191,36,1)' },
                  ]}
                />
              )}
              {pillar.id === 'credit' && get('hy_oas').length > 0 && (
                <MultiLineChart
                  title="Credit Spreads (%) — rising = stress / risk-off"
                  dualAxis
                  datasets={[
                    { label: 'HY OAS (%)', pts: get('hy_oas'), color: 'rgba(239,68,68,1)', axis: 'y', fill: true },
                    { label: 'IG OAS (%)', pts: get('ig_oas'), color: 'rgba(251,191,36,1)', axis: 'y1' },
                  ]}
                />
              )}
              {pillar.id === 'vol' && get('vix').length > 0 && (
                <MultiLineChart
                  title="Equity vs Bond Volatility — MOVE rising while VIX calm is a warning divergence"
                  dualAxis
                  datasets={[
                    { label: 'VIX', pts: get('vix'), color: 'rgba(139,92,246,1)', axis: 'y' },
                    { label: 'MOVE', pts: get('move'), color: 'rgba(244,114,182,1)', axis: 'y1' },
                  ]}
                />
              )}
              {pillar.id === 'crossasset' && get('copper_gold').length > 0 && (
                <MultiLineChart
                  title="Copper/Gold Ratio (×1000) — rising = growth optimism, leads the 10Y yield"
                  datasets={[
                    { label: 'Copper/Gold', pts: get('copper_gold'), color: 'rgba(234,88,12,1)', fill: true },
                  ]}
                />
              )}
              {pillar.id === 'funding' && data.spy.series_obv.length > 0 && (
                <MultiLineChart
                  title="SPY Price vs On-Balance Volume (1Y) — price at new highs while OBV lags = weak conviction"
                  dualAxis
                  datasets={[
                    // Price (2Y series) sliced to OBV's window so both align by trading day.
                    { label: 'SPY ($)', pts: data.spy.series_price.slice(-data.spy.series_obv.length), color: 'rgba(99,102,241,1)', axis: 'y' },
                    { label: 'OBV', pts: data.spy.series_obv, color: 'rgba(56,189,248,1)', axis: 'y1', fill: true },
                  ]}
                />
              )}
            </section>
          ))}

          {/* Net liquidity components note — liquidity view only */}
          {isLiquidity && comp && (
            <div className="text-[11px] text-base-content/45 rounded-xl border border-white/[0.05] bg-base-100/30 p-3">
              <span className="font-semibold text-base-content/60">Net Liquidity breakdown:</span>{' '}
              Fed Balance Sheet (WALCL) {fmtValue(comp.walcl_b, '$B')} − Treasury General Account (TGA) {fmtValue(comp.tga_b, '$B')} − Reverse Repo (RRP) {fmtValue(comp.rrp_b, '$B')}
            </div>
          )}

          {/* AI Briefing — regime view only */}
          {isRegime && <div className="rounded-2xl border border-primary/20 bg-primary/5 p-4">
            <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-primary" />
                <span className="text-xs font-bold uppercase tracking-wide text-primary">AI Macro Strategist Briefing</span>
              </div>
              {!narrative ? (
                <button onClick={getAI} disabled={loadingNarrative} className="btn btn-xs btn-primary gap-1">
                  {loadingNarrative
                    ? <><Loader2 className="w-3 h-3 animate-spin" /> Analyzing…</>
                    : <><Sparkles className="w-3 h-3" /> Get AI Briefing</>}
                </button>
              ) : (
                <button onClick={getAI} disabled={loadingNarrative} className="btn btn-xs btn-ghost gap-1">
                  <RefreshCw className={`w-3 h-3 ${loadingNarrative ? 'animate-spin' : ''}`} /> Refresh
                </button>
              )}
            </div>
            {narrative
              ? <p className="text-sm text-base-content/85 leading-relaxed whitespace-pre-wrap">{narrative}</p>
              : <p className="text-xs text-base-content/40 italic">
                  Generate a 5-section portfolio-committee brief reading the full board — liquidity & funding,
                  conditions & curve, credit & volatility, cross-asset confirmation, and prioritized watch-items.
                </p>
            }
          </div>}

          <div className="text-[10px] text-base-content/30">
            FRED: WALCL · WTREGEN · RRPONTSYD · WRESBAL · SOFR · NFCI · ANFCI · T10Y2Y · T10Y3M · DFII10 · T5YIFR · BAMLH0A0HYM2 · BAMLC0A0CM
            · Yahoo Finance: SPY · DXY · VIX · VIX3M · MOVE · Copper · Gold · Crude · JPY · Not investment advice
          </div>
        </div>
      ) : !loading ? (
        <div className="rounded-2xl border border-white/[0.06] bg-base-100/40 p-6 text-center space-y-2">
          <p className="text-sm text-base-content/60">No indicator data was returned.</p>
          <p className="text-xs text-base-content/40">
            The macro feed (FRED / Yahoo Finance) may be temporarily unavailable, or the response was empty.
          </p>
          <button onClick={load} className="btn btn-xs btn-ghost gap-1 mt-1">
            <RefreshCw className="w-3 h-3" /> Retry
          </button>
        </div>
      ) : null}
    </div>
  );
}
