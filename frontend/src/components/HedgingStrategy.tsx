import React, { useState, useMemo, useEffect, useCallback } from 'react';
import {
  Shield, Loader2, AlertTriangle, Calendar, Percent,
  TrendingUp, TrendingDown, Info, ChevronDown, ChevronUp, Sliders,
  CheckCircle2, XCircle, AlertCircle, Database, Zap, Activity, Layers,
  Save, FolderOpen, Trash2, RotateCcw, Wallet, Edit3, Clock, Landmark,
  Gauge, Compass, TableProperties, Sparkles, Globe, Plus, RefreshCw, Bot,
} from 'lucide-react';
import { Line, Chart as MixedChart } from 'react-chartjs-2';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement,
  BarElement, LineController, BarController, Tooltip as ChartTooltip, Legend as ChartLegend, Filler,
} from 'chart.js';
import {
  computeHedging, getHedgingMarketCheck, getHedgingPriceHistory, fetchSavedStrategies, saveStrategy, updateSavedStrategy, deleteSavedStrategy,
  createAgent,
} from '../api';
import type { SavedStrategyItem } from '../api';
import type {
  HedgingResponse, HedgeStructure, HedgeLeg, HedgeQuote, MarketConditions, IndexOverlay, AgentCreateInput,
  RndCurve, RndSummary, HedgePriceHistory,
} from '../types';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, BarElement,
  LineController, BarController, ChartTooltip, ChartLegend, Filler);
import { useAuth } from '../contexts/AuthContext';
import AgentCreateModal from './AgentCreateModal';
import PreTradeAdvisor, { type AdvisorMetric } from './PreTradeAdvisor';

// ── Editable leg = just the choices the user can change ────────────────────
interface EditLeg { action: 'BUY' | 'SELL'; type: 'CALL' | 'PUT'; strike: number; contracts: number; }

interface Metrics {
  net_cost: number; cost_pct: number; annualized: number; is_credit: boolean; within_budget: boolean;
  floor: number | null; floor_pct: number | null; buffer_bottom: number | null;
  cap: number | null; cap_pct: number | null;
  giveup_from: number | null; participate_above: number | null;
  max_loss: number; max_loss_pct: number | null;
  upside_breakeven: number; covered: number; uncovered: number;
  intrinsic_value: number; time_value: number; theta_per_day: number;
  greeks: { delta: number; gamma: number; theta: number; vega: number };
}

// Plain-English summary built from the *live* metrics, so it tracks strike edits.
function describe(id: string, m: Metrics, spot: number): string {
  const f = m.floor != null ? `$${m.floor.toFixed(2)}` : '—';
  const bb = m.buffer_bottom != null ? `$${m.buffer_bottom.toFixed(2)}` : '—';
  const c = m.cap != null ? `$${m.cap.toFixed(2)}` : '—';
  const pa = m.participate_above != null ? `$${m.participate_above.toFixed(2)}` : '—';
  const bufPct = m.buffer_bottom != null && spot ? `${Math.round((spot - m.buffer_bottom) / spot * 100)}%` : '';
  const giveupPct = m.giveup_from != null && m.participate_above != null && spot
    ? `${Math.round((m.participate_above - m.giveup_from) / spot * 100)}%` : '';
  switch (id) {
    case 'protective_put':
      return `Locks in a worst price of ${f}. You keep all the upside and only pay the premium.`;
    case 'deep_itm_put':
      return `High floor at ${f} with almost no time value to decay — strong, low-bleed protection that ties up more capital.`;
    case 'put_spread':
      return `No loss for the first ${bufPct} the stock falls (down to ${bb}); below that you only lose the part beyond the buffer.`;
    case 'collar':
      return `Protected below ${f}, capped above ${c}. The short call pays for most of the put${m.is_credit ? ' — currently a net credit' : ''}.`;
    case 'buffered_covered_call':
      return `No loss for the first ${bufPct} the stock falls (down to ${bb}); the covered call caps gains at ${c} and pays for the buffer${m.is_credit ? ' — currently a net credit' : ''}.`;
    case 'put_spread_collar':
      return `Forgo the first ${giveupPct} of gains (up to ${pa}) to take zero loss on the first ${bufPct} of the fall (down to ${bb}). ${m.is_credit ? 'Currently a net credit.' : 'Near zero cost.'}`;
    case 'giveup_funded_floor': {
      const floorPct = m.floor != null && spot ? ((spot - m.floor) / spot * 100) : 0;
      return `Protected below ${f} — your ${giveupPct} give-up call spread finances the floor. You absorb the first ${floorPct.toFixed(0)}% loss; participation resumes above ${pa}. ${m.is_credit ? 'Currently a net credit.' : 'Near zero cost.'}`;
    }
    case 'tail_hedge':
      return `Cheap insurance at ${f} that only pays off in a sharp crash.`;
    default:
      return geometryDescribe(m, spot);
  }
}

// Geometry-driven description — composed purely from the live payoff shape, so
// it stays accurate when the user customizes the legs into a non-standard structure.
function geometryDescribe(m: Metrics, spot: number): string {
  const f = m.floor != null ? `$${m.floor.toFixed(2)}` : '';
  const bb = m.buffer_bottom != null ? `$${m.buffer_bottom.toFixed(2)}` : '';
  const c = m.cap != null ? `$${m.cap.toFixed(2)}` : '';
  const pa = m.participate_above != null ? `$${m.participate_above.toFixed(2)}` : '';
  const bufPct = m.buffer_bottom != null && spot ? `${Math.round((spot - m.buffer_bottom) / spot * 100)}%` : '';
  const giveupPct = m.giveup_from != null && m.participate_above != null && spot
    ? `${Math.round((m.participate_above - m.giveup_from) / spot * 100)}%` : '';
  const credit = m.is_credit ? ' — currently a net credit' : '';
  const hasFloor = m.floor != null, hasBuffer = m.buffer_bottom != null;
  const hasCap = m.cap != null, hasGiveup = m.giveup_from != null && m.participate_above != null;
  // A short put below the floor only acts as a zero-loss *buffer* when the long
  // put sits at/above spot. If the long put is below spot it's a *deductible*
  // floor financed by a sold tail put (protected band, then exposed below it).
  const isBufferTop = hasFloor && m.floor! >= spot * 0.99;
  const deductiblePct = hasFloor && spot ? Math.round((spot - m.floor!) / spot * 100) : 0;

  const down =
    isBufferTop && hasBuffer ? `No loss for the first ${bufPct} the stock falls (down to ${bb}); below that losses resume`
    : hasBuffer && hasFloor ? `You take the first ${deductiblePct}% (down to ${f}), are protected from ${f} down to ${bb}, then exposed again below ${bb} where the sold put recovers premium`
    : isBufferTop ? `High floor at ${f} with little time value to decay`
    : hasFloor && m.floor! < spot * 0.85 ? `Cheap insurance at ${f} that only pays off in a sharp drop`
    : hasFloor ? `Floor at ${f} — you take the first ${deductiblePct}%, then you're protected below it`
    : '';
  const up =
    hasGiveup ? `you forgo the first ${giveupPct} of gains (up to ${pa}), then participate again`
    : hasCap ? `gains are capped above ${c}${credit}`
    : (hasFloor && m.floor! >= spot * 0.85) || hasBuffer ? 'you keep all the upside' : '';

  const parts = [down, up].filter(Boolean);
  if (!parts.length) return '';
  const s = parts.join('; ') + '.';
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// Strike-aware US tax considerations, recomputed live from the current legs.
const INDEX_TICKERS = ['SPX', 'XSP', 'NDX', 'RUT', 'VIX', 'DJX', 'OEX', 'GSPC'];
function taxFlags(legs: HedgeLeg[], m: Metrics, ticker: string, spot: number): { category: string; severity: string; note: string }[] {
  const out: { category: string; severity: string; note: string }[] = [];
  const longPut = legs.find((l) => l.type === 'PUT' && l.action === 'BUY');
  const shortCall = legs.find((l) => l.type === 'CALL' && l.action === 'SELL');
  const isIndex = INDEX_TICKERS.includes(ticker.replace('^', '').toUpperCase());

  // Constructive sale (§1259) — only when downside is (near-)fully removed with
  // no tail, i.e. a floor at/above spot AND no buffer below it.
  if (m.floor != null && m.floor >= spot * 0.98 && m.buffer_bottom == null) {
    out.push({ category: 'Constructive Sale (§1259)', severity: 'High',
      note: `The put floor ($${m.floor}) sits at or above today's price ($${spot}), removing nearly all downside. The IRS may treat this as a constructive sale and force you to recognize the stock's gain now.` });
  } else if (m.cap != null && m.floor != null && (m.cap - m.floor) / spot < 0.10) {
    out.push({ category: 'Constructive Sale (§1259)', severity: 'Medium',
      note: `This collar is tight (floor $${m.floor} → cap $${m.cap}, a ${(((m.cap - m.floor) / spot) * 100).toFixed(0)}% band). Very tight collars can be recharacterized as a constructive sale — widen the band to stay clear.` });
  } else {
    out.push({ category: 'Constructive Sale (§1259)', severity: 'Low',
      note: 'The floor is below today\'s price and meaningful risk/reward remains, so constructive-sale treatment is unlikely.' });
  }

  // Straddle rules (§1092)
  if (longPut) {
    out.push({ category: 'Straddle Rules (§1092)', severity: 'Medium',
      note: 'Stock + a protective put is a straddle: losses on one leg are deferred against unrealized gains on the other, and your holding period can be suspended — potentially turning a long-term gain short-term. A put bought the same day you acquired the shares (a "married put") is added to basis instead.' });
  }

  // Qualified covered call (collar)
  if (shortCall) {
    if (shortCall.strike < spot) {
      out.push({ category: 'Qualified Covered Call', severity: 'High',
        note: `The short call ($${shortCall.strike}) is in-the-money, so it is NOT a qualified covered call and can taint the stock's holding period under the straddle rules.` });
    } else {
      out.push({ category: 'Qualified Covered Call', severity: 'Low',
        note: `The short call ($${shortCall.strike}) is out-of-the-money and likely qualifies as a covered call (exempt from straddle treatment) — confirm it meets the strike/term tests.` });
    }
  }

  // Section 1256
  if (isIndex) {
    out.push({ category: 'Section 1256 (60/40)', severity: 'Info',
      note: 'Broad-based index options get 60% long-term / 40% short-term treatment regardless of holding period, plus year-end mark-to-market. Combined with the stock this can be a "mixed straddle" with its own elections.' });
  } else {
    out.push({ category: 'Section 1256 / Index Alternative', severity: 'Info',
      note: `${ticker} options are taxed as ordinary equity options under the full straddle rules. Hedging with broad-based index options (SPX, XSP) instead earns 60/40 treatment — at the cost of basis risk versus your specific holding.` });
  }

  // Qualified dividends
  out.push({ category: 'Qualified Dividends', severity: 'Info',
    note: 'A hedge can break the holding-period test for the lower qualified-dividend rate, pushing dividends earned while hedged to ordinary income.' });

  return out;
}

// ── Pure payoff helpers (at expiration) ────────────────────────────────────
const legIntrinsic = (l: { type: string; strike: number }, p: number) =>
  l.type === 'PUT' ? Math.max(l.strike - p, 0) : Math.max(p - l.strike, 0);
const legPnL = (l: HedgeLeg, p: number) =>
  (l.action === 'BUY' ? 1 : -1) * l.contracts * 100 * (legIntrinsic(l, p) - l.mid);
const hedgePnL = (legs: HedgeLeg[], p: number) => legs.reduce((s, l) => s + legPnL(l, p), 0);

// ── Monte Carlo (lognormal / GBM) for institutional risk metrics ───────────
function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Terminal-price samples from spot using ATM IV over the horizon.
function genTerminalPrices(spot: number, ivPct: number | null, dte: number, n = 8000): Float64Array {
  const iv = (ivPct ?? 25) / 100;
  const T = Math.max(dte, 1) / 365;
  const r = 0.04;
  const drift = (r - 0.5 * iv * iv) * T;
  const vol = iv * Math.sqrt(T);
  const rng = mulberry32(0x9e3779b9);
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const u1 = rng() || 1e-9, u2 = rng();
    const z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
    out[i] = spot * Math.exp(drift + vol * z);
  }
  return out;
}

interface RiskMetrics {
  cvar_hedged: number; cvar_unhedged: number; cvar_reduction: number;
  var_hedged: number; var_unhedged: number;
  prob_pays: number; expected_hedge_pl: number;
}

// VaR/CVaR (95%) of the hedged vs unhedged position + prob the hedge profits.
function hedgeRisk(legs: HedgeLeg[], shares: number, spot: number, samples: Float64Array): RiskMetrics {
  const n = samples.length;
  const hed = new Float64Array(n), unh = new Float64Array(n);
  let paysCount = 0, sumHedge = 0;
  for (let i = 0; i < n; i++) {
    const P = samples[i];
    const u = shares * (P - spot);
    const h = hedgePnL(legs, P);
    unh[i] = u; hed[i] = u + h; sumHedge += h;
    if (h > 0) paysCount++;
  }
  const hs = Float64Array.from(hed); hs.sort();
  const us = Float64Array.from(unh); us.sort();
  const k = Math.max(1, Math.floor(0.05 * n));
  const cvar = (a: Float64Array) => { let s = 0; for (let i = 0; i < k; i++) s += a[i]; return -s / k; };
  const var95 = (a: Float64Array) => -a[k - 1];
  const cvarH = cvar(hs), cvarU = cvar(us);
  return {
    cvar_hedged: cvarH, cvar_unhedged: cvarU, cvar_reduction: cvarU - cvarH,
    var_hedged: var95(hs), var_unhedged: var95(us),
    prob_pays: paysCount / n, expected_hedge_pl: sumHedge / n,
  };
}

function lookupQuote(chain: { puts: HedgeQuote[]; calls: HedgeQuote[] }, type: string, strike: number): HedgeQuote | undefined {
  const arr = type === 'PUT' ? chain.puts : chain.calls;
  return arr.find((q) => q.strike === strike);
}

// Merge an editable leg with its live quote from the chain.
function toFullLeg(e: EditLeg, chain: { puts: HedgeQuote[]; calls: HedgeQuote[] }, expiration: string): HedgeLeg {
  const q = lookupQuote(chain, e.type, e.strike);
  return {
    action: e.action, type: e.type, strike: e.strike, expiration, contracts: e.contracts,
    bid: q?.bid ?? 0, ask: q?.ask ?? 0, mid: q?.mid ?? 0, iv: q?.iv ?? null, oi: q?.oi ?? 0, vol: q?.vol ?? 0,
    delta: q?.delta ?? null, gamma: q?.gamma ?? null, theta: q?.theta ?? null, vega: q?.vega ?? null,
  };
}

// Recompute every headline metric from the legs — mirrors the backend exactly.
function computeMetrics(legs: HedgeLeg[], shares: number, spot: number, dte: number, notional: number, maxCostPct: number): Metrics {
  const longPuts = legs.filter((l) => l.type === 'PUT' && l.action === 'BUY');
  const shortPuts = legs.filter((l) => l.type === 'PUT' && l.action === 'SELL');
  const shortCalls = legs.filter((l) => l.type === 'CALL' && l.action === 'SELL');
  const longCalls = legs.filter((l) => l.type === 'CALL' && l.action === 'BUY');
  const floor = longPuts.length ? Math.max(...longPuts.map((l) => l.strike)) : null;
  const belows = floor != null ? shortPuts.filter((l) => l.strike < floor).map((l) => l.strike) : [];
  const buffer_bottom = belows.length ? Math.max(...belows) : null;
  // Upside: lone short call = hard cap; short call + higher long call = give-up band (no cap).
  let cap: number | null = null, giveup_from: number | null = null, participate_above: number | null = null;
  if (shortCalls.length) {
    const scMin = Math.min(...shortCalls.map((l) => l.strike));
    const higherLong = longCalls.filter((l) => l.strike > scMin).map((l) => l.strike);
    if (higherLong.length) { giveup_from = scMin; participate_above = Math.min(...higherLong); }
    else cap = scMin;
  }
  const coveredRaw = longPuts.reduce((s, l) => s + l.contracts, 0) * 100;
  const covered = coveredRaw || shares;

  const net_cost = legs.reduce((s, l) => s + (l.action === 'BUY' ? 1 : -1) * l.mid * l.contracts * 100, 0);
  const cost_pct = notional ? (net_cost / notional) * 100 : 0;
  const annualized = dte > 0 ? cost_pct * (365 / dte) : cost_pct;
  // Honest worst case: stock to zero, options held to expiry.
  const pnlAt = (p: number) => shares * (p - spot) + legs.reduce((s, l) => {
    const intr = l.type === 'PUT' ? Math.max(l.strike - p, 0) : Math.max(p - l.strike, 0);
    return s + (l.action === 'BUY' ? 1 : -1) * l.contracts * 100 * (intr - l.mid);
  }, 0);
  const max_loss = -pnlAt(0);
  const budget = notional * maxCostPct / 100;

  const greeks = { delta: shares, gamma: 0, theta: 0, vega: 0 };
  let intrinsic_value = 0, time_value = 0;
  for (const l of legs) {
    const sign = l.action === 'BUY' ? 1 : -1;
    const qty = sign * l.contracts * 100;
    greeks.delta += qty * (l.delta ?? 0);
    greeks.gamma += qty * (l.gamma ?? 0);
    greeks.theta += qty * (l.theta ?? 0);
    greeks.vega += qty * (l.vega ?? 0);
    const intr = l.type === 'PUT' ? Math.max(l.strike - spot, 0) : Math.max(spot - l.strike, 0);
    const ext = Math.max(l.mid - intr, 0);
    intrinsic_value += qty * intr;
    time_value += qty * ext;
  }

  return {
    net_cost: Math.round(net_cost * 100) / 100,
    cost_pct: Math.round(cost_pct * 1000) / 1000,
    annualized: Math.round(annualized * 100) / 100,
    is_credit: net_cost < 0,
    within_budget: net_cost <= budget + 1e-6,
    floor: floor != null ? Math.round(floor * 100) / 100 : null,
    floor_pct: floor != null ? Math.round((floor - spot) / spot * 10000) / 100 : null,
    buffer_bottom: buffer_bottom != null ? Math.round(buffer_bottom * 100) / 100 : null,
    cap: cap != null ? Math.round(cap * 100) / 100 : null,
    cap_pct: cap != null ? Math.round((cap - spot) / spot * 10000) / 100 : null,
    giveup_from: giveup_from != null ? Math.round(giveup_from * 100) / 100 : null,
    participate_above: participate_above != null ? Math.round(participate_above * 100) / 100 : null,
    max_loss: Math.round(max_loss * 100) / 100,
    max_loss_pct: notional ? Math.round(max_loss / notional * 10000) / 100 : null,
    upside_breakeven: net_cost > 0 && shares ? Math.round((spot + net_cost / shares) * 100) / 100 : Math.round(spot * 100) / 100,
    covered: Math.round(covered), uncovered: Math.max(0, Math.round(shares - covered)),
    intrinsic_value: Math.round(intrinsic_value), time_value: Math.round(time_value),
    theta_per_day: Math.round(greeks.theta * 100) / 100,
    greeks: {
      delta: Math.round(greeks.delta * 10) / 10, gamma: Math.round(greeks.gamma * 10000) / 10000,
      theta: Math.round(greeks.theta * 100) / 100, vega: Math.round(greeks.vega * 100) / 100,
    },
  };
}

const fmtUSD = (v: number) => `${v < 0 ? '-' : ''}$${Math.abs(Math.round(v)).toLocaleString()}`;

// ── Market-implied probability helpers (RND curve shipped by the backend) ──
// Interpolate P(S_T ≤ spot·(1+pct/100)) from the downsampled CDF.
function probBelowFromCurve(curve: RndCurve, pct: number): number {
  const xs = curve.pct, ys = curve.cdf_pct;
  if (!xs.length) return NaN;
  if (pct <= xs[0]) return ys[0];
  if (pct >= xs[xs.length - 1]) return ys[ys.length - 1];
  let i = 1;
  while (i < xs.length && xs[i] < pct) i++;
  const t = (pct - xs[i - 1]) / (xs[i] - xs[i - 1]);
  return ys[i - 1] + t * (ys[i] - ys[i - 1]);
}

// Market-implied probs for the CURRENT (possibly edited) payoff geometry —
// recomputed live from the curve, so strike edits update the numbers.
function impliedProbs(curve: RndCurve | null | undefined, spot: number, m: Metrics) {
  if (!curve || !spot) return null;
  const pctOf = (lvl: number) => (lvl / spot - 1) * 100;
  const out: { breach?: number; cap?: number; inBand?: number; buffer?: number } = {};
  if (m.floor != null) out.breach = probBelowFromCurve(curve, pctOf(m.floor));
  if (m.cap != null) out.cap = 100 - probBelowFromCurve(curve, pctOf(m.cap));
  if (out.breach != null && out.cap != null) out.inBand = Math.max(0, 100 - out.breach - out.cap);
  if (m.buffer_bottom != null) out.buffer = probBelowFromCurve(curve, pctOf(m.buffer_bottom));
  return out;
}

// ── RND distribution section (lives in the Market Timing panel) ────────────
function RndSection({ rnd, earnings }: { rnd?: RndSummary | null; earnings?: MarketConditions['next_earnings'] }) {
  if (!rnd) return null;
  const pills = [
    { label: 'P(−5%)', v: rnd.p_down_5_pct },
    { label: 'P(−10%)', v: rnd.p_down_10_pct },
    { label: 'P(−20%)', v: rnd.p_down_20_pct },
  ];
  const curve = rnd.curve;
  const chartData = curve ? {
    labels: curve.pct.map((p) => `${p}%`),
    datasets: [
      { data: curve.pdf_per_pct.map((y, i) => (curve.pct[i] <= 0 ? y : null)),
        borderColor: 'rgba(248,114,114,0.9)', backgroundColor: 'rgba(248,114,114,0.16)',
        fill: 'origin', pointRadius: 0, borderWidth: 1.5, tension: 0.3 },
      { data: curve.pdf_per_pct.map((y, i) => (curve.pct[i] >= 0 ? y : null)),
        borderColor: 'rgba(74,222,128,0.9)', backgroundColor: 'rgba(74,222,128,0.12)',
        fill: 'origin', pointRadius: 0, borderWidth: 1.5, tension: 0.3 },
    ],
  } : null;
  const chartOpts = {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: {
      callbacks: { title: (items: { dataIndex: number }[]) => `${curve?.pct[items[0]?.dataIndex ?? 0]}% move`,
        label: (item: { parsed: { y: number | null } }) => `${(item.parsed.y ?? 0).toFixed(2)}% odds per 1% bucket` } } },
    scales: {
      x: { grid: { display: false }, ticks: { maxTicksLimit: 9, color: 'rgba(160,160,180,0.6)', font: { size: 9 } } },
      y: { display: false },
    },
  };
  return (
    <div className="rounded-lg bg-base-100/40 p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap text-[11px]">
        <span className="font-semibold text-secondary flex items-center gap-1"><Activity className="w-3.5 h-3.5" /> Market-implied odds (SVI→RND)</span>
        <span className="text-base-content/40">the probability distribution options are pricing for expiry</span>
        <span className={`ml-auto ${rnd.arb_free ? 'text-success/70' : 'text-warning'}`}>{rnd.arb_free ? 'arb-free' : 'arb flag'} · fit {rnd.svi_rmse_vol_pts} vol-pts</span>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {pills.map((p) => (
          <div key={p.label} className="bg-base-200/40 rounded-md p-2">
            <div className="flex items-baseline justify-between">
              <span className="text-[10px] text-base-content/50">{p.label}</span>
              <span className={`text-sm font-mono font-semibold ${p.v >= 30 ? 'text-error' : p.v >= 15 ? 'text-warning' : 'text-base-content'}`}>{p.v}%</span>
            </div>
            <div className="h-1.5 mt-1 rounded-full bg-base-content/10 overflow-hidden">
              <div className={`h-full rounded-full ${p.v >= 30 ? 'bg-error/70' : p.v >= 15 ? 'bg-warning/70' : 'bg-success/60'}`} style={{ width: `${Math.min(100, p.v)}%` }} />
            </div>
          </div>
        ))}
      </div>
      {chartData && (
        <div className="h-24"><Line data={chartData} options={chartOpts} /></div>
      )}
      <div className="flex items-center gap-3 flex-wrap text-[11px] text-base-content/60">
        <span title="Set your Floor here and the market gives it a 10% (resp. 5%) chance of being breached by expiry">
          Floor for 10% breach risk <span className="font-mono font-medium text-base-content">{rnd.floor_for_10pct_breach_pct}%</span> · 5% <span className="font-mono font-medium text-base-content">{rnd.floor_for_5pct_breach_pct}%</span>
        </span>
        {rnd.heston && (
          <span title={`Heston stochastic-vol fit (RMSE ${rnd.heston.rmse_vol_pts} vol-pts). High vol-of-vol ⇒ gap risk — favor owning protection over short-vol financing.`}>
            · Heston vol-of-vol <span className={`font-mono font-medium ${rnd.heston.vol_of_vol > 1 ? 'text-warning' : 'text-base-content'}`}>{Math.round(rnd.heston.vol_of_vol * 100)}%</span> · spot/vol ρ <span className="font-mono">{rnd.heston.spot_vol_corr}</span>
          </span>
        )}
        {earnings && (
          <span className={earnings.inside_horizon ? 'text-warning font-medium' : 'text-base-content/50'}>
            · <Calendar className="w-3 h-3 inline-block -mt-0.5" /> earnings {earnings.date} ({earnings.days}d){earnings.inside_horizon ? ' — inside your horizon: IV is bid into the print, and collapses after' : ''}
          </span>
        )}
      </div>
    </div>
  );
}

// ── Price & technicals chart (context for choosing the hedge window) ────────
function PriceTechChart({ ph }: { ph: HedgePriceHistory }) {
  const dates = ph.dates ?? [];
  const n = dates.length;
  if (!n) return null;
  const maxVol = Math.max(1, ...(ph.volume ?? [1]));
  const data = {
    labels: dates.map((d) => d.slice(5)),
    datasets: [
      { type: 'line' as const, label: 'Close', data: ph.close ?? [], borderColor: 'rgba(129,140,248,1)',
        pointRadius: 0, borderWidth: 1.8, tension: 0.2, yAxisID: 'y' },
      { type: 'line' as const, label: 'SMA20', data: ph.sma20 ?? [], borderColor: 'rgba(250,204,21,0.65)',
        pointRadius: 0, borderWidth: 1, tension: 0.2, yAxisID: 'y' },
      { type: 'line' as const, label: 'SMA50', data: ph.sma50 ?? [], borderColor: 'rgba(96,165,250,0.65)',
        pointRadius: 0, borderWidth: 1, tension: 0.2, yAxisID: 'y' },
      ...(ph.supports ?? []).map((s) => ({
        type: 'line' as const, label: `Support $${s.level}`, data: Array(n).fill(s.level),
        borderColor: 'rgba(74,222,128,0.5)', borderDash: [6, 4], pointRadius: 0, borderWidth: 1, yAxisID: 'y' })),
      ...(ph.resistances ?? []).map((r) => ({
        type: 'line' as const, label: `Resistance $${r.level}`, data: Array(n).fill(r.level),
        borderColor: 'rgba(248,114,114,0.5)', borderDash: [6, 4], pointRadius: 0, borderWidth: 1, yAxisID: 'y' })),
      { type: 'bar' as const, label: 'Volume', data: ph.volume ?? [], backgroundColor: 'rgba(148,163,184,0.22)', yAxisID: 'y1' },
    ],
  };
  const options = {
    responsive: true, maintainAspectRatio: false, interaction: { mode: 'index' as const, intersect: false },
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { display: false }, ticks: { maxTicksLimit: 8, color: 'rgba(160,160,180,0.6)', font: { size: 9 } } },
      y: { position: 'right' as const, grid: { color: 'rgba(148,163,184,0.08)' }, ticks: { color: 'rgba(160,160,180,0.7)', font: { size: 9 } } },
      y1: { display: false, max: maxVol * 4 },  // squash volume bars to the bottom quarter
    },
  };
  return (
    <div className="h-52">
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      <MixedChart type="bar" data={data as any} options={options as any} />
    </div>
  );
}

// ── Market conditions & hedge-timing panel ─────────────────────────────────
function MarketPanel({ market }: { market: MarketConditions }) {
  const score = Math.round(market.score ?? 50);
  const verdict = market.verdict ?? 'Fair';
  const vColor = verdict === 'Favorable' ? 'text-success' : verdict === 'Expensive' ? 'text-error' : 'text-warning';
  const ring = verdict === 'Favorable' ? 'text-success' : verdict === 'Expensive' ? 'text-error' : 'text-warning';
  const toneText = (t: string) => (t === 'good' ? 'text-success' : t === 'bad' ? 'text-error' : 'text-base-content/60');
  const toneDot = (t: string) => (t === 'good' ? 'bg-success' : t === 'bad' ? 'bg-error' : 'bg-base-content/30');
  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-200/30 p-4 space-y-3">
      <div className="flex items-center gap-4 flex-wrap">
        <div className={`radial-progress ${ring}`} style={{ '--value': score, '--size': '3.6rem', '--thickness': '4px' } as React.CSSProperties} role="progressbar">
          <span className="text-sm font-bold">{score}</span>
        </div>
        <div className="flex-1 min-w-[200px]">
          <p className="text-[10px] uppercase tracking-wider text-base-content/50 flex items-center gap-1">
            <Compass className="w-3 h-3" /> Hedge Timing — is now a good time?
          </p>
          <p className={`text-lg font-bold ${vColor}`}>{verdict}</p>
          <p className="text-xs text-base-content/60">{market.headline}</p>
        </div>
        <div className="text-right text-xs text-base-content/60 space-y-0.5">
          {market.atm_iv_pct != null && <p>ATM IV <span className="font-mono font-medium text-base-content">{market.atm_iv_pct}%</span>{market.iv_rank != null && <span className="text-base-content/40"> (rank {market.iv_rank}%)</span>}</p>}
          {market.rv20_pct != null && <p>Realized <span className="font-mono">{market.rv20_pct}%</span><span className="text-base-content/40"> (20d)</span></p>}
          {market.term_structure_ratio != null && (
            <p>VIX curve <span className={`font-mono font-medium ${market.term_structure_ratio < 0.97 ? 'text-success' : market.term_structure_ratio > 1.0 ? 'text-error' : 'text-base-content'}`}>{market.term_structure_ratio.toFixed(2)}×</span>
              <span className="text-base-content/40"> {market.term_structure_ratio < 0.97 ? 'contango' : market.term_structure_ratio > 1.0 ? 'inverted' : 'flat'}</span></p>
          )}
          {market.beta != null && <p>β <span className="font-mono">{market.beta}</span></p>}
        </div>
      </div>
      {market.signals && market.signals.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {market.signals.map((s, i) => (
            <div key={i} className="bg-base-100/40 rounded-lg p-2.5 flex items-start gap-2">
              <span className={`w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 ${toneDot(s.tone)}`} />
              <div className="min-w-0">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-xs font-medium">{s.label}</span>
                  <span className={`text-xs font-mono font-semibold ${toneText(s.tone)}`}>{s.value}</span>
                </div>
                <p className="text-[10px] text-base-content/50 leading-tight mt-0.5">{s.read}</p>
              </div>
            </div>
          ))}
        </div>
      )}
      <RndSection rnd={market.rnd} earnings={market.next_earnings} />
      <p className="text-[10px] text-base-content/40">
        Score blends implied-vol rank, variance risk premium, skew, the VIX regime and recent trend.
        Higher = protection is cheaper / a better moment to hedge.
        ATM IV and skew pull from the live option chain; VIX and realized vol use end-of-day history.
        Not investment advice.
      </p>
    </div>
  );
}

// ── Beta-weighted index put — macro overlay ────────────────────────────────
function IndexOverlayCard({ overlay, notional }: { overlay: IndexOverlay; notional: number }) {
  const pnlColor = (v: number) => (v > 0 ? 'text-success' : v < 0 ? 'text-error' : 'text-base-content/60');
  return (
    <div className="rounded-xl border border-info/20 bg-info/5 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Globe className="w-4 h-4 text-info" />
        <h4 className="text-sm font-semibold">Macro Overlay — Beta-Weighted Index Put</h4>
        <span className="badge badge-info badge-xs">alternative</span>
      </div>
      <p className="text-sm text-base-content/70">
        Instead of hedging {`with the stock's own options`}, hedge the <strong>market</strong> portion of your risk with
        liquid <strong>{overlay.benchmark}</strong> puts, sized to your β of <strong>{overlay.beta}</strong>. Cheaper and
        more liquid, but carries <em>basis risk</em> — stock-specific moves aren't covered.
      </p>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-base-100/40 rounded-lg p-2.5 text-center">
          <p className="text-[10px] uppercase tracking-wider text-base-content/50">Buy</p>
          <p className="text-base font-bold">{overlay.contracts} × {overlay.benchmark} P</p>
          <p className="text-[10px] text-base-content/50">${overlay.strike} ({overlay.strike_pct}%) · {overlay.dte}d</p>
        </div>
        <div className="bg-base-100/40 rounded-lg p-2.5 text-center">
          <p className="text-[10px] uppercase tracking-wider text-base-content/50">Cost</p>
          <p className="text-base font-bold">{fmtUSD(overlay.cost)}</p>
          <p className="text-[10px] text-base-content/50">{overlay.cost_pct_of_notional}% of position</p>
        </div>
        <div className="bg-base-100/40 rounded-lg p-2.5 text-center">
          <p className="text-[10px] uppercase tracking-wider text-base-content/50">Market Risk Hedged</p>
          <p className="text-base font-bold">{fmtUSD(overlay.hedge_notional)}</p>
          <p className="text-[10px] text-base-content/50">β × {fmtUSD(notional)}</p>
        </div>
        <div className="bg-base-100/40 rounded-lg p-2.5 text-center">
          <p className="text-[10px] uppercase tracking-wider text-base-content/50">Tax</p>
          <p className="text-base font-bold">{overlay.is_broad_index ? '60/40' : 'Equity'}</p>
          <p className="text-[10px] text-base-content/50">{overlay.is_broad_index ? '§1256 treatment' : 'ordinary'}</p>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="table table-xs w-full">
          <thead><tr className="text-base-content/60"><th>Stock move</th><th>≈ Index move</th><th>Stock P&L</th><th>Hedge P&L</th><th>Net</th></tr></thead>
          <tbody>
            {overlay.scenarios.map((s) => (
              <tr key={s.move_pct}>
                <td className={`font-mono ${s.move_pct < 0 ? 'text-error' : s.move_pct > 0 ? 'text-success' : ''}`}>{s.move_pct > 0 ? '+' : ''}{s.move_pct}%</td>
                <td className="font-mono text-base-content/50">{s.move_pct > 0 ? '+' : ''}{(s.move_pct / overlay.beta).toFixed(1)}%</td>
                <td className={`font-mono ${pnlColor(s.stock_pl)}`}>{fmtUSD(s.stock_pl)}</td>
                <td className={`font-mono ${pnlColor(s.hedge_pl)}`}>{fmtUSD(s.hedge_pl)}</td>
                <td className={`font-mono font-semibold ${pnlColor(s.net_pl)}`}>{fmtUSD(s.net_pl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-base-content/40">
        Index moves are the beta-implied expectation; actual stock-specific (idiosyncratic) moves are not hedged by this overlay.
      </p>
    </div>
  );
}

// ─── Per-leg & per-structure timing — "am I buying cheap / selling rich today?" ───
// Each leg reacts to today's vol surface in opposite directions depending on whether
// you BUY or SELL it (and put vs call). We score richness R of an option from two
// signals already in `market`:
//   • surface level — where IV sits in its 1-yr range (iv_rank)  → whole surface rich/cheap
//   • skew          — this strike's IV vs ATM IV                 → puts bid, calls cheap, etc.
// R > 0 = expensive option, R < 0 = cheap. Edge = (+1 sell / −1 buy) × R, so a positive
// edge = favorable (selling rich or buying cheap) and negative = unfavorable.
interface LegTiming { tone: 'good' | 'neutral' | 'bad'; label: string; edge: number; }

function legTiming(leg: { action: 'BUY' | 'SELL'; iv: number | null }, m?: MarketConditions | null): LegTiming | null {
  if (!m || leg.iv == null || m.atm_iv_pct == null || m.atm_iv_pct <= 0) return null;
  const surfaceRich = m.iv_rank != null ? (m.iv_rank / 100 - 0.5) : 0;   // −0.5 (cheap) .. +0.5 (rich)
  const skewRich = (leg.iv - m.atm_iv_pct) / m.atm_iv_pct;               // this strike vs ATM
  const R = surfaceRich + skewRich;                                      // >0 rich, <0 cheap
  const edge = (leg.action === 'SELL' ? 1 : -1) * R;
  const tone = edge >= 0.08 ? 'good' : edge <= -0.08 ? 'bad' : 'neutral';
  const rich = R >= 0;
  const label = leg.action === 'SELL' ? (rich ? 'selling rich' : 'selling cheap')
                                      : (rich ? 'buying rich' : 'buying cheap');
  return { tone, label, edge };
}

// Structure edge = vega-weighted average of its leg edges (bigger vol exposure weighs more).
function structureTiming(legs: HedgeLeg[], m?: MarketConditions | null): LegTiming | null {
  if (!m) return null;
  let wsum = 0, esum = 0;
  for (const l of legs) {
    const t = legTiming(l, m);
    if (!t) continue;
    const w = Math.abs(l.vega ?? 0.1) * l.contracts;
    wsum += w; esum += w * t.edge;
  }
  if (wsum === 0) return null;
  const edge = esum / wsum;
  const tone = edge >= 0.06 ? 'good' : edge <= -0.06 ? 'bad' : 'neutral';
  const label = edge >= 0.06 ? 'well-timed' : edge <= -0.06 ? 'rich entry' : 'neutral timing';
  return { tone, label, edge };
}

const TONE_BADGE: Record<LegTiming['tone'], string> = {
  good: 'badge-success', bad: 'badge-error', neutral: 'badge-ghost',
};

export function HedgingStrategy({ ticker: initialTicker }: { ticker?: string }) {
  // ── Inputs ──
  const [ticker, setTicker] = useState(initialTicker || '');
  const [shares, setShares] = useState(100);
  const [horizon, setHorizon] = useState(90);
  const [downside, setDownside] = useState(10);
  const [upside, setUpside] = useState(15);
  const [upsideGiveup, setUpsideGiveup] = useState(8);
  const [downsideCap, setDownsideCap] = useState(0);
  const [maxCost, setMaxCost] = useState(2);
  const [hedgeRatio, setHedgeRatio] = useState(1.0);
  const [quoteSource, setQuoteSource] = useState<'yfinance' | 'ibkr'>('yfinance');
  const { user } = useAuth();
  const [showAgentModal, setShowAgentModal] = useState(false);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<HedgingResponse | null>(null);
  const [marketCheck, setMarketCheck] = useState<MarketConditions | null>(null);
  const [marketCheckLoading, setMarketCheckLoading] = useState(false);
  const [marketCheckTs, setMarketCheckTs] = useState<Date | null>(null);
  const [marketCheckDone, setMarketCheckDone] = useState(false);  // gates Step 2
  const [forceConfig, setForceConfig] = useState(false);          // user clicked "configure anyway"
  const [priceHist, setPriceHist] = useState<HedgePriceHistory | null>(null);
  const [selected, setSelected] = useState(0);
  const [showRisks, setShowRisks] = useState(false);
  const [showOverlay, setShowOverlay] = useState(false);
  const [editing, setEditing] = useState(false);
  const [movePct, setMovePct] = useState(0);
  const [detailTab, setDetailTab] = useState<'scenario' | 'risk' | 'legs' | 'tax'>('scenario');

  // Per-hedge editable legs, keyed by hedge id.
  const [editedLegs, setEditedLegs] = useState<Record<string, EditLeg[]>>({});

  // Save / load
  const [saved, setSaved] = useState<SavedStrategyItem[]>([]);
  const [loadedId, setLoadedId] = useState<number | null>(null);
  const [showSaveInput, setShowSaveInput] = useState(false);
  const [saveName, setSaveName] = useState('');
  const [savingFlag, setSavingFlag] = useState(false);
  const [showSavedList, setShowSavedList] = useState(false);

  useEffect(() => { fetchSavedStrategies('hedging').then(setSaved).catch(() => {}); }, []);

  const doMarketCheck = useCallback(async (t: string, h: number) => {
    if (!t) return;
    setMarketCheckLoading(true);
    try {
      const mc = await getHedgingMarketCheck(t.toUpperCase(), 'SPY', h);
      if (mc.available) { setMarketCheck(mc); setMarketCheckTs(new Date()); }
      else setMarketCheck(null);
    } catch {
      setMarketCheck(null);
    } finally {
      setMarketCheckLoading(false);
      setMarketCheckDone(true);  // timing has been read at least once → reveal Step 2
    }
  }, []);

  // Auto-fetch timing read when the ticker changes — shows MarketPanel before the full build.
  useEffect(() => {
    if (!ticker || ticker.length < 1) return;
    const id = setTimeout(() => doMarketCheck(ticker, horizon), 700);
    return () => clearTimeout(id);
  }, [ticker, horizon, doMarketCheck]);

  // Price & technicals context chart — lookback ≈ 2× the hedge horizon.
  useEffect(() => {
    if (!ticker || ticker.trim().length < 1) { setPriceHist(null); return; }
    const id = setTimeout(() => {
      getHedgingPriceHistory(ticker.trim().toUpperCase(), Math.min(365, Math.max(30, horizon * 2)))
        .then((d) => setPriceHist(d?.available ? d : null))
        .catch(() => setPriceHist(null));
    }, 900);
    return () => clearTimeout(id);
  }, [ticker, horizon]);

  const initEdits = (res: HedgingResponse) => {
    const map: Record<string, EditLeg[]> = {};
    for (const h of res.hedges) {
      map[h.id] = h.legs.map((l) => ({ action: l.action, type: l.type, strike: l.strike, contracts: l.contracts }));
    }
    setEditedLegs(map);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true); setError(null); setResult(null); setSelected(0); setMovePct(0); setLoadedId(null); setEditing(false);
    try {
      const data = await computeHedging({
        ticker: ticker.toUpperCase(), shares, horizon_days: horizon,
        protection_pct: downside, upside_pct: upside,
        downside_buffer: 0, upside_giveup: upsideGiveup, downside_cap: downsideCap,
        max_cost_pct: maxCost, hedge_ratio: hedgeRatio, quote_source: quoteSource,
      });
      if (data.error && (!data.hedges || data.hedges.length === 0)) setError(data.error);
      else { setResult(data); initEdits(data); }
    } catch (err: any) {
      setError(err?.message || 'Failed to compute hedges');
    } finally {
      setLoading(false);
    }
  };

  const spot = result?.current_price ?? 0;

  // Live full legs + metrics for every hedge (recomputed on any edit).
  const fullLegsByHedge = useMemo(() => {
    const map: Record<string, HedgeLeg[]> = {};
    if (!result) return map;
    for (const h of result.hedges) {
      const edits = editedLegs[h.id] || h.legs.map((l) => ({ action: l.action, type: l.type, strike: l.strike, contracts: l.contracts }));
      map[h.id] = edits.map((e) => toFullLeg(e, result.chain, result.expiration));
    }
    return map;
  }, [result, editedLegs]);

  const metricsByHedge = useMemo(() => {
    const map: Record<string, Metrics> = {};
    if (!result) return map;
    for (const h of result.hedges) {
      map[h.id] = computeMetrics(fullLegsByHedge[h.id] || [], result.shares, spot, result.dte, result.notional, result.max_cost_pct);
    }
    return map;
  }, [result, fullLegsByHedge, spot]);

  // Monte-Carlo terminal-price samples (reused across all hedges + edits).
  const samples = useMemo(
    () => (result ? genTerminalPrices(spot, result.atm_iv, result.dte) : new Float64Array(0)),
    [result, spot],
  );
  const riskByHedge = useMemo(() => {
    const map: Record<string, RiskMetrics> = {};
    if (!result || samples.length === 0) return map;
    for (const h of result.hedges) {
      map[h.id] = hedgeRisk(fullLegsByHedge[h.id] || [], result.shares, spot, samples);
    }
    return map;
  }, [result, fullLegsByHedge, samples, spot]);

  const hedge = result?.hedges?.[selected];
  const legs = hedge ? fullLegsByHedge[hedge.id] || [] : [];
  const m = hedge ? metricsByHedge[hedge.id] : undefined;
  const risk = hedge ? riskByHedge[hedge.id] : undefined;
  const isEdited = hedge && editedLegs[hedge.id] && JSON.stringify(editedLegs[hedge.id]) !==
    JSON.stringify(hedge.legs.map((l) => ({ action: l.action, type: l.type, strike: l.strike, contracts: l.contracts })));

  const scenarioPrice = useMemo(() => spot * (1 + movePct / 100), [spot, movePct]);
  const scenario = useMemo(() => {
    if (!result || !legs.length) return null;
    const unhedged = result.shares * (scenarioPrice - spot);
    const h = hedgePnL(legs, scenarioPrice);
    return { unhedged, hedge: h, net: unhedged + h };
  }, [result, legs, scenarioPrice, spot]);

  const scenarioGrid = useMemo(() => {
    if (!result || !legs.length) return [];
    return [-50, -40, -30, -20, -10, -5, 0, 5, 10, 20, 30, 40, 50].map((mv) => {
      const p = spot * (1 + mv / 100);
      const unhedged = result.shares * (p - spot);
      const h = hedgePnL(legs, p);
      return { move: mv, price: p, unhedged, hedge: h, net: unhedged + h };
    });
  }, [result, legs, spot]);

  const chart = useMemo(() => {
    if (!result || !legs.length) return null;
    const lo = spot * 0.4, hi = spot * 1.6, step = (hi - lo) / 48;
    const labels: number[] = [], hedged: number[] = [], naked: number[] = [];
    for (let p = lo; p <= hi + 1e-6; p += step) {
      labels.push(Math.round(p * 100) / 100);
      const u = result.shares * (p - spot);
      naked.push(Math.round(u));
      hedged.push(Math.round(u + hedgePnL(legs, p)));
    }
    return { labels, hedged, naked };
  }, [result, legs, spot]);

  // ── Editing actions ──
  const updateLeg = (i: number, patch: Partial<EditLeg>) => {
    if (!hedge) return;
    setEditedLegs((prev) => {
      const cur = prev[hedge.id] ? [...prev[hedge.id]] : [];
      cur[i] = { ...cur[i], ...patch };
      return { ...prev, [hedge.id]: cur };
    });
  };
  const resetHedge = () => {
    if (!hedge) return;
    setEditedLegs((prev) => ({
      ...prev,
      [hedge.id]: hedge.legs.map((l) => ({ action: l.action, type: l.type, strike: l.strike, contracts: l.contracts })),
    }));
  };
  const removeLeg = (i: number) => {
    if (!hedge) return;
    setEditedLegs((prev) => {
      const cur = prev[hedge.id] ? [...prev[hedge.id]] : [];
      cur.splice(i, 1);
      return { ...prev, [hedge.id]: cur };
    });
  };
  const addLeg = () => {
    if (!hedge || !result) return;
    // Default a new leg to a long put at the strike nearest spot, so it maps to a real quote.
    const nearest = result.chain.puts.reduce(
      (best, q) => (Math.abs(q.strike - spot) < Math.abs(best - spot) ? q.strike : best),
      result.chain.puts[0]?.strike ?? spot,
    );
    const newLeg: EditLeg = { action: 'BUY', type: 'PUT', strike: nearest, contracts: result.contracts || 1 };
    setEditedLegs((prev) => ({ ...prev, [hedge.id]: [...(prev[hedge.id] || []), newLeg] }));
    if (!editing) setEditing(true);
  };

  // ── Save / load ──
  const doSave = async (asNew = true) => {
    if (!result || !hedge || !m) return;
    const name = saveName.trim();
    if (!name && asNew) return;
    setSavingFlag(true);
    try {
      const parameters = { ticker, shares, horizon, downside, upside, upsideGiveup, downsideCap, maxCost, hedgeRatio, quoteSource, selected };
      const legs_data = legs.map((l) => ({ action: l.action, type: l.type, strike: l.strike, contracts: l.contracts, mid: l.mid }));
      const result_snapshot = { result, editedLegs, selected };
      if (!asNew && loadedId) {
        await updateSavedStrategy(loadedId, { name: name || undefined, parameters, legs_data, result_snapshot });
      } else {
        const created = await saveStrategy({ strategy_type: 'hedging', name, ticker, parameters, legs_data, result_snapshot });
        setLoadedId(created.id);
      }
      setSaved(await fetchSavedStrategies('hedging'));
      setShowSaveInput(false); setSaveName('');
    } catch (err: any) {
      setError(err.message || 'Failed to save');
    } finally {
      setSavingFlag(false);
    }
  };

  const doLoad = (s: SavedStrategyItem) => {
    const p = s.parameters || {};
    setTicker(p.ticker || s.ticker); setShares(p.shares ?? 100); setHorizon(p.horizon ?? 90);
    setDownside(p.downside ?? 10); setUpside(p.upside ?? 15);
    setUpsideGiveup(p.upsideGiveup ?? 8);
    setDownsideCap(p.downsideCap ?? 0);
    setMaxCost(p.maxCost ?? 2);
    setHedgeRatio(p.hedgeRatio ?? 1); setQuoteSource(p.quoteSource ?? 'yfinance');
    const snap = s.result_snapshot || {};
    if (snap.result) {
      setResult(snap.result);
      setEditedLegs(snap.editedLegs || {});
      setSelected(snap.selected ?? p.selected ?? 0);
    }
    setLoadedId(s.id); setShowSavedList(false); setEditing(false); setMovePct(0);
  };

  const doDelete = async (id: number) => {
    try {
      await deleteSavedStrategy(id);
      setSaved((prev) => prev.filter((x) => x.id !== id));
      if (loadedId === id) setLoadedId(null);
    } catch (err: any) {
      setError(err.message || 'Failed to delete');
    }
  };

  // ── Formatting ──
  const fmt$ = (v: number) => `${v < 0 ? '-' : ''}$${Math.abs(Math.round(v)).toLocaleString()}`;
  const pnlColor = (v: number) => (v > 0 ? 'text-success' : v < 0 ? 'text-error' : 'text-base-content/60');
  const severityColor = (s: string) => s === 'High' ? 'badge-error' : s === 'Medium' ? 'badge-warning' : s === 'Low' ? 'badge-success' : 'badge-info';
  const putStrikes = result ? result.chain.puts.map((q) => q.strike) : [];
  const callStrikes = result ? result.chain.calls.map((q) => q.strike) : [];

  // Step 2 (size + preferences + build) only unlocks after the timing read has run
  // at least once — so the journey is: pick ticker & horizon → see timing → size it.
  const showConfig = marketCheckDone || forceConfig || !!result;

  return (
    <div className="space-y-4">
      {/* Intro — one-line hint, hidden once results are up */}
      {!result && (
        <div className="bg-base-200/40 rounded-xl px-4 py-2.5 border border-white/[0.03] flex items-center gap-3 text-sm text-base-content/70">
          <Info className="w-4 h-4 text-info flex-shrink-0" />
          <p><strong className="text-base-content">How it works:</strong> ① pick the holding &amp; horizon · ② read live hedge-timing · ③ <em>then</em> size it &amp; set preferences · ④ compare hedges &amp; stress-test.</p>
        </div>
      )}

      {/* Input form */}
      <form onSubmit={handleSubmit} className="space-y-3">
        {/* ── STEP 1 — what & how long (no share count needed yet) ── */}
        <div className="rounded-xl border border-white/[0.08] bg-base-200/30 p-3">
          <div className="flex items-center justify-between gap-2 mb-2">
            <p className="text-xs font-semibold flex items-center gap-1.5">
              <span className="badge badge-secondary badge-sm font-bold">1</span>
              What do you want to hedge — and for how long?
            </p>
            {/* Saved hedges always reachable */}
            <div className="relative">
              <button type="button" className="btn btn-ghost btn-xs gap-1" onClick={() => setShowSavedList(!showSavedList)}>
                <FolderOpen className="w-3.5 h-3.5" /> Saved ({saved.length})
              </button>
              {showSavedList && (
                <div className="absolute top-full mt-1 right-0 z-50 w-80 max-h-72 overflow-y-auto bg-base-100 border border-base-300 rounded-xl shadow-xl p-2 space-y-1">
                  {saved.length === 0 && <div className="text-center text-sm opacity-60 py-4">No saved hedges yet</div>}
                  {saved.map((s) => (
                    <div key={s.id} className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg hover:bg-base-200 cursor-pointer group" onClick={() => doLoad(s)}>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium truncate">{s.name}</div>
                        <div className="text-xs opacity-50">{s.ticker} · {new Date(s.updated_at).toLocaleDateString()}</div>
                      </div>
                      <button type="button" className="btn btn-ghost btn-xs text-error opacity-0 group-hover:opacity-100"
                        onClick={(e) => { e.stopPropagation(); doDelete(s.id); }}><Trash2 className="w-3 h-3" /></button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-xs font-medium">Ticker to protect</span></label>
              <input type="text" className="input input-bordered input-sm w-full" value={ticker}
                onChange={(e) => { setTicker(e.target.value.toUpperCase()); setMarketCheck(null); setMarketCheckDone(false); setForceConfig(false); }}
                placeholder="SMH, SPY, AAPL" required autoFocus />
            </div>
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-xs font-medium">Protect for how long?</span></label>
              <div className="relative">
                <Calendar className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40" />
                <input type="number" className="input input-bordered input-sm w-full pl-7" value={horizon}
                  onChange={(e) => setHorizon(Number(e.target.value))} min={5} max={730} required />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] text-base-content/40">days</span>
              </div>
            </div>
          </div>
          <p className="text-[10px] text-base-content/40 mt-1.5">
            We read live market timing automatically — no share count needed yet. Size the trade after you see whether now is a good moment.
          </p>
        </div>

        {/* ── TIMING READ — always sits above preferences & Build; refreshes on ticker change ── */}
        {(marketCheckLoading || marketCheckDone) && (
          <div className="rounded-xl border border-secondary/20 bg-base-200/30 p-3 space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[11px] font-semibold text-secondary flex items-center gap-1.5">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-60" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-success" />
                </span>
                <Gauge className="w-3.5 h-3.5" /> Market Timing — Live
              </span>
              {marketCheck?.spot != null && (
                <span className="text-xs font-mono bg-base-100/50 px-2 py-0.5 rounded-full">
                  {ticker} <span className="font-semibold text-base-content">${marketCheck.spot.toFixed(2)}</span>
                </span>
              )}
              {marketCheckTs && (
                <span className="text-[10px] text-base-content/40 ml-auto flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  As of {marketCheckTs.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                </span>
              )}
              <button type="button" onClick={() => doMarketCheck(ticker, horizon)} disabled={marketCheckLoading}
                className="btn btn-ghost btn-xs gap-1 text-base-content/50 hover:text-secondary" title="Refresh market data">
                <RefreshCw className={`w-3 h-3 ${marketCheckLoading ? 'animate-spin' : ''}`} />
                {marketCheckLoading ? 'Refreshing…' : 'Refresh'}
              </button>
              <button type="button" onClick={() => setShowAgentModal(true)} disabled={!ticker}
                className="btn btn-ghost btn-xs gap-1 text-base-content/50 hover:text-primary"
                title="Create a scheduled agent that re-checks this hedge timing and emails you when the window is favorable">
                <Bot className="w-3 h-3" /> Monitor with agent
              </button>
            </div>
            {marketCheckLoading && !marketCheck && (
              <div className="flex items-center gap-2 text-sm text-base-content/50 py-1">
                <Loader2 className="w-4 h-4 animate-spin" /> Fetching market conditions for {ticker}…
              </div>
            )}
            {marketCheck?.available && <MarketPanel market={marketCheck} />}
            {!marketCheckLoading && marketCheckDone && !marketCheck?.available && (
              <p className="text-xs text-base-content/50 py-1 flex items-center gap-2">
                <AlertCircle className="w-3.5 h-3.5 text-warning shrink-0" />
                Couldn't read a timing score for {ticker} (thin or no listed options). You can still size and build a hedge below.
              </p>
            )}
          </div>
        )}

        {/* Price & technicals — the run-up into the window you're hedging */}
        {priceHist?.available && ticker && (
          <div className="rounded-xl border border-white/[0.08] bg-base-200/30 p-3 space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[11px] font-semibold text-secondary flex items-center gap-1.5">
                <TrendingUp className="w-3.5 h-3.5" /> Price &amp; technicals — last {priceHist.lookback_days}d
              </span>
              <span className="text-[10px] text-base-content/40">(2× your {horizon}d hedge horizon, for context)</span>
              {priceHist.last_volume_ratio != null && (
                <span className={`text-[10px] ml-auto ${priceHist.last_volume_ratio > 1.5 ? 'text-warning' : 'text-base-content/40'}`}
                  title="Yesterday's volume vs the lookback average — a spike often marks distribution or capitulation">
                  vol {priceHist.last_volume_ratio}× avg
                </span>
              )}
            </div>
            <PriceTechChart ph={priceHist} />
            <div className="flex items-center gap-2 flex-wrap text-[10px] text-base-content/50">
              <span className="text-indigo-300">— Close</span>
              <span className="text-yellow-300/80">— SMA20</span>
              <span className="text-blue-300/80">— SMA50</span>
              {(priceHist.supports ?? []).map((s) => (
                <span key={`s${s.level}`} className="text-success/80" title={`${s.touches} touches`}>┄ S ${s.level}</span>
              ))}
              {(priceHist.resistances ?? []).map((r) => (
                <span key={`r${r.level}`} className="text-error/80" title={`${r.touches} touches`}>┄ R ${r.level}</span>
              ))}
              <span className="ml-auto opacity-70">A floor just under a strong support is cheaper to defend; a cap under resistance forfeits less.</span>
            </div>
          </div>
        )}

        {/* Nudge to reveal step 2 before the auto-check lands */}
        {!result && !showConfig && ticker && (
          <button type="button" onClick={() => setForceConfig(true)}
            className="btn btn-ghost btn-xs gap-1 text-base-content/50">
            Skip ahead — size it now <ChevronDown className="w-3 h-3" />
          </button>
        )}

        {/* ── STEP 2 — size & preferences (unlocks after timing) ── */}
        <div className={showConfig ? 'space-y-3' : 'hidden'}>
        <div className="rounded-xl border border-white/[0.08] bg-base-200/30 p-3">
          <p className="text-xs font-semibold flex items-center gap-1.5 mb-2">
            <span className="badge badge-secondary badge-sm font-bold">2</span>
            Size it &amp; set your preferences
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-xs font-medium">How many shares?</span></label>
              <div className="relative">
                <Layers className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40" />
                <input type="number" className="input input-bordered input-sm w-full pl-7" value={shares}
                  onChange={(e) => setShares(Number(e.target.value))} min={1} step={1} required />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] text-base-content/40">
                  {marketCheck?.spot ? `≈ $${(shares * marketCheck.spot).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '1 contract = 100 sh'}
                </span>
              </div>
            </div>
            <div className="form-control">
              <label className="label py-1"><span className="label-text text-xs font-medium">How much of the position to hedge</span></label>
              <select className="select select-bordered select-sm w-full" value={hedgeRatio}
                onChange={(e) => setHedgeRatio(Number(e.target.value))}>
                <option value={0.25}>25% — partial</option>
                <option value={0.5}>50% — half</option>
                <option value={0.75}>75%</option>
                <option value={1.0}>100% — full</option>
              </select>
            </div>
          </div>
        </div>

        {/* The three protection dials */}
        <div className="rounded-xl border border-secondary/20 bg-secondary/5 p-3">
          <p className="text-xs font-semibold text-secondary mb-2 flex items-center gap-1.5">
            <Shield className="w-3.5 h-3.5" /> Your protection preferences
          </p>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Downside — two distinct styles */}
            <div className="rounded-lg bg-error/5 border border-error/15 p-2.5">
              <p className="text-[11px] font-semibold text-error/90 mb-2 flex items-center gap-1"><TrendingDown className="w-3.5 h-3.5" /> Downside — where you’re protected</p>
              <div className="space-y-2">
                {/* Floor — where protection starts */}
                <div className="rounded-md bg-base-100/50 border border-error/10 p-2 flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <span className="text-xs font-medium">Floor — {downside === 0 ? <>“<span className="text-error">protect from today’s price</span>”</> : <>“protected below <span className="text-error">−{downside}%</span>”</>}</span>
                    <p className="text-[10px] text-base-content/50 mt-0.5 leading-tight">{downside === 0 ? 'Protected starting at today’s price — 0% deductible (always on; this is the floor, not “off”)' : `You absorb the first ${downside}% (a deductible)`}{downsideCap > 0 ? `, then protected down to the −${downsideCap}% cap below.` : ', then protected all the way down.'} <span className="opacity-70">→ Protective Put / Collar.</span></p>
                    {marketCheck?.rnd?.floor_for_10pct_breach_pct != null && (
                      <div className="flex items-center gap-1 mt-1 flex-wrap">
                        <span className="text-[10px] text-secondary/80">Suggested from market odds:</span>
                        <button type="button" className="btn btn-ghost btn-xs h-5 min-h-0 px-1.5 text-[10px] text-secondary border border-secondary/20"
                          title="Sets the Floor at the strike the market gives a 10% chance of breaching by expiry"
                          onClick={() => setDownside(Math.min(40, Math.max(0, Math.round(-marketCheck.rnd!.floor_for_10pct_breach_pct))))}>
                          10% breach → −{Math.round(-marketCheck.rnd.floor_for_10pct_breach_pct)}%
                        </button>
                        {marketCheck.rnd.floor_for_5pct_breach_pct != null && (
                          <button type="button" className="btn btn-ghost btn-xs h-5 min-h-0 px-1.5 text-[10px] text-secondary border border-secondary/20"
                            title="Sets the Floor at the strike the market gives a 5% chance of breaching by expiry"
                            onClick={() => setDownside(Math.min(40, Math.max(0, Math.round(-marketCheck.rnd!.floor_for_5pct_breach_pct))))}>
                            5% → −{Math.round(-marketCheck.rnd.floor_for_5pct_breach_pct)}%
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                  <div className="relative shrink-0 w-20">
                    <Percent className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40" />
                    <input type="number" className="input input-bordered input-sm w-full pl-7" value={downside}
                      onChange={(e) => setDownside(Number(e.target.value))} min={0} max={40} step={1} required />
                  </div>
                </div>
                {/* Downside cap — optional financing (insurance turns off) */}
                <div className={`rounded-md border p-2 flex items-start gap-2 ${downsideCap > 0 ? 'bg-warning/5 border-warning/20' : 'bg-base-100/50 border-error/10'}`}>
                  <div className="min-w-0 flex-1">
                    <span className="text-xs font-medium">Cap — “stop protecting below <span className="text-warning">−{downsideCap}%</span>”</span>
                    <p className="text-[10px] text-base-content/50 mt-0.5 leading-tight">{downsideCap === 0 ? 'Off — protection runs all the way down (no financing put sold).' : `Sell a put here to subsidize the premium; below −${downsideCap}% you’re exposed again (a fall that deep judged unlikely).`} <span className="opacity-70">→ Financed Floor / -Collar.</span></p>
                  </div>
                  <div className="relative shrink-0 w-20">
                    <Percent className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40" />
                    <input type="number" className="input input-bordered input-sm w-full pl-7" value={downsideCap}
                      onChange={(e) => setDownsideCap(Number(e.target.value))} min={0} max={60} step={1} />
                  </div>
                </div>
              </div>
            </div>
            {/* Upside — what you trade away to pay for it */}
            <div className="rounded-lg bg-success/5 border border-success/15 p-2.5">
              <p className="text-[11px] font-semibold text-success/90 mb-2 flex items-center gap-1"><TrendingUp className="w-3.5 h-3.5" /> Upside — what funds the protection</p>
              <div className="space-y-2">
                {/* Cap */}
                <div className="rounded-md bg-base-100/50 border border-success/10 p-2 flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <span className="text-xs font-medium">Cap — {upside === 0 ? <>“<span className="text-success">no cap — unlimited upside</span>”</> : <>“stop gaining above <span className="text-success">+{upside}%</span>”</>}</span>
                    <p className="text-[10px] text-base-content/50 mt-0.5 leading-tight">{upside === 0 ? 'Off — keep all the upside (Collar / Covered Call need a cap, so they won’t be offered).' : <>A hard ceiling; the call premium pays for the put. <span className="opacity-70">→ Collar / Covered Call.</span></>}</p>
                  </div>
                  <div className="relative shrink-0 w-20">
                    <Percent className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40" />
                    <input type="number" className="input input-bordered input-sm w-full pl-7" value={upside}
                      onChange={(e) => setUpside(Number(e.target.value))} min={0} max={60} step={1} />
                  </div>
                </div>
                {/* Give-up band */}
                <div className="rounded-md bg-base-100/50 border border-success/10 p-2 flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <span className="text-xs font-medium">Give-up — "forgo the first <span className="text-success">{upsideGiveup}%</span> of gains"</span>
                    <p className="text-[10px] text-base-content/50 mt-0.5 leading-tight">
                      {upsideGiveup === 0 ? 'Keep all upside.'
                        : `Surrender gains up to +${upsideGiveup}%, then participate again. → Funds a protective floor (Give-up Funded Floor).`}
                    </p>
                  </div>
                  <div className="relative shrink-0 w-20">
                    <Percent className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40" />
                    <input type="number" className="input input-bordered input-sm w-full pl-7" value={upsideGiveup}
                      onChange={(e) => setUpsideGiveup(Number(e.target.value))} min={0} max={40} step={1} required />
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div className="form-control mt-3 max-w-xs">
            <label className="label py-0.5"><span className="label-text text-xs flex items-center gap-1"><Wallet className="w-3 h-3 text-warning" /> Budget (max net cost)</span></label>
            <div className="relative">
              <Percent className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40" />
              <input type="number" className="input input-bordered input-sm w-full pl-7" value={maxCost}
                onChange={(e) => setMaxCost(Number(e.target.value))} min={0} max={20} step={0.1} required />
            </div>
            <span className="text-[10px] text-base-content/40 mt-0.5">% of position value you'll spend.</span>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-end">
          <div className="form-control">
            <label className="label py-1"><span className="label-text text-xs font-medium">Price data for building</span></label>
            <div className="flex gap-4 h-8 items-center">
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input type="radio" name="hedgeQuoteSource" className="radio radio-xs radio-info"
                  checked={quoteSource === 'yfinance'} onChange={() => setQuoteSource('yfinance')} />
                <Database className="w-3.5 h-3.5 text-base-content/60" /><span className="text-xs">Yahoo</span>
              </label>
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input type="radio" name="hedgeQuoteSource" className="radio radio-xs radio-info"
                  checked={quoteSource === 'ibkr'} onChange={() => setQuoteSource('ibkr')} />
                <Zap className="w-3.5 h-3.5 text-base-content/60" /><span className="text-xs">IBKR</span>
              </label>
            </div>
          </div>
          <div className="form-control">
            <button type="submit" className="btn btn-primary btn-sm gap-2 w-full" disabled={loading || !ticker}>
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Shield className="w-4 h-4" />}
              {loading ? 'Building...' : 'Build Hedges'}
            </button>
          </div>
        </div>
        </div>
      </form>

      {error && (
        <div className="alert alert-error text-sm">
          <AlertTriangle className="w-4 h-4 shrink-0" /><span className="flex-1 min-w-0">{error}</span>
        </div>
      )}

      {result && result.hedges?.length > 0 && (
        <div className="space-y-4">
          {/* Position summary */}
          <div className="bg-base-200/40 rounded-xl p-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm border border-white/[0.03]">
            <span className="font-medium">{result.ticker}</span>
            <span className="text-base-content/60">Spot ${result.current_price}</span>
            <span className="text-base-content/60">{result.shares.toLocaleString()} sh · <span className="font-medium text-base-content">${result.notional.toLocaleString()}</span></span>
            {result.beta != null && <span className="badge badge-outline badge-sm gap-1">β {result.beta} vs {result.benchmark}</span>}
            <span className="text-base-content/60">Expiry {result.expiration} · {result.dte}d</span>
            <span className="text-base-content/60">{result.contracts} contracts ({result.covered_shares.toLocaleString()} sh)</span>
            <span className="badge badge-warning badge-sm gap-1"><Wallet className="w-2.5 h-2.5" /> Budget ${(result.notional * result.max_cost_pct / 100).toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
          </div>

          {/* Desk pick — best balance of near-zero cost + protection, given today's surface */}
          {(() => {
            const curve = result.rnd?.curve;
            const volOfVol = result.rnd?.heston?.vol_of_vol ?? 0;
            let best: { i: number; score: number; why: string } | null = null;
            result.hedges.forEach((h, i) => {
              const hm2 = metricsByHedge[h.id];
              if (!hm2 || hm2.floor == null) return;                      // must actually protect
              const ip = curve ? impliedProbs(curve, spot, hm2) : null;
              const breach = ip?.breach ?? h.probs?.p_breach_floor_pct;
              if (breach == null) return;
              const costPct = Math.abs(hm2.cost_pct ?? 0);
              const costScore = Math.max(0, 1 - costPct / 2);             // 0% cost → 1, 2%+ → 0
              const protScore = 1 - breach / 100;                         // low breach odds → high
              const volPen = h.smile_risk?.short_vol && volOfVol > 1.0 ? 0.12 : 0;
              const budgetPen = hm2.within_budget ? 0 : 0.15;
              const score = 0.45 * costScore + 0.55 * protScore - volPen - budgetPen;
              if (!best || score > best.score) {
                best = {
                  i, score,
                  why: `${hm2.is_credit ? 'net credit' : `${costPct.toFixed(1)}% cost`} · P(breach floor) ${breach.toFixed(0)}%`
                    + (volPen ? ' · short-vol trimmed (vol-of-vol is high — gap risk)' : ''),
                };
              }
            });
            if (!best) return null;
            const b: { i: number; score: number; why: string } = best;
            const bh = result.hedges[b.i];
            return (
              <div className="rounded-xl border border-secondary/25 bg-secondary/[0.06] p-3 flex items-center gap-3 flex-wrap">
                <Sparkles className="w-4 h-4 text-secondary shrink-0" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold">Desk pick — {bh.name}</p>
                  <p className="text-[11px] text-base-content/60">Best balance of near-zero cost and protection on today's surface: {b.why}. Weighs cost vs market-implied breach odds, penalizes short-vol in a jumpy-vol regime and over-budget structures.</p>
                </div>
                {selected !== b.i && (
                  <button type="button" className="btn btn-secondary btn-xs" onClick={() => { setSelected(b.i); setMovePct(0); }}>Inspect</button>
                )}
              </div>
            );
          })()}

          {/* Compare-all table with institutional risk metrics */}
          <div className="rounded-xl border border-white/[0.06] overflow-hidden">
            <div className="bg-base-200/40 px-3 py-2 flex items-center gap-2 text-sm font-semibold border-b border-white/[0.06]">
              <TableProperties className="w-4 h-4 text-secondary" /> Compare All Hedges
              <span className="text-[10px] font-normal text-base-content/40">CVaR/VaR from {(samples.length / 1000).toFixed(0)}k Monte-Carlo paths @ {result.atm_iv ?? '—'}% IV · click a row to open</span>
            </div>
            <div className="overflow-x-auto">
              <table className="table table-xs w-full">
                <thead>
                  <tr className="text-base-content/60">
                    <th>Structure</th><th>Net Cost</th><th>Protects</th>
                    <th title="Market-implied probability the stock breaches the floor by expiry (Breeden-Litzenberger risk-neutral density)">P(breach)</th>
                    <th>Worst Case</th>
                    <th title="Expected shortfall: average of the worst 5% outcomes, hedged">CVaR&nbsp;95%</th>
                    <th title="How much the hedge cuts the worst-5% average loss">Tail&nbsp;↓</th>
                    <th title="Probability the hedge finishes profitable">P(pays)</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {result.hedges.map((h, i) => {
                    const hm = metricsByHedge[h.id]; const hr = riskByHedge[h.id];
                    if (!hm) return null;
                    const st = structureTiming(h.legs, result.market);
                    const tailored = h.id === 'tailored';
                    return (
                      <tr key={h.id} className={`cursor-pointer ${selected === i ? 'bg-primary/10' : tailored ? 'bg-accent/[0.06] hover:bg-accent/15' : 'hover:bg-base-200/40'} ${tailored ? 'ring-1 ring-inset ring-accent/40' : ''}`}
                        onClick={() => { setSelected(i); setMovePct(0); }}>
                        <td className="font-medium">
                          {h.name}
                          {st && st.tone !== 'neutral' && (
                            <span className={`badge badge-xs gap-0.5 ml-1 ${st.tone === 'good' ? 'badge-success' : 'badge-error badge-outline'}`}
                              title={st.tone === 'good' ? "Well-timed for today's vol surface — net selling rich / buying cheap options" : "Rich entry — net buying expensive / selling cheap options today"}>
                              {st.tone === 'good' ? <Sparkles className="w-2.5 h-2.5" /> : null} {st.label}
                            </span>
                          )}
                        </td>
                        <td className={`font-mono ${hm.is_credit ? 'text-success' : ''}`}>{hm.is_credit ? `+${fmt$(-hm.net_cost)}` : fmt$(hm.net_cost)}</td>
                        <td className="font-mono text-xs">{hm.floor != null ? `$${hm.floor}${hm.buffer_bottom != null ? `→$${hm.buffer_bottom}` : ''}` : '—'}</td>
                        <td className="font-mono text-xs">{(() => {
                          // Live: recomputed from the RND curve against the CURRENT (possibly edited) geometry.
                          const ip = result.rnd?.curve ? impliedProbs(result.rnd.curve, spot, hm) : null;
                          const breach = ip?.breach ?? h.probs?.p_breach_floor_pct;
                          const capP = ip?.cap ?? h.probs?.p_hit_cap_pct;
                          const bandP = ip?.inBand ?? h.probs?.p_in_band_pct;
                          return breach != null
                            ? <span title={[capP != null ? `P(hit cap) ${capP.toFixed(1)}%` : '', bandP != null ? `P(in band) ${bandP.toFixed(1)}%` : ''].filter(Boolean).join(' · ')}
                                className={breach >= 25 ? 'text-warning' : breach <= 10 ? 'text-success' : ''}>{breach.toFixed(1)}%</span>
                            : <span className="text-base-content/30">—</span>;
                        })()}</td>
                        <td className="font-mono text-error">{fmt$(hm.max_loss)}</td>
                        <td className="font-mono">{hr ? fmt$(hr.cvar_hedged) : '—'}</td>
                        <td className="font-mono text-success">{hr ? `−${fmt$(hr.cvar_reduction)}` : '—'}</td>
                        <td className="font-mono">{hr ? `${Math.round(hr.prob_pays * 100)}%` : '—'}</td>
                        <td>{!hm.within_budget && <span className="badge badge-warning badge-xs" title="Over budget">$</span>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {hedge && m && (
            <div className="space-y-4 rounded-2xl border border-white/[0.08] bg-base-200/20 p-4">
              {/* Plain-English summary */}
              <div className={`rounded-xl p-4 border ${m.within_budget ? 'border-success/30 bg-success/5' : 'border-warning/30 bg-warning/5'}`}>
                <div className="flex items-start justify-between gap-3 flex-wrap">
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-base-content/40 mb-0.5">Inspecting — pick another row above to switch</p>
                    <h3 className="font-bold text-base flex items-center gap-2">{hedge.name}
                      {isEdited && <span className="badge badge-xs badge-accent gap-1"><Edit3 className="w-2.5 h-2.5" /> edited</span>}
                      {(() => { const st = structureTiming(legs, result.market); return st && st.tone !== 'neutral'
                        ? <span className={`badge badge-xs gap-0.5 ${st.tone === 'good' ? 'badge-success' : 'badge-error badge-outline'}`}
                            title={st.tone === 'good' ? "Well-timed for today's vol surface" : "Rich entry for today's vol surface"}>
                            {st.tone === 'good' ? <Sparkles className="w-2.5 h-2.5" /> : null} {st.label}</span>
                        : null; })()}
                    </h3>
                    <p className="text-sm text-base-content/70 mt-0.5">{isEdited ? (geometryDescribe(m, spot) || describe(hedge.id, m, spot)) : (hedge.id === 'tailored' ? hedge.plain : describe(hedge.id, m, spot))}</p>
                    {(() => {
                      const st = structureTiming(legs, result.market);
                      if (!st || !result.market) return null;
                      const tags = legs.map((l) => legTiming(l, result.market)).filter(Boolean) as LegTiming[];
                      const good = tags.filter((t) => t.tone === 'good').length;
                      const bad = tags.filter((t) => t.tone === 'bad').length;
                      return (
                        <p className="text-[11px] text-base-content/50 mt-1">
                          <Gauge className="w-3 h-3 inline-block mr-0.5 -mt-0.5" />
                          Leg timing: <span className={st.tone === 'good' ? 'text-success font-medium' : st.tone === 'bad' ? 'text-error font-medium' : ''}>{st.label}</span>
                          {(good || bad) ? ` — ${good} favorable, ${bad} rich today (see Legs tab).` : '.'}
                        </p>
                      );
                    })()}
                    {(() => {
                      // Live market-implied probs — recomputed from the RND curve for the
                      // CURRENT legs, so strike edits move these numbers immediately.
                      const ip = result.rnd?.curve ? impliedProbs(result.rnd.curve, spot, m) : null;
                      const breach = ip?.breach ?? hedge.probs?.p_breach_floor_pct;
                      const capP = ip?.cap ?? hedge.probs?.p_hit_cap_pct;
                      const bandP = ip?.inBand ?? hedge.probs?.p_in_band_pct;
                      const bufP = ip?.buffer ?? hedge.probs?.p_below_buffer_pct;
                      if (breach == null && capP == null) return null;
                      const below = breach ?? 0;
                      const above = capP ?? 0;
                      const band = bandP ?? Math.max(0, 100 - below - above);
                      return (
                        <div className="mt-1.5 space-y-1">
                          <p className="text-[11px] text-base-content/50">
                            <Activity className="w-3 h-3 inline-block mr-0.5 -mt-0.5" />
                            Market-implied{isEdited ? ' (live — reflects your edited strikes)' : ''}:{' '}
                            {breach != null && <>P(breach floor) <span className="font-mono font-medium text-base-content/70">{breach.toFixed(1)}%</span></>}
                            {capP != null && <> · P(hit cap) <span className="font-mono">{capP.toFixed(1)}%</span></>}
                            {bandP != null && <> · P(in band) <span className="font-mono">{bandP.toFixed(1)}%</span></>}
                            {bufP != null && <> · P(below buffer) <span className="font-mono">{bufP.toFixed(1)}%</span></>}
                          </p>
                          {breach != null && (
                            <div className="max-w-sm">
                              <div className="h-2 w-full rounded-full overflow-hidden flex" title="Where the market expects this position to finish at expiry">
                                <div className="bg-error/70" style={{ width: `${Math.min(100, below)}%` }} />
                                <div className="bg-base-content/15" style={{ width: `${Math.max(0, Math.min(100, band))}%` }} />
                                <div className="bg-warning/70" style={{ width: `${Math.min(100, above)}%` }} />
                              </div>
                              <div className="flex justify-between text-[9px] text-base-content/40 mt-0.5">
                                <span>below floor {below.toFixed(0)}%</span>
                                <span>protected band {band.toFixed(0)}%</span>
                                <span>{capP != null ? `above cap ${above.toFixed(0)}%` : ''}</span>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })()}
                    {(() => {
                      // Fair-value check: expected hedge P&L under the market's own density.
                      const curve = result.rnd?.curve;
                      if (!curve || !legs.length || !curve.pct.length) return null;
                      const step = curve.pct.length > 1 ? curve.pct[1] - curve.pct[0] : 1;
                      let e = 0, mass = 0;
                      curve.pct.forEach((p, i) => {
                        const w = (curve.pdf_per_pct[i] / 100) * step;
                        mass += w;
                        e += w * hedgePnL(legs, spot * (1 + p / 100));
                      });
                      if (mass <= 0) return null;
                      e /= mass;
                      return (
                        <p className="text-[11px] text-base-content/50 mt-1">
                          <Landmark className="w-3 h-3 inline-block mr-0.5 -mt-0.5" />
                          RND-expected hedge P&amp;L: <span className={`font-mono font-medium ${e >= 0 ? 'text-success' : 'text-base-content/70'}`}>{e >= 0 ? '+' : '−'}{fmt$(Math.abs(e))}</span>
                          <span className="opacity-70"> — the market-implied expected cost of this insurance over the horizon (negative ≈ the premium you're truly paying).</span>
                        </p>
                      );
                    })()}
                    {hedge.smile_risk && (
                      <p className="text-[11px] text-base-content/50 mt-1">
                        <Zap className="w-3 h-3 inline-block mr-0.5 -mt-0.5" />
                        VIX +{hedge.smile_risk.vix_shock_pts} spike:{' '}
                        <span className={`font-mono font-medium ${hedge.smile_risk.pnl_on_vol_spike >= 0 ? 'text-success' : 'text-error'}`}>
                          {hedge.smile_risk.pnl_on_vol_spike >= 0 ? '+' : '−'}{fmt$(Math.abs(hedge.smile_risk.pnl_on_vol_spike))}
                        </span>
                        {hedge.smile_risk.short_vol
                          ? <span className="text-warning/80"> — short vol: a spike inflates the short legs (margin risk)</span>
                          : ' — long vol: a spike helps the hedge'}
                      </p>
                    )}
                  </div>
                  <div className="text-right">
                    <p className="text-[10px] uppercase tracking-wider text-base-content/50">Net {m.is_credit ? 'credit' : 'cost'}</p>
                    <p className={`text-xl font-bold ${m.is_credit ? 'text-success' : 'text-base-content'}`}>{m.is_credit ? `+${fmt$(-m.net_cost)}` : fmt$(m.net_cost)}</p>
                    <p className="text-[11px]">
                      <span className={m.within_budget ? 'text-success' : 'text-warning'}>
                        {Math.abs(m.cost_pct).toFixed(2)}% of position · {m.within_budget ? 'within budget' : 'over budget'}
                      </span>
                    </p>
                  </div>
                </div>
              </div>

              {/* Key metrics */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="bg-base-200/40 rounded-xl p-3 text-center border border-white/[0.03]">
                  <p className="text-[10px] text-base-content/50 uppercase tracking-wider">Protected Floor</p>
                  <p className="text-lg font-bold text-info">{m.floor != null ? `$${m.floor}` : '—'}</p>
                  <p className="text-xs text-base-content/60">{m.floor_pct != null ? `${m.floor_pct}%` : ''}{m.buffer_bottom != null ? ` → $${m.buffer_bottom}` : ''}</p>
                </div>
                <div className="bg-base-200/40 rounded-xl p-3 text-center border border-white/[0.03]">
                  {m.giveup_from != null && m.participate_above != null ? (
                    <>
                      <p className="text-[10px] text-base-content/50 uppercase tracking-wider">Upside Give-Up</p>
                      <p className="text-lg font-bold">${m.giveup_from}→${m.participate_above}</p>
                      <p className="text-xs text-base-content/60">forgo, then back in (no cap)</p>
                    </>
                  ) : (
                    <>
                      <p className="text-[10px] text-base-content/50 uppercase tracking-wider">Upside Cap</p>
                      <p className="text-lg font-bold">{m.cap != null ? `$${m.cap}` : 'None'}</p>
                      <p className="text-xs text-base-content/60">{m.cap_pct != null ? `+${m.cap_pct}%` : 'unlimited'}</p>
                    </>
                  )}
                </div>
                <div className="bg-base-200/40 rounded-xl p-3 text-center border border-white/[0.03]">
                  <p className="text-[10px] text-base-content/50 uppercase tracking-wider">Worst-Case Loss</p>
                  <p className="text-lg font-bold text-error">{fmt$(m.max_loss)}</p>
                  <p className="text-xs text-base-content/60">{m.max_loss_pct != null ? `${m.max_loss_pct}% of position` : ''}</p>
                </div>
                <div className="bg-base-200/40 rounded-xl p-3 text-center border border-white/[0.03]">
                  <p className="text-[10px] text-base-content/50 uppercase tracking-wider">Annual Cost Drag</p>
                  <p className={`text-lg font-bold ${m.annualized <= 0 ? 'text-success' : 'text-warning'}`}>{m.annualized <= 0 ? '+' : ''}{Math.abs(m.annualized).toFixed(1)}%</p>
                  <p className="text-xs text-base-content/60">if rolled all year</p>
                </div>
              </div>

              {/* Detail sub-tabs — collapse the deep panels so only one shows at a time */}
              <div role="tablist" className="tabs tabs-boxed bg-base-200/40 w-fit">
                <button role="tab" className={`tab gap-1.5 ${detailTab === 'scenario' ? 'tab-active' : ''}`} onClick={() => setDetailTab('scenario')}>
                  <Sliders className="w-3.5 h-3.5" /> Scenario
                </button>
                <button role="tab" className={`tab gap-1.5 ${detailTab === 'risk' ? 'tab-active' : ''}`} onClick={() => setDetailTab('risk')}>
                  <Gauge className="w-3.5 h-3.5" /> Risk &amp; Greeks
                </button>
                <button role="tab" className={`tab gap-1.5 ${detailTab === 'legs' ? 'tab-active' : ''}`} onClick={() => setDetailTab('legs')}>
                  <Layers className="w-3.5 h-3.5" /> Legs {isEdited && <span className="w-1.5 h-1.5 rounded-full bg-accent" />}
                </button>
                <button role="tab" className={`tab gap-1.5 ${detailTab === 'tax' ? 'tab-active' : ''}`} onClick={() => setDetailTab('tax')}>
                  <Landmark className="w-3.5 h-3.5" /> Tax
                </button>
              </div>

              {detailTab === 'risk' && (<>
              {/* Net Greeks */}
              <div className="rounded-xl border border-secondary/20 bg-secondary/5 p-3">
                <div className="flex items-center gap-2 mb-2">
                  <Activity className="w-4 h-4 text-secondary" />
                  <span className="text-sm font-semibold">Net Greeks — hedged position</span>
                  <span className="text-[10px] text-base-content/40">unhedged delta is {result.shares.toLocaleString()}</span>
                </div>
                <div className="grid grid-cols-4 gap-3 text-center">
                  {([['Delta', m.greeks.delta, 'direction'], ['Gamma', m.greeks.gamma, 'convexity'],
                     ['Theta', m.greeks.theta, '$/day'], ['Vega', m.greeks.vega, '$/vol pt']] as const).map(([k, v, sub]) => (
                    <div key={k}>
                      <p className="text-[10px] uppercase tracking-wider text-base-content/50">{k}</p>
                      <p className="text-base font-bold font-mono">{v.toLocaleString()}</p>
                      <p className="text-[10px] text-base-content/40">{sub}</p>
                    </div>
                  ))}
                </div>
                {/* Time-decay split — only the extrinsic (time value) bleeds away */}
                <div className="mt-3 pt-3 border-t border-white/[0.06] flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
                  <span className="flex items-center gap-1.5 text-base-content/70">
                    <Clock className="w-3.5 h-3.5 text-warning" />
                    Time value at risk: <span className="font-mono font-semibold text-warning">{fmt$(Math.abs(m.time_value))}</span>
                    <span className="text-base-content/40">(decays)</span>
                  </span>
                  <span className="text-base-content/70">
                    Decay rate: <span className={`font-mono font-semibold ${m.theta_per_day < 0 ? 'text-error' : 'text-success'}`}>{fmt$(m.theta_per_day)}/day</span>
                  </span>
                  <span className="text-base-content/70">
                    Intrinsic: <span className="font-mono font-semibold">{fmt$(Math.abs(m.intrinsic_value))}</span>
                    <span className="text-base-content/40"> (recoverable)</span>
                  </span>
                </div>
                <p className="text-[10px] text-base-content/40 mt-1">
                  Lower time value = less theta bleed. Deep-ITM puts carry mostly intrinsic value, so they barely decay.
                </p>
              </div>

              {/* Risk profile — Monte Carlo */}
              {risk && (
                <div className="rounded-xl border border-info/20 bg-info/5 p-3">
                  <div className="flex items-center gap-2 mb-2">
                    <Gauge className="w-4 h-4 text-info" />
                    <span className="text-sm font-semibold">Risk Profile</span>
                    <span className="text-[10px] text-base-content/40">{(samples.length / 1000).toFixed(0)}k Monte-Carlo paths @ {result.atm_iv ?? '—'}% IV over {result.dte}d</span>
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-center">
                    <div>
                      <p className="text-[10px] uppercase tracking-wider text-base-content/50">CVaR 95% (hedged)</p>
                      <p className="text-base font-bold font-mono text-error">{fmt$(risk.cvar_hedged)}</p>
                      <p className="text-[10px] text-base-content/40">was {fmt$(risk.cvar_unhedged)} naked</p>
                    </div>
                    <div>
                      <p className="text-[10px] uppercase tracking-wider text-base-content/50">Tail Loss Cut</p>
                      <p className="text-base font-bold font-mono text-success">−{fmt$(risk.cvar_reduction)}</p>
                      <p className="text-[10px] text-base-content/40">avg worst-5% improvement</p>
                    </div>
                    <div>
                      <p className="text-[10px] uppercase tracking-wider text-base-content/50">VaR 95% (hedged)</p>
                      <p className="text-base font-bold font-mono">{fmt$(risk.var_hedged)}</p>
                      <p className="text-[10px] text-base-content/40">was {fmt$(risk.var_unhedged)} naked</p>
                    </div>
                    <div>
                      <p className="text-[10px] uppercase tracking-wider text-base-content/50">P(hedge pays)</p>
                      <p className="text-base font-bold font-mono">{Math.round(risk.prob_pays * 100)}%</p>
                      <p className="text-[10px] text-base-content/40">exp. payoff {fmt$(risk.expected_hedge_pl)}</p>
                    </div>
                  </div>
                </div>
              )}

              </>)}

              {detailTab === 'legs' && (<>
              {/* Legs table — editable */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-sm font-semibold flex items-center gap-2"><TrendingUp className="w-4 h-4" /> Option Legs ({legs.length})</h4>
                  <div className="flex gap-2">
                    <button className={`btn btn-xs gap-1 ${editing ? 'btn-accent' : 'btn-outline'}`} onClick={() => setEditing(!editing)}>
                      <Edit3 className="w-3 h-3" /> {editing ? 'Done editing' : 'Edit strikes'}
                    </button>
                    {isEdited && <button className="btn btn-xs btn-ghost gap-1" onClick={resetHedge}><RotateCcw className="w-3 h-3" /> Reset</button>}
                  </div>
                </div>
                {editing && (
                  <p className="text-[11px] text-base-content/50 mb-2">
                    Change the side, type, strike or contract count — or add and remove legs to build your own structure. Prices, cost, floor and the scenario chart all update instantly.
                  </p>
                )}
                <div className="overflow-x-auto">
                  <table className="table table-xs table-pro w-full">
                    <thead>
                      <tr className="text-base-content/60">
                        <th>Action</th><th>Type</th><th>Strike</th><th>Contracts</th>
                        <th>Bid</th><th>Ask</th><th>Mid</th><th>IV</th>
                        <th title="Given today's vol surface, are you buying this cheap / selling it rich?">Timing</th>
                        <th>Delta</th><th>Theta</th><th>OI</th><th>Vol</th>
                        {editing && <th></th>}
                      </tr>
                    </thead>
                    <tbody>
                      {legs.map((leg, i) => (
                        <tr key={i} className={leg.action === 'BUY' ? 'bg-success/5' : 'bg-error/5'}>
                          <td>
                            {editing ? (
                              <select className="select select-bordered select-xs w-[4.5rem]" value={leg.action}
                                onChange={(e) => updateLeg(i, { action: e.target.value as 'BUY' | 'SELL' })}>
                                <option value="BUY">BUY</option>
                                <option value="SELL">SELL</option>
                              </select>
                            ) : <span className={`badge badge-xs ${leg.action === 'BUY' ? 'badge-success' : 'badge-error'}`}>{leg.action}</span>}
                          </td>
                          <td className="font-medium">
                            {editing ? (
                              <select className="select select-bordered select-xs w-[4.5rem]" value={leg.type}
                                onChange={(e) => {
                                  const newType = e.target.value as 'PUT' | 'CALL';
                                  const avail = newType === 'PUT' ? putStrikes : callStrikes;
                                  const snapped = avail.includes(leg.strike) ? leg.strike
                                    : avail.reduce((b, s) => (Math.abs(s - leg.strike) < Math.abs(b - leg.strike) ? s : b), avail[0] ?? leg.strike);
                                  updateLeg(i, { type: newType, strike: snapped });
                                }}>
                                <option value="PUT">PUT</option>
                                <option value="CALL">CALL</option>
                              </select>
                            ) : leg.type}
                          </td>
                          <td>
                            {editing ? (
                              <select className="select select-bordered select-xs w-24 font-mono" value={leg.strike}
                                onChange={(e) => updateLeg(i, { strike: Number(e.target.value) })}>
                                {(leg.type === 'PUT' ? putStrikes : callStrikes).map((s) => <option key={s} value={s}>${s}</option>)}
                              </select>
                            ) : <span className="font-mono">${leg.strike}</span>}
                          </td>
                          <td>
                            {editing ? (
                              <input type="number" min={1} className="input input-bordered input-xs w-16 font-mono" value={leg.contracts}
                                onChange={(e) => updateLeg(i, { contracts: Math.max(1, Number(e.target.value)) })} />
                            ) : <span className="font-mono">{leg.contracts}</span>}
                          </td>
                          <td className="font-mono">${leg.bid.toFixed(2)}</td>
                          <td className="font-mono">${leg.ask.toFixed(2)}</td>
                          <td className="font-mono font-medium text-primary">${leg.mid.toFixed(2)}
                            {leg.mid > 0 && leg.ask > leg.bid && (leg.ask - leg.bid) / leg.mid > 0.15 && (
                              <span className="text-warning ml-0.5 cursor-help"
                                title={`Wide market: bid-ask is ${Math.round(((leg.ask - leg.bid) / leg.mid) * 100)}% of mid — the mid may be a mirage. Work a limit order; don't cross the spread.`}>⚠</span>
                            )}</td>
                          <td>{leg.iv != null ? `${leg.iv}%` : '—'}</td>
                          <td>{(() => { const t = legTiming(leg, result.market); return t
                            ? <span className={`badge badge-xs whitespace-nowrap ${TONE_BADGE[t.tone]}`} title={`Edge ${(t.edge * 100).toFixed(0)} — ${t.label === 'selling rich' || t.label === 'buying cheap' ? 'favorable' : t.label === 'selling cheap' || t.label === 'buying rich' ? 'unfavorable' : 'neutral'}`}>{t.label}</span>
                            : <span className="text-base-content/30">—</span>; })()}</td>
                          <td className="font-mono">{leg.delta != null ? leg.delta.toFixed(3) : '—'}</td>
                          <td className="font-mono">{leg.theta != null ? leg.theta.toFixed(3) : '—'}</td>
                          <td>{leg.oi.toLocaleString()}</td>
                          <td>{leg.vol.toLocaleString()}</td>
                          {editing && (
                            <td>
                              <button className="btn btn-ghost btn-xs text-error px-1" title="Remove leg"
                                onClick={() => removeLeg(i)} disabled={legs.length <= 1}>
                                <Trash2 className="w-3 h-3" />
                              </button>
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {editing && (
                  <button className="btn btn-xs btn-outline btn-success gap-1 mt-2" onClick={addLeg}>
                    <Plus className="w-3 h-3" /> Add leg
                  </button>
                )}
              </div>

              {/* Save controls */}
              <div className="flex items-center gap-2">
                {showSaveInput ? (
                  <>
                    <input type="text" className="input input-sm input-bordered w-52" placeholder="Name this hedge..." value={saveName}
                      onChange={(e) => setSaveName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && saveName.trim() && doSave(true)} autoFocus />
                    <button className="btn btn-sm btn-success gap-1" onClick={() => doSave(true)} disabled={!saveName.trim() || savingFlag}>
                      {savingFlag ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />} Save
                    </button>
                    <button className="btn btn-sm btn-ghost" onClick={() => setShowSaveInput(false)}>Cancel</button>
                  </>
                ) : (
                  <>
                    <button className="btn btn-sm btn-outline btn-success gap-1" onClick={() => { setShowSaveInput(true); setSaveName(''); }}>
                      <Save className="w-3 h-3" /> Save as New
                    </button>
                    {loadedId && (
                      <button className="btn btn-sm btn-outline btn-info gap-1" onClick={() => { const l = saved.find((s) => s.id === loadedId); setSaveName(l?.name ?? ''); doSave(false); }} disabled={savingFlag}>
                        {savingFlag ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />} Update
                      </button>
                    )}
                  </>
                )}
              </div>

              </>)}

              {/* Interactive scenario analysis */}
              {detailTab === 'scenario' && (
              <div className="rounded-xl border border-primary/20 bg-primary/5 p-4 space-y-4">
                <div className="flex items-center gap-2">
                  <Sliders className="w-4 h-4 text-primary" />
                  <h4 className="text-sm font-semibold">What if the price moves? — drag to explore</h4>
                  <span className="text-[10px] text-base-content/40">P&amp;L at expiration ({result.expiration})</span>
                </div>
                <div>
                  <div className="flex items-center justify-between mb-1 text-xs">
                    <span className="text-base-content/60">{result.ticker} move from today</span>
                    <span className="font-mono font-bold">{movePct > 0 ? '+' : ''}{movePct}% → ${scenarioPrice.toFixed(2)}</span>
                  </div>
                  <input type="range" min={-100} max={100} step={1} value={movePct}
                    onChange={(e) => setMovePct(Number(e.target.value))} className="range range-primary range-xs w-full" />
                  <div className="flex justify-between text-[10px] text-base-content/40 mt-0.5">
                    <span>-100%</span><span>-50%</span><span>0</span><span>+50%</span><span>+100%</span>
                  </div>
                </div>

                {scenario && (
                  <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                    <div className="bg-base-100/40 rounded-lg p-3 text-center">
                      <p className="text-[10px] uppercase tracking-wider text-base-content/50">Without Hedge</p>
                      <p className={`text-xl font-bold ${pnlColor(scenario.unhedged)}`}>{fmt$(scenario.unhedged)}</p>
                      <p className="text-[10px] text-base-content/40">stock only</p>
                    </div>
                    <div className="bg-base-100/40 rounded-lg p-3 text-center">
                      <p className="text-[10px] uppercase tracking-wider text-base-content/50">Hedge Payoff</p>
                      <p className={`text-xl font-bold ${pnlColor(scenario.hedge)}`}>{fmt$(scenario.hedge)}</p>
                      <p className="text-[10px] text-base-content/40">options</p>
                    </div>
                    <div className="bg-base-100/40 rounded-lg p-3 text-center border border-primary/30">
                      <p className="text-[10px] uppercase tracking-wider text-base-content/50">With Hedge (net)</p>
                      <p className={`text-xl font-bold ${pnlColor(scenario.net)}`}>{fmt$(scenario.net)}</p>
                      <p className="text-[10px] text-base-content/40">hedge {scenario.hedge >= 0 ? 'added' : 'cost'} {fmt$(Math.abs(scenario.hedge))}</p>
                    </div>
                  </div>
                )}

                {chart && (
                  <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]" style={{ height: 220 }}>
                    <Line
                      data={{
                        labels: chart.labels,
                        datasets: [
                          { label: 'With hedge', data: chart.hedged, borderColor: '#36d399', backgroundColor: 'rgba(54,211,153,0.08)', fill: true, tension: 0, pointRadius: 0, borderWidth: 2 },
                          { label: 'No hedge', data: chart.naked, borderColor: '#f87272', borderDash: [5, 4], fill: false, tension: 0, pointRadius: 0, borderWidth: 1.5 },
                          { label: 'Break-even', data: chart.labels.map(() => 0), borderColor: 'rgba(255,255,255,0.2)', borderDash: [3, 3], pointRadius: 0, borderWidth: 1 },
                        ],
                      }}
                      options={{
                        responsive: true, maintainAspectRatio: false,
                        plugins: {
                          legend: { display: true, labels: { font: { size: 10 }, boxWidth: 12 } },
                          tooltip: { callbacks: { title: (it) => `Price $${it[0].label}`, label: (ctx) => `${ctx.dataset.label}: ${fmt$(Number(ctx.parsed.y ?? 0))}` } },
                        },
                        scales: {
                          x: { title: { display: true, text: 'Price at Expiration', font: { size: 10 } }, ticks: { font: { size: 9 }, maxTicksLimit: 9, callback: (_v, i) => `$${chart.labels[i]}` }, grid: { color: 'rgba(255,255,255,0.05)' } },
                          y: { title: { display: true, text: 'P&L ($)', font: { size: 10 } }, ticks: { font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
                        },
                      }}
                    />
                  </div>
                )}

                <div className="overflow-x-auto">
                  <table className="table table-xs table-pro w-full">
                    <thead>
                      <tr className="text-base-content/60"><th>Move</th><th>Price</th><th>Without Hedge</th><th>With Hedge</th><th>Hedge Helped</th></tr>
                    </thead>
                    <tbody>
                      {scenarioGrid.map((r) => (
                        <tr key={r.move} className={`cursor-pointer ${r.move === movePct ? 'bg-primary/10' : 'hover:bg-base-200/40'}`} onClick={() => setMovePct(r.move)}>
                          <td className={`font-mono ${r.move < 0 ? 'text-error' : r.move > 0 ? 'text-success' : ''}`}>{r.move > 0 ? '+' : ''}{r.move}%</td>
                          <td className="font-mono">${r.price.toFixed(2)}</td>
                          <td className={`font-mono ${pnlColor(r.unhedged)}`}>{fmt$(r.unhedged)}</td>
                          <td className={`font-mono font-semibold ${pnlColor(r.net)}`}>{fmt$(r.net)}</td>
                          <td className="font-mono text-xs">
                            {r.net - r.unhedged > 1 ? <span className="text-success">+{fmt$(r.net - r.unhedged)}</span>
                              : r.net - r.unhedged < -1 ? <span className="text-error/70">{fmt$(r.net - r.unhedged)}</span> : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="text-[10px] text-base-content/40">P&amp;L vs today's price, options held to expiry. Click a row or drag the slider to focus a scenario.</p>
              </div>
              )}

              {/* Tax considerations — strike-aware, live */}
              {detailTab === 'tax' && (
                <div className="space-y-2">
                  <div className="text-[11px] text-base-content/50 bg-base-200/30 rounded-lg px-3 py-2 border border-white/[0.03]">
                    General US-federal guidance, computed from this hedge's actual strikes — <strong>not tax advice</strong>. Confirm with a qualified tax professional.
                  </div>
                  {taxFlags(legs, m, result.ticker, spot).map((t, i) => (
                    <div key={i} className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
                      <div className="flex items-center gap-2 mb-1">
                        {t.severity === 'High' && <XCircle className="w-4 h-4 text-error" />}
                        {t.severity === 'Medium' && <AlertCircle className="w-4 h-4 text-warning" />}
                        {t.severity === 'Low' && <CheckCircle2 className="w-4 h-4 text-success" />}
                        {t.severity === 'Info' && <Info className="w-4 h-4 text-info" />}
                        <span className="font-medium text-sm">{t.category}</span>
                        <span className={`badge badge-xs ${severityColor(t.severity)}`}>{t.severity}</span>
                      </div>
                      <p className="text-xs text-base-content/70">{t.note}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Macro overlay — beta-weighted index put (collapsible alternative) */}
          {result.index_overlay && (
            <div>
              <button className="btn btn-ghost btn-sm gap-2 mb-2" onClick={() => setShowOverlay(!showOverlay)}>
                <Globe className="w-4 h-4 text-info" /> Macro Alternative — Beta-Weighted Index Put
                {showOverlay ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
              </button>
              {showOverlay && <IndexOverlayCard overlay={result.index_overlay} notional={result.notional} />}
            </div>
          )}

          {/* Risks */}
          {result.risks?.length > 0 && (
            <div>
              <button className="btn btn-ghost btn-sm gap-2 mb-2" onClick={() => setShowRisks(!showRisks)}>
                <Shield className="w-4 h-4 text-warning" /> Risk Analysis &amp; Mitigation
                {showRisks ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
              </button>
              {showRisks && (
                <div className="space-y-2">
                  {result.risks.map((risk, i) => (
                    <div key={i} className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
                      <div className="flex items-center gap-2 mb-1">
                        {risk.severity === 'High' && <XCircle className="w-4 h-4 text-error" />}
                        {risk.severity === 'Medium' && <AlertCircle className="w-4 h-4 text-warning" />}
                        {risk.severity === 'Low' && <CheckCircle2 className="w-4 h-4 text-success" />}
                        {risk.severity === 'Info' && <Info className="w-4 h-4 text-info" />}
                        <span className="font-medium text-sm">{risk.category}</span>
                        <span className={`badge badge-xs ${severityColor(risk.severity)}`}>{risk.severity}</span>
                      </div>
                      <p className="text-xs text-base-content/60 mb-1">{risk.description}</p>
                      <p className="text-xs text-base-content/80"><strong>Mitigation:</strong> {risk.mitigation}</p>
                    </div>
                  ))}
                </div>
              )}

              {/* Desk Review — pre-trade advisory (Risk · Trader · PM · Quant) */}
              {(() => {
                if (!hedge || !m) return null;
                const reduces = risk ? risk.cvar_reduction > 0 : true;
                const tone: 'good' | 'warn' | 'bad' = reduces && m.within_budget
                  ? 'good' : (reduces || m.within_budget ? 'warn' : 'bad');
                const quantMetrics: AdvisorMetric[] = [
                  { label: 'Worst Loss', value: fmt$(m.max_loss), tone: 'bad', hint: 'Max loss on the hedged position' },
                  { label: 'Floor', value: m.floor != null ? `$${m.floor}` : '—', hint: 'Protected floor price' },
                  { label: 'Cap', value: m.cap != null ? `$${m.cap}` : 'None' },
                  { label: m.is_credit ? 'Net Credit' : 'Net Cost', value: m.is_credit ? `+${fmt$(-m.net_cost)}` : fmt$(m.net_cost), tone: m.is_credit ? 'good' : '' },
                  { label: 'CVaR95 ↓', value: risk ? fmt$(risk.cvar_reduction) : '—', tone: 'good', hint: 'Tail-loss reduction vs unhedged' },
                  { label: 'Prob Pays', value: risk ? `${Math.round(risk.prob_pays * 100)}%` : '—', hint: 'Chance the hedge finishes profitable' },
                  { label: 'Cost %', value: `${Math.abs(m.cost_pct).toFixed(2)}%`, tone: m.within_budget ? '' : 'warn' },
                  { label: 'DTE', value: `${result.dte}` },
                ];
                const quantVerdict = {
                  label: tone === 'good' ? 'Efficient protection' : tone === 'warn' ? 'Protection at a cost — weigh it' : 'Weak protection for the cost',
                  tone,
                  note: risk ? `cuts tail loss by ${fmt$(risk.cvar_reduction)} for ${m.is_credit ? 'a credit' : fmt$(m.net_cost)}` : undefined,
                };
                const llmMetrics = {
                  current_price: spot,
                  shares_held: result.shares,
                  position_value: Math.round(result.shares * spot),
                  net_cost: Math.round(m.net_cost), cost_pct: m.cost_pct, is_credit: m.is_credit ? 'yes' : 'no',
                  protected_floor: m.floor, floor_pct: m.floor_pct, buffer_bottom: m.buffer_bottom,
                  upside_cap: m.cap, cap_pct: m.cap_pct,
                  max_loss: Math.round(m.max_loss), max_loss_pct: m.max_loss_pct,
                  upside_breakeven: m.upside_breakeven,
                  net_delta: m.greeks.delta, net_gamma: m.greeks.gamma, net_theta: m.greeks.theta, net_vega: m.greeks.vega,
                  var95_hedged: risk ? Math.round(risk.var_hedged) : null,
                  cvar95_hedged: risk ? Math.round(risk.cvar_hedged) : null,
                  cvar95_unhedged: risk ? Math.round(risk.cvar_unhedged) : null,
                  cvar_reduction: risk ? Math.round(risk.cvar_reduction) : null,
                  prob_hedge_pays_pct: risk ? Math.round(risk.prob_pays * 100) : null,
                  horizon_dte: result.dte, annualized_cost_pct: m.annualized,
                };
                // Full hedged-position payoff so the agents judge the whole profile.
                const posValue = result.shares * spot;
                const scenarios = [-30, -20, -10, -5, 0, 5, 10, 20].map(mv => {
                  const P = spot * (1 + mv / 100);
                  const pnl = result.shares * (P - spot) + hedgePnL(legs, P);
                  return { move_pct: mv, price: Math.round(P * 100) / 100, pnl: Math.round(pnl), roi: posValue ? Math.round(pnl / posValue * 1000) / 10 : 0 };
                });
                return (
                  <PreTradeAdvisor
                    ticker={(ticker || '').toUpperCase()}
                    strategyType={`hedge_${hedge.id}`}
                    legs={legs.map(l => ({ action: l.action, contracts: l.contracts, type: l.type, strike: l.strike, expiration: l.expiration, iv: l.iv ?? undefined }))}
                    llmMetrics={llmMetrics}
                    scenarios={scenarios}
                    breakevens={m.upside_breakeven ? [`$${m.upside_breakeven} (upside breakeven)`] : []}
                    expiration={result.expiration}
                    spot={spot}
                    capital={posValue}
                    dte={result.dte}
                    stockShares={result.shares}
                    maxLoss={-Math.abs(m.max_loss)}
                    maxProfit={m.cap != null ? Math.round(result.shares * (m.cap - spot) + hedgePnL(legs, m.cap)) : null}
                    quantMetrics={quantMetrics}
                    quantVerdict={quantVerdict}
                    notes={`${describe(hedge.id, m, spot)} Protecting ${result.shares} shares of ${(ticker || '').toUpperCase()} (long) — the hedge caps the loss; the "max loss" is the floored worst case, not a routine outcome.`}
                  />
                );
              })()}
            </div>
          )}
        </div>
      )}

      <AgentCreateModal
        open={showAgentModal}
        onClose={() => setShowAgentModal(false)}
        onCreate={async (data) => { await createAgent(data); }}
        initial={{
          name: `Hedge watch — ${(ticker || '').toUpperCase()}${horizon ? ` (${horizon}d)` : ''}`.trim(),
          description: `Watches the hedge-timing window for ${(ticker || 'this ticker').toUpperCase()} and flags when it's a good time to put on protection.`,
          instruction:
`Monitor the hedge-timing window for ${(ticker || '').toUpperCase()} over a ~${horizon}-day horizon.

Each run, call the get_hedge_timing tool for "${(ticker || '').toUpperCase()}" (horizon_days=${horizon}) and report:
- the timing score and verdict (Favorable / Fair / Expensive) and the one-line headline
- VIX level and term structure (contango vs backwardation)
- implied-vol rank, variance risk premium, and put/call skew
- 1-week and 1-month price trend and the drawdown from the 52-week high

Then judge whether NOW is a good time to put on downside protection with these preferences:
- Floor (where protection starts): ${downside}%   (0 = protect from today's price)
- Downside cap (where protection stops / sell a put): ${downsideCap}%   (0 = none)
- Upside cap: ${upside}%   (0 = unlimited upside)
- Give-up: ${upsideGiveup}%   (0 = none)

Conclude with a clear recommendation — HEDGE NOW or WAIT — and one sentence of reasoning grounded in the VIX term structure (contango = protection is cheaper, a normal window to hedge; backwardation = protection is rich and you may already be late). Keep it concise.`,
          schedule_type: 'recurring',
          schedule_cron: '0 13 * * 1-5',
          send_email_on_run: true,
          email_report_to: user?.email || '',
        } as Partial<AgentCreateInput>}
      />
    </div>
  );
}
