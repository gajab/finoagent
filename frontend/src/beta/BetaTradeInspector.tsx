/**
 * BetaTradeInspector — the "Decide" level for ONE position (replaces classic's ~18-block expanded card).
 *
 *   header  → who / when / what it is, plus the few actions that matter
 *   verdict → ONE state + one sentence (Act · Watch · Harvest · Hold · No quote), never six competing badges
 *   hero    → payoff + the handful of numbers that decide it
 *   tabs    → Overview (underlying · legs · numbers) · Risk · Quant · Manage · Journal. Manage has two Beta
 *             panels — Underlying Analysis (BetaUnderlyingAnalysis) and Defend & repair (BetaDefend). Heavy panels
 *             are mounted only when first opened, so nothing expensive runs on landing — the same on-demand
 *             behavior as classic, with one click instead of a long scroll.
 *
 * The Manage panels use the SAME endpoints as the classic Trade Manager / Defend desk with a Beta layout; Quant,
 * Greeks grids and history still reuse the classic components (lifted to a readable text size).
 * Beta changes how the answer is organised and presented, not the maths behind it.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { X, RefreshCw, Loader2, ExternalLink, Shield, LogOut, PlusCircle, AlertTriangle, RotateCcw, Pencil, Plus, Check } from 'lucide-react';
import type { SavedStrategyItem, LivePnlResponse, UnderlyingDeskResult } from '../api';
import { fetchUnderlyingDesk, fetchTradeTransactions, setTradeCovered } from '../api';
import { fmtMoney, fmtPct, fmtDate, fmtDTE, fmtQty } from '../lib/tradeFormat';
import {
  tradePurpose, PURPOSE_META, tradeStructure, expiryFrom, dteFrom, daysHeld, effectivePnl, deskFocusForTrade,
  classifyTrade, tradeHasStock,
} from '../components/trades/MyTradesV2';
import PayoffChart from '../components/trades/PayoffChart';
import { QuantAnalysisLoader } from '../components/trades/QuantExitCard';
import TransactionHistoryPanel from '../components/trades/TransactionHistoryPanel';
import { TraderGrid, PmGrid, RiskGrid } from '../components/trades/DeskMetrics';
import { TickerChrome } from '../components/DerivativeIncome';
import { StateChip } from './BetaChrome';
import BetaUnderlyingAnalysis from './BetaUnderlyingAnalysis';
import BetaDefend from './BetaDefend';
import { STATE_META, legHasMarket, type TradeState } from './betaState';
import { RollForm, EditLegForm, AddLegForm } from './LegForms';
import { canMarkCovered, hasShortCall } from './betaLegEdit';

type Tab = 'overview' | 'risk' | 'quant' | 'manage' | 'journal';
const TABS: { id: Tab; label: string }[] = [
  { id: 'overview', label: 'Overview' }, { id: 'risk', label: 'Risk' },
  { id: 'quant', label: 'Quant' }, { id: 'manage', label: 'Manage' }, { id: 'journal', label: 'Journal' },
];

const signedMoney = (n: number | null | undefined) => fmtMoney(n, { signed: true });
const tone = (n: number | null | undefined) => (n == null ? '' : n >= 0 ? 'text-success' : 'text-error');

function Kv({ label, value, sub, valueClass = '', hint }: { label: string; value: React.ReactNode; sub?: React.ReactNode; valueClass?: string; hint?: string }) {
  return (
    <div className="rounded-lg bg-base-200/50 px-2.5 py-2 min-w-0" title={hint}>
      <div className="text-[11px] text-base-content/50">{label}</div>
      <div className={`text-sm font-semibold tabular-nums truncate ${valueClass}`}>{value}</div>
      {sub != null && <div className="text-[11px] text-base-content/45 truncate">{sub}</div>}
    </div>
  );
}

function Notice({ children, tone: t = 'neutral' }: { children: React.ReactNode; tone?: 'neutral' | 'warning' }) {
  return (
    <div className={`rounded-lg border px-3 py-2 text-xs leading-relaxed ${t === 'warning' ? 'border-warning/30 bg-warning/[0.07] text-warning/90' : 'border-white/[0.08] bg-base-200/40 text-base-content/70'}`}>
      {children}
    </div>
  );
}

export default function BetaTradeInspector({
  trade, pnl, state, quoteSource, loading, onRefresh, onClose, onChanged, onCloseTrade, onUpdatePosition,
}: {
  trade: SavedStrategyItem;
  pnl: LivePnlResponse | undefined;
  state: TradeState;
  quoteSource: string;
  loading: boolean;
  onRefresh: () => void;
  onClose: () => void;
  onChanged: () => void;
  onCloseTrade: () => void;
  onUpdatePosition: () => void;
}) {
  const [tab, setTab] = useState<Tab>('overview');
  const [seen, setSeen] = useState<Set<Tab>>(new Set(['overview']));
  // null = no explicit choice yet: follow the live state (Defend when the position needs defending, else the plan).
  const [manageChoice, setManageChoice] = useState<'plan' | 'defend' | null>(null);
  const hasOptions = (trade.legs_data || []).some((l: any) => /call|put/i.test(l?.type || ''));
  // A stock-only position has no Defend & repair (that is option management): its Manage view is just the
  // Underlying Analysis (hold / exit plan). For option positions the default follows the live state.
  const manageSub: 'plan' | 'defend' = !hasOptions ? 'plan' : (manageChoice ?? (state.state === 'act' ? 'defend' : 'plan'));
  const setManageSub = (v: 'plan' | 'defend') => setManageChoice(v);
  const [manageSeen, setManageSeen] = useState<Set<string>>(new Set());
  const [force, setForce] = useState(false);          // "show anyway" on quote-dependent panels
  const [coveredSaving, setCoveredSaving] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // A different trade resets the view (the inspector is reused as the user clicks through rows).
  useEffect(() => {
    setTab('overview'); setSeen(new Set(['overview'])); setManageSeen(new Set()); setForce(false);
    setManageChoice(null); setActionError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trade.id]);

  const go = (t: Tab) => { setTab(t); setSeen(s => new Set(s).add(t)); };
  const openManage = (sub: 'plan' | 'defend') => { setManageSub(sub); setManageSeen(s => new Set(s).add(sub)); go('manage'); };
  useEffect(() => { if (tab === 'manage') setManageSeen(s => new Set(s).add(manageSub)); }, [tab, manageSub]);

  const cached = !!(pnl as any)?._cached;
  const live = !!pnl && !cached;
  const m = STATE_META[state.state];
  const struct = tradeStructure(trade);
  const expiry = expiryFrom(trade, pnl);
  const dte = dteFrom(expiry);
  const purpose = PURPOSE_META[tradePurpose(trade)];
  const roll = trade.roll && (trade.roll.count || 0) > 0 ? trade.roll : null;
  const noQuote = state.state === 'noquote';
  const blocked = noQuote && !force;
  const deskFocus = useMemo(() => deskFocusForTrade(trade, pnl), [trade, pnl]);
  const a: any = (pnl as any)?.analysis || {};
  const unreal = effectivePnl(pnl, false);
  // "Mark covered": a short call whose shares are held ELSEWHERE (no stock leg here) — same rule as classic.
  const markedCovered = !!trade.parameters?.covered;
  const coverable = canMarkCovered(tradeHasStock(trade), hasShortCall(trade), classifyTrade(trade), markedCovered);
  const toggleCovered = async () => {
    setCoveredSaving(true); setActionError(null);
    try { await setTradeCovered(trade.id, !markedCovered); onChanged(); }
    catch (e: any) { setActionError(e?.message || 'Could not change the covered flag.'); }
    finally { setCoveredSaving(false); }
  };
  const be: number[] = Array.isArray(pnl?.breakevens) ? Array.from(new Set(pnl!.breakevens.map(x => Math.round(x * 100) / 100))).slice(0, 3) : [];
  const theta = (pnl as any)?.net_greeks?.theta;
  const delta = (pnl as any)?.net_greeks?.delta;
  const realized = trade.parameters?.realized_pnl != null ? Number(trade.parameters.realized_pnl) : null;

  const maxP = pnl?.unbounded_profit ? 'Unlimited' : pnl?.max_profit != null ? fmtMoney(pnl.max_profit) : '—';
  const maxL = pnl?.unbounded_loss ? 'Unlimited' : pnl?.max_loss != null ? fmtMoney(pnl.max_loss) : '—';

  return (
    <div className="rounded-2xl border border-white/[0.08] bg-base-200/30 overflow-hidden">
      {/* header */}
      <div className="px-4 pt-3 pb-3 border-b border-white/[0.06]">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xl font-bold leading-none">{trade.ticker}</span>
              <span className="text-sm text-base-content/70">{struct.label}</span>
              <StateChip state={state.state} />
              {roll && (
                <span className="inline-flex items-center gap-1 rounded-full border border-warning/30 bg-warning/10 px-2 py-0.5 text-[11px] text-warning" title={`Rolled ${roll.count}× — one continuing campaign`}>
                  <RotateCcw className="w-3 h-3" /> rolled ×{roll.count}
                </span>
              )}
            </div>
            <div className="text-xs text-base-content/55 mt-1">
              {expiry ? <>exp {fmtDate(expiry)} · <b className="font-semibold text-base-content/75">{fmtDTE(dte, { long: true })}</b> · </> : null}
              held {daysHeld(trade.entry_date)}d · {purpose.label}
              {pnl && (pnl as any).underlying_price > 0 ? <> · spot <span className="tabular-nums">{fmtMoney((pnl as any).underlying_price)}</span></> : null}
            </div>
          </div>
          <button className="btn btn-ghost btn-xs btn-circle" onClick={onClose} aria-label="Close inspector"><X className="w-4 h-4" /></button>
        </div>

        {/* actions: ONE primary, the rest quiet */}
        <div className="flex items-center gap-2 flex-wrap mt-3">
          {hasOptions && state.state === 'act' && (
            <button className="btn btn-primary btn-sm gap-1.5" onClick={() => openManage('defend')}><Shield className="w-3.5 h-3.5" /> Defend</button>
          )}
          <button className={`btn btn-sm gap-1.5 ${state.state === 'harvest' ? 'btn-primary' : 'btn-outline border-white/15'}`} onClick={onCloseTrade}>
            <LogOut className="w-3.5 h-3.5" /> Close…
          </button>
          <button className="btn btn-sm btn-ghost border border-white/10 gap-1.5" onClick={onUpdatePosition}><PlusCircle className="w-3.5 h-3.5" /> Update</button>
          {coverable && (
            <button disabled={coveredSaving} onClick={toggleCovered} aria-pressed={markedCovered}
              className={`btn btn-sm gap-1.5 ${markedCovered ? 'btn-outline border-success/40 text-success bg-success/10' : 'btn-ghost border border-white/10'}`}
              title={markedCovered
                ? 'Marked covered — the risk desk treats this short call as covered (shares held elsewhere). Click to unmark.'
                : 'Mark as a covered call without adding stock — you hold the shares elsewhere. The risk desk then excludes it from naked-assignment risk.'}>
              {coveredSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : markedCovered ? <Check className="w-3.5 h-3.5" /> : <Shield className="w-3.5 h-3.5" />}
              {markedCovered ? 'Covered' : 'Mark covered'}
            </button>
          )}
          <button className="btn btn-sm btn-ghost border border-white/10 gap-1.5" onClick={onRefresh} disabled={loading} title="Fetch live prices for this position">
            {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />} Refresh
          </button>
          <Link to={`/my-trades?open=${trade.id}`} className="btn btn-sm btn-ghost gap-1.5 ml-auto text-base-content/60"
            title="Add stock, change purpose, delete the trade, create an agent, and the LLM desks — these still live in the classic card">
            Classic <ExternalLink className="w-3 h-3" />
          </Link>
        </div>
      </div>

      {actionError && <div role="alert" className="mx-4 mt-2 text-xs text-error">{actionError}</div>}

      {/* verdict */}
      <div className={`mx-4 mt-3 rounded-xl border px-3 py-2.5 ${m.border} ${m.soft}`}>
        <div className="flex items-start gap-2">
          {noQuote && <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0 text-base-content/60" />}
          <div className="min-w-0">
            <div className={`text-sm font-semibold leading-snug ${m.text}`}>{state.headline}</div>
            {state.reasons.length > 0 && (
              <ul className="mt-1.5 space-y-0.5">
                {state.reasons.map((r, i) => <li key={i} className="text-xs text-base-content/65 leading-snug flex gap-1.5"><span className="opacity-40">•</span><span>{r}</span></li>)}
              </ul>
            )}
            {state.classicSignal && !noQuote && (
              <div className="text-[11px] text-base-content/40 mt-1.5">Classic quant label: {state.classicSignal}</div>
            )}
          </div>
        </div>
        {state.state === 'pending' && (
          <button className="btn btn-xs btn-outline mt-2 gap-1" onClick={onRefresh} disabled={loading}>
            {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />} Load live P&amp;L
          </button>
        )}
      </div>

      {cached && (
        <div className="mx-4 mt-2 text-[11px] text-base-content/45 flex items-center gap-1.5">
          {loading ? <><Loader2 className="w-3 h-3 animate-spin" /> Fetching live prices…</> : 'Showing the last saved snapshot — press Refresh for live prices.'}
        </div>
      )}

      {/* tabs */}
      <div className="flex gap-1 px-4 mt-3 border-b border-white/[0.06] overflow-x-auto overflow-y-hidden">
        {TABS.map(t => (
          <button key={t.id} onClick={() => go(t.id)}
            className={`px-2.5 py-2 text-sm whitespace-nowrap border-b-2 -mb-px transition-colors ${tab === t.id ? 'border-primary text-base-content font-semibold' : 'border-transparent text-base-content/55 hover:text-base-content/80'}`}>
            {t.label}
          </button>
        ))}
      </div>

      <div className="p-4">
        {/* OVERVIEW */}
        <div className={tab === 'overview' ? 'space-y-4' : 'hidden'}>
          {/* 1 · the underlying: price, volatility, events, technicals */}
          <UnderlyingContext tradeId={trade.id} quoteSource={quoteSource} />
          {/* 2 · the legs */}
          <LegsSection trade={trade} pnl={pnl} cached={cached} onChanged={onChanged} />
          {/* 3 · the position's own numbers */}
          {live && !noQuote && pnl!.scenarios && pnl!.scenarios.length > 1 && (
            <div className="rounded-xl border border-white/[0.06] bg-base-100/30 p-2"><PayoffChart pnl={pnl!} /></div>
          )}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            <Kv label="Unrealized P&L" value={noQuote ? '—' : signedMoney(unreal)} valueClass={noQuote ? '' : tone(unreal)}
              sub={!noQuote && pnl?.pnl_pct != null ? fmtPct(pnl.pnl_pct, { signed: true }) : undefined} />
            <Kv label="Captured" value={noQuote || state.capturedPct == null ? '—' : `${state.capturedPct.toFixed(0)}%`} sub="of max profit" />
            <Kv label="Win chance" value={noQuote || a.probability_of_profit == null ? '—' : `${Number(a.probability_of_profit).toFixed(0)}%`} sub={a.pop_method === 'rnd' ? 'market-implied' : undefined} />
            <Kv label="Max profit" value={maxP} valueClass="text-success" />
            <Kv label="Max loss" value={maxL} valueClass="text-error" />
            <Kv label="Break-even" value={be.length ? be.map(x => `$${x}`).join(' · ') : '—'} />
            <Kv label="Cost basis" value={pnl?.entry_cost != null ? fmtMoney(Math.abs(pnl.entry_cost)) : '—'} />
            <Kv label="Value now" value={noQuote || pnl?.current_value == null ? '—' : fmtMoney(pnl.current_value)} />
            <Kv label="Expected value" value={noQuote || a.expected_value == null ? '—' : signedMoney(a.expected_value)} valueClass={noQuote ? '' : tone(a.expected_value)} />
            <Kv label="Theta / day" value={noQuote || theta == null ? '—' : signedMoney(theta)} valueClass={noQuote ? '' : tone(theta)} />
            <Kv label="Net delta" value={noQuote || delta == null ? '—' : Number(delta).toFixed(1)} />
            <Kv label="Realized so far" value={realized != null && Math.abs(realized) > 0.005 ? signedMoney(realized) : '—'} valueClass={tone(realized)} />
          </div>
          {pnl?.leg_analysis && pnl.leg_analysis.length > 0 && !noQuote && (
            <div>
              <div className="text-xs font-semibold text-base-content/70 mb-1.5">Per-leg read</div>
              <div className="flex flex-wrap gap-1.5">
                {pnl.leg_analysis.map((l, i) => (
                  <span key={i} title={l.reason} className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.08] bg-base-200/50 px-2 py-1 text-xs">
                    <span className="font-mono">{l.type?.[0]?.toUpperCase()}{l.strike}</span>
                    <span className={l.action === 'HOLD' ? 'text-success' : l.action === 'LET_EXPIRE' ? 'text-base-content/60' : 'text-warning'}>{l.action === 'LET_EXPIRE' ? 'Expire' : l.action[0] + l.action.slice(1).toLowerCase()}</span>
                  </span>
                ))}
              </div>
            </div>
          )}
          <div className="flex flex-wrap gap-2 pt-1">
            <button className="btn btn-xs btn-outline border-white/15" onClick={() => openManage('plan')}>{hasOptions ? 'Underlying analysis & exit plan' : 'Hold / exit plan & underlying analysis'}</button>
            {hasOptions && <button className="btn btn-xs btn-outline border-white/15" onClick={() => openManage('defend')}>Defend &amp; repair options</button>}
            {deskFocus && <button className="btn btn-xs btn-outline border-white/15" onClick={() => go('quant')}>Why this score</button>}
          </div>
        </div>

        {/* RISK */}
        {seen.has('risk') && (
          <div className={tab === 'risk' ? 'space-y-3 beta-readable' : 'hidden'}>
            {!hasOptions ? (
              <Notice>Greeks and option risk grids apply to option legs. For this stock position, the hold / exit plan and its risk read are in{' '}
                <button className="underline hover:text-base-content" onClick={() => openManage('plan')}>Manage → Underlying Analysis</button>.</Notice>
            ) : noQuote ? (
              <Notice tone="warning">Greeks and risk metrics need live option prices. Refresh, or switch the quote source — these grids would otherwise show zeros.</Notice>
            ) : !pnl?.lifecycle || cached ? (
              <Notice>{loading ? 'Loading live risk metrics…' : 'Press Refresh to load live Greeks and risk metrics.'}</Notice>
            ) : (
              <>
                {pnl.lifecycle.trader && <div><div className="text-xs font-semibold text-base-content/70 mb-1.5">Dynamic Greeks</div><TraderGrid t={{ ...(pnl.lifecycle.trader as any), avg_iv_pct: pnl.lifecycle.avg_iv_pct }} /></div>}
                {pnl.lifecycle.risk && <div><div className="text-xs font-semibold text-base-content/70 mb-1.5">Capital risk</div><RiskGrid r={pnl.lifecycle.risk} /></div>}
                {pnl.lifecycle.pm && <div><div className="text-xs font-semibold text-base-content/70 mb-1.5">Risk-adjusted quality</div>
                  <PmGrid pm={{ ...(pnl.lifecycle.pm as any), pop: a.probability_of_profit, expected_value: a.expected_value, kelly_fraction: a.kelly_fraction }} /></div>}
              </>
            )}
          </div>
        )}

        {/* QUANT */}
        {seen.has('quant') && (
          <div className={tab === 'quant' ? 'beta-readable' : 'hidden'}>
            {!deskFocus ? <Notice>The quant score is built for option-selling structures; this position has none.</Notice>
              : blocked ? <BlockedNotice onForce={() => setForce(true)} />
              : !pnl ? <Notice>Load live P&amp;L first.</Notice>
              : <QuantAnalysisLoader trade={trade} pnl={pnl} deskFocus={deskFocus} />}
          </div>
        )}

        {/* MANAGE */}
        {seen.has('manage') && (
          <div className={tab === 'manage' ? 'space-y-3' : 'hidden'}>
            {!hasOptions ? (
              <>
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className="text-sm font-semibold">Underlying Analysis</span>
                  <span className="text-xs text-base-content/50">hold / exit plan for the shares · Defend &amp; repair is for option positions</span>
                </div>
                {pnl ? <BetaUnderlyingAnalysis trade={trade} pnl={pnl} /> : <Notice>Load live P&amp;L first — press Refresh.</Notice>}
              </>
            ) : blocked ? <BlockedNotice onForce={() => setForce(true)} /> : (
              <>
                <div className="inline-flex rounded-lg border border-white/10 bg-base-100/40 p-0.5 text-sm" role="tablist" aria-label="Manage sections">
                  {([['plan', 'Underlying Analysis'], ['defend', 'Defend & repair']] as const).map(([k, lbl]) => (
                    <button key={k} role="tab" aria-selected={manageSub === k} onClick={() => setManageSub(k)}
                      className={`px-3 py-1 rounded-md transition-colors ${manageSub === k ? 'bg-primary/20 text-primary font-semibold' : 'text-base-content/60 hover:text-base-content'}`}>{lbl}</button>
                  ))}
                </div>
                {manageSeen.has('plan') && pnl && <div className={manageSub === 'plan' ? '' : 'hidden'}><BetaUnderlyingAnalysis trade={trade} pnl={pnl} /></div>}
                {manageSeen.has('defend') && <div className={manageSub === 'defend' ? '' : 'hidden'}><BetaDefend tradeId={trade.id} quoteSource={quoteSource} ticker={trade.ticker} /></div>}
                <div className="text-[11px] text-base-content/45">The LLM desk (Run Institutional Desk, Review hold or exit) is still in <Link className="link" to={`/my-trades?open=${trade.id}`}>the classic card</Link>.</div>
              </>
            )}
          </div>
        )}

        {/* JOURNAL */}
        {seen.has('journal') && (
          <div className={tab === 'journal' ? 'space-y-3 beta-readable' : 'hidden'}>
            {(trade.parameters?.notes || trade.notes) && (
              <div className="rounded-lg border border-white/[0.06] bg-base-200/40 px-3 py-2">
                <div className="text-[11px] text-base-content/45 mb-0.5">Thesis / notes</div>
                <div className="text-sm whitespace-pre-wrap">{trade.parameters?.notes || trade.notes}</div>
              </div>
            )}
            <TransactionHistoryPanel strategyId={trade.id} onFetch={fetchTradeTransactions} trade={trade} pnl={pnl as any} onPositionChanged={onChanged} />
          </div>
        )}
      </div>
    </div>
  );
}

/** The position's legs — strike, quantity, expiry, entry, live bid/ask, the per-leg read — and the leg actions. */
function LegsSection({ trade, pnl, cached, onChanged }: { trade: SavedStrategyItem; pnl: LivePnlResponse | undefined; cached: boolean; onChanged: () => void }) {
  const legs = (trade.legs_data || []) as any[];
  // One inline form at a time: roll or edit a specific leg, or add a new one.
  const [mode, setMode] = useState<{ kind: 'roll' | 'edit'; idx: number } | { kind: 'add' } | null>(null);
  useEffect(() => { setMode(null); }, [trade.id]);
  const done = () => { setMode(null); onChanged(); };
  const hasOpts = legs.some(l => /call|put/i.test(l?.type || ''));
  const btn = 'btn btn-ghost btn-xs btn-square';
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <div className="text-xs font-semibold text-base-content/70">Legs</div>
        <button className="btn btn-ghost btn-xs gap-1 ml-auto border border-white/10" onClick={() => setMode(m => (m && m.kind === 'add' ? null : { kind: 'add' }))} aria-expanded={mode?.kind === 'add'}>
          <Plus className="w-3 h-3" /> Add leg
        </button>
      </div>
      {!hasOpts ? <Notice>No option legs — this is a stock position. Use <b>Add leg</b> to sell a covered call or buy protection.</Notice> : (
        <div className="overflow-x-auto rounded-lg border border-white/[0.06]">
          <table className="w-full text-xs">
            <thead><tr className="text-base-content/45 text-left">
              {['Leg', 'Strike', 'Qty', 'Expiry', 'Entry', 'Bid', 'Ask', 'Read'].map(h => <th key={h} className="px-2 py-1.5 font-normal">{h}</th>)}
              <th className="px-1 py-1.5 font-normal text-right"><span className="sr-only">Actions</span></th>
            </tr></thead>
            <tbody>
              {legs.map((l, i) => {
                if (!/call|put/i.test(l?.type || '')) return null;
                const q = (pnl?.current_quotes || []).find((c: any) => c.leg === i);
                const adv = (pnl?.leg_analysis || []).find(x => x.leg === i);
                const gap = !!pnl && !cached && !legHasMarket(q);
                const short = /sell|short/i.test(l.action || '');
                const active = mode && mode.kind !== 'add' && mode.idx === i;
                return (
                  <tr key={i} className={`border-t border-white/[0.04] ${active ? 'bg-primary/[0.06]' : ''}`}>
                    <td className="px-2 py-1.5 whitespace-nowrap"><span className={short ? 'text-error' : 'text-success'}>{short ? 'Short' : 'Long'}</span> {String(l.type).toLowerCase()}</td>
                    <td className="px-2 py-1.5 tabular-nums">{l.strike}</td>
                    <td className="px-2 py-1.5 tabular-nums">{fmtQty(l.qty ?? 1)}</td>
                    <td className="px-2 py-1.5 whitespace-nowrap">{fmtDate(l.expiration || l.expiry)}</td>
                    <td className="px-2 py-1.5 tabular-nums">{l.premium != null ? `$${Number(l.premium).toFixed(2)}` : '—'}</td>
                    <td className="px-2 py-1.5 tabular-nums">{q?.bid != null ? `$${Number(q.bid).toFixed(2)}` : '—'}</td>
                    <td className="px-2 py-1.5 tabular-nums">{q?.ask != null ? `$${Number(q.ask).toFixed(2)}` : '—'}</td>
                    <td className="px-2 py-1.5">{gap ? <span className="text-base-content/50">no quote</span> : adv ? <span title={adv.reason}>{adv.action === 'LET_EXPIRE' ? 'Expire' : adv.action[0] + adv.action.slice(1).toLowerCase()}</span> : '—'}</td>
                    <td className="px-1 py-1 whitespace-nowrap text-right">
                      <button className={btn} title="Roll this leg — buy it back and open a replacement" aria-label={`Roll leg ${i + 1}`} onClick={() => setMode({ kind: 'roll', idx: i })}><RotateCcw className="w-3.5 h-3.5" /></button>
                      <button className={btn} title="Edit this leg — correct a strike, quantity, expiry or entry" aria-label={`Edit leg ${i + 1}`} onClick={() => setMode({ kind: 'edit', idx: i })}><Pencil className="w-3.5 h-3.5" /></button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {mode?.kind === 'roll' && <RollForm key={`roll-${trade.id}-${mode.idx}`} trade={trade} pnl={pnl} idx={mode.idx} onDone={done} onCancel={() => setMode(null)} />}
      {mode?.kind === 'edit' && <EditLegForm key={`edit-${trade.id}-${mode.idx}`} trade={trade} idx={mode.idx} onDone={done} onCancel={() => setMode(null)} />}
      {mode?.kind === 'add' && <AddLegForm key={`add-${trade.id}`} trade={trade} onDone={done} onCancel={() => setMode(null)} />}
      <div className="text-[11px] text-base-content/45">Add stock, change purpose, delete the trade or create an agent? Those are still in <Link className="link" to={`/my-trades?open=${trade.id}`}>the classic card</Link>.</div>
    </div>
  );
}

function BlockedNotice({ onForce }: { onForce: () => void }) {
  return (
    <Notice tone="warning">
      This read is built on live option prices and one or more legs have no quote, so it could be wrong.{' '}
      <button className="underline hover:text-warning" onClick={onForce}>Show it anyway</button>
    </Notice>
  );
}

/** Price, volatility, events and technicals for the underlying — fetched only when the Risk tab is first opened. */
function UnderlyingContext({ tradeId, quoteSource }: { tradeId: number; quoteSource: string }) {
  const [data, setData] = useState<UnderlyingDeskResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    setLoading(true); setFailed(false);
    fetchUnderlyingDesk(tradeId, quoteSource).then(d => { if (alive) setData(d); }).catch(() => { if (alive) setFailed(true); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [tradeId, quoteSource]);
  return (
    <div>
      <div className="text-xs font-semibold text-base-content/70 mb-1.5">Underlying</div>
      {loading ? <div className="flex items-center gap-2 text-xs text-base-content/45"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading context…</div>
        : failed || !data?.context ? <Notice>Underlying context is unavailable right now.</Notice>
        : <TickerChrome ticker={data.ticker} context={data.context} events={data.events} expirySummaries={data.expiry_summaries} />}
    </div>
  );
}
