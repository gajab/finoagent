/**
 * IncomeScanBeta — the Beta "Scan" workbench for the Income Desk.
 *
 *   filters   → three presets (Keep it safe · Balanced · Chase income) instead of a slider + 6 chips + 2 prose
 *               toggles; the old controls live in "Advanced"
 *   strip     → the ticker's context on ONE line (price · IV/HV · gamma · earnings), full chrome on demand
 *   picks     → Best overall · Safest · Most income, always three different trades, with a compare tray
 *   list      → the desk ranking with a price ladder per trade (where it loses / keeps its premium)
 *   decision  → grade + keep chance + the numbers that matter, the score card, and ONE primary action;
 *               the full classic drill-down (legs, Greeks, monitoring, sentiment, exposure, LLM desk) is
 *               one click away under "Full audit"
 *
 * Same endpoint as classic (POST /api/stock/{t}/desk-review) — Beta changes the layout, not the engine.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Search, SlidersHorizontal, Check, ChevronDown, AlertTriangle, Briefcase, X, Zap } from 'lucide-react';
import { runDeskReview, fetchOptionExpirations, createPaperTrade } from '../api';
import type { DeskReviewParams } from '../api';
import type { DeskReviewResult, DeskRankedTrade } from '../types';
import { TradeExplorer } from '../components/DeskReview';
import { TickerChrome } from '../components/DerivativeIncome';
import { useAuth } from '../contexts/AuthContext';
import ScoreCard from './ScoreCard';
import SplitPane from './SplitPane';
import PriceLadder from './PriceLadder';
import { choosePicks, capitalOf, type Pick } from './betaPicks';

// ── presets ──────────────────────────────────────────────────────────────────
type PresetKey = 'safe' | 'balanced' | 'income';
const PRESETS: Record<PresetKey, { label: string; desc: string; minProb: number; structures: string[]; hideEarnings: boolean }> = {
  safe: {
    label: 'Keep it safe', hideEarnings: true, minProb: 93,
    desc: 'Keep chance 93% or more. Puts, spreads and covered calls only. Hides trades that cross earnings.',
    structures: ['cash_secured_put', 'credit_spread', 'covered_call'],
  },
  balanced: {
    label: 'Balanced', hideEarnings: false, minProb: 90,
    desc: 'Keep chance 90% or more. Earnings-aware scoring. Defined risk preferred.',
    structures: ['covered_call', 'cash_secured_put', 'credit_spread', 'iron_condor'],
  },
  income: {
    label: 'Chase income', hideEarnings: false, minProb: 85,
    desc: 'Keep chance 85% or more. Adds strangles — the desk still vetoes unsafe ones.',
    structures: ['covered_call', 'cash_secured_put', 'credit_spread', 'iron_condor', 'short_strangle'],
  },
};
const STRUCTURES: { id: string; label: string; premiumOnly?: boolean }[] = [
  { id: 'covered_call', label: 'Covered call' }, { id: 'cash_secured_put', label: 'Cash-secured put' },
  { id: 'credit_spread', label: 'Credit spreads' }, { id: 'iron_condor', label: 'Iron condor' },
  { id: 'short_strangle', label: 'Short strangle' },
  { id: 'jade_lizard', label: 'Jade lizard', premiumOnly: true }, { id: 'calendar', label: 'Calendar', premiumOnly: true },
  { id: 'back_ratio', label: 'Back ratio', premiumOnly: true },
];

// ── tiny helpers ─────────────────────────────────────────────────────────────
const money = (n: number | null | undefined, d = 0) =>
  n == null || !Number.isFinite(Number(n)) ? '—' : `${Number(n) < 0 ? '−' : ''}$${Math.abs(Number(n)).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const keepPct = (n: number | null | undefined) => (n == null ? '—' : `${Math.min(99.9, Math.max(0, n)).toFixed(1)}%`);
const dteFromExpiry = (e: string) => Math.max(0, Math.round((new Date(e + 'T00:00:00').getTime() - Date.now()) / 86400000));
const annText = (n: number | null | undefined) => (n == null ? '' : n >= 1000 ? 'ann. n/m' : `~${n.toFixed(0)}%/yr`);

function strikeText(t: DeskRankedTrade) {
  if (t.structure === 'iron_condor') return `${t.put_long}/${t.put_short} – ${t.call_short}/${t.call_long}`;
  if (t.structure === 'jade_lizard') return `put ${t.put_short} · call ${t.call_short}/${t.call_long}`;
  if (t.structure === 'short_strangle') return `put ${t.put_short} · call ${t.call_short}`;
  if (t.long_strike) return `${t.short_strike}/${t.long_strike}`;
  return `${t.short_strike}`;
}
const isVetoed = (t: DeskRankedTrade) => (t.grade_blocking || []).length > 0;
const isWaiting = (t: DeskRankedTrade) => !isVetoed(t) && (t.grade_timing_hold || []).length > 0;

/** A print falls between today and this trade's expiry. */
function crossesEarnings(t: DeskRankedTrade, nextEarnings: string | null | undefined): boolean {
  if (!nextEarnings || !t.expiration) return false;
  const e = String(nextEarnings).slice(0, 10);
  return e >= new Date().toISOString().slice(0, 10) && e <= String(t.expiration).slice(0, 10);
}

function GradeChip({ t }: { t: DeskRankedTrade }) {
  const v = isVetoed(t), w = isWaiting(t);
  const g = (t.algo_grade || '—').toUpperCase();
  const cls = v ? 'bg-error/15 text-error border-error/30'
    : w ? 'bg-warning/15 text-warning border-warning/30'
    : g === 'A' || g === 'B' ? 'bg-success/15 text-success border-success/30'
    : g === 'C' || g === 'D' ? 'bg-warning/15 text-warning border-warning/30'
    : 'bg-base-content/10 text-base-content/70 border-base-content/20';
  return <span className={`inline-flex h-7 w-7 items-center justify-center rounded-full border text-xs font-bold ${cls}`} title={v ? `Vetoed — ${(t.grade_blocking || []).join('; ')}` : w ? `Wait (timing) — ${(t.grade_timing_hold || []).join('; ')}` : `Desk grade ${g}`}>{v ? 'V' : g}</span>;
}

export type ScanSeed = { ticker: string; expiry?: string | null; minProb?: number; minIncome?: number };

export default function IncomeScanBeta({ seed = null, onSeedConsumed }: { seed?: ScanSeed | null; onSeedConsumed?: () => void }) {
  const { isPremium } = useAuth();
  const quoteSource: 'yfinance' | 'ibkr' = (() => { try { return localStorage.getItem('incomeDesk.quoteSource') === 'ibkr' ? 'ibkr' : 'yfinance'; } catch { return 'yfinance'; } })();

  const [ticker, setTicker] = useState('');
  const [expiries, setExpiries] = useState<string[]>([]);
  const [expiry, setExpiry] = useState('');             // '' = auto (monthlies ≤ 45d)
  const [preset, setPreset] = useState<PresetKey>('balanced');
  const [showAdv, setShowAdv] = useState(false);
  const [minIncome, setMinIncome] = useState(20);
  const [advStructures, setAdvStructures] = useState<string[] | null>(null);   // null = follow the preset
  const [ownsShares, setOwnsShares] = useState(false);
  const [earningsAware, setEarningsAware] = useState(true);
  const [minProbOverride, setMinProbOverride] = useState<number | null>(null);

  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [rev, setRev] = useState<DeskReviewResult | null>(null);
  const [scanned, setScanned] = useState<DeskReviewParams | null>(null);
  const [scannedKey, setScannedKey] = useState('');
  const [sel, setSel] = useState<number | null>(null);       // index into rev.ranked
  const [hideEarnings, setHideEarnings] = useState(PRESETS.balanced.hideEarnings);
  const [sortBy, setSortBy] = useState<'rank' | 'keep' | 'return' | 'premium'>('rank');
  const [cmp, setCmp] = useState<number[]>([]);
  const [showCtx, setShowCtx] = useState(false);
  const [audit, setAudit] = useState(false);
  const [paper, setPaper] = useState<{ state: 'idle' | 'busy' | 'done' | 'error'; msg?: string }>({ state: 'idle' });
  const scanSeq = useRef(0);

  const structures = advStructures ?? PRESETS[preset].structures;
  const minProb = minProbOverride ?? PRESETS[preset].minProb;

  // Option-expiry calendar for the ticker (metadata only — not a scan). Debounced.
  useEffect(() => {
    const sym = ticker.trim().toUpperCase();
    if (!sym) { setExpiries([]); return; }
    let alive = true;
    const id = setTimeout(async () => {
      try {
        const r = await fetchOptionExpirations(sym);
        if (!alive) return;
        setExpiries(r.expirations || []);
        setExpiry(prev => (r.expirations || []).includes(prev) ? prev : '');
      } catch { if (alive) setExpiries([]); }
    }, 350);
    return () => { alive = false; clearTimeout(id); };
  }, [ticker]);

  // Progress: the scan takes ~30–45s; show that something is happening and how long.
  useEffect(() => {
    if (!loading) return;
    setElapsed(0);
    const id = setInterval(() => setElapsed(s => s + 1), 1000);
    return () => clearInterval(id);
  }, [loading]);

  const buildParams = useCallback((o?: Partial<{ expiry: string; minProb: number; minIncome: number }>): DeskReviewParams => {
    const exp = o?.expiry ?? expiry;
    return {
      target_dte: exp ? dteFromExpiry(exp) : null,
      target_expiration: exp || null,
      min_prob: (o?.minProb ?? minProb) / 100,
      min_income: o?.minIncome ?? minIncome,
      structures,
      quote_source: quoteSource,
      owns_underlying: ownsShares,
      earnings_aware: earningsAware,
    };
  }, [expiry, minProb, minIncome, structures, quoteSource, ownsShares, earningsAware]);

  const run = useCallback(async (sym: string, params: DeskReviewParams) => {
    const mine = ++scanSeq.current;
    setLoading(true); setError(null); setRev(null); setSel(null); setCmp([]); setAudit(false); setPaper({ state: 'idle' });
    try {
      const data = await runDeskReview(sym, params);
      if (mine !== scanSeq.current) return;           // a newer scan replaced this one
      if (data.error) { setError(data.error); return; }
      setRev(data); setScanned(params); setScannedKey(JSON.stringify([sym, params]));
      const picks = choosePicks(data.ranked || []);
      setSel(picks.length ? picks[0].index : (data.ranked?.length ? 0 : null));
    } catch (e: any) {
      if (mine === scanSeq.current) setError(e?.message || 'Failed to scan opportunities');
    } finally {
      if (mine === scanSeq.current) setLoading(false);
    }
  }, []);

  const scan = () => { const sym = ticker.trim().toUpperCase(); if (sym) run(sym, buildParams()); };

  // Hand-off from WatchList / Screener: fill the form and scan straight away.
  useEffect(() => {
    if (!seed) return;
    const sym = seed.ticker.toUpperCase();
    setTicker(sym); setExpiry(seed.expiry || '');
    if (seed.minProb != null) setMinProbOverride(seed.minProb);
    if (seed.minIncome != null) setMinIncome(seed.minIncome);
    run(sym, buildParams({ expiry: seed.expiry || '', minProb: seed.minProb ?? minProb, minIncome: seed.minIncome ?? minIncome }));
    onSeedConsumed?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed]);

  const stale = !!rev && !!scanned && scannedKey !== JSON.stringify([ticker.trim().toUpperCase(), buildParams()]);
  const nextEarnings = rev?.context?.next_earnings ?? null;

  const ranked = rev?.ranked ?? [];
  const picks: Pick[] = useMemo(() => choosePicks(ranked), [ranked]);
  const rows = useMemo(() => {
    const list = ranked.map((t, i) => ({ t, i })).filter(({ t }) => !(hideEarnings && crossesEarnings(t, nextEarnings)));
    const n = (v: number | null | undefined) => (v == null ? -Infinity : v);
    const cmpFn: Record<string, (a: { t: DeskRankedTrade; i: number }, b: { t: DeskRankedTrade; i: number }) => number> = {
      rank: (a, b) => a.i - b.i,
      keep: (a, b) => n(b.t.prob_keep_pct) - n(a.t.prob_keep_pct),
      return: (a, b) => n(b.t.static_return_pct) - n(a.t.static_return_pct),
      premium: (a, b) => n(b.t.premium) - n(a.t.premium),
    };
    return list.sort(cmpFn[sortBy]);
  }, [ranked, hideEarnings, nextEarnings, sortBy]);
  const hiddenCount = ranked.length - rows.length;
  const crossingCount = useMemo(() => ranked.filter(t => crossesEarnings(t, nextEarnings)).length, [ranked, nextEarnings]);
  const selected = sel != null ? ranked[sel] ?? null : null;

  const placePaper = async () => {
    if (!selected || !rev) return;
    setPaper({ state: 'busy' });
    try {
      await createPaperTrade(rev.ticker, selected, rev.spot ?? null, quoteSource);
      setPaper({ state: 'done' });
    } catch (e: any) { setPaper({ state: 'error', msg: e?.message || 'Could not place the paper trade' }); }
  };

  const toggleCmp = (i: number) => setCmp(c => c.includes(i) ? c.filter(x => x !== i) : c.length >= 3 ? c : [...c, i]);

  // ── render ────────────────────────────────────────────────────────────────
  const ctx = rev?.context ?? null;
  const exp0 = rev?.expiry_summaries?.[0];

  return (
    <div className="space-y-4">
      {/* filters */}
      <div className="rounded-xl border border-white/[0.07] bg-base-200/30 p-3 space-y-3">
        <div className="flex items-end gap-3 flex-wrap">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-base-content/55">Ticker (stock, ETF or index)</span>
            <input className="input input-bordered input-sm w-40 uppercase" value={ticker} placeholder="AAPL, SPY, .SPX"
              onChange={e => setTicker(e.target.value.toUpperCase())} onKeyDown={e => { if (e.key === 'Enter') scan(); }} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] text-base-content/55">Expiry</span>
            <select className="select select-bordered select-sm w-52" value={expiry} onChange={e => setExpiry(e.target.value)} disabled={!expiries.length && !!ticker}>
              <option value="">Auto — monthlies ≤ 45d</option>
              {expiries.map(d => [d, dteFromExpiry(d)] as const).filter(([, n]) => n >= 1 && n <= 366)
                .map(([d, n]) => <option key={d} value={d}>{d} · {n}d</option>)}
            </select>
          </label>
          <div className="flex flex-col gap-1">
            <span className="text-[11px] text-base-content/55">Style</span>
            <div className="flex rounded-lg border border-white/10 overflow-hidden" role="group" aria-label="Style preset">
              {(Object.keys(PRESETS) as PresetKey[]).map((k, i) => (
                <button key={k} onClick={() => { setPreset(k); setHideEarnings(PRESETS[k].hideEarnings); setAdvStructures(null); setMinProbOverride(null); }}
                  className={`px-3 py-1.5 text-sm transition-colors ${i > 0 ? 'border-l border-white/10' : ''} ${preset === k ? 'bg-primary/20 text-primary font-semibold' : 'text-base-content/60 hover:bg-white/[0.04]'}`}>{PRESETS[k].label}</button>
              ))}
            </div>
          </div>
          <button className="btn btn-ghost btn-sm gap-1.5 border border-white/10" onClick={() => setShowAdv(v => !v)} aria-expanded={showAdv}>
            <SlidersHorizontal className="w-3.5 h-3.5" /> Advanced
          </button>
          <div className="ml-auto flex items-center gap-2">
            {stale && <span className="text-xs text-warning">Filters changed since the last scan.</span>}
            <button className={`btn btn-primary btn-sm gap-2 ${stale ? 'animate-pulse' : ''}`} onClick={scan} disabled={loading || !ticker.trim() || structures.length === 0}>
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
              {loading ? 'Scanning…' : rev ? 'Re-scan' : 'Find opportunities'}
            </button>
          </div>
        </div>
        <p className="text-xs text-base-content/50">{PRESETS[preset].desc}</p>
        {showAdv && (
          <div className="rounded-lg border border-white/[0.06] bg-base-100/30 p-3 space-y-3">
            <div className="flex items-center gap-4 flex-wrap">
              <label className="flex items-center gap-2 text-xs">Minimum keep chance
                <input type="range" min={70} max={99} step={1} value={minProb} className="range range-xs range-primary w-40" onChange={e => setMinProbOverride(Number(e.target.value))} />
                <b className="tabular-nums w-9">{minProb}%</b>
              </label>
              <label className="flex items-center gap-2 text-xs">Minimum premium
                <span className="relative"><span className="absolute left-2 top-1/2 -translate-y-1/2 text-base-content/40">$</span>
                  <input type="number" min={0} step={5} className="input input-bordered input-xs w-24 pl-5" value={minIncome} onChange={e => setMinIncome(Number(e.target.value) || 0)} /></span>
                <span className="text-base-content/45">per contract</span>
              </label>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {STRUCTURES.map(s => {
                const locked = !!s.premiumOnly && !isPremium; const on = structures.includes(s.id);
                return (
                  <button key={s.id} disabled={locked} title={locked ? 'Premium feature' : undefined}
                    onClick={() => setAdvStructures(structures.includes(s.id) ? structures.filter(x => x !== s.id) : [...structures, s.id])}
                    className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${on ? 'border-primary/50 bg-primary/15 text-primary' : 'border-white/10 text-base-content/60 hover:border-white/25'} ${locked ? 'opacity-40' : ''}`}>{s.label}</button>
                );
              })}
            </div>
            <label className="flex items-start gap-2 text-xs cursor-pointer text-base-content/70">
              <input type="checkbox" className="checkbox checkbox-xs mt-0.5" checked={ownsShares} onChange={e => setOwnsShares(e.target.checked)} />
              <span>I already hold the shares — score covered calls as an income overlay, not a buy-write.</span>
            </label>
            <label className="flex items-start gap-2 text-xs cursor-pointer text-base-content/70">
              <input type="checkbox" className="checkbox checkbox-xs mt-0.5" checked={earningsAware} onChange={e => setEarningsAware(e.target.checked)} />
              <span>Earnings-aware scoring — discount strikes an earnings gap can leap. No effect when no print falls in the window.</span>
            </label>
          </div>
        )}
      </div>

      {error && <div className="alert alert-error text-sm py-2"><AlertTriangle className="w-4 h-4 shrink-0" /><span className="flex-1">{error}</span><button className="btn btn-ghost btn-xs" onClick={() => setError(null)} aria-label="Dismiss"><X className="w-3 h-3" /></button></div>}

      {/* loading: skeleton + honest progress */}
      {loading && (
        <div className="space-y-2" aria-live="polite">
          <div className="flex items-center gap-2 text-sm text-base-content/70">
            <Loader2 className="w-4 h-4 animate-spin text-primary" /> Scanning the options chain for {ticker.trim().toUpperCase()}… {elapsed}s
            <span className="text-xs text-base-content/40">usually 30–45 seconds</span>
          </div>
          {Array.from({ length: 6 }).map((_, i) => <div key={i} className="h-11 rounded-lg bg-base-200/40 animate-pulse" />)}
        </div>
      )}

      {!loading && !rev && !error && (
        <div className="text-center py-14 text-base-content/40">
          <Zap className="w-10 h-10 mx-auto mb-3 opacity-25" />
          <p className="font-medium text-base-content/60">Pick a ticker and a style, then find opportunities</p>
          <p className="text-xs mt-1">Every option-selling trade is graded A–F and ranked; you get three clear picks plus the full list.</p>
        </div>
      )}

      {rev && !loading && (
        <div className="space-y-4">
          {/* ticker strip */}
          <div className="rounded-xl border border-white/[0.07] bg-base-200/30 px-3 py-2.5">
            <div className="flex items-center gap-x-4 gap-y-1.5 flex-wrap">
              <span className="text-lg font-bold">{rev.ticker}</span>
              <span className="text-lg font-semibold tabular-nums">{money(rev.spot, 2)}</span>
              {ctx?.change_pct != null && <span className={`text-sm tabular-nums ${ctx.change_pct >= 0 ? 'text-success' : 'text-error'}`}>{ctx.change_pct >= 0 ? '+' : '−'}{Math.abs(ctx.change_pct).toFixed(2)}%</span>}
              {exp0?.iv_hv_ratio != null && <span className="rounded-full border border-white/10 px-2 py-0.5 text-xs" title={`ATM IV vs 30-day realized vol — premium is ${exp0.premium_richness}`}>IV/HV {exp0.iv_hv_ratio}× · {exp0.premium_richness}</span>}
              {rev.gex?.regime && <span className={`rounded-full border px-2 py-0.5 text-xs ${rev.gex.regime === 'long' ? 'border-success/30 text-success' : 'border-error/30 text-error'}`} title="Dealer gamma proxy: long gamma suppresses volatility (good for sellers); short gamma amplifies it.">Gamma {rev.gex.regime === 'long' ? 'long' : 'short'}</span>}
              {nextEarnings && <span className="rounded-full border border-warning/30 bg-warning/10 px-2 py-0.5 text-xs text-warning">Earnings {String(nextEarnings).slice(0, 10)}</span>}
              {ctx?.week52 && <span className="text-xs text-base-content/50">{ctx.week52.position_pct.toFixed(0)}% of 52-week range</span>}
              <button className="ml-auto text-xs text-base-content/55 hover:text-base-content flex items-center gap-1" onClick={() => setShowCtx(v => !v)}>
                Context <ChevronDown className={`w-3.5 h-3.5 transition-transform ${showCtx ? '' : '-rotate-90'}`} />
              </button>
            </div>
            {showCtx && ctx && <div className="mt-3"><TickerChrome ticker={rev.ticker} context={ctx} events={rev.flag_events} expirySummaries={rev.expiry_summaries} /></div>}
          </div>
          {rev.data_source_note && <div className="rounded-lg border border-warning/30 bg-warning/[0.07] px-3 py-2 text-xs text-warning flex items-center gap-1.5"><AlertTriangle className="w-3.5 h-3.5 shrink-0" /> {rev.data_source_note}</div>}

          {ranked.length === 0 ? (
            <div className="alert alert-warning text-sm"><AlertTriangle className="w-4 h-4" /><span>{rev.note || 'No candidate trades cleared these filters. Try a lower keep chance, a longer expiry, or a more volatile underlying.'}</span></div>
          ) : (
            <>
              {/* picks */}
              {picks.length > 0 && (
                <div>
                  <div className="text-sm font-semibold mb-2">Three picks <span className="text-xs font-normal text-base-content/45">each answers a different question</span></div>
                  <div className="grid gap-3 sm:grid-cols-3">
                    {picks.map(p => {
                      const cap = capitalOf(p.trade);
                      const on = sel === p.index;
                      return (
                        <div key={p.key} className={`rounded-xl border p-3 transition-colors cursor-pointer ${on ? 'border-primary/60 bg-primary/[0.07]' : 'border-white/[0.08] bg-base-200/30 hover:bg-base-200/50'}`}
                          onClick={() => setSel(p.index)}>
                          <div className="flex items-center justify-between gap-2">
                            <span className={`text-[11px] rounded-full px-2 py-0.5 ${p.key === 'best' ? 'bg-primary/15 text-primary' : p.key === 'safest' ? 'bg-success/15 text-success' : 'bg-base-content/10 text-base-content/70'}`}>{p.label}</span>
                            <GradeChip t={p.trade} />
                          </div>
                          <div className="mt-2 text-sm font-semibold leading-tight">{p.trade.label}</div>
                          <div className="text-xs text-base-content/50">{strikeText(p.trade)} · {p.trade.dte}d</div>
                          <div className="mt-2 flex items-baseline gap-1.5"><span className="text-2xl font-semibold tabular-nums text-success">{keepPct(p.trade.prob_keep_pct)}</span><span className="text-xs text-base-content/50">keep chance</span></div>
                          <div className="mt-1.5 grid grid-cols-2 gap-x-3 text-xs">
                            <span className="text-base-content/50">Premium</span><span className="text-right tabular-nums">{money(p.trade.premium)}</span>
                            <span className="text-base-content/50">{cap.label}</span><span className="text-right tabular-nums" title={cap.sub}>{money(cap.value)}</span>
                            <span className="text-base-content/50">Return</span><span className="text-right tabular-nums">{p.trade.static_return_pct != null ? `${p.trade.static_return_pct.toFixed(1)}%` : '—'}</span>
                          </div>
                          <div className="text-xs text-base-content/55 mt-2 leading-snug">{p.why}</div>
                          <label className="flex items-center gap-1.5 mt-2 text-xs text-base-content/60 cursor-pointer" onClick={e => e.stopPropagation()}>
                            <input type="checkbox" className="checkbox checkbox-xs" checked={cmp.includes(p.index)} onChange={() => toggleCmp(p.index)} /> Compare
                          </label>
                        </div>
                      );
                    })}
                  </div>
                  {cmp.length >= 2 && (
                    <div className="mt-3 rounded-xl border border-white/[0.1] bg-base-200/40 p-3">
                      <div className="flex items-center justify-between mb-2"><span className="text-sm font-semibold">Comparing {cmp.length} trades</span><button className="btn btn-ghost btn-xs" onClick={() => setCmp([])}>Clear</button></div>
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                          <thead><tr className="text-left text-base-content/45"><th className="font-normal py-1 pr-3" />{cmp.map(i => <th key={i} className="font-semibold py-1 px-2 text-base-content/85">{ranked[i].label} <span className="font-normal text-base-content/45">{strikeText(ranked[i])}</span></th>)}</tr></thead>
                          <tbody>
                            {([
                              ['Keep chance', (t: DeskRankedTrade) => keepPct(t.prob_keep_pct)],
                              ['Premium', (t: DeskRankedTrade) => money(t.premium)],
                              ['Capital', (t: DeskRankedTrade) => { const c = capitalOf(t); return `${money(c.value)} ${c.label.toLowerCase()}`; }],
                              ['Break-even', (t: DeskRankedTrade) => `${money(t.breakeven, 2)}${t.cushion_pct != null ? ` (${t.cushion_pct.toFixed(1)}% cushion)` : ''}`],
                              ['Return', (t: DeskRankedTrade) => t.static_return_pct != null ? `${t.static_return_pct.toFixed(1)}% in ${t.dte}d` : '—'],
                              ['Desk score', (t: DeskRankedTrade) => `${Math.round(t.desk_score)}${t.algo_grade ? ` (${t.algo_grade})` : ''}`],
                            ] as [string, (t: DeskRankedTrade) => string][]).map(([k, f]) => (
                              <tr key={k} className="border-t border-white/[0.05]"><td className="py-1.5 pr-3 text-base-content/55 whitespace-nowrap">{k}</td>{cmp.map(i => <td key={i} className="py-1.5 px-2 tabular-nums">{f(ranked[i])}</td>)}</tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* list + decision */}
              <SplitPane storageKey="finoagent.beta.split.income" defaultLeft={55}>
                <div className="min-w-0">
                  <div className="flex items-center gap-3 mb-2 flex-wrap">
                    <span className="text-sm font-semibold">Ranked by the desk <span className="text-xs font-normal text-base-content/45">{rows.length} of {ranked.length}</span></span>
                    <label className="ml-auto flex items-center gap-1.5 text-xs text-base-content/60">Sort
                      <select className="select select-xs select-bordered" value={sortBy} onChange={e => setSortBy(e.target.value as any)}>
                        <option value="rank">Desk rank</option><option value="keep">Keep chance</option><option value="return">Return</option><option value="premium">Premium</option>
                      </select></label>
                    {crossingCount > 0 && <label className="flex items-center gap-1.5 text-xs text-base-content/60 cursor-pointer"><input type="checkbox" className="checkbox checkbox-xs" checked={hideEarnings} onChange={e => setHideEarnings(e.target.checked)} /> Hide trades that cross earnings{hiddenCount > 0 && hideEarnings ? ` (${hiddenCount})` : ''}</label>}
                  </div>
                  <div className="rounded-xl border border-white/[0.07] bg-base-200/20 overflow-hidden">
                    <table className="w-full text-sm table-fixed">
                      <colgroup><col style={{ width: 36 }} /><col style={{ width: 44 }} /><col /><col style={{ width: 108 }} /><col style={{ width: 64 }} /><col style={{ width: 84 }} /><col className="hidden xl:table-column" style={{ width: 70 }} /></colgroup>
                      <thead><tr className="text-[11px] text-base-content/45 text-left">
                        <th className="pl-3 py-2 font-normal">#</th><th className="py-2 font-normal">Grade</th><th className="px-2 py-2 font-normal">Trade</th>
                        <th className="px-2 py-2 font-normal">Price ladder</th><th className="px-2 py-2 font-normal text-right">Keep</th><th className="px-2 py-2 font-normal text-right">Return</th>
                        <th className="pr-3 py-2 font-normal text-right hidden xl:table-cell">Premium</th></tr></thead>
                      <tbody>
                        {rows.map(({ t, i }) => {
                          const on = i === sel; const v = isVetoed(t);
                          return (
                            <tr key={i} tabIndex={0} onClick={() => setSel(i)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSel(i); } }}
                              className={`cursor-pointer border-t border-white/[0.05] outline-none transition-colors focus-visible:bg-white/[0.05] ${on ? 'bg-primary/[0.09]' : 'hover:bg-white/[0.03]'} ${v ? 'opacity-55' : ''}`}>
                              <td className="pl-3 py-2 text-xs text-base-content/45 tabular-nums">{i + 1}</td>
                              <td className="py-2"><GradeChip t={t} /></td>
                              <td className="px-2 py-2 overflow-hidden">
                                <div className="font-medium truncate">{t.label}{(t.nearby_count ?? 0) > 0 && <span className="ml-1 text-[11px] text-base-content/40" title={`Best of ${(t.nearby_count ?? 0) + 1} adjacent strikes`}>+{t.nearby_count}</span>}</div>
                                <div className="text-xs text-base-content/50 truncate">{strikeText(t)} · {t.dte}d{crossesEarnings(t, nextEarnings) ? <span className="text-warning"> · earnings</span> : null}{isWaiting(t) ? <span className="text-warning"> · wait</span> : null}</div>
                              </td>
                              <td className="px-2 py-2"><PriceLadder t={t} spot={rev.spot} width={96} /></td>
                              <td className="px-2 py-2 text-right tabular-nums">{keepPct(t.prob_keep_pct)}{t.prob_method === 'BS_fallback' && <span className="text-warning" title="IV estimate — the option chain's implied-vol surface was inconsistent, so this is a flat-vol estimate."> ≈</span>}</td>
                              <td className="px-2 py-2 text-right tabular-nums leading-tight">{t.static_return_pct != null ? `${t.static_return_pct.toFixed(1)}%` : '—'}<div className="text-[11px] text-base-content/40">{annText(t.premium_annualized_pct)}</div></td>
                              <td className="pr-3 py-2 text-right tabular-nums hidden xl:table-cell">{money(t.premium)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <p className="text-[11px] text-base-content/40 mt-2">Return is for the holding period shown. Green on the ladder is where the trade keeps its premium; ticks are short strikes; the bold line is spot.</p>
                </div>

                {/* decision panel */}
                {selected && (
                  <div className="space-y-3 lg:sticky lg:top-16 lg:max-h-[calc(100vh-5rem)] lg:overflow-y-auto min-w-0">
                    <div className="rounded-xl border border-white/[0.08] bg-base-200/30 p-3">
                      <div className="flex items-start gap-2">
                        <GradeChip t={selected} />
                        <div className="min-w-0 flex-1">
                          <div className="text-base font-semibold leading-tight">{selected.label}</div>
                          <div className="text-xs text-base-content/55">{strikeText(selected)}{selected.short_strike_pct != null ? ` (${selected.short_strike_pct >= 0 ? '+' : ''}${selected.short_strike_pct}% from spot)` : ''} · exp {selected.expiration} · {selected.dte}d</div>
                        </div>
                        {selected.confidence?.label && <span className="text-[11px] rounded-full border border-white/10 px-2 py-0.5 text-base-content/60 whitespace-nowrap" title={(selected.confidence.reasons || []).join(' · ')}>Pricing {selected.confidence.label}</span>}
                      </div>
                      <div className="mt-3 flex items-baseline gap-2">
                        <span className="text-4xl font-semibold tabular-nums text-success leading-none">{keepPct(selected.prob_keep_pct)}</span>
                        <span className="text-sm text-base-content/55">chance you keep the premium{selected.prob_method === 'BS_fallback' ? ' (IV estimate)' : ''}</span>
                      </div>
                      <div className="grid grid-cols-2 gap-2 mt-3">
                        {[
                          ['Premium', money(selected.premium), `${money(selected.premium_per_share, 2)} per share`],
                          [capitalOf(selected).label, money(capitalOf(selected).value), capitalOf(selected).sub],
                          ['Break-even', money(selected.breakeven, 2), selected.cushion_pct != null ? `${selected.cushion_pct.toFixed(1)}% cushion` : undefined],
                          ['Return', selected.static_return_pct != null ? `${selected.static_return_pct.toFixed(1)}%` : '—', `in ${selected.dte}d ${annText(selected.premium_annualized_pct)}`],
                          ['Theta / day', money(selected.theta_per_day), 'decay income'],
                          ['Short delta', selected.short_delta != null ? selected.short_delta.toFixed(2) : '—', 'exercise proxy'],
                        ].map(([k, v, sub]) => (
                          <div key={k as string} className="rounded-lg bg-base-200/50 px-2.5 py-2 min-w-0"><div className="text-[11px] text-base-content/50">{k}</div><div className="text-sm font-semibold tabular-nums truncate">{v}</div>{sub && <div className="text-[11px] text-base-content/45 truncate">{sub}</div>}</div>
                        ))}
                      </div>
                      {crossesEarnings(selected, nextEarnings) && <div className="mt-3 rounded-lg border border-warning/30 bg-warning/[0.07] px-2.5 py-2 text-xs text-warning">Earnings on {String(nextEarnings).slice(0, 10)} fall before this expires. {rev.earnings_aware ? 'The score already discounts for it.' : 'Earnings-aware scoring is off — the score does not.'}</div>}
                      {isVetoed(selected) && <div className="mt-3 rounded-lg border border-error/30 bg-error/[0.07] px-2.5 py-2 text-xs text-error"><b>Vetoed:</b> {(selected.grade_blocking || []).join(' · ')}</div>}
                      <div className="flex items-center gap-2 flex-wrap mt-3">
                        <button className={`btn btn-sm gap-1.5 ${paper.state === 'done' ? 'btn-success btn-outline' : 'btn-primary'}`} onClick={placePaper} disabled={paper.state === 'busy' || paper.state === 'done' || isVetoed(selected)}
                          title="Places 1 contract as a PAPER trade (no real order) and snapshots this analysis. Track it in My Trades ▸ Paper.">
                          {paper.state === 'busy' ? <Loader2 className="w-4 h-4 animate-spin" /> : paper.state === 'done' ? <Check className="w-4 h-4" /> : <Briefcase className="w-4 h-4" />}
                          {paper.state === 'busy' ? 'Placing…' : paper.state === 'done' ? 'Paper trade placed' : 'Place paper trade'}
                        </button>
                        <button className="btn btn-sm btn-ghost border border-white/10 gap-1.5" onClick={() => setAudit(v => !v)} aria-expanded={audit}>
                          Full audit <ChevronDown className={`w-3.5 h-3.5 transition-transform ${audit ? '' : '-rotate-90'}`} />
                        </button>
                        {paper.state === 'done' && <span className="text-xs text-success">Track it in My Trades ▸ Paper.</span>}
                        {paper.state === 'error' && <span className="text-xs text-warning">{paper.msg}</span>}
                      </div>
                    </div>
                    <ScoreCard t={selected} />
                    {audit && scanned && (
                      <div className="rounded-xl border border-white/[0.08] bg-base-200/20">
                        <TradeExplorer t={selected} ticker={rev.ticker} params={scanned} spot={rev.spot} nextEarnings={nextEarnings} />
                      </div>
                    )}
                  </div>
                )}
              </SplitPane>
            </>
          )}
        </div>
      )}
    </div>
  );
}
