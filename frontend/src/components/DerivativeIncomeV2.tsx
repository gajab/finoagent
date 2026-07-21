import React, { useState } from 'react';
import {
  Sparkles, Search, Calendar, DollarSign, TrendingUp, Shield, ShieldCheck,
  Layers, Feather, Loader2, AlertTriangle, Coins, ChevronRight, ListOrdered, Gauge,
} from 'lucide-react';
import { runDerivativeIncome } from '../api';
import type { DerivativeIncomeResult, DerivativeIncomeOpportunity } from '../types';
import { OpportunityCard, VolatilityPanel, EventsBanner, LazyTechnicals } from './DerivativeIncome';
import { DeskReview } from './DeskReview';

// ── Derivative Income v2 ──────────────────────────────────────────────────────
// Same engine + same building blocks as v1, re-composed into a master–detail
// workspace: a compact config toolbar, a one-line "underlying" context strip
// (full technicals tucked behind a drawer), then a ranked opportunities LIST
// (master) with ONE detail card at a time — instead of stacking every card, its
// advisor and its desk review inline. The heavy Desk-ranking + LLM debate moves
// to its own tab. v1 (DerivativeIncome) is left untouched.

const STRUCTURE_OPTIONS = [
  { id: 'covered_call', label: 'Covered Call', icon: <TrendingUp className="w-3.5 h-3.5" /> },
  { id: 'cash_secured_put', label: 'Cash-Secured Put', icon: <DollarSign className="w-3.5 h-3.5" /> },
  { id: 'collar', label: 'Collar', icon: <Shield className="w-3.5 h-3.5" /> },
  { id: 'credit_spread', label: 'Credit Spreads', icon: <ShieldCheck className="w-3.5 h-3.5" /> },
  { id: 'iron_condor', label: 'Iron Condor', icon: <Layers className="w-3.5 h-3.5" /> },
  { id: 'jade_lizard', label: 'Jade Lizard', icon: <Feather className="w-3.5 h-3.5" /> },
];

const money = (n: number | null | undefined, d = 0) =>
  n == null ? '—' : `$${n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const pct = (n: number | null | undefined, d = 1) => (n == null ? '—' : `${n.toFixed(d)}%`);
const annPct = (n: number | null | undefined) =>
  n == null ? '—' : n >= 1000 ? `${Math.round(n).toLocaleString()}%` : `${n.toFixed(0)}%`;
const probTone = (p: number) => (p >= 95 ? 'text-success' : p >= 85 ? 'text-success/90' : 'text-warning');
const rankTone = (v: number | null | undefined) =>
  v == null ? '' : v >= 70 ? 'text-success' : v >= 40 ? 'text-warning' : 'text-base-content/70';
const confTone = (l?: string) =>
  l === 'High' ? 'text-success bg-success/10 border-success/25'
    : l === 'Medium' ? 'text-warning bg-warning/10 border-warning/25'
      : 'text-error bg-error/10 border-error/25';

function strikeStr(o: DerivativeIncomeOpportunity) {
  const isSpread = o.structure.includes('spread');
  const isCollar = o.structure === 'collar';
  const isCondor = o.structure === 'iron_condor';
  const isJade = o.structure === 'jade_lizard';
  return isCondor ? `${o.put_long}/${o.put_short}–${o.call_short}/${o.call_long}`
    : isJade ? `put ${o.put_short} · call ${o.call_short}/${o.call_long}`
      : isSpread ? `${o.short_strike}/${o.long_strike}`
        : isCollar ? `cap ${o.short_strike} · floor ${o.floor_strike}`
          : `${o.short_strike}`;
}

const Chip = ({ children }: { children: React.ReactNode }) => (
  <span className="text-[11px] px-2 py-0.5 rounded-lg border border-white/[0.06] bg-base-200/40 text-base-content/70">{children}</span>
);

type Tab = 'opportunities' | 'desk';

export function DerivativeIncomeV2() {
  // ── config ──
  const [ticker, setTicker] = useState('AAPL');
  const [targetDte, setTargetDte] = useState('');
  const [minProb, setMinProb] = useState(85);
  const [minIncome, setMinIncome] = useState(20);
  const [structures, setStructures] = useState<string[]>(['covered_call', 'cash_secured_put', 'collar', 'credit_spread']);
  const [quoteSource, setQuoteSource] = useState<'yfinance' | 'ibkr'>('yfinance');

  // ── results ──
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DerivativeIncomeResult | null>(null);

  // ── workspace ──
  const [tab, setTab] = useState<Tab>('opportunities');
  const [listMode, setListMode] = useState<'best' | 'all'>('best');
  const [selected, setSelected] = useState(0);
  const [showUnderlying, setShowUnderlying] = useState(false);

  const commonParams = () => ({
    target_dte: targetDte.trim() ? Number(targetDte) : null,
    min_prob: minProb / 100,
    min_income: minIncome,
    structures,
    quote_source: quoteSource,
  });

  const toggleStructure = (id: string) =>
    setStructures(s => (s.includes(id) ? s.filter(x => x !== id) : [...s, id]));

  const handleScan = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true); setError(null); setResult(null);
    setSelected(0); setShowUnderlying(false); setListMode('best'); setTab('opportunities');
    try {
      const data = await runDerivativeIncome(ticker.trim().toUpperCase(), commonParams());
      if (data.error) setError(data.error); else setResult(data);
    } catch (err: any) { setError(err?.message || 'Failed to scan opportunities'); }
    finally { setLoading(false); }
  };

  return (
    <div className="space-y-4">
      {/* Heading */}
      <div className="flex items-center gap-2 flex-wrap">
        <Sparkles className="w-4 h-4 text-secondary" />
        <span className="text-sm font-semibold">Derivative Income</span>
        <span className="badge badge-xs badge-secondary">v2</span>
        <span className="text-xs text-base-content/50">Master–detail workspace · same engine, calmer layout</span>
      </div>

      {/* Config toolbar */}
      <form onSubmit={handleScan} className="rounded-xl border border-white/[0.06] bg-base-200/30 p-3 space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="form-control">
            <label className="label py-0.5"><span className="label-text text-[11px] font-medium">Ticker</span></label>
            <input type="text" className="input input-bordered input-sm w-28" value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())} placeholder="AAPL" required />
          </div>
          <div className="form-control">
            <label className="label py-0.5"><span className="label-text text-[11px] font-medium">Target DTE</span></label>
            <input type="number" className="input input-bordered input-sm w-36" value={targetDte}
              onChange={(e) => setTargetDte(e.target.value)} min={1} max={365} placeholder="≤45d monthlies" />
          </div>
          <div className="form-control">
            <label className="label py-0.5"><span className="label-text text-[11px] font-medium">Min prob <b className="text-secondary">{minProb}%</b></span></label>
            <input type="range" min={70} max={99} step={1} value={minProb}
              onChange={(e) => setMinProb(Number(e.target.value))} className="range range-secondary range-xs w-32 mt-1.5" />
          </div>
          <div className="form-control">
            <label className="label py-0.5"><span className="label-text text-[11px] font-medium">Min premium $</span></label>
            <input type="number" className="input input-bordered input-sm w-24" value={minIncome}
              onChange={(e) => setMinIncome(Number(e.target.value))} min={0} step={5} />
          </div>
          <div className="form-control">
            <label className="label py-0.5"><span className="label-text text-[11px] font-medium">Quote source</span></label>
            <select className="select select-bordered select-sm w-44" value={quoteSource}
              onChange={(e) => setQuoteSource(e.target.value as 'yfinance' | 'ibkr')}>
              <option value="yfinance">Yahoo (free, delayed)</option>
              <option value="ibkr">IBKR (real-time)</option>
            </select>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-base-content/50">Structures</span>
          {STRUCTURE_OPTIONS.map((s) => (
            <button type="button" key={s.id}
              className={`btn btn-xs gap-1 ${structures.includes(s.id) ? 'btn-secondary' : 'btn-outline'}`}
              onClick={() => toggleStructure(s.id)}>{s.icon}{s.label}</button>
          ))}
          <button type="submit" className="btn btn-primary btn-sm gap-2 ml-auto" disabled={loading || !ticker.trim() || !structures.length}>
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            {loading ? 'Scanning…' : 'Scan'}
          </button>
        </div>
      </form>

      {error && (
        <div className="alert alert-error text-sm"><AlertTriangle className="w-4 h-4 shrink-0" /><span>{error}</span></div>
      )}

      {!result && !loading && !error && (
        <div className="rounded-xl border border-dashed border-white/[0.08] p-10 text-center text-sm text-base-content/50">
          <Coins className="w-6 h-6 mx-auto mb-2 opacity-40" />
          Enter a ticker and scan to see ranked income opportunities.
        </div>
      )}

      {loading && (
        <div className="flex items-center gap-2 text-sm text-base-content/50 py-10 justify-center">
          <Loader2 className="w-4 h-4 animate-spin" /> Scanning options…
        </div>
      )}

      {result && (() => {
        const ctx = result.context;
        const vs = ctx?.vol_stats;
        const spot = ctx?.spot;
        const list: DerivativeIncomeOpportunity[] = listMode === 'best' ? (result.best_by_structure || []) : (result.opportunities || []);
        const selIdx = list.length ? Math.min(Math.max(selected, 0), list.length - 1) : 0;
        const sel = list[selIdx];
        const primaryQuant = result.expiry_summaries?.[0]?.quant;
        const quantForExp = (exp: string) => result.expiry_summaries?.find(s => s.expiration === exp)?.quant ?? primaryQuant;

        return (
          <div className="space-y-4">
            {/* Underlying context strip — detail is one click away */}
            {ctx && (
              <div className="rounded-xl border border-white/[0.06] bg-base-200/20 px-4 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-2">
                <div className="flex items-baseline gap-2">
                  <span className="text-base font-bold">{result.ticker}</span>
                  <span className="text-2xl font-bold tabular-nums">{money(ctx.spot, 2)}</span>
                </div>
                {vs?.iv_rank != null && <Chip>IV rank <b className={rankTone(vs.iv_rank)}>{vs.iv_rank}</b></Chip>}
                {ctx.hv30_pct != null && <Chip>HV30 {pct(ctx.hv30_pct)}</Chip>}
                {ctx.week52?.position_pct != null && <Chip>52W {pct(ctx.week52.position_pct, 0)}</Chip>}
                {ctx.sofr_pct != null && <Chip>SOFR {pct(ctx.sofr_pct)}</Chip>}
                {ctx.next_earnings && (
                  <span className="badge badge-warning badge-sm gap-1"><Calendar className="w-3 h-3" />ER {ctx.next_earnings}</span>
                )}
                <button type="button" onClick={() => setShowUnderlying(v => !v)}
                  className="btn btn-ghost btn-xs gap-1 text-secondary ml-auto">
                  Underlying analysis
                  <ChevronRight className={`w-3.5 h-3.5 transition-transform ${showUnderlying ? 'rotate-90' : ''}`} />
                </button>
              </div>
            )}
            {showUnderlying && (
              <div className="space-y-3">
                {vs && <VolatilityPanel vs={vs} />}
                {result.events && result.events.length > 0 && <EventsBanner events={result.events} />}
                <LazyTechnicals ticker={result.ticker} />
              </div>
            )}

            {/* Workspace tabs */}
            <div className="flex gap-1 p-1 bg-base-200/40 rounded-xl border border-white/[0.03] w-fit">
              <button type="button" className={`btn btn-sm gap-1.5 ${tab === 'opportunities' ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setTab('opportunities')}>
                <ListOrdered className="w-3.5 h-3.5" /> Opportunities
              </button>
              <button type="button" className={`btn btn-sm gap-1.5 ${tab === 'desk' ? 'btn-primary' : 'btn-ghost'}`}
                onClick={() => setTab('desk')}>
                <Gauge className="w-3.5 h-3.5" /> Desk review
              </button>
            </div>

            {/* ── Opportunities: master list + single detail card ── */}
            {tab === 'opportunities' && (
              list.length > 0 ? (
                <div className="grid grid-cols-1 lg:grid-cols-[340px_minmax(0,1fr)] gap-4 items-start">
                  {/* Master */}
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-semibold text-base-content/60">Ranked opportunities</span>
                      <div className="flex gap-0.5 p-0.5 bg-base-200/40 rounded-lg">
                        <button type="button" className={`btn btn-xs ${listMode === 'best' ? 'btn-secondary' : 'btn-ghost'}`}
                          onClick={() => { setListMode('best'); setSelected(0); }}>Best/structure</button>
                        <button type="button" className={`btn btn-xs ${listMode === 'all' ? 'btn-secondary' : 'btn-ghost'}`}
                          onClick={() => { setListMode('all'); setSelected(0); }}>All {result.opportunities.length}</button>
                      </div>
                    </div>
                    <div className="rounded-xl border border-white/[0.06] overflow-hidden divide-y divide-white/[0.04]">
                      {list.map((o, i) => {
                        const active = i === selIdx;
                        return (
                          <button type="button" key={i} onClick={() => setSelected(i)}
                            className={`w-full text-left px-3 py-2.5 border-l-2 transition-colors ${active ? 'bg-secondary/10 border-secondary' : 'border-transparent hover:bg-base-200/40'}`}>
                            <div className="flex items-center justify-between gap-2">
                              <span className={`text-sm font-semibold truncate ${active ? 'text-secondary' : ''}`}>{o.label}</span>
                              <span className={`text-sm font-bold shrink-0 ${probTone(o.prob_keep_pct)}`}>{pct(o.prob_keep_pct, 0)}</span>
                            </div>
                            <div className="flex items-center justify-between gap-2 mt-0.5">
                              <span className="text-[11px] text-base-content/50 truncate font-mono">{strikeStr(o)} · {o.dte}d</span>
                              {o.confidence && (
                                <span className={`text-[10px] px-1.5 py-px rounded border shrink-0 ${confTone(o.confidence.label)}`}>{o.confidence.label}</span>
                              )}
                            </div>
                            <div className="flex items-center justify-between gap-2 mt-1 text-[11px]">
                              <span className="text-success">{money(o.contracts ? o.total_premium : o.premium, 0)} prem</span>
                              <span className="text-base-content/60">{pct(o.static_return_pct)} · {annPct(o.premium_annualized_pct)} ann</span>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                    <p className="text-[11px] text-base-content/40 mt-2 px-1">Pick a trade to see the full desk analysis on the right.</p>
                  </div>

                  {/* Detail */}
                  <div>
                    {sel && (
                      <OpportunityCard opp={sel} ticker={result.ticker} spot={spot}
                        quant={quantForExp(sel.expiration)} deskParams={commonParams()} />
                    )}
                  </div>
                </div>
              ) : (
                <div className="alert alert-warning text-sm">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  <span>{result.note || 'No executable opportunities cleared your filters. Try a lower probability, a longer target DTE, or a more volatile underlying.'}</span>
                </div>
              )
            )}

            {/* ── Desk review: full ranking + LLM debate (on its own tab) ── */}
            {tab === 'desk' && (
              <DeskReview
                ticker={result.ticker}
                params={commonParams()}
                renderTrade={(t) => (
                  <OpportunityCard opp={t} ticker={result.ticker} spot={spot} quant={quantForExp(t.expiration)} />
                )}
              />
            )}
          </div>
        );
      })()}
    </div>
  );
}
