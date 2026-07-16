import React, { useState } from 'react';
import {
  Coins, Loader2, AlertTriangle, Info, Search, Briefcase, Shield,
  TrendingUp, Gauge, DollarSign, Calendar, Clock, CheckCircle2, ShieldCheck,
  AlertCircle, ChevronDown, ChevronUp, Database, Zap, Activity, Landmark,
  BarChart3, Target,
} from 'lucide-react';
import { runDerivativeIncome, runDerivativeIncomePortfolio, fetchTechnicalForTimeframe } from '../api';
import type {
  DerivativeIncomeResult, DerivativeIncomeOpportunity, DerivativeIncomePortfolioResult,
  DerivativeIncomePortfolioRow, DerivativeIncomeFlag, DerivativeIncomeExpirySummary,
  DerivativeIncomeContext, DerivativeIncomeQuant, DerivativeIncomeLeg, TechnicalData,
} from '../types';
import { TechnicalAnalysis } from './TechnicalAnalysis';
import PreTradeAdvisor, { type AdvisorMetric, type QuantSignal } from './PreTradeAdvisor';

type Mode = 'single' | 'portfolio';

/** Build the pricing-confidence signals (RND/SVI/chain/expected move/Heston) that
 * now live inside each opportunity's Quant desk card. */
function buildQuantSignals(quant?: DerivativeIncomeQuant | null): QuantSignal[] {
  if (!quant) return [];
  const h = quant.heston;
  const sig: QuantSignal[] = [
    { label: 'Probability model', value: quant.rnd_available ? 'RND (smile)' : 'BS fallback', sub: quant.rnd_available ? 'Breeden-Litzenberger' : 'thin chain' },
    { label: 'SVI fit', value: quant.svi_rmse_vol_pts != null ? `${quant.svi_rmse_vol_pts} vp` : '—', sub: quant.arb_free == null ? 'rmse' : quant.arb_free ? 'arb-free ✓' : 'arb flag ⚠' },
    { label: 'Chain depth', value: `${quant.n_quotes ?? '—'}`, sub: 'strikes fit' },
    { label: 'Expected move', value: quant.expected_move_pct != null ? `±${quant.expected_move_pct}%` : '—', sub: '1σ to expiry' },
  ];
  if (h) sig.push({ label: 'Heston (QuantLib)', value: h.vol_of_vol != null ? `σᵥ ${h.vol_of_vol}` : '—', sub: `ρ ${h.spot_vol_corr ?? '—'} · ${h.feller_ok ? 'Feller ✓' : 'Feller ⚠'}` });
  return sig;
}

const STRUCTURE_OPTIONS = [
  { id: 'covered_call', label: 'Covered Call', icon: <TrendingUp className="w-3.5 h-3.5" /> },
  { id: 'cash_secured_put', label: 'Cash-Secured Put', icon: <DollarSign className="w-3.5 h-3.5" /> },
  { id: 'collar', label: 'Collar', icon: <Shield className="w-3.5 h-3.5" /> },
  { id: 'credit_spread', label: 'Credit Spreads', icon: <ShieldCheck className="w-3.5 h-3.5" /> },
];

const money = (n: number | null | undefined, d = 0) =>
  n == null ? '—' : `$${n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const pct = (n: number | null | undefined, d = 1) => (n == null ? '—' : `${n.toFixed(d)}%`);
// Geometric annualization is the repo standard but explodes for short-dated, high-ROC
// trades — format compactly; the period (static) return is the honest anchor.
const annPct = (n: number | null | undefined) =>
  n == null ? '—' : n >= 1000 ? `${Math.round(n).toLocaleString()}%` : `${n.toFixed(0)}%`;

const probTone = (p: number) => (p >= 95 ? 'text-success' : p >= 85 ? 'text-success/90' : 'text-warning');
const richnessBadge = (r: string) =>
  r === 'rich' ? 'badge-success' : r === 'cheap' ? 'badge-warning' : 'badge-ghost';
const confTone = (l?: string) =>
  l === 'High' ? 'text-success bg-success/10 border-success/25'
    : l === 'Medium' ? 'text-warning bg-warning/10 border-warning/25'
      : 'text-error bg-error/10 border-error/25';

// ─────────────────────────── small pieces ───────────────────────────

function FlagPill({ flag }: { flag: DerivativeIncomeFlag }) {
  const tone = flag.level === 'good' ? 'text-success bg-success/10 border-success/20'
    : flag.level === 'warn' ? 'text-warning bg-warning/10 border-warning/20'
      : 'text-info bg-info/10 border-info/20';
  const Icon = flag.level === 'good' ? CheckCircle2 : flag.level === 'warn' ? AlertCircle : Info;
  return (
    <span className={`inline-flex items-start gap-1.5 text-[11px] rounded-lg border px-2 py-1 ${tone}`}>
      <Icon className="w-3 h-3 mt-0.5 shrink-0" />
      <span>{flag.text}</span>
    </span>
  );
}

function ConfidenceBadge({ conf }: { conf?: DerivativeIncomeOpportunity['confidence'] }) {
  const [open, setOpen] = useState(false);
  if (!conf) return null;
  return (
    <div className="relative">
      <button type="button" onClick={() => setOpen(o => !o)}
        className={`inline-flex items-center gap-1 text-[11px] font-bold rounded-lg border px-2 py-1 ${confTone(conf.label)}`}>
        <Gauge className="w-3 h-3" /> {conf.label} · {conf.score}
        <ChevronDown className="w-3 h-3 opacity-60" />
      </button>
      {open && conf.reasons?.length > 0 && (
        <div className="absolute z-20 right-0 mt-1 w-60 bg-base-100 border border-base-300 rounded-xl shadow-xl p-2 text-[11px] space-y-1">
          <p className="font-semibold text-base-content/70">Confidence drivers</p>
          {conf.reasons.map((r, i) => (
            <div key={i} className="flex items-start gap-1.5 text-base-content/60">
              <span className="text-secondary mt-0.5">•</span><span>{r}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function MetricTile({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: string }) {
  return (
    <div className="bg-base-200/40 rounded-lg p-2.5 border border-white/[0.03]">
      <p className="text-[10px] uppercase tracking-wider text-base-content/50">{label}</p>
      <p className={`text-sm font-bold ${tone || ''}`}>{value}</p>
      {sub != null && <div className="text-[10px] text-base-content/50">{sub}</div>}
    </div>
  );
}

// 52-week range with a live spot marker (req: spot + 52W H-L)
function Week52Bar({ ctx }: { ctx: DerivativeIncomeContext }) {
  const w = ctx.week52;
  if (!w) return null;
  const posClamped = Math.max(2, Math.min(98, w.position_pct));
  return (
    <div className="min-w-[220px] flex-1">
      <div className="flex items-center justify-between text-[10px] text-base-content/50 mb-1">
        <span>52W Low {money(w.low, 0)}</span>
        <span className="text-base-content/70 font-semibold">{pct(w.position_pct, 0)} of range</span>
        <span>52W High {money(w.high, 0)}</span>
      </div>
      <div className="relative h-2.5 rounded-full bg-gradient-to-r from-error/40 via-warning/40 to-success/40">
        <div className="absolute -top-1 w-1.5 h-4.5 rounded-full bg-base-content shadow"
          style={{ left: `calc(${posClamped}% - 3px)`, height: '18px' }}
          title={`Spot ${money(ctx.spot, 2)}`} />
      </div>
    </div>
  );
}

function TickerHeader({ result, ctx, shares, costBasis }: {
  result: DerivativeIncomeResult; ctx: DerivativeIncomeContext; shares?: number; costBasis?: number | null;
}) {
  const gainPct = (shares != null && costBasis != null && costBasis > 0)
    ? ((ctx.spot - costBasis) / costBasis) * 100 : null;
  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/30 p-4">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div>
          {/* Heading: TICKER  $price  #shares  cost-basis  ER */}
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="text-xl font-bold">{result.ticker}</span>
            <span className="text-3xl font-bold tabular-nums">{money(ctx.spot, 2)}</span>
            {shares != null && <span className="text-sm text-base-content/70">{shares.toLocaleString()} sh</span>}
            {costBasis != null && (
              <span className="text-sm text-base-content/60">
                basis {money(costBasis, 2)}
                {gainPct != null && <span className={gainPct >= 0 ? 'text-success ml-1' : 'text-error ml-1'}>({gainPct >= 0 ? '+' : ''}{gainPct.toFixed(1)}%)</span>}
              </span>
            )}
            {ctx.next_earnings && <span className="badge badge-warning badge-sm gap-1"><Calendar className="w-3 h-3" />ER {ctx.next_earnings}</span>}
          </div>
          <div className="flex flex-wrap items-center gap-2 mt-1">
            <span className="text-xs text-base-content/50">{ctx.shares_per_contract} sh/contract = {money(ctx.notional_per_contract, 0)}</span>
            {ctx.european && <span className="badge badge-success badge-xs gap-1"><Shield className="w-3 h-3" />{ctx.exercise_style}</span>}
            <span className="badge badge-info badge-xs gap-1">
              {result.quote_source === 'ibkr' ? <Zap className="w-3 h-3" /> : <Database className="w-3 h-3" />}{result.quote_source}
            </span>
          </div>
        </div>
        <Week52Bar ctx={ctx} />
        <div className="flex flex-wrap gap-2">
          <span className="badge badge-outline badge-sm gap-1"><Landmark className="w-3 h-3" />SOFR {pct(ctx.sofr_pct)}</span>
          {ctx.hv30_pct != null && <span className="badge badge-outline badge-sm">HV30 {pct(ctx.hv30_pct)}</span>}
        </div>
      </div>
    </div>
  );
}

// Events banner — common (market-wide) events shown once at the top; ticker-specific
// events (earnings) shown per name.
function EventsBanner({ events, title }: { events: DerivativeIncomeFlag[]; title?: string }) {
  if (!events?.length) return null;
  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/20 p-3">
      <p className="text-[10px] uppercase tracking-wider text-base-content/50 mb-2 flex items-center gap-1.5">
        <Calendar className="w-3.5 h-3.5" /> {title || 'Upcoming events · next 90 days'}
      </p>
      <div className="flex flex-wrap gap-1.5">
        {events.map((e, i) => <FlagPill key={i} flag={e} />)}
      </div>
    </div>
  );
}

// Institutional quant read that builds confidence (req 6)
// Standard app leg table (req 4)
function LegsTable({ legs }: { legs: DerivativeIncomeLeg[] }) {
  const hasGreeks = legs.some(l => l.delta != null);
  return (
    <div className="overflow-x-auto">
      <table className="table table-xs table-pro w-full">
        <thead>
          <tr className="text-base-content/60">
            <th>Action</th><th>Type</th><th>Strike</th>
            <th title="Probability the underlying reaches this strike by expiry">P(reach)</th>
            <th>Exp</th>
            <th>Bid</th><th>Ask</th><th>Mid</th><th>IV</th><th>OI</th><th>Vol</th>
            {hasGreeks && (<><th>Δ</th><th>Θ</th></>)}
          </tr>
        </thead>
        <tbody>
          {legs.map((l, i) => (
            <tr key={i} className={l.action === 'BUY' ? 'bg-success/5' : 'bg-error/5'}>
              <td><span className={`badge badge-xs ${l.action === 'BUY' ? 'badge-success' : 'badge-error'}`}>{l.action}</span></td>
              <td className="font-medium">{l.type}</td>
              <td className="font-mono">${l.strike}</td>
              <td className="font-mono">{l.prob_reach_pct != null ? `${l.prob_reach_pct}%` : '—'}</td>
              <td className="text-[10px] text-base-content/50">{l.expiration?.slice(5)}</td>
              <td className="font-mono">${l.bid.toFixed(2)}</td>
              <td className="font-mono">${l.ask.toFixed(2)}</td>
              <td className="font-mono font-medium">${l.mid.toFixed(2)}</td>
              <td>{l.iv != null ? `${l.iv}%` : '—'}</td>
              <td>{l.oi.toLocaleString()}</td>
              <td>{l.vol.toLocaleString()}</td>
              {hasGreeks && (<>
                <td className="font-mono">{l.delta != null ? l.delta.toFixed(2) : '—'}</td>
                <td className="font-mono">{l.theta != null ? l.theta.toFixed(2) : '—'}</td>
              </>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Lazy technical analysis — reuses the app's TA component; fetched + mounted only on expand (req 5)
function LazyTechnicals({ ticker }: { ticker: string }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<TechnicalData | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !data && !loading) {
      setLoading(true); setErr(null);
      try {
        const r = await fetchTechnicalForTimeframe(ticker, 'medium_term');
        setData(r.technical as TechnicalData);
      } catch (e: any) {
        setErr(e?.message || 'Failed to load technicals');
      } finally { setLoading(false); }
    }
  };

  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/20">
      <button type="button" onClick={toggle}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold">
        <span className="flex items-center gap-2"><BarChart3 className="w-4 h-4 text-secondary" /> Technical Analysis
          <span className="text-[10px] font-normal text-base-content/40">price · support/resistance · 20/50/200 MA · MACD · RSI</span>
        </span>
        {open ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
      </button>
      {open && (
        <div className="px-3 pb-3">
          {loading && (
            <div className="flex items-center gap-2 text-xs text-base-content/50 py-8 justify-center">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading technicals…
            </div>
          )}
          {err && <div className="alert alert-error text-xs"><AlertTriangle className="w-4 h-4" /><span>{err}</span></div>}
          {data && !loading && <TechnicalAnalysis technical={data} ticker={ticker} />}
        </div>
      )}
    </div>
  );
}

function OpportunityCard({ opp, compact, ticker, spot, quant }: {
  opp: DerivativeIncomeOpportunity; compact?: boolean; ticker?: string; spot?: number; quant?: DerivativeIncomeQuant | null;
}) {
  const [showLegs, setShowLegs] = useState(false);
  const isSpread = opp.structure.includes('spread');
  const isCollar = opp.structure === 'collar';
  const strikeStr = isSpread ? `${opp.short_strike} / ${opp.long_strike}`
    : isCollar ? `cap ${opp.short_strike} · floor ${opp.floor_strike}`
      : `${opp.short_strike}`;

  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/30 p-4 space-y-3 hover:border-secondary/30 transition-colors">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-bold text-sm">{opp.label}</span>
            <ConfidenceBadge conf={opp.confidence} />
          </div>
          <p className="text-xs text-base-content/50 mt-0.5">
            Strike {strikeStr}
            {opp.short_strike_pct != null && (
              <span className="text-base-content/70 font-medium"> ({opp.short_strike_pct >= 0 ? '+' : ''}{opp.short_strike_pct}%)</span>
            )} · {opp.dte}d · exp {opp.expiration}
          </p>
        </div>
        <div className="text-right shrink-0">
          <p className={`text-2xl font-bold ${probTone(opp.prob_keep_pct)}`}>{pct(opp.prob_keep_pct)}</p>
          <p className="text-[10px] uppercase tracking-wider text-base-content/50">keep · {opp.prob_method}</p>
        </div>
      </div>

      {/* Hero metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <MetricTile label="Premium income" tone="text-success"
          value={money(opp.contracts ? opp.total_premium : opp.premium, 0)}
          sub={opp.contracts ? `${opp.contracts}× $${opp.premium.toFixed(0)}` : `$${opp.premium_per_share.toFixed(2)}/sh`} />
        <MetricTile label="Return / period" value={pct(opp.static_return_pct)} sub={`~${annPct(opp.premium_annualized_pct)} ann.`} />
        <MetricTile label="vs SOFR" tone={opp.beats_sofr ? 'text-success' : 'text-base-content/70'}
          value={opp.beats_sofr ? `+${annPct(opp.sofr_excess_pct)}` : 'below'} sub="excess yield" />
        <MetricTile label="Capital / risk" value={money(opp.max_loss != null ? opp.max_loss : opp.collateral, 0)}
          sub={opp.max_loss != null ? 'max loss (defined)' : 'collateral'} />
      </div>

      {/* Secondary metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <MetricTile label="Theta / day" tone="text-success" value={money(opp.theta_per_day, 0)} sub="decay income" />
        <MetricTile label="Breakeven" value={`$${opp.breakeven}`} sub={`${pct(opp.cushion_pct)} cushion`} />
        <MetricTile label="Short Δ" value={opp.short_delta != null ? opp.short_delta.toFixed(2) : '—'} sub="exercise proxy" />
        <MetricTile label="IV / HV" value={opp.iv_hv_ratio != null ? `${opp.iv_hv_ratio}×` : '—'}
          sub={<span className={`badge badge-xs ${richnessBadge(opp.premium_richness)}`}>{opp.premium_richness}</span>} />
      </div>

      {/* collar / spread extras */}
      {(isCollar || isSpread) && (
        <div className="flex flex-wrap gap-3 text-[11px] text-base-content/60">
          {isCollar && opp.prob_in_band_pct != null && <span>P(in band): <b>{pct(opp.prob_in_band_pct)}</b></span>}
          {isCollar && opp.floor_pct != null && <span>Floor: <b>{pct(opp.floor_pct)}</b></span>}
          {opp.max_profit != null && <span>Max profit: <b>{money(opp.max_profit, 0)}</b></span>}
          {opp.width != null && <span>Width: <b>${opp.width}</b></span>}
          {opp.expected_pnl != null && <span>E[P&amp;L]: <b>{money(opp.expected_pnl, 0)}</b></span>}
        </div>
      )}

      {/* opportunity-specific flags only (common events live at top) */}
      {opp.flags?.length > 0 && (
        <div className="flex flex-wrap gap-1.5">{opp.flags.map((f, i) => <FlagPill key={i} flag={f} />)}</div>
      )}

      {/* Desk Review — lazily computed when expanded (Risk · Trader · PM · Quant) */}
      {!compact && ticker && spot ? (() => {
        const m0 = (v: number) => money(v, 0);
        const contracts = opp.contracts || 1;
        const hasStock = opp.structure === 'covered_call' || opp.structure === 'collar';
        const capital = opp.max_loss != null ? Math.abs(opp.max_loss) : opp.collateral;
        const beats = opp.beats_sofr;
        const llmMetrics = {
          structure: opp.structure, prob_keep_pct: opp.prob_keep_pct, prob_method: opp.prob_method,
          static_return_pct: opp.static_return_pct, annualized_pct: opp.premium_annualized_pct,
          sofr_excess_pct: opp.sofr_excess_pct, beats_sofr: beats ? 'yes' : 'no',
          premium: opp.contracts ? (opp.total_premium ?? opp.premium) : opp.premium,
          max_loss: opp.max_loss, collateral: opp.collateral, max_profit: opp.max_profit,
          breakeven: opp.breakeven, cushion_pct: opp.cushion_pct,
          short_delta: opp.short_delta, iv_hv_ratio: opp.iv_hv_ratio, premium_richness: opp.premium_richness,
          expected_pnl: opp.expected_pnl, theta_per_day: opp.theta_per_day, dte: opp.dte, expiration: opp.expiration,
        };
        return (
          <div className="pt-1">
            <PreTradeAdvisor
              lazy
              ticker={ticker}
              strategyType={`income_${opp.structure}`}
              legs={opp.legs.map(l => ({ action: l.action, type: l.type, strike: l.strike, expiration: l.expiration, iv: l.iv ?? undefined, price: l.mid }))}
              llmMetrics={llmMetrics}
              expiration={opp.expiration}
              spot={spot}
              capital={capital}
              dte={opp.dte}
              stockShares={hasStock ? 100 * contracts : 0}
              maxLoss={opp.max_loss != null ? -Math.abs(opp.max_loss) : (opp.collateral ? -opp.collateral : null)}
              maxProfit={opp.max_profit ?? (opp.contracts ? (opp.total_premium ?? opp.premium) : opp.premium)}
              quantSignals={buildQuantSignals(quant)}
              breakevens={[`$${opp.breakeven} (${pct(opp.cushion_pct)} cushion)`]}
              notes={`Income trade (${opp.label}) — selling premium with a ${pct(opp.prob_keep_pct)} probability of NOT being exercised; capital/risk ${capital != null ? m0(capital) : '—'}. Evaluating whether to sell this.`}
            />
          </div>
        );
      })() : null}

      {/* standard legs table (expandable) */}
      {!compact && opp.legs?.length > 0 && (
        <div>
          <button className="btn btn-ghost btn-xs gap-1" onClick={() => setShowLegs(s => !s)}>
            <Activity className="w-3 h-3" /> Trade legs ({opp.legs.length})
            {showLegs ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>
          {showLegs && <div className="mt-1"><LegsTable legs={opp.legs} /></div>}
        </div>
      )}
    </div>
  );
}

function ExpiryChips({ summaries }: { summaries: DerivativeIncomeExpirySummary[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {summaries.map((s) => (
        <div key={s.expiration} className="rounded-lg border border-white/[0.06] bg-base-200/30 px-3 py-2 text-xs">
          <div className="flex items-center gap-2">
            <Calendar className="w-3 h-3 text-base-content/50" />
            <span className="font-medium">{s.expiration}</span>
            <span className="text-base-content/50">{s.dte}d</span>
            {s.monthly && <span className="badge badge-xs badge-outline">monthly</span>}
          </div>
          <div className="flex items-center gap-2 mt-1 text-[11px] text-base-content/60">
            <span>IV {pct(s.atm_iv_pct)}</span><span>·</span><span>HV {pct(s.hv30_pct)}</span>
            {s.iv_hv_ratio != null && <span className={`badge badge-xs ${richnessBadge(s.premium_richness)}`}>{s.iv_hv_ratio}× {s.premium_richness}</span>}
            <span className="text-base-content/40">{s.n_opportunities} ideas</span>
          </div>
        </div>
      ))}
    </div>
  );
}

// Full single-ticker detail — reused for the Single-Ticker tab AND for each
// expanded holding in Portfolio mode, so every ticker gets identical treatment.
function SingleTickerResult({ result, shares, costBasis, hideCommonEvents }: {
  result: DerivativeIncomeResult; shares?: number; costBasis?: number | null; hideCommonEvents?: boolean;
}) {
  const primaryQuant = result.expiry_summaries?.[0]?.quant;
  const spot = result.context?.spot;
  // Pricing-confidence signals are per-expiration; fall back to the primary one.
  const quantForExp = (exp: string) => result.expiry_summaries?.find(s => s.expiration === exp)?.quant ?? primaryQuant;
  // In portfolio mode the market-wide (common) events are shown once at the top,
  // so a holding's expanded view only repeats its ticker-specific events.
  const shownEvents = hideCommonEvents ? (result.events || []).filter(e => e.scope !== 'common') : result.events;
  return (
    <div className="space-y-4">
      {result.context && <TickerHeader result={result} ctx={result.context} shares={shares} costBasis={costBasis} />}
      {shownEvents && shownEvents.length > 0 && (
        <EventsBanner events={shownEvents} title={hideCommonEvents ? `${result.ticker} events` : undefined} />
      )}
      <LazyTechnicals ticker={result.ticker} />

      {result.expiry_summaries?.length > 0 && <ExpiryChips summaries={result.expiry_summaries} />}

      {result.best_by_structure?.length > 0 ? (
        <>
          <h4 className="text-sm font-semibold flex items-center gap-2">
            <Target className="w-4 h-4 text-secondary" /> Best opportunity per structure
          </h4>
          <div className="grid grid-cols-1 gap-3">
            {result.best_by_structure.map((o, i) => (
              <OpportunityCard key={i} opp={o} ticker={result.ticker} spot={spot} quant={quantForExp(o.expiration)} />
            ))}
          </div>
        </>
      ) : (
        <div className="alert alert-warning text-sm">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          <span>{result.note || 'No executable opportunities cleared your filters. Try a lower probability, a longer target DTE, or a more volatile underlying.'}</span>
        </div>
      )}

      {result.opportunities.length > result.best_by_structure.length && (
        <details className="rounded-xl border border-white/[0.06] bg-base-200/20">
          <summary className="cursor-pointer select-none px-4 py-3 text-sm font-semibold flex items-center gap-2">
            <TrendingUp className="w-4 h-4" /> All {result.opportunities.length} ranked opportunities
          </summary>
          <div className="overflow-x-auto px-2 pb-2">
            <table className="table table-xs w-full">
              <thead>
                <tr className="text-base-content/50">
                  <th>Structure</th><th>Strike</th><th>DTE</th><th>P(keep)</th><th>Conf</th>
                  <th>Premium</th><th>Period</th><th>Ann.</th><th>Max loss</th><th>Spread</th>
                </tr>
              </thead>
              <tbody>
                {result.opportunities.map((o, i) => (
                  <tr key={i}>
                    <td className="whitespace-nowrap">{o.label}</td>
                    <td className="font-mono">{o.short_strike}{o.long_strike ? `/${o.long_strike}` : ''}</td>
                    <td>{o.dte}</td>
                    <td className={probTone(o.prob_keep_pct)}>{pct(o.prob_keep_pct)}</td>
                    <td><span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${confTone(o.confidence?.label)}`}>{o.confidence?.label ?? '—'}</span></td>
                    <td className="text-success">{money(o.premium, 0)}</td>
                    <td>{pct(o.static_return_pct)}</td>
                    <td className="text-base-content/60">{annPct(o.premium_annualized_pct)}</td>
                    <td>{o.max_loss != null ? money(o.max_loss, 0) : <span className="opacity-50">open</span>}</td>
                    <td>{o.liquidity.spread_pct != null ? `${o.liquidity.spread_pct}%` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}

interface DiParams {
  target_dte: number | null;
  min_prob: number;
  min_income: number;
  structures: string[];
  quote_source: string;
}

// One portfolio holding: compact best-opportunity summary + an expandable, lazily
// fetched FULL single-ticker analysis (TA, quant, all structures) — so Portfolio
// mode is consistent with the Single-Ticker tab.
function PortfolioHoldingRow({ row, params }: { row: DerivativeIncomePortfolioRow; params: DiParams }) {
  const [expanded, setExpanded] = useState(false);
  const [full, setFull] = useState<DerivativeIncomeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const toggle = async () => {
    const next = !expanded;
    setExpanded(next);
    if (next && !full && !loading) {
      setLoading(true); setErr(null);
      try {
        const r = await runDerivativeIncome(row.ticker, params);   // cached from the sweep
        if (r.error) setErr(r.error); else setFull(r);
      } catch (e: any) { setErr(e?.message || 'Failed to load analysis'); }
      finally { setLoading(false); }
    }
  };

  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/30 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-bold text-sm">{row.ticker}</span>
          <span className="text-xs text-base-content/50">{row.shares} sh · {money(row.market_value, 0)}</span>
          {row.exercise_style && <span className="badge badge-xs badge-ghost">{row.exercise_style}</span>}
          {row.next_earnings && <span className="text-[11px] text-warning">ER {row.next_earnings}</span>}
        </div>
        {row.best_opportunity && (
          <div className="flex items-center gap-3 text-xs">
            <ConfidenceBadge conf={row.best_opportunity.confidence} />
            <span className={`font-bold ${probTone(row.best_opportunity.prob_keep_pct)}`}>{pct(row.best_opportunity.prob_keep_pct)} keep</span>
            <span className="text-success font-semibold">{money(row.best_opportunity.total_premium ?? row.best_opportunity.premium, 0)}</span>
          </div>
        )}
      </div>

      {row.best_opportunity ? (
        <div className="mt-2"><OpportunityCard opp={row.best_opportunity} compact /></div>
      ) : (
        <p className="text-xs text-base-content/50 mt-1 flex items-center gap-1"><Info className="w-3 h-3" /> {row.note || 'No qualifying covered call right now.'}</p>
      )}

      {row.best_opportunity && (
        <div className="mt-2">
          <button type="button" onClick={toggle} className="btn btn-ghost btn-xs gap-1 text-secondary">
            <BarChart3 className="w-3 h-3" /> {expanded ? 'Hide full analysis' : 'Full analysis — TA, quant & all structures'}
            {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>
          {expanded && (
            <div className="mt-2 pl-2 border-l-2 border-secondary/20">
              {loading && (
                <div className="flex items-center gap-2 text-xs text-base-content/50 py-4 justify-center">
                  <Loader2 className="w-4 h-4 animate-spin" /> Loading {row.ticker} analysis…
                </div>
              )}
              {err && <div className="alert alert-error text-xs"><AlertTriangle className="w-4 h-4" /><span>{err}</span></div>}
              {full && !loading && <SingleTickerResult result={full} shares={row.shares} costBasis={row.cost_basis} hideCommonEvents />}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────── main ───────────────────────────

export function DerivativeIncome() {
  const [mode, setMode] = useState<Mode>('single');
  const [ticker, setTicker] = useState('AAPL');
  const [targetDte, setTargetDte] = useState('');
  const [minProb, setMinProb] = useState(85);
  const [minIncome, setMinIncome] = useState(20);
  const [structures, setStructures] = useState<string[]>(['covered_call', 'cash_secured_put', 'collar', 'credit_spread']);
  const [quoteSource, setQuoteSource] = useState<'yfinance' | 'ibkr'>('yfinance');

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DerivativeIncomeResult | null>(null);

  const [pfResult, setPfResult] = useState<DerivativeIncomePortfolioResult | null>(null);
  const [pfLoading, setPfLoading] = useState(false);
  const [pfError, setPfError] = useState<string | null>(null);

  const toggleStructure = (id: string) =>
    setStructures(s => (s.includes(id) ? s.filter(x => x !== id) : [...s, id]));

  const commonParams = () => ({
    target_dte: targetDte.trim() ? Number(targetDte) : null,
    min_prob: minProb / 100,
    min_income: minIncome,
    structures,
    quote_source: quoteSource,
  });

  const handleSingle = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true); setError(null); setResult(null);
    try {
      const data = await runDerivativeIncome(ticker.trim().toUpperCase(), commonParams());
      if (data.error) setError(data.error); else setResult(data);
    } catch (err: any) { setError(err?.message || 'Failed to scan opportunities'); }
    finally { setLoading(false); }
  };

  const handlePortfolio = async (offset = 0) => {
    setPfLoading(true); setPfError(null);
    if (offset === 0) setPfResult(null);
    try {
      const data = await runDerivativeIncomePortfolio({ offset, limit: 10, ...commonParams() });
      if (data.error && !data.results?.length) setPfError(data.error); else setPfResult(data);
    } catch (err: any) { setPfError(err?.message || 'Failed to scan portfolio'); }
    finally { setPfLoading(false); }
  };

  return (
    <div className="space-y-4">
      {/* Intro */}
      <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
        <div className="flex items-start gap-3">
          <Coins className="w-5 h-5 text-secondary mt-0.5 shrink-0" />
          <div className="text-sm text-base-content/70">
            <p className="font-semibold text-base-content mb-1">Derivative Income</p>
            <p>
              Sell option premium with a high probability of <b>not being exercised</b> — covered calls,
              cash-secured puts, collars and defined-risk credit spreads — ranked by annualized yield over SOFR.
              Probabilities come from the market-implied <b>risk-neutral density</b> (SVI smile), and only
              <b> executable</b> quotes (real bid, tight spread, live book) are shown.
            </p>
          </div>
        </div>
      </div>

      {/* Mode toggle */}
      <div className="flex gap-1 p-1 bg-base-200/40 rounded-xl border border-white/[0.03] w-fit">
        <button type="button" className={`btn btn-sm gap-1.5 ${mode === 'single' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setMode('single')}>
          <Search className="w-3.5 h-3.5" /> Single Ticker
        </button>
        <button type="button" className={`btn btn-sm gap-1.5 ${mode === 'portfolio' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setMode('portfolio')}>
          <Briefcase className="w-3.5 h-3.5" /> My Portfolio
        </button>
      </div>

      {/* Form */}
      <form onSubmit={mode === 'single' ? handleSingle : (e) => { e.preventDefault(); handlePortfolio(0); }}
        className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {mode === 'single' && (
          <div className="form-control">
            <label className="label py-1"><span className="label-text text-xs font-medium">Ticker (ETF / stock / index)</span></label>
            <input type="text" className="input input-bordered input-sm w-full" value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())} placeholder="AAPL, SPY, .SPX" required />
          </div>
        )}
        <div className="form-control">
          <label className="label py-1"><span className="label-text text-xs font-medium">Target days-to-expiry</span></label>
          <div className="relative">
            <Calendar className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40" />
            <input type="number" className="input input-bordered input-sm w-full pl-7" value={targetDte}
              onChange={(e) => setTargetDte(e.target.value)} min={1} max={365} placeholder="blank = monthlies ≤45d" />
          </div>
        </div>
        <div className="form-control">
          <label className="label py-1">
            <span className="label-text text-xs font-medium">Min. prob. not exercised: <b className="text-secondary">{minProb}%</b></span>
          </label>
          <input type="range" min={70} max={99} step={1} value={minProb}
            onChange={(e) => setMinProb(Number(e.target.value))} className="range range-secondary range-xs mt-2" />
        </div>
        <div className="form-control">
          <label className="label py-1"><span className="label-text text-xs font-medium">Min. premium ($/contract)</span></label>
          <div className="relative">
            <DollarSign className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40" />
            <input type="number" className="input input-bordered input-sm w-full pl-7" value={minIncome}
              onChange={(e) => setMinIncome(Number(e.target.value))} min={0} step={5} />
          </div>
        </div>
        <div className="form-control">
          <label className="label py-1"><span className="label-text text-xs font-medium">Quote source</span></label>
          <select className="select select-bordered select-sm w-full" value={quoteSource}
            onChange={(e) => setQuoteSource(e.target.value as 'yfinance' | 'ibkr')}>
            <option value="yfinance">Yahoo Finance (free, delayed)</option>
            <option value="ibkr">IBKR (real-time, Greeks)</option>
          </select>
        </div>
        <div className="form-control lg:col-span-3">
          <label className="label py-1"><span className="label-text text-xs font-medium">Structures to evaluate</span></label>
          <div className="flex flex-wrap gap-2">
            {STRUCTURE_OPTIONS.map((s) => (
              <button type="button" key={s.id}
                className={`btn btn-xs gap-1 ${structures.includes(s.id) ? 'btn-secondary' : 'btn-outline'}`}
                onClick={() => toggleStructure(s.id)}>{s.icon}{s.label}</button>
            ))}
          </div>
        </div>
        <div className="form-control lg:col-span-3">
          {mode === 'single' ? (
            <button type="submit" className="btn btn-primary btn-sm gap-2 w-fit" disabled={loading || !ticker.trim() || !structures.length}>
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Coins className="w-4 h-4" />}
              {loading ? 'Scanning options…' : 'Find Income Opportunities'}
            </button>
          ) : (
            <button type="submit" className="btn btn-primary btn-sm gap-2 w-fit" disabled={pfLoading || !structures.length}>
              {pfLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Briefcase className="w-4 h-4" />}
              {pfLoading ? 'Scanning holdings…' : 'Scan Top Holdings'}
            </button>
          )}
        </div>
      </form>

      {/* ───────── SINGLE-TICKER RESULTS ───────── */}
      {mode === 'single' && error && (
        <div className="alert alert-error text-sm"><AlertTriangle className="w-4 h-4 shrink-0" /><span>{error}</span></div>
      )}

      {mode === 'single' && result && <SingleTickerResult result={result} />}

      {/* ───────── PORTFOLIO RESULTS ───────── */}
      {mode === 'portfolio' && pfError && (
        <div className="alert alert-error text-sm"><AlertTriangle className="w-4 h-4 shrink-0" /><span>{pfError}</span></div>
      )}

      {mode === 'portfolio' && pfResult && (
        <div className="space-y-3">
          <div className="bg-base-200/40 rounded-xl p-3 flex flex-wrap items-center gap-3 text-sm border border-white/[0.03]">
            <Briefcase className="w-4 h-4 text-secondary" />
            <span className="text-base-content/70">
              Holdings {pfResult.offset + 1}–{Math.min(pfResult.offset + pfResult.limit, pfResult.total_holdings)} of {pfResult.total_holdings}
            </span>
            <span className="text-base-content/50">· ≥{pfResult.min_prob_pct}% keep · ≥${pfResult.min_income} premium · executable only</span>
          </div>

          {pfResult.common_events && pfResult.common_events.length > 0 && (
            <EventsBanner events={pfResult.common_events} title="Market events · next 90 days" />
          )}

          <div className="space-y-2">
            {pfResult.results.map((row) => (
              <PortfolioHoldingRow key={row.ticker} row={row} params={commonParams()} />
            ))}
          </div>

          <div className="flex items-center gap-2">
            {pfResult.offset > 0 && (
              <button className="btn btn-outline btn-sm gap-1" disabled={pfLoading}
                onClick={() => handlePortfolio(Math.max(0, pfResult.offset - pfResult.limit))}>
                <ChevronUp className="w-3.5 h-3.5" /> Previous 10
              </button>
            )}
            {pfResult.has_more && pfResult.next_offset != null && (
              <button className="btn btn-primary btn-sm gap-1" disabled={pfLoading}
                onClick={() => handlePortfolio(pfResult.next_offset!)}>
                {pfLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ChevronDown className="w-3.5 h-3.5" />}
                Next 10 holdings
              </button>
            )}
          </div>
          <p className="text-[11px] text-base-content/40 flex items-center gap-1">
            <Clock className="w-3 h-3" /> Top {pfResult.limit} holdings per page are scanned to spare the quote provider. Results cached briefly.
          </p>
        </div>
      )}
    </div>
  );
}
