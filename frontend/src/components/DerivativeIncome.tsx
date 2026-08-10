import React, { useState, useEffect } from 'react';
import {
  Coins, Loader2, AlertTriangle, Info, Search, Briefcase, Shield,
  TrendingUp, Gauge, DollarSign, Calendar, Clock, CheckCircle2, ShieldCheck,
  AlertCircle, ChevronDown, ChevronUp, Activity, Landmark,
  BarChart3, Layers, Feather, ClipboardCheck, Plus, Trash2,
} from 'lucide-react';
import { runDerivativeIncome, runDerivativeIncomePortfolio, runDeskReview, evaluateDeskTrade, fetchTechnicalForTimeframe, fetchOptionExpirations } from '../api';
import type { DeskEvaluateParams, EvaluateLeg } from '../api';
import type {
  DerivativeIncomeResult, DerivativeIncomeOpportunity, DerivativeIncomePortfolioResult,
  DerivativeIncomePortfolioRow, DerivativeIncomeFlag,
  DerivativeIncomeContext, DerivativeIncomeQuant, DerivativeIncomeLeg, DerivativeIncomeVolStats,
  DerivativeIncomeExpirySummary, DeskReviewResult,
  TechnicalData,
} from '../types';
import { TechnicalAnalysis } from './TechnicalAnalysis';
import PreTradeAdvisor, { type AdvisorMetric, type QuantSignal } from './PreTradeAdvisor';
import { DeskReview, SingleTradeDeskReview } from './DeskReview';

type Mode = 'single' | 'portfolio' | 'evaluate';

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
  { id: 'short_strangle', label: 'Short Strangle', icon: <Activity className="w-3.5 h-3.5" /> },
  { id: 'credit_spread', label: 'Credit Spreads', icon: <ShieldCheck className="w-3.5 h-3.5" /> },
  { id: 'iron_condor', label: 'Iron Condor', icon: <Layers className="w-3.5 h-3.5" /> },
  { id: 'jade_lizard', label: 'Jade Lizard', icon: <Feather className="w-3.5 h-3.5" /> },
  { id: 'calendar', label: 'Calendar', icon: <Calendar className="w-3.5 h-3.5" /> },
];

const money = (n: number | null | undefined, d = 0) =>
  n == null ? '—' : `$${n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const pct = (n: number | null | undefined, d = 1) => (n == null ? '—' : `${n.toFixed(d)}%`);
// Win / keep probability shown to the seller: asymptotic to — never exactly — 100%, so cap the
// DISPLAY at 99.9% (100.0% reads as a false guarantee). One decimal, floored at 0.
const winPct = (n: number | null | undefined) =>
  n == null ? '—' : `${Math.min(99.9, Math.max(0, n)).toFixed(1)}%`;
// Calendar-days from today to a YYYY-MM-DD expiry (UTC midnights → no DST drift).
const dteFromExpiry = (iso: string): number => {
  const [y, m, d] = iso.split('-').map(Number);
  const exp = Date.UTC(y, (m || 1) - 1, d || 1);
  const now = new Date();
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return Math.round((exp - today) / 86400000);
};

// Standard monthly option expiries (3rd Friday) for the next `n` months, ISO yyyy-mm-dd, today forward.
// The Portfolio scan has no single ticker, so it offers these; a picked date is sent as a target DTE and
// each holding maps to its own nearest listed expiry (±10-day band on the backend).
const upcomingMonthlyExpiries = (n = 6): string[] => {
  const out: string[] = [];
  const now = new Date();
  const todayUTC = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  let y = now.getUTCFullYear(), m = now.getUTCMonth();
  while (out.length < n) {
    const firstDow = new Date(Date.UTC(y, m, 1)).getUTCDay();
    const thirdFriday = 1 + ((5 - firstDow + 7) % 7) + 14;   // first Friday of month + 2 weeks
    const d = Date.UTC(y, m, thirdFriday);
    if (d >= todayUTC) out.push(new Date(d).toISOString().slice(0, 10));
    m++; if (m > 11) { m = 0; y++; }
  }
  return out;
};
// Geometric annualization is the repo standard but explodes for short-dated, high-ROC
// trades — format compactly; the period (static) return is the honest anchor.
const annPct = (n: number | null | undefined) =>
  n == null ? '—' : n >= 1000 ? `${Math.round(n).toLocaleString()}%` : `${n.toFixed(0)}%`;

const probTone = (p: number) => (p >= 95 ? 'text-success' : p >= 85 ? 'text-success/90' : 'text-warning');
// rich premium (good for a seller) = green; cheap (poor) = red; fair = neutral. Kept OFF amber so it
// never collides with the amber earnings (ER) badge — different information, different colour.
const richnessBadge = (r: string) =>
  r === 'rich' ? 'badge-success' : r === 'cheap' ? 'badge-error' : 'badge-ghost';
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
  // Only ticker + expiry_summaries are read here — narrowed so both the scan result and the
  // folded desk-review payload satisfy it.
  result: { ticker: string; expiry_summaries?: DerivativeIncomeExpirySummary[] };
  ctx: DerivativeIncomeContext; shares?: number; costBasis?: number | null;
}) {
  const gainPct = (shares != null && costBasis != null && costBasis > 0)
    ? ((ctx.spot - costBasis) / costBasis) * 100 : null;
  const primaryExp = result.expiry_summaries?.[0];   // nearest expiry — its IV/HV richness reads at stock level
  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/30 p-3">
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div>
          {/* Heading: TICKER  $price  #shares  cost-basis  ER */}
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="text-lg font-bold">{result.ticker}</span>
            <span className="text-2xl font-bold tabular-nums">{money(ctx.spot, 2)}</span>
            {shares != null && <span className="text-sm text-base-content/70">{shares.toLocaleString()} sh</span>}
            {costBasis != null && (
              <span className="text-sm text-base-content/60">
                basis {money(costBasis, 2)}
                {gainPct != null && <span className={gainPct >= 0 ? 'text-success ml-1' : 'text-error ml-1'}>({gainPct >= 0 ? '+' : ''}{gainPct.toFixed(1)}%)</span>}
              </span>
            )}
            {ctx.next_earnings && <span className="badge badge-warning badge-sm gap-1"><Calendar className="w-3 h-3" />ER {ctx.next_earnings}</span>}
          </div>
          {ctx.european && (
            <div className="flex flex-wrap items-center gap-2 mt-1">
              <span className="badge badge-success badge-xs gap-1"><Shield className="w-3 h-3" />{ctx.exercise_style}</span>
            </div>
          )}
          <div className="flex flex-wrap gap-2 mt-2">
            <span className="badge badge-outline badge-sm gap-1"><Landmark className="w-3 h-3" />SOFR {pct(ctx.sofr_pct)}</span>
            {ctx.hv30_pct != null && <span className="badge badge-outline badge-sm">HV30 {pct(ctx.hv30_pct)}</span>}
            {primaryExp?.iv_hv_ratio != null && (
              <span className={`badge badge-sm ${richnessBadge(primaryExp.premium_richness)}`}
                title={`Nearest expiry ${primaryExp.expiration} (${primaryExp.dte}d): ATM IV ${pct(primaryExp.atm_iv_pct)} vs HV ${pct(primaryExp.hv30_pct)} — premium is ${primaryExp.premium_richness}`}>
                IV/HV {primaryExp.iv_hv_ratio}× {primaryExp.premium_richness}
              </span>
            )}
          </div>
        </div>
        <div className="w-full lg:w-[400px]">
          <Week52Bar ctx={ctx} />
        </div>
      </div>
    </div>
  );
}

// Events banner — common (market-wide) events shown once at the top; ticker-specific
// events (earnings) shown per name.
export function EventsBanner({ events, title }: { events: DerivativeIncomeFlag[]; title?: string }) {
  if (!events?.length) return null;
  return (
    <details className="rounded-xl border border-white/[0.06] bg-base-200/20 group">
      <summary className="cursor-pointer list-none select-none px-3 py-2.5 text-[10px] uppercase tracking-wider text-base-content/50 flex items-center gap-1.5 [&::-webkit-details-marker]:hidden">
        <Calendar className="w-3.5 h-3.5" /> {title || 'Upcoming events · next 90 days'}
        <span className="normal-case text-base-content/35">· {events.length}</span>
        <ChevronDown className="w-3.5 h-3.5 ml-auto opacity-50 transition-transform duration-200 group-open:rotate-180" />
      </summary>
      <div className="px-4 pb-4 pt-1">
        <div className="relative border-l-2 border-white/10 ml-2 pl-4 space-y-4">
          {events.map((e, i) => {
            const parts = e.text.split(':');
            const dateStr = parts.length > 1 ? parts[0].trim() : '';
            const eventStr = parts.length > 1 ? parts.slice(1).join(':').trim() : e.text;
            return (
              <div key={i} className="relative flex items-baseline gap-3">
                <div className={`absolute -left-[21px] top-1.5 w-2 h-2 rounded-full border border-base-200 shadow-sm ${
                  e.level === 'warn' ? 'bg-warning' : e.level === 'good' ? 'bg-success' : 'bg-info'
                }`} />
                {dateStr && <div className="text-xs font-semibold whitespace-nowrap text-base-content/60 w-16">{dateStr}</div>}
                <div className="text-xs font-medium text-base-content/80 leading-snug">{eventStr}</div>
              </div>
            );
          })}
        </div>
      </div>
    </details>
  );
}

// Institutional quant read that builds confidence (req 6)
// Standard app leg table (req 4)
export function LegsTable({ legs }: { legs: DerivativeIncomeLeg[] }) {
  const hasGreeks = legs.some(l => l.delta != null);
  return (
    <div className="w-full overflow-x-auto">
      <table className="table table-xs table-pro w-full">
        <thead>
          <tr className="text-base-content/60">
            <th>Leg</th><th>Strike</th>
            <th title="Probability the underlying reaches this strike by expiry">P(reach)</th>
            <th>Exp</th>
            <th>Bid/Ask</th><th>Mid</th><th>IV</th><th>OI/Vol</th>
            {hasGreeks && (<><th>Δ</th><th>Θ</th></>)}
          </tr>
        </thead>
        <tbody>
          {legs.map((l, i) => (
            <tr key={i} className={l.action === 'BUY' ? 'bg-success/5' : 'bg-error/5'}>
              <td className="whitespace-nowrap">
                <span className={`badge badge-xs mr-1.5 ${l.action === 'BUY' ? 'badge-success' : 'badge-error'}`}>{l.action}</span>
                <span className="font-medium">{l.type}</span>
              </td>
              <td className="font-mono">${l.strike}</td>
              <td className="font-mono">{l.prob_reach_pct != null ? `${l.prob_reach_pct}%` : '—'}</td>
              <td className="text-[10px] text-base-content/50 whitespace-nowrap">{l.expiration?.slice(5)}</td>
              <td className="font-mono text-[10px] text-base-content/50 whitespace-nowrap">${l.bid.toFixed(2)} / ${l.ask.toFixed(2)}</td>
              <td className="font-mono font-medium">${l.mid.toFixed(2)}</td>
              <td>{l.iv != null ? `${l.iv}%` : '—'}</td>
              <td className="text-[10px] text-base-content/60 whitespace-nowrap">{l.oi.toLocaleString()} / {l.vol.toLocaleString()}</td>
              {hasGreeks && (<>
                <td className="font-mono text-[11px]">{l.delta != null ? l.delta.toFixed(2) : '—'}</td>
                <td className="font-mono text-[11px]">{l.theta != null ? l.theta.toFixed(2) : '—'}</td>
              </>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Lazy technical analysis — reuses the app's TA component; fetched + mounted only on expand (req 5)
export function LazyTechnicals({ ticker }: { ticker: string }) {
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
        <span className="flex items-center gap-2"><BarChart3 className="w-4 h-4 text-secondary" /> Technical Analysis</span>
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

// The summary "hero" (header + metric tiles + multi-leg extras + flags) — reused by the ranked
// desk-review explorer so its top matches this card exactly. No outer card wrapper: the caller frames it.
export function OpportunitySummary({ opp }: { opp: DerivativeIncomeOpportunity }) {
  const isSpread = opp.structure.includes('spread');
  const isStrangle = opp.structure === 'short_strangle';
  const isCondor = opp.structure === 'iron_condor';
  const isJade = opp.structure === 'jade_lizard';
  const strikeStr = isCondor ? `${opp.put_long}/${opp.put_short} – ${opp.call_short}/${opp.call_long}`
    : isJade ? `put ${opp.put_short} · call ${opp.call_short}/${opp.call_long}`
      : isStrangle ? `put ${opp.put_short} · call ${opp.call_short}`
        : isSpread ? `${opp.short_strike} / ${opp.long_strike}`
          : `${opp.short_strike}`;
  return (
    <div className="space-y-3">
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
          <p className={`text-2xl font-bold ${probTone(opp.prob_keep_pct)}`}>{winPct(opp.prob_keep_pct)}</p>
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
        {opp.capital_basis === 'reg_t_margin' ? (
          <MetricTile label="Margin (BPR)" value={money(opp.collateral, 0)}
            sub={opp.notional_capital ? `Reg-T · risk ${money(opp.notional_capital, 0)}` : 'Reg-T naked margin'} />
        ) : (
          <MetricTile label="Capital / risk" value={money(opp.max_loss != null ? opp.max_loss : opp.collateral, 0)}
            sub={opp.max_loss != null ? 'max loss (defined)' : 'collateral'} />
        )}
      </div>

      {/* Secondary metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <MetricTile label="Theta / day" tone="text-success" value={money(opp.theta_per_day, 0)} sub="decay income" />
        <MetricTile label="Breakeven" value={`$${opp.breakeven}`} sub={`${pct(opp.cushion_pct)} cushion`} />
        <MetricTile label="Short Δ" value={opp.short_delta != null ? opp.short_delta.toFixed(2) : '—'} sub="exercise proxy" />
        <MetricTile label="IV / HV" value={opp.iv_hv_ratio != null ? `${opp.iv_hv_ratio}×` : '—'}
          sub={<span className={`badge badge-xs ${richnessBadge(opp.premium_richness)}`}>{opp.premium_richness}</span>} />
      </div>

      {/* multi-leg extras (strangle / spread / condor / jade lizard) */}
      {(isStrangle || isSpread || isCondor || isJade) && (
        <div className="flex flex-wrap gap-3 text-[11px] text-base-content/60">
          {opp.prob_in_band_pct != null && <span>P(in band): <b>{pct(opp.prob_in_band_pct)}</b></span>}
          {(isCondor || isStrangle) && opp.band_low != null && <span>Profit band: <b>${opp.band_low}–${opp.band_high}</b></span>}
          {isStrangle && <span className="text-warning">⚠ undefined risk (naked)</span>}
          {opp.max_profit != null && <span>Max profit: <b>{money(opp.max_profit, 0)}</b></span>}
          {opp.width != null && <span>Width: <b>${opp.width}</b></span>}
          {opp.expected_pnl != null && <span>E[P&amp;L]: <b>{money(opp.expected_pnl, 0)}</b></span>}
        </div>
      )}

      {/* opportunity-specific flags only (common events live at top) */}
      {opp.flags?.length > 0 && (
        <div className="flex flex-wrap gap-1.5">{opp.flags.map((f, i) => <FlagPill key={i} flag={f} />)}</div>
      )}
    </div>
  );
}

export function OpportunityCard({ opp, compact, ticker, spot, quant, deskParams }: {
  opp: DerivativeIncomeOpportunity; compact?: boolean; ticker?: string; spot?: number; quant?: DerivativeIncomeQuant | null;
  deskParams?: DiParams;
}) {
  const [showLegs, setShowLegs] = useState(false);

  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/30 p-4 space-y-3 hover:border-secondary/30 transition-colors">
      <OpportunitySummary opp={opp} />

      {/* Desk Review — lazily computed when expanded (Risk · Trader · PM · Quant) */}
      {!compact && ticker && spot ? (() => {
        const m0 = (v: number) => money(v, 0);
        const contracts = opp.contracts || 1;
        const hasStock = opp.structure === 'covered_call';
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
      {ticker && deskParams && !compact && (
        <SingleTradeDeskReview
          ticker={ticker}
          params={deskParams}
          trade={{
            structure: opp.structure, expiration: opp.expiration, label: opp.label,
            short_strike: opp.short_strike ?? opp.put_short ?? opp.call_short ?? null,
          }}
        />
      )}
    </div>
  );
}


// Full single-ticker detail — reused for the Single-Ticker tab AND for each
// expanded holding in Portfolio mode, so every ticker gets identical treatment.
const rankTone = (v: number | null | undefined) =>
  v == null ? '' : v >= 70 ? 'text-success' : v >= 40 ? 'text-warning' : 'text-base-content/70';

// Per-ticker volatility read: IV/vol rank & percentile + skew.
export function VolatilityPanel({ vs }: { vs: DerivativeIncomeVolStats }) {
  const skewSub = vs.skew_pts == null ? '—' : vs.skew_pts > 1 ? 'puts richer' : vs.skew_pts < -1 ? 'calls richer' : 'flat';
  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/20 p-3">
      <p className="text-xs font-bold flex items-center gap-1.5 mb-2">
        <Activity className="w-4 h-4 text-secondary" /> Volatility
        <span className="text-[10px] font-normal text-base-content/40" title={vs.basis}>
          — IV/vol rank vs trailing 1y realized vol · higher = premium richer
        </span>
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-2">
        <MetricTile label="ATM IV" value={pct(vs.iv_atm_pct)}
          sub={vs.iv_atm_pct != null && vs.hv30_pct ? `${(vs.iv_atm_pct / vs.hv30_pct).toFixed(2)}× HV30` : 'implied'} />
        {vs.har_rv_pct != null && (
          <MetricTile label="Fwd RV · HAR" value={pct(vs.har_rv_pct)}
            tone={vs.iv_vs_har_pts != null ? (vs.iv_vs_har_pts >= 0 ? 'text-success' : 'text-error') : ''}
            sub={vs.iv_vs_har_pts != null ? `IV ${vs.iv_vs_har_pts >= 0 ? '+' : ''}${vs.iv_vs_har_pts}vp` : '1-mo forecast'} />
        )}
        <MetricTile label="IV Rank" tone={rankTone(vs.iv_rank)} value={vs.iv_rank ?? '—'} sub="0–100" />
        <MetricTile label="IV Percentile" tone={rankTone(vs.iv_percentile)} value={vs.iv_percentile != null ? `${vs.iv_percentile}%` : '—'} sub="of 1y" />
        <MetricTile label="Vol Rank" tone={rankTone(vs.vol_rank)} value={vs.vol_rank ?? '—'} sub="realized" />
        <MetricTile label="Vol Percentile" tone={rankTone(vs.vol_percentile)} value={vs.vol_percentile != null ? `${vs.vol_percentile}%` : '—'} sub="realized" />
        <MetricTile label="Skew" value={vs.skew_pts != null ? `${vs.skew_pts}vp` : '—'} sub={skewSub} />
      </div>
      {/* Realized-vol term structure in one line — HV10/20/30 (trading-day windows, annualized).
          HV30 is the desk baseline every IV/HV comparison (ATM-IV tile, richness, rank) keys off. */}
      <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[11px] text-base-content/60"
        title="Historical (realized) volatility over 10/20/30 trading days, annualized. IV is compared against HV30.">
        <span className="text-[9px] uppercase tracking-wider text-base-content/40">Realized HV</span>
        <span>10d <b className="font-mono text-base-content/80">{pct(vs.hv10_pct)}</b></span>
        <span>20d <b className="font-mono text-base-content/80">{pct(vs.hv20_pct)}</b></span>
        <span>30d <b className="font-mono text-base-content/80">{pct(vs.hv30_pct)}</b></span>
        <span className="text-base-content/35">annualized · IV/HV vs 30d</span>
      </div>
    </div>
  );
}

// Renders one ticker's full read. Two feeds share the same chrome:
//   • SINGLE-TICKER: `desk` = the ONE /desk-review payload (chrome folded in + the ranking) — no
//     second /derivative-income call, and DeskReview renders it presentationally (`data`).
//   • PORTFOLIO EXPAND: `result` = a /derivative-income scan (no desk ranking); shows chrome + the
//     empty-state note only.
// The underlying "chrome" — ticker header (price/ER/SOFR/HV/IV-HV/52W) + the vol
// panel + events + lazy TA. Exported so My Trades shows the SAME stock context the
// Income desk does, above a placed trade.
export function TickerChrome({ ticker, context, events = [], expirySummaries = [], shares, costBasis, hideCommonEvents }: {
  ticker: string;
  context: DerivativeIncomeContext | null;
  events?: DerivativeIncomeFlag[];
  expirySummaries?: DerivativeIncomeExpirySummary[];
  shares?: number; costBasis?: number | null; hideCommonEvents?: boolean;
}) {
  if (!context) return null;
  const shown = hideCommonEvents ? events.filter(e => e.scope !== 'common') : events;
  return (
    <div className="space-y-4">
      <TickerHeader result={{ ticker, expiry_summaries: expirySummaries }} ctx={context} shares={shares} costBasis={costBasis} />
      {context.vol_stats && <VolatilityPanel vs={context.vol_stats} />}
      {shown.length > 0 && <EventsBanner events={shown} title={hideCommonEvents ? `${ticker} events` : undefined} />}
      <LazyTechnicals ticker={ticker} />
    </div>
  );
}

function SingleTickerResult({ result, desk, shares, costBasis, hideCommonEvents, deskParams, evaluate }: {
  result?: DerivativeIncomeResult; desk?: DeskReviewResult;
  shares?: number; costBasis?: number | null; hideCommonEvents?: boolean; deskParams?: DiParams;
  // EVALUATE tab: the user's bring-your-own legs, so each trade's LLM desk re-evaluates that exact trade.
  evaluate?: DeskEvaluateParams;
}) {
  const ticker = desk?.ticker ?? result?.ticker ?? '';
  const context = desk?.context ?? result?.context ?? null;
  const expirySummaries = desk?.expiry_summaries ?? result?.expiry_summaries ?? [];
  const events: DerivativeIncomeFlag[] = desk?.flag_events ?? result?.events ?? [];
  const note = desk?.note ?? result?.note;
  const oppEmpty = desk ? desk.ranked.length === 0 : (result?.opportunities.length ?? 0) === 0;

  const primaryQuant = expirySummaries[0]?.quant;
  const spot = context?.spot;
  // Pricing-confidence signals are per-expiration; fall back to the primary one.
  const quantForExp = (exp: string) => expirySummaries.find(s => s.expiration === exp)?.quant ?? primaryQuant;
  return (
    <div className="space-y-4">
      <TickerChrome ticker={ticker} context={context} events={events} expirySummaries={expirySummaries}
        shares={shares} costBasis={costBasis} hideCommonEvents={hideCommonEvents} />

      {desk && deskParams && (
        <DeskReview
          ticker={ticker}
          params={deskParams}
          data={desk}
          evaluate={evaluate}
          renderTrade={(t) => (
            <OpportunityCard opp={t} ticker={ticker} spot={spot} quant={quantForExp(t.expiration)} />
          )}
        />
      )}

      {/* Empty-state note — only the portfolio-expand path (no desk); the single-ticker DeskReview
          shows its own "no candidate trades" banner. */}
      {!desk && oppEmpty && (
        <div className="alert alert-warning text-sm">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          <span>{note || 'No executable opportunities cleared your filters. Try a lower probability, a longer target DTE, or a more volatile underlying.'}</span>
        </div>
      )}

    </div>
  );
}

interface DiParams {
  target_dte: number | null;
  target_expiration: string | null;
  min_prob: number;
  min_income: number;
  structures: string[];
  quote_source: string;
  owns_underlying?: boolean;
}

// One portfolio holding: compact best-opportunity summary + an expandable, lazily
// fetched FULL single-ticker analysis (TA, quant, all structures) — so Portfolio
// mode is consistent with the Single-Ticker tab.
function PortfolioHoldingRow({ row, params }: { row: DerivativeIncomePortfolioRow; params: DiParams }) {
  const [expanded, setExpanded] = useState(false);
  const [full, setFull] = useState<DeskReviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Holdings are owned → covered calls score as an income overlay; identical desk surface to Single Ticker.
  const holdingParams: DiParams = { ...params, owns_underlying: true };

  const toggle = async () => {
    const next = !expanded;
    setExpanded(next);
    if (next && !full && !loading) {
      setLoading(true); setErr(null);
      try {
        const r = await runDeskReview(row.ticker, holdingParams);   // full desk surface, cached from the sweep
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
            <span className={`font-bold ${probTone(row.best_opportunity.prob_keep_pct)}`}>{winPct(row.best_opportunity.prob_keep_pct)} keep</span>
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
              {full && !loading && <SingleTickerResult desk={full} deskParams={holdingParams} shares={row.shares} costBasis={row.cost_basis} hideCommonEvents />}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────── Evaluate (bring-your-own trade) ───────────────────────────

// A client-side mirror of the backend `_classify_structure`, for a LIVE "detected structure" hint
// as the user builds legs. The backend classification in the result is authoritative.
function detectStructure(legs: EvaluateLeg[], hasStock: boolean): { label: string; custom: boolean } {
  const valid = legs.filter(l => l.strike > 0 && l.expiration);
  if (!valid.length) return { label: '—', custom: false };
  const multi = new Set(valid.map(l => l.expiration)).size > 1;
  const sc = valid.filter(l => l.type === 'CALL' && l.action === 'SELL').map(l => l.strike).sort((a, b) => a - b);
  const lc = valid.filter(l => l.type === 'CALL' && l.action === 'BUY').map(l => l.strike).sort((a, b) => a - b);
  const sp = valid.filter(l => l.type === 'PUT' && l.action === 'SELL').map(l => l.strike).sort((a, b) => a - b);
  const lp = valid.filter(l => l.type === 'PUT' && l.action === 'BUY').map(l => l.strike).sort((a, b) => a - b);
  const k = (a: number, b: number, c: number, d: number) => sc.length === a && lc.length === b && sp.length === c && lp.length === d;
  if (!multi) {
    // A lone short call is a COVERED call when you hold the shares, else a NAKED call.
    if (k(1, 0, 0, 0)) return hasStock ? { label: 'Covered Call', custom: false } : { label: 'Naked Call', custom: false };
    if (hasStock && k(1, 0, 0, 1)) return { label: 'Collar', custom: false };
    // Pure-option structures — independent of holding stock.
    if (k(0, 0, 1, 0)) return { label: 'Cash-Secured Put', custom: false };
    if (k(0, 0, 1, 1) && lp[0] < sp[0]) return { label: 'Put Credit Spread', custom: false };
    if (k(1, 1, 0, 0) && lc[0] > sc[0]) return { label: 'Call Credit Spread', custom: false };
    if (k(1, 0, 1, 0)) return { label: 'Short Strangle', custom: false };
    if (k(1, 1, 1, 1)) return { label: 'Iron Condor', custom: false };
    if (k(1, 1, 1, 0) && lc[0] > sc[0]) return { label: 'Jade Lizard', custom: false };
  }
  if (multi && valid.length === 2 && new Set(valid.map(l => l.type)).size === 1) {
    return valid[0].strike === valid[1].strike
      ? { label: 'Calendar Spread', custom: true } : { label: 'Diagonal Spread', custom: true };
  }
  return { label: 'Custom Multi-Leg', custom: true };
}

function EvaluateForm({ defaultQuoteSource }: { defaultQuoteSource: 'yfinance' | 'ibkr' }) {
  const [ticker, setTicker] = useState('AAPL');
  const [expiries, setExpiries] = useState<string[]>([]);
  const [expiryLoading, setExpiryLoading] = useState(false);
  const [quoteSource, setQuoteSource] = useState<'yfinance' | 'ibkr'>(defaultQuoteSource);
  const [legs, setLegs] = useState<EvaluateLeg[]>([{ action: 'SELL', type: 'PUT', strike: 0, expiration: '' }]);
  const [owns, setOwns] = useState(false);   // "I hold the underlying" — the sole stock signal

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DeskReviewResult | null>(null);
  const [submitted, setSubmitted] = useState<DeskEvaluateParams | null>(null);

  // Load the option-expiry calendar for the ticker (metadata only). Seed empty leg expiries with
  // the nearest listed one so the dropdowns start ready.
  useEffect(() => {
    const sym = ticker.trim().toUpperCase();
    if (!sym) { setExpiries([]); return; }
    let alive = true;
    setExpiryLoading(true);
    const id = setTimeout(async () => {
      try {
        const r = await fetchOptionExpirations(sym);
        if (!alive) return;
        const exps = r.expirations || [];
        setExpiries(exps);
        if (exps.length) setLegs(ls => ls.map(l => (l.expiration ? l : { ...l, expiration: exps[0] })));
      } catch { if (alive) setExpiries([]); }
      finally { if (alive) setExpiryLoading(false); }
    }, 350);
    return () => { alive = false; clearTimeout(id); };
  }, [ticker]);

  const validExpiries = expiries
    .map(d => [d, dteFromExpiry(d)] as const)
    .filter(([, dte]) => dte >= 1 && dte <= 366);

  const setLeg = (i: number, patch: Partial<EvaluateLeg>) =>
    setLegs(ls => ls.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  // New legs inherit the FIRST leg's expiry — multi-leg income trades almost always share one expiration,
  // so the user rarely has to touch it (they can still change any leg to a different date).
  const addLeg = () => setLegs(ls => [...ls, { action: 'SELL', type: 'CALL', strike: 0, expiration: ls[0]?.expiration || expiries[0] || '' }]);
  const removeLeg = (i: number) => setLegs(ls => ls.filter((_, idx) => idx !== i));

  const hasStock = owns;
  const detected = detectStructure(legs, hasStock);
  const legsReady = legs.length > 0 && legs.every(l => l.strike > 0 && !!l.expiration);
  const canSubmit = !!ticker.trim() && legsReady && !loading;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const payload: DeskEvaluateParams = {
      legs: legs.filter(l => l.strike > 0 && l.expiration),
      quote_source: quoteSource,
      owns_underlying: owns,
    };
    setLoading(true); setError(null); setResult(null); setSubmitted(null);
    try {
      const r = await evaluateDeskTrade(ticker.trim().toUpperCase(), payload);
      if (r.error) setError(r.error); else { setResult(r); setSubmitted(payload); }
    } catch (err: any) { setError(err?.message || 'Failed to evaluate the trade'); }
    finally { setLoading(false); }
  };

  // Filler params for the DeskReview LLM call — evaluate mode routes through `evaluate`, so the
  // scan filters (min_prob / structures) are ignored; only quote_source / owns_underlying matter.
  const deskParams: DiParams = {
    target_dte: null, target_expiration: null, min_prob: 0, min_income: 0,
    structures: [], quote_source: quoteSource, owns_underlying: owns,
  };

  return (
    <div className="space-y-4">
      <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03] text-sm text-base-content/70 flex items-start gap-2">
        <Info className="w-4 h-4 text-secondary mt-0.5 shrink-0" />
        <span>Built a trade elsewhere? Enter the exact legs and the desk runs the <b>same read as Single Ticker</b> — price, volatility, technicals and the full Desk Review — on <b>your</b> trade. Live quotes; the structure is auto-detected (calendars &amp; custom combos welcome).</span>
      </div>

      <form onSubmit={submit} className="space-y-3">
        {/* ticker + quote source + detected structure */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          <div className="form-control">
            <label className="label py-1"><span className="label-text text-xs font-medium">Ticker (ETF / stock / index)</span></label>
            <input type="text" className="input input-bordered input-sm w-full" value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())} placeholder="AAPL, SPY, .SPX" required />
          </div>
          <div className="form-control">
            <label className="label py-1"><span className="label-text text-xs font-medium">Detected structure</span></label>
            <div className="h-8 flex items-center">
              <span className={`badge badge-sm ${detected.custom ? 'badge-warning' : 'badge-secondary'}`}>{detected.label}</span>
              {detected.custom && detected.label !== '—' && (
                <span className="text-[10px] text-base-content/40 ml-2">grade is indicative</span>
              )}
            </div>
          </div>
        </div>

        {/* option legs */}
        <div className="rounded-xl border border-white/[0.06] bg-base-200/20 p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold flex items-center gap-1.5"><Layers className="w-4 h-4 text-secondary" /> Option legs</span>
            <button type="button" className="btn btn-ghost btn-xs gap-1" onClick={addLeg}><Plus className="w-3 h-3" /> Add leg</button>
          </div>
          {legs.map((l, i) => (
            <div key={i} className="grid grid-cols-2 sm:grid-cols-[5rem_5rem_1fr_1.6fr_auto] gap-2 items-center">
              <select className="select select-bordered select-xs" value={l.action} onChange={e => setLeg(i, { action: e.target.value as 'BUY' | 'SELL' })}>
                <option value="SELL">Sell</option><option value="BUY">Buy</option>
              </select>
              <select className="select select-bordered select-xs" value={l.type} onChange={e => setLeg(i, { type: e.target.value as 'CALL' | 'PUT' })}>
                <option value="CALL">Call</option><option value="PUT">Put</option>
              </select>
              <input type="number" step="0.5" min={0} className="input input-bordered input-xs" placeholder="Strike"
                value={l.strike || ''} onChange={e => setLeg(i, { strike: Number(e.target.value) })} />
              <select className="select select-bordered select-xs" value={l.expiration} onChange={e => setLeg(i, { expiration: e.target.value })}>
                <option value="">{expiryLoading ? 'Loading…' : 'Expiry…'}</option>
                {validExpiries.map(([d, dte]) => <option key={d} value={d}>{d} · {dte}d</option>)}
              </select>
              <button type="button" className="btn btn-ghost btn-xs btn-square text-error"
                onClick={() => removeLeg(i)} disabled={legs.length <= 1} title="Remove leg">
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>

        {/* holding toggle — the sole stock signal (100 sh/contract is implied when checked) */}
        <label className="flex items-start gap-2 cursor-pointer text-[11px] text-base-content/70 rounded-xl border border-white/[0.06] bg-base-200/20 p-3">
          <input type="checkbox" className="checkbox checkbox-xs checkbox-secondary mt-0.5" checked={owns} onChange={e => setOwns(e.target.checked)} />
          <span>I already hold the shares — a short call is scored as a <b>covered-call income overlay</b> (no fresh-capital / beta penalty) instead of a naked call, and a long put + short call becomes a <b>collar</b>.</span>
        </label>

        <div className="flex items-center gap-2 justify-end flex-wrap">
          <span className="text-[11px] text-base-content/45 mr-auto">Filters off — the desk scores exactly the trade you enter.</span>
          <button type="submit" className="btn btn-primary btn-sm gap-2" disabled={!canSubmit}>
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <ClipboardCheck className="w-4 h-4" />}
            {loading ? 'Evaluating…' : 'Run Desk Evaluation'}
          </button>
        </div>
      </form>

      {error && <div className="alert alert-error text-sm"><AlertTriangle className="w-4 h-4 shrink-0" /><span>{error}</span></div>}
      {result && submitted && (
        <SingleTickerResult desk={result} deskParams={deskParams} evaluate={submitted} />
      )}
    </div>
  );
}

// ─────────────────────────── main ───────────────────────────

export function DerivativeIncome() {
  const [mode, setMode] = useState<Mode>('single');
  const [ticker, setTicker] = useState('AAPL');
  const [selectedExpiry, setSelectedExpiry] = useState('');   // '' = auto (monthlies ≤45d)
  const [expiries, setExpiries] = useState<string[]>([]);
  const [expiryLoading, setExpiryLoading] = useState(false);
  const [minProb, setMinProb] = useState(90);
  const [minIncome, setMinIncome] = useState(20);
  const [structures, setStructures] = useState<string[]>(
    ['covered_call', 'cash_secured_put', 'credit_spread', 'iron_condor', 'jade_lizard', 'short_strangle', 'calendar']);
  // Data source is chosen once in Settings (functionality-level), not per-scan. Default Yahoo Finance.
  const [quoteSource] = useState<'yfinance' | 'ibkr'>(
    () => (localStorage.getItem('incomeDesk.quoteSource') === 'ibkr' ? 'ibkr' : 'yfinance'));
  const [ownsShares, setOwnsShares] = useState(false);   // already hold the stock → covered call = overlay

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Single-ticker uses ONE /desk-review call — its payload carries the chrome (header/volatility/
  // events) AND the ranking, so there's no separate /derivative-income fetch.
  const [deskResult, setDeskResult] = useState<DeskReviewResult | null>(null);
  // Params are FROZEN at scan time — changing the ticker/date/sliders afterwards must NOT
  // re-run the scan or the desk review; only "Find Income Opportunities" does (#4).
  const [scanParams, setScanParams] = useState<DiParams | null>(null);

  const [pfResult, setPfResult] = useState<DerivativeIncomePortfolioResult | null>(null);
  const [pfLoading, setPfLoading] = useState(false);
  const [pfError, setPfError] = useState<string | null>(null);

  // Load the option-expiry calendar for the current ticker (metadata only — NOT a scan), so the
  // date dropdown is ready to pick from. Debounced; resets the picked date when the ticker changes.
  useEffect(() => {
    if (mode === 'portfolio') {
      // No single ticker in Portfolio mode — offer STANDARD monthly expiries so a portfolio-wide date can
      // be picked; it's sent as a target DTE and each holding maps to its own nearest listed expiry.
      const monthlies = upcomingMonthlyExpiries();
      setExpiryLoading(false);
      setExpiries(monthlies);
      setSelectedExpiry(prev => (monthlies.includes(prev) ? prev : ''));
      return;
    }
    if (mode !== 'single') return;   // 'evaluate' has its own child form + expiry logic
    const sym = ticker.trim().toUpperCase();
    if (!sym) { setExpiries([]); return; }
    let alive = true;
    setExpiryLoading(true);
    const id = setTimeout(async () => {
      try {
        const r = await fetchOptionExpirations(sym);
        if (!alive) return;
        setExpiries(r.expirations || []);
        setSelectedExpiry(prev => (r.expirations || []).includes(prev) ? prev : '');
      } catch { if (alive) setExpiries([]); }
      finally { if (alive) setExpiryLoading(false); }
    }, 350);
    return () => { alive = false; clearTimeout(id); };
  }, [ticker, mode]);

  const toggleStructure = (id: string) =>
    setStructures(s => (s.includes(id) ? s.filter(x => x !== id) : [...s, id]));

  const commonParams = (): DiParams => {
    // Single ticker: scan the EXACT picked expiry. Portfolio: that exact date won't be listed for every
    // holding, so send it as a target DTE — each name maps to its nearest listed expiry (±10-day band).
    const useExp = !!selectedExpiry;
    return {
      target_dte: useExp ? dteFromExpiry(selectedExpiry) : null,
      target_expiration: (mode === 'single' && useExp) ? selectedExpiry : null,
      min_prob: minProb / 100,
      min_income: minIncome,
      structures,
      quote_source: quoteSource,
      owns_underlying: mode === 'portfolio' ? true : ownsShares,   // holdings are owned by definition
    };
  };

  // True when the filters differ from the scan that produced the results on screen — so the frozen
  // results (#4) are never silently stale; the button pulses and a hint appears.
  const singleStale = !!deskResult && !!scanParams &&
    (ticker.trim().toUpperCase() !== deskResult.ticker ||
      JSON.stringify(commonParams()) !== JSON.stringify(scanParams));

  const handleSingle = async (e: React.FormEvent) => {
    e.preventDefault();
    const frozen = commonParams();
    setScanParams(frozen);
    setLoading(true); setError(null); setDeskResult(null);
    try {
      // ONE call: /desk-review carries the ticker chrome (header/volatility/events) + the ranking.
      const data = await runDeskReview(ticker.trim().toUpperCase(), frozen);
      if (data.error) setError(data.error); else setDeskResult(data);
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
            <p className="font-semibold text-base-content mb-1">Income Desk</p>
            <p>
              Every option-selling trade — covered calls, cash-secured puts, spreads, condors, strangles —
              <b> graded A–F</b> on volatility-risk-premium, dealer gamma &amp; technicals, then the
              Quant · Risk · PM desk debates the best. Probabilities from the market-implied
              <b> risk-neutral density</b>; only <b>executable</b> quotes are shown.
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
        <button type="button" className={`btn btn-sm gap-1.5 ${mode === 'evaluate' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setMode('evaluate')}>
          <ClipboardCheck className="w-3.5 h-3.5" /> Evaluate
        </button>
      </div>

      {mode === 'evaluate' && <EvaluateForm defaultQuoteSource={quoteSource} />}

      {/* Form */}
      {mode !== 'evaluate' && (<>
      <form onSubmit={mode === 'single' ? handleSingle : (e) => { e.preventDefault(); handlePortfolio(0); }}
        className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {mode === 'single' && (
          <div className="form-control">
            <label className="label py-1"><span className="label-text text-xs font-medium">Ticker (ETF / stock / index)</span></label>
            <input type="text" className="input input-bordered input-sm w-full" value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())} placeholder="AAPL, SPY, .SPX" required />
          </div>
        )}
        <div className="form-control">
          <label className="label py-1"><span className="label-text text-xs font-medium">Expiry date</span></label>
          <div className="relative">
            <Calendar className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40 pointer-events-none z-10" />
            <select className="select select-bordered select-sm w-full pl-7" value={selectedExpiry}
              onChange={(e) => setSelectedExpiry(e.target.value)} disabled={mode === 'single' && expiryLoading}>
              <option value="">Auto — monthlies ≤45d</option>
              {expiries
                .map(d => [d, dteFromExpiry(d)] as const)
                .filter(([, dte]) => dte >= 1 && dte <= 366)
                .map(([d, dte]) => <option key={d} value={d}>{d} · {dte}d</option>)}
            </select>
          </div>
          <span className="text-[10px] text-base-content/45 mt-1 h-3">
            {expiryLoading ? 'Loading expiry dates…'
              : selectedExpiry
                ? (mode === 'portfolio' ? `~${dteFromExpiry(selectedExpiry)} DTE · nearest listed per holding` : `${dteFromExpiry(selectedExpiry)} DTE`)
                : (mode === 'portfolio' ? 'Auto — nearest monthlies per holding' : 'Nearest monthlies within 45 days')}
          </span>
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
        <div className="form-control lg:col-span-4">
          <label className="label py-1"><span className="label-text text-xs font-medium">Structures to evaluate</span></label>
          <div className="flex flex-wrap gap-2">
            {STRUCTURE_OPTIONS.map((s) => (
              <button type="button" key={s.id}
                className={`btn btn-xs gap-1 ${structures.includes(s.id) ? 'btn-secondary' : 'btn-outline'}`}
                onClick={() => toggleStructure(s.id)}>{s.icon}{s.label}</button>
            ))}
          </div>
          {mode === 'single' && structures.includes('covered_call') && (
            <label className="flex items-start gap-2 mt-2 cursor-pointer text-[11px] text-base-content/70">
              <input type="checkbox" className="checkbox checkbox-xs checkbox-secondary mt-0.5" checked={ownsShares}
                onChange={e => setOwnsShares(e.target.checked)} />
              <span>I already hold the shares — score covered calls as an <b>income overlay</b> on the position (returns as yield on held stock, no fresh-capital or beta penalty), not a buy-write.</span>
            </label>
          )}
        </div>
        <div className="form-control lg:col-span-4 gap-1">
          {mode === 'single' ? (
            <div className="flex items-center gap-2 flex-wrap justify-end">
              {singleStale && (
                <span className="inline-flex items-center gap-1 text-[11px] text-warning mr-auto">
                  <Info className="w-3.5 h-3.5" /> Filters changed — results below are from the previous scan.
                </span>
              )}
              <button type="submit" className={`btn btn-sm gap-2 w-fit ${singleStale ? 'btn-primary animate-pulse' : 'btn-primary'}`} disabled={loading || !ticker.trim() || !structures.length}>
                {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Coins className="w-4 h-4" />}
                {loading ? 'Scanning options…' : deskResult ? 'Re-scan opportunities' : 'Find Income Opportunities'}
              </button>
            </div>
          ) : (
            <button type="submit" className="btn btn-primary btn-sm gap-2 w-fit self-end" disabled={pfLoading || !structures.length}>
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

      {mode === 'single' && deskResult && <SingleTickerResult desk={deskResult} deskParams={scanParams ?? undefined} />}

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
      </>
      )}
    </div>
  );
}
