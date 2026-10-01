import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Filter, Loader2, Search, Plus, ChevronLeft, ChevronRight, ArrowLeft, Play, Square,
  AlertTriangle, Info, SlidersHorizontal, ChevronDown, ChevronUp, ExternalLink, X, Calendar,
} from 'lucide-react';
import { screenIncomeUniverse, fetchIncomeVolMetrics, evaluateIncomeTicker } from '../api';
import type {
  IncomeScreenParams, IncomeScreenRow, IncomeVolRow, IncomeEvalParams, IncomeEvalResult, IncomeEvalTrade,
} from '../types';

/** Income Desk → Screener. Three steps, each progressive so a large universe never blocks the UI:
 *  1. screen (one fast server query) → table appears immediately;
 *  2. IV30/HV30 fill in batch-by-batch (vol filters apply as each batch lands);
 *  3. Evaluate runs the desk engine per selected ticker (bounded concurrency) and streams A/B trades in. */

const SECTORS = ['Technology', 'Communication Services', 'Consumer Cyclical', 'Consumer Defensive', 'Financial Services',
  'Healthcare', 'Industrials', 'Energy', 'Basic Materials', 'Real Estate', 'Utilities'];
const VOL_BATCH = 20;
const VOL_CONCURRENCY = 2;
const EVAL_CONCURRENCY = 2;       // the server serialises the heavy scan; 2 keeps the next request queued
const PAGE_SIZE = 25;
const RESULT_PAGE_SIZE = 8;
const LS_KEY = 'incomeScreener.v1';

type VolFilters = { ivGtHv: boolean; minRatio: number | null; ivMin: number | null; ivMax: number | null };
type Row = IncomeScreenRow & { manual?: boolean };
type SortKey = 'ticker' | 'current_price' | 'today_pct' | 'week52_pos_pct' | 'atm_iv' | 'hv30' | 'iv_hv_ratio' | 'days_to_earnings';
type EvalState = { status: 'queued' | 'running' | 'done' | 'error'; result?: IncomeEvalResult; error?: string };

const DEFAULT_SCREEN: IncomeScreenParams = {
  price_min: 20, price_max: 500, market_cap_min_b: 2, avg_volume_min: 1_000_000,
  beta_min: null, beta_max: null, change_min: null, change_max: null,
  week52_pos_min: null, week52_pos_max: null, sectors: [], exclude_earnings_within_days: null, max_results: 100,
};
const DEFAULT_VOL: VolFilters = { ivGtHv: true, minRatio: null, ivMin: null, ivMax: null };
const DEFAULT_EVAL: IncomeEvalParams = {
  min_prob: 0.85, min_income: 20, min_dte: 14, max_dte: 45, earnings: 'exclude', earnings_aware: true, grades: ['A', 'B'],
};

function loadSaved(): { screen: IncomeScreenParams; vol: VolFilters; evalP: IncomeEvalParams } {
  try {
    const s = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
    return { screen: { ...DEFAULT_SCREEN, ...s.screen }, vol: { ...DEFAULT_VOL, ...s.vol }, evalP: { ...DEFAULT_EVAL, ...s.evalP } };
  } catch { return { screen: DEFAULT_SCREEN, vol: DEFAULT_VOL, evalP: DEFAULT_EVAL }; }
}

const f2 = (n?: number | null) => (n == null ? '—' : n.toFixed(2));
const f1pct = (n?: number | null) => (n == null ? '—' : `${n.toFixed(1)}%`);
const money0 = (n?: number | null) => (n == null ? '—' : `$${Math.round(n).toLocaleString()}`);
const numOrNull = (v: string): number | null => (v.trim() === '' || isNaN(Number(v)) ? null : Number(v));

/** Run `worker` over `items` with at most `n` in flight; stops scheduling when `alive()` turns false. */
async function pool<T>(items: T[], n: number, alive: () => boolean, worker: (t: T) => Promise<void>) {
  let i = 0;
  const next = async (): Promise<void> => {
    while (alive() && i < items.length) { const it = items[i++]; await worker(it); }
  };
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, next));
}

function NumField({ label, value, onChange, step = 1, placeholder, suffix }: {
  label: string; value: number | null | undefined; onChange: (v: number | null) => void; step?: number; placeholder?: string; suffix?: string;
}) {
  return (
    <label className="form-control">
      <span className="label-text text-[11px] text-base-content/60 mb-1">{label}</span>
      <div className="relative">
        <input type="number" step={step} className="input input-bordered input-xs w-full pr-6" placeholder={placeholder ?? 'any'}
          value={value ?? ''} onChange={e => onChange(numOrNull(e.target.value))} />
        {suffix && <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-base-content/40">{suffix}</span>}
      </div>
    </label>
  );
}

function RangePos({ pos }: { pos?: number | null }) {
  if (pos == null) return null;
  return (
    <div className="w-16 h-1 bg-base-300 rounded-full relative mx-auto mt-1" title={`${pos.toFixed(0)}% of the 52W range`}>
      <div className="absolute top-1/2 -translate-y-1/2 w-1.5 h-1.5 rounded-full bg-primary" style={{ left: `calc(${Math.min(100, Math.max(0, pos))}% - 3px)` }} />
    </div>
  );
}

function GradeBadge({ g }: { g?: string | null }) {
  const tone = g === 'A' ? 'badge-success' : g === 'B' ? 'badge-info' : 'badge-ghost';
  return <span className={`badge badge-sm font-bold ${tone}`}>{g ?? '—'}</span>;
}

function Pager({ page, pages, setPage }: { page: number; pages: number; setPage: (p: number) => void }) {
  if (pages <= 1) return null;
  return (
    <div className="join">
      <button type="button" className="btn btn-xs join-item" disabled={page === 0} onClick={() => setPage(page - 1)}><ChevronLeft className="w-3 h-3" /></button>
      <span className="btn btn-xs join-item no-animation pointer-events-none">{page + 1} / {pages}</span>
      <button type="button" className="btn btn-xs join-item" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}><ChevronRight className="w-3 h-3" /></button>
    </div>
  );
}

// ───────────────────────── Step 3 — results ─────────────────────────

function TradeRow({ t, onOpen }: { t: IncomeEvalTrade; onOpen: () => void }) {
  const isCall = t.side === 'call';
  return (
    <tr className="hover:bg-base-200/20">
      <td>
        <div className={`flex items-center gap-1.5 whitespace-nowrap text-xs font-medium ${isCall ? 'text-warning' : 'text-secondary'}`}>
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${isCall ? 'bg-warning' : 'bg-secondary'}`} />
          {isCall ? 'Naked call' : 'Cash-secured put'}
        </div>
        <div className="text-[10px] text-base-content/45 mt-0.5 pl-3 whitespace-nowrap">{t.pick === 'nearest' ? 'nearest to spot' : 'farthest from spot'}</div>
      </td>
      <td className="font-mono text-xs">
        {t.short_strike}
        {t.short_strike_pct != null && <span className="text-base-content/45"> ({t.short_strike_pct > 0 ? '+' : ''}{t.short_strike_pct.toFixed(1)}%)</span>}
      </td>
      <td className="text-center">
        <GradeBadge g={t.algo_grade} />
        {t.timing_hold && <div className="text-[9px] text-warning mt-0.5" title="Good trade, but momentum is against a reachable strike — wait">WAIT</div>}
      </td>
      <td className="text-right font-mono text-xs">{t.desk_score ?? '—'}</td>
      <td className="text-right font-mono text-xs">{money0(t.premium)}<div className="text-[10px] text-base-content/45">{f2(t.premium_per_share)}/sh</div></td>
      <td className="text-right font-mono text-xs text-success">{f1pct(t.prob_keep_pct)}</td>
      <td className="text-right font-mono text-xs">{f1pct(t.prob_touch_pct)}</td>
      <td className="text-right font-mono text-xs">{f1pct(t.premium_annualized_pct)}</td>
      <td className="text-right font-mono text-xs">{money0(t.collateral)}</td>
      <td className="text-right font-mono text-xs">{f2(t.breakeven)}</td>
      <td className="text-right font-mono text-xs">{t.short_delta != null ? t.short_delta.toFixed(2) : '—'}</td>
      <td className="text-right">
        <button type="button" className="btn btn-xs btn-ghost text-primary gap-1" onClick={onOpen} title="Open the full desk analysis for this ticker & expiry">
          Desk <ExternalLink className="w-3 h-3" />
        </button>
      </td>
    </tr>
  );
}

function TickerResultCard({ ticker, st, quote, onOpen }: {
  ticker: string; st: EvalState; quote?: { price: number | null; pct: number | null }; onOpen: (expiry?: string | null) => void;
}) {
  const r = st.result;
  return (
    <div className="rounded-lg border border-white/[0.04] bg-base-200/30">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 border-b border-white/[0.03]">
        <span className="font-semibold">{ticker}</span>
        {(r?.spot ?? quote?.price) != null && (
          <span className="font-mono text-xs flex items-center gap-1.5">
            ${(r?.spot ?? quote!.price)!.toFixed(2)}
            {quote?.pct != null && (
              <span className={quote.pct > 0 ? 'text-success' : quote.pct < 0 ? 'text-error' : 'text-base-content/50'}>
                {quote.pct > 0 ? '+' : ''}{quote.pct.toFixed(2)}% today
              </span>
            )}
          </span>
        )}
        {r?.expiration && <span className="text-xs text-base-content/60 flex items-center gap-1"><Calendar className="w-3 h-3" />{r.expiration} · {r.dte}d</span>}
        {r?.next_earnings && <span className="text-xs text-base-content/50">ER {r.next_earnings}</span>}
        {r && r.n_candidates != null && <span className="text-[11px] text-base-content/40">{r.n_qualified ?? 0} qualified of {r.n_candidates} scanned</span>}
        <span className="ml-auto">
          {st.status === 'running' && <span className="text-xs text-base-content/50 flex items-center gap-1"><Loader2 className="w-3 h-3 animate-spin" /> evaluating…</span>}
          {st.status === 'queued' && <span className="text-xs text-base-content/40">queued</span>}
        </span>
      </div>
      {st.status === 'error' && <div className="px-3 py-2 text-xs text-error">{st.error}</div>}
      {r && r.trades.length > 0 && (
        <div className="overflow-x-auto">
          <table className="table table-xs w-full">
            <thead>
              <tr className="text-base-content/55">
                <th>Trade</th><th>Strike</th><th className="text-center">Grade</th><th className="text-right">Score</th>
                <th className="text-right">Premium</th><th className="text-right" title="Probability the short expires OTM (market-implied)">Win %</th>
                <th className="text-right" title="Probability the short strike is touched before expiry">P(touch)</th>
                <th className="text-right">Ann. yield</th><th className="text-right" title="Reg-T margin blocked">BPR</th>
                <th className="text-right">Breakeven</th><th className="text-right">Δ</th><th />
              </tr>
            </thead>
            <tbody>
              {r.trades.map(t => <TradeRow key={`${t.side}-${t.short_strike}`} t={t} onOpen={() => onOpen(t.expiration)} />)}
            </tbody>
          </table>
        </div>
      )}
      {r && r.trades.length === 0 && r.skipped && <div className="px-3 py-2 text-xs text-base-content/50">{r.skipped}</div>}
    </div>
  );
}

function ResultsView({ order, evals, quotes, evalP, running, onStop, onBack, onOpen }: {
  order: string[]; evals: Record<string, EvalState>; quotes: Record<string, { price: number | null; pct: number | null }>; evalP: IncomeEvalParams; running: boolean;
  onStop: () => void; onBack: () => void; onOpen: (ticker: string, expiry?: string | null) => void;
}) {
  const [onlyWithTrades, setOnlyWithTrades] = useState(true);
  const [side, setSide] = useState<'all' | 'call' | 'put'>('all');
  const [sortBy, setSortBy] = useState<'score' | 'order' | 'ticker'>('score');
  const [page, setPage] = useState(0);
  const [showSkipped, setShowSkipped] = useState(false);

  const done = order.filter(t => evals[t]?.status === 'done' || evals[t]?.status === 'error').length;
  const withTrades = order.filter(t => (evals[t]?.result?.trades.length ?? 0) > 0);
  const nTrades = withTrades.reduce((s, t) => s + (evals[t]?.result?.trades.length ?? 0), 0);
  const skipped = order.filter(t => evals[t]?.status === 'error' || (evals[t]?.status === 'done' && !(evals[t]?.result?.trades.length)));

  const list = useMemo(() => {
    const view = (t: string): EvalState => {
      const st = evals[t];
      if (!st?.result || side === 'all') return st;
      return { ...st, result: { ...st.result, trades: st.result.trades.filter(x => x.side === side) } };
    };
    let tickers = order.filter(t => {
      const st = evals[t];
      if (!onlyWithTrades) return true;
      if (st?.status === 'queued' || st?.status === 'running') return false;
      return (view(t)?.result?.trades.length ?? 0) > 0;
    });
    const best = (t: string) => Math.max(-1, ...(view(t)?.result?.trades ?? []).map(x => x.desk_score ?? 0));
    if (sortBy === 'score') tickers = [...tickers].sort((a, b) => best(b) - best(a));
    else if (sortBy === 'ticker') tickers = [...tickers].sort();
    return tickers.map(t => [t, view(t)] as const);
  }, [order, evals, onlyWithTrades, side, sortBy]);

  const pages = Math.max(1, Math.ceil(list.length / RESULT_PAGE_SIZE));
  useEffect(() => { if (page >= pages) setPage(pages - 1); }, [page, pages]);
  const slice = list.slice(page * RESULT_PAGE_SIZE, (page + 1) * RESULT_PAGE_SIZE);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 bg-base-200/40 p-3 rounded-lg border border-white/[0.03]">
        <button type="button" className="btn btn-xs btn-ghost gap-1" onClick={onBack}><ArrowLeft className="w-3.5 h-3.5" /> Back to screener</button>
        <div className="text-xs text-base-content/60">
          {(evalP.grades ?? ['A', 'B']).join('/')} grade · naked calls &amp; CSPs · ≥{Math.round(evalP.min_prob * 100)}% win · ≥${evalP.min_income} premium ·
          {' '}{evalP.min_dte}–{evalP.max_dte} DTE · earnings {evalP.earnings === 'exclude' ? 'excluded' : 'allowed'}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {running ? (
            <>
              <span className="text-xs flex items-center gap-1.5"><Loader2 className="w-3.5 h-3.5 animate-spin" /> {done}/{order.length} evaluated</span>
              <button type="button" className="btn btn-xs btn-outline btn-error gap-1" onClick={onStop}><Square className="w-3 h-3" /> Stop</button>
            </>
          ) : (
            <span className="text-xs text-base-content/60">{done}/{order.length} evaluated · <b className="text-base-content">{nTrades}</b> trades on <b className="text-base-content">{withTrades.length}</b> tickers</span>
          )}
        </div>
        {running && <progress className="progress progress-primary w-full h-1" value={done} max={order.length} />}
      </div>

      <div className="flex flex-wrap items-center gap-3 text-xs">
        <div className="join">
          {(['all', 'call', 'put'] as const).map(s => (
            <button key={s} type="button" className={`btn btn-xs join-item ${side === s ? 'btn-primary' : 'btn-ghost'}`} onClick={() => { setSide(s); setPage(0); }}>
              {s === 'all' ? 'All' : s === 'call' ? 'Naked calls' : 'Cash-secured puts'}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1.5 cursor-pointer">
          <input type="checkbox" className="checkbox checkbox-xs" checked={onlyWithTrades} onChange={e => { setOnlyWithTrades(e.target.checked); setPage(0); }} />
          Only tickers with trades
        </label>
        <select className="select select-bordered select-xs" value={sortBy} onChange={e => setSortBy(e.target.value as any)}>
          <option value="score">Sort: best desk score</option>
          <option value="order">Sort: screener order</option>
          <option value="ticker">Sort: ticker</option>
        </select>
        <span className="ml-auto"><Pager page={page} pages={pages} setPage={setPage} /></span>
      </div>

      {slice.length === 0 && (
        <div className="p-8 text-center text-sm text-base-content/50">
          {running ? <><Loader2 className="w-4 h-4 animate-spin mx-auto mb-2" />Evaluating — graded trades appear here as each ticker finishes.</> : 'No trades matched. Try lowering the minimum win % or premium, widening the DTE window, or allowing earnings.'}
        </div>
      )}
      <div className="space-y-2">
        {slice.map(([t, st]) => st && <TickerResultCard key={t} ticker={t} st={st} quote={quotes[t]} onOpen={(exp) => onOpen(t, exp)} />)}
      </div>
      <div className="flex justify-end"><Pager page={page} pages={pages} setPage={setPage} /></div>

      {!running && skipped.length > 0 && onlyWithTrades && (
        <div className="rounded-lg border border-white/[0.03] bg-base-200/20">
          <button type="button" className="w-full flex items-center gap-2 px-3 py-2 text-xs text-base-content/60" onClick={() => setShowSkipped(v => !v)}>
            {showSkipped ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            {skipped.length} ticker{skipped.length === 1 ? '' : 's'} with no qualifying trade
          </button>
          {showSkipped && (
            <ul className="px-3 pb-2 text-xs space-y-0.5">
              {skipped.map(t => <li key={t}><b className="text-base-content/80">{t}</b> <span className="text-base-content/50">— {evals[t]?.error || evals[t]?.result?.skipped || 'no trades'}</span></li>)}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

// ───────────────────────── Main ─────────────────────────

export function IncomeScreener({ onOpenTicker }: {
  onOpenTicker: (ticker: string, opts: { expiry?: string | null; minProb: number; minIncome: number }) => void;
}) {
  const saved = useMemo(loadSaved, []);
  const [screenP, setScreenP] = useState<IncomeScreenParams>(saved.screen);
  const [volF, setVolF] = useState<VolFilters>(saved.vol);
  const [evalP, setEvalP] = useState<IncomeEvalParams>(saved.evalP);
  const [showFilters, setShowFilters] = useState(true);

  const [rows, setRows] = useState<Row[]>([]);
  const [meta, setMeta] = useState<{ total?: number | null; as_of?: string; note?: string } | null>(null);
  const [screening, setScreening] = useState(false);
  const [screenErr, setScreenErr] = useState<string | null>(null);
  const [vol, setVol] = useState<Record<string, IncomeVolRow | null>>({});   // null = fetched, no data
  const [volBusy, setVolBusy] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDesc, setSortDesc] = useState(true);
  const [page, setPage] = useState(0);
  const [addTicker, setAddTicker] = useState('');

  const [view, setView] = useState<'screen' | 'results'>('screen');
  const [evalOrder, setEvalOrder] = useState<string[]>([]);
  const [evals, setEvals] = useState<Record<string, EvalState>>({});
  const [evalRunning, setEvalRunning] = useState(false);
  const [evalParamsUsed, setEvalParamsUsed] = useState<IncomeEvalParams>(evalP);

  const screenRun = useRef(0);
  const evalRun = useRef(0);

  useEffect(() => {
    try { localStorage.setItem(LS_KEY, JSON.stringify({ screen: screenP, vol: volF, evalP })); } catch { /* storage unavailable */ }
  }, [screenP, volF, evalP]);
  useEffect(() => () => { screenRun.current++; evalRun.current++; }, []);   // cancel in-flight loops on unmount

  const loadVol = async (items: { ticker: string; price?: number | null }[], runId: number) => {
    const batches: typeof items[] = [];
    for (let i = 0; i < items.length; i += VOL_BATCH) batches.push(items.slice(i, i + VOL_BATCH));
    await pool(batches, VOL_CONCURRENCY, () => screenRun.current === runId, async (b) => {
      try {
        const res = await fetchIncomeVolMetrics(b);
        if (screenRun.current !== runId) return;
        const got: Record<string, IncomeVolRow | null> = {};
        b.forEach(x => { got[x.ticker] = null; });
        res.forEach(r => { got[r.ticker] = r; });
        setVol(v => ({ ...v, ...got }));
      } catch {
        if (screenRun.current !== runId) return;
        setVol(v => ({ ...v, ...Object.fromEntries(b.map(x => [x.ticker, null])) }));
      }
    });
  };

  const runScreen = async () => {
    const runId = ++screenRun.current;
    setScreening(true); setScreenErr(null); setPage(0);
    const manual = rows.filter(r => r.manual);
    try {
      const res = await screenIncomeUniverse(screenP);
      if (screenRun.current !== runId) return;
      const manualSet = new Set(manual.map(m => m.ticker));
      const next: Row[] = [...manual, ...res.rows.filter(r => !manualSet.has(r.ticker))];
      setRows(next);
      setMeta({ total: res.total_matches, as_of: res.as_of, note: res.universe_note });
      setSelected(sel => new Set([...sel].filter(t => manualSet.has(t))));
      setVol(v => Object.fromEntries(Object.entries(v).filter(([t]) => manualSet.has(t))));
      setShowFilters(false);
      setScreening(false);
      setVolBusy(true);
      await loadVol(res.rows.filter(r => !manualSet.has(r.ticker)).map(r => ({ ticker: r.ticker, price: r.current_price })), runId);
    } catch (e: any) {
      if (screenRun.current === runId) setScreenErr(e?.message || 'Screen failed');
    } finally {
      if (screenRun.current === runId) { setScreening(false); setVolBusy(false); }
    }
  };

  const addManual = async (e: React.FormEvent) => {
    e.preventDefault();
    const tickers = addTicker.split(/[\s,]+/).map(s => s.trim().toUpperCase()).filter(Boolean);
    if (!tickers.length) return;
    setAddTicker('');
    const fresh = tickers.filter(t => !rows.some(r => r.ticker === t));
    setRows(rs => [...fresh.map(t => ({ ticker: t, current_price: null, today_pct: null, week52_low: null, week52_high: null, manual: true } as Row)), ...rs]);
    setSelected(sel => new Set([...sel, ...tickers]));
    if (!fresh.length) return;
    try {
      const res = await fetchIncomeVolMetrics(fresh.map(t => ({ ticker: t })));
      const byT = Object.fromEntries(res.map(r => [r.ticker, r]));
      setVol(v => ({ ...v, ...Object.fromEntries(fresh.map(t => [t, byT[t] ?? null])) }));
      setRows(rs => rs.map(r => {
        const v = byT[r.ticker];
        if (!r.manual || !v) return r;
        const pos = v.week52_high != null && v.week52_low != null && v.current_price != null && v.week52_high > v.week52_low
          ? Math.round((v.current_price - v.week52_low) / (v.week52_high - v.week52_low) * 1000) / 10 : null;
        return { ...r, current_price: v.current_price ?? null, today_pct: v.today_pct ?? null, week52_high: v.week52_high ?? null, week52_low: v.week52_low ?? null, week52_pos_pct: pos };
      }));
    } catch {
      setVol(v => ({ ...v, ...Object.fromEntries(fresh.map(t => [t, null])) }));
    }
  };

  const removeManual = (t: string) => {
    setRows(rs => rs.filter(r => r.ticker !== t));
    setSelected(sel => { const s = new Set(sel); s.delete(t); return s; });
  };

  const volActive = volF.ivGtHv || volF.minRatio != null || volF.ivMin != null || volF.ivMax != null;
  const passesVol = (v: IncomeVolRow | null | undefined): boolean | 'pending' => {
    if (v === undefined) return 'pending';
    if (!volActive) return true;
    if (!v || v.atm_iv == null || v.hv30 == null) return false;
    if (volF.ivGtHv && !(v.atm_iv > v.hv30)) return false;
    if (volF.minRatio != null && (v.atm_iv / v.hv30) < volF.minRatio) return false;
    if (volF.ivMin != null && v.atm_iv < volF.ivMin) return false;
    if (volF.ivMax != null && v.atm_iv > volF.ivMax) return false;
    return true;
  };

  const pendingCount = rows.filter(r => !r.manual && vol[r.ticker] === undefined).length;
  const visible = useMemo(() => {
    const vis = rows.filter(r => r.manual || passesVol(vol[r.ticker]) === true || (!volActive && vol[r.ticker] === undefined));
    if (!sortKey) return vis;
    const val = (r: Row): number | string | null => {
      const v = vol[r.ticker];
      if (sortKey === 'atm_iv') return v?.atm_iv ?? null;
      if (sortKey === 'hv30') return v?.hv30 ?? null;
      if (sortKey === 'iv_hv_ratio') return v?.atm_iv != null && v?.hv30 ? v.atm_iv / v.hv30 : null;
      return (r as any)[sortKey] ?? null;
    };
    return [...vis].sort((a, b) => {
      const va = val(a), vb = val(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      const c = va < vb ? -1 : va > vb ? 1 : 0;
      return sortDesc ? -c : c;
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, vol, volF, sortKey, sortDesc]);

  const pages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  useEffect(() => { if (page >= pages) setPage(pages - 1); }, [page, pages]);
  const pageRows = visible.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const visibleTickers = visible.map(r => r.ticker);
  const allVisibleSelected = visibleTickers.length > 0 && visibleTickers.every(t => selected.has(t));
  const someSelected = visibleTickers.some(t => selected.has(t));
  const selectedList = rows.map(r => r.ticker).filter(t => selected.has(t));

  const toggleAll = () => setSelected(sel => {
    const s = new Set(sel);
    if (allVisibleSelected) visibleTickers.forEach(t => s.delete(t)); else visibleTickers.forEach(t => s.add(t));
    return s;
  });
  const toggle = (t: string) => setSelected(sel => { const s = new Set(sel); s.has(t) ? s.delete(t) : s.add(t); return s; });

  const headerSort = (k: SortKey) => {
    if (sortKey === k) setSortDesc(d => !d); else { setSortKey(k); setSortDesc(k !== 'ticker'); }
  };
  const Th = ({ k, children, right = true, title }: { k: SortKey; children: React.ReactNode; right?: boolean; title?: string }) => (
    <th className={`cursor-pointer hover:text-base-content select-none ${right ? 'text-right' : ''}`} onClick={() => headerSort(k)} title={title}>
      {children}{sortKey === k && (sortDesc ? ' ↓' : ' ↑')}
    </th>
  );

  const evaluate = async () => {
    if (!selectedList.length) return;
    const runId = ++evalRun.current;
    const params = { ...evalP };
    const order = [...selectedList];
    const earningsHint = Object.fromEntries(rows.map(r => [r.ticker, r.next_earnings ?? null]));
    setEvalParamsUsed(params);
    setEvalOrder(order);
    setEvals(Object.fromEntries(order.map(t => [t, { status: 'queued' } as EvalState])));
    setView('results');
    setEvalRunning(true);
    await pool(order, EVAL_CONCURRENCY, () => evalRun.current === runId, async (t) => {
      setEvals(s => ({ ...s, [t]: { status: 'running' } }));
      try {
        const res = await evaluateIncomeTicker(t, { ...params, next_earnings: earningsHint[t] ?? null });
        if (evalRun.current === runId) setEvals(s => ({ ...s, [t]: { status: 'done', result: res } }));
      } catch (e: any) {
        if (evalRun.current === runId) setEvals(s => ({ ...s, [t]: { status: 'error', error: e?.message || 'Evaluation failed' } }));
      }
    });
    if (evalRun.current === runId) setEvalRunning(false);
  };

  const stopEval = () => {
    evalRun.current++;
    setEvalRunning(false);
    setEvals(s => Object.fromEntries(Object.entries(s).map(([t, st]) =>
      [t, st.status === 'queued' || st.status === 'running' ? { status: 'error', error: 'Stopped' } as EvalState : st])));
  };

  if (view === 'results') {
    return (
      <ResultsView order={evalOrder} evals={evals}
        quotes={Object.fromEntries(rows.map(r => [r.ticker, { price: r.current_price, pct: r.today_pct }]))} evalP={evalParamsUsed} running={evalRunning}
        onStop={stopEval} onBack={() => setView('screen')}
        onOpen={(t, exp) => onOpenTicker(t, { expiry: exp, minProb: Math.round(evalParamsUsed.min_prob * 100), minIncome: evalParamsUsed.min_income })} />
    );
  }

  const setS = (k: keyof IncomeScreenParams) => (v: number | null) => setScreenP(p => ({ ...p, [k]: v }));
  const setE = <K extends keyof IncomeEvalParams>(k: K, v: IncomeEvalParams[K]) => setEvalP(p => ({ ...p, [k]: v }));
  const lastEvalDone = evalOrder.length > 0;

  return (
    <div className="space-y-4">
      {/* ── Filters ── */}
      <div className="bg-base-200/40 rounded-lg border border-white/[0.03]">
        <button type="button" className="w-full flex items-center gap-2 px-3 py-2.5 text-sm font-medium" onClick={() => setShowFilters(s => !s)}>
          <SlidersHorizontal className="w-4 h-4 text-primary" /> Screen the market for premium-selling candidates
          <span className="ml-auto text-xs text-base-content/50 font-normal">{showFilters ? 'Hide filters' : 'Edit filters'}</span>
          {showFilters ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </button>
        {showFilters && (
          <form className="px-3 pb-3 space-y-3" onSubmit={e => { e.preventDefault(); runScreen(); }}>
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2">
              <NumField label="Price min" value={screenP.price_min} onChange={setS('price_min')} suffix="$" />
              <NumField label="Price max" value={screenP.price_max} onChange={setS('price_max')} suffix="$" />
              <NumField label="Market cap ≥" value={screenP.market_cap_min_b} onChange={setS('market_cap_min_b')} suffix="$B" step={0.5} />
              <NumField label="Avg volume ≥ (M sh)" value={screenP.avg_volume_min != null ? screenP.avg_volume_min / 1e6 : null}
                onChange={v => setScreenP(p => ({ ...p, avg_volume_min: v == null ? null : v * 1e6 }))} step={0.5} />
              <NumField label="Beta ≥" value={screenP.beta_min} onChange={setS('beta_min')} step={0.1} />
              <NumField label="Beta ≤" value={screenP.beta_max} onChange={setS('beta_max')} step={0.1} />
              <NumField label="Today % ≥" value={screenP.change_min} onChange={setS('change_min')} step={0.5} suffix="%" />
              <NumField label="Today % ≤" value={screenP.change_max} onChange={setS('change_max')} step={0.5} suffix="%" />
              <NumField label="52W position ≥ (0=low)" value={screenP.week52_pos_min} onChange={setS('week52_pos_min')} step={5} suffix="%" />
              <NumField label="52W position ≤ (100=high)" value={screenP.week52_pos_max} onChange={setS('week52_pos_max')} step={5} suffix="%" />
              <NumField label="No earnings within" value={screenP.exclude_earnings_within_days} onChange={setS('exclude_earnings_within_days')} suffix="d" />
              <label className="form-control">
                <span className="label-text text-[11px] text-base-content/60 mb-1">Max names</span>
                <select className="select select-bordered select-xs" value={screenP.max_results}
                  onChange={e => setScreenP(p => ({ ...p, max_results: Number(e.target.value) }))}>
                  {[50, 100, 150, 250].map(n => <option key={n} value={n}>{n} most liquid</option>)}
                </select>
              </label>
            </div>
            <div>
              <span className="label-text text-[11px] text-base-content/60">Sectors (none = all)</span>
              <div className="flex flex-wrap gap-1.5 mt-1">
                {SECTORS.map(s => {
                  const on = (screenP.sectors ?? []).includes(s);
                  return (
                    <button key={s} type="button" className={`btn btn-xs ${on ? 'btn-secondary' : 'btn-outline'}`}
                      onClick={() => setScreenP(p => ({ ...p, sectors: on ? (p.sectors ?? []).filter(x => x !== s) : [...(p.sectors ?? []), s] }))}>{s}</button>
                  );
                })}
              </div>
            </div>
            <div className="flex flex-wrap items-end gap-3 pt-1 border-t border-white/[0.04]">
              <span className="text-[11px] text-base-content/60 w-full pt-2">Volatility filters (applied live as IV30/HV30 load)</span>
              <label className="flex items-center gap-2 text-xs cursor-pointer">
                <input type="checkbox" className="checkbox checkbox-xs checkbox-primary" checked={volF.ivGtHv} onChange={e => setVolF(v => ({ ...v, ivGtHv: e.target.checked }))} />
                ATM IV30 &gt; HV30 <span className="text-base-content/45">(options priced rich vs realized)</span>
              </label>
              <div className="w-28"><NumField label="IV/HV ratio ≥" value={volF.minRatio} onChange={x => setVolF(v => ({ ...v, minRatio: x }))} step={0.05} /></div>
              <div className="w-24"><NumField label="IV30 ≥" value={volF.ivMin} onChange={x => setVolF(v => ({ ...v, ivMin: x }))} suffix="%" /></div>
              <div className="w-24"><NumField label="IV30 ≤" value={volF.ivMax} onChange={x => setVolF(v => ({ ...v, ivMax: x }))} suffix="%" /></div>
              <div className="ml-auto flex gap-2">
                <button type="button" className="btn btn-xs btn-ghost" onClick={() => { setScreenP(DEFAULT_SCREEN); setVolF(DEFAULT_VOL); }}>Reset</button>
                <button type="submit" className="btn btn-sm btn-primary gap-1.5" disabled={screening}>
                  {screening ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                  {screening ? 'Screening…' : rows.length ? 'Re-run screen' : 'Run screen'}
                </button>
              </div>
            </div>
          </form>
        )}
      </div>

      {screenErr && <div className="alert alert-error text-sm"><AlertTriangle className="w-4 h-4" /><span>{screenErr}</span></div>}

      {/* ── Table toolbar ── */}
      {(rows.length > 0 || screening) && (
        <div className="flex flex-wrap items-center gap-3">
          <div className="text-xs text-base-content/60">
            <b className="text-base-content">{visible.length}</b> match{visible.length === 1 ? '' : 'es'}
            {meta?.total != null && <> · {rows.filter(r => !r.manual).length} most liquid of {meta.total} screened</>}
            {selectedList.length > 0 && <> · <b className="text-primary">{selectedList.length} selected</b></>}
          </div>
          {(volBusy || pendingCount > 0) && rows.length > 0 && (
            <div className="flex items-center gap-2 text-xs text-base-content/60">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> IV30/HV30 {rows.filter(r => !r.manual).length - pendingCount}/{rows.filter(r => !r.manual).length}
              {volActive && pendingCount > 0 && <span className="text-base-content/40">· names appear as they pass</span>}
            </div>
          )}
          {lastEvalDone && (
            <button type="button" className="btn btn-xs btn-ghost text-primary" onClick={() => setView('results')}>View last results →</button>
          )}
          <form onSubmit={addManual} className="join ml-auto">
            <input type="text" className="input input-xs input-bordered join-item w-44 uppercase" placeholder="Add ticker(s): SPY, TSLA"
              value={addTicker} onChange={e => setAddTicker(e.target.value.toUpperCase())} />
            <button type="submit" className="btn btn-xs btn-primary join-item" disabled={!addTicker.trim()}><Plus className="w-3.5 h-3.5" /> Add</button>
          </form>
        </div>
      )}

      {/* ── Table ── */}
      {rows.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-white/[0.03]">
          <table className="table table-sm w-full">
            <thead>
              <tr className="bg-base-200/50 text-base-content/70">
                <th className="w-8">
                  <input type="checkbox" className="checkbox checkbox-xs" checked={allVisibleSelected}
                    ref={el => { if (el) el.indeterminate = !allVisibleSelected && someSelected; }}
                    onChange={toggleAll} title={allVisibleSelected ? 'Unselect all matches' : `Select all ${visible.length} matches (all pages)`} />
                </th>
                <Th k="ticker" right={false}>Ticker</Th>
                <Th k="current_price">Price</Th>
                <Th k="today_pct">Today %</Th>
                <th className="text-right">52W Low</th>
                <Th k="week52_pos_pct" title="Where the price sits in its 52-week range"><span className="block text-center">Range</span></Th>
                <th className="text-right">52W High</th>
                <Th k="atm_iv" title="At-the-money implied vol at a constant 30-day tenor">ATM IV30</Th>
                <Th k="hv30" title="30-day realized (historical) volatility">HV30</Th>
                <Th k="iv_hv_ratio" title="IV30 ÷ HV30 — above 1 means options are priced rich">IV/HV</Th>
                <Th k="days_to_earnings">Earnings</Th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map(r => {
                const v = vol[r.ticker];
                const ratio = v?.atm_iv != null && v?.hv30 ? v.atm_iv / v.hv30 : null;
                return (
                  <tr key={r.ticker} className={`hover:bg-base-200/20 cursor-pointer ${selected.has(r.ticker) ? 'bg-primary/5' : ''}`} onClick={() => toggle(r.ticker)}>
                    <td onClick={e => e.stopPropagation()}>
                      <input type="checkbox" className="checkbox checkbox-xs" checked={selected.has(r.ticker)} onChange={() => toggle(r.ticker)} />
                    </td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        <span className="font-medium">{r.ticker}</span>
                        {r.manual && <span className="badge badge-ghost badge-xs">added</span>}
                        {r.manual && (
                          <button type="button" className="text-base-content/30 hover:text-error" title="Remove" onClick={e => { e.stopPropagation(); removeManual(r.ticker); }}>
                            <X className="w-3 h-3" />
                          </button>
                        )}
                      </div>
                      {r.name && <div className="text-[10px] text-base-content/45 truncate max-w-[160px]">{r.name}</div>}
                    </td>
                    <td className="text-right font-mono text-xs">{f2(r.current_price)}</td>
                    <td className={`text-right font-mono text-xs ${(r.today_pct ?? 0) > 0 ? 'text-success' : (r.today_pct ?? 0) < 0 ? 'text-error' : ''}`}>
                      {r.today_pct != null ? `${r.today_pct > 0 ? '+' : ''}${r.today_pct.toFixed(2)}%` : '—'}
                    </td>
                    <td className="text-right font-mono text-xs">{f2(r.week52_low)}</td>
                    <td><RangePos pos={r.week52_pos_pct} /></td>
                    <td className="text-right font-mono text-xs">{f2(r.week52_high)}</td>
                    <td className="text-right font-mono text-xs" title={v?.iv_tenor ?? undefined}>
                      {v === undefined ? <Loader2 className="w-3 h-3 animate-spin inline text-base-content/30" /> : f1pct(v?.atm_iv)}
                    </td>
                    <td className="text-right font-mono text-xs">{v === undefined ? '' : f1pct(v?.hv30)}</td>
                    <td className={`text-right font-mono text-xs ${ratio == null ? '' : ratio > 1 ? 'text-success' : 'text-base-content/50'}`}>{ratio != null ? ratio.toFixed(2) : ''}</td>
                    <td className="text-right text-xs">
                      {r.next_earnings ? <span className={r.days_to_earnings != null && r.days_to_earnings <= evalP.max_dte ? 'text-warning' : 'text-base-content/60'}>{r.next_earnings.slice(5)} <span className="text-base-content/40">({r.days_to_earnings}d)</span></span> : '—'}
                    </td>
                  </tr>
                );
              })}
              {pageRows.length === 0 && (
                <tr><td colSpan={11} className="text-center text-xs text-base-content/50 py-6">
                  {pendingCount > 0 ? 'Loading volatility — matches will appear here…' : 'No names pass the volatility filters. Loosen IV/HV or re-screen.'}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > 0 && (
        <div className="flex items-center justify-between text-[11px] text-base-content/40">
          <span className="flex items-center gap-1"><Info className="w-3 h-3" /> {meta?.note ?? 'Manually added tickers always show.'} Click a row to select it.</span>
          <Pager page={page} pages={pages} setPage={setPage} />
        </div>
      )}

      {!rows.length && !screening && (
        <div className="p-8 text-center text-sm text-base-content/50 rounded-lg border border-dashed border-white/[0.06]">
          <Filter className="w-5 h-5 mx-auto mb-2 text-base-content/30" />
          Set your filters and <b>Run screen</b> — or add tickers directly:
          <form onSubmit={addManual} className="join mt-3 justify-center flex">
            <input type="text" className="input input-sm input-bordered join-item w-56 uppercase" placeholder="SPY, AAPL, TSLA"
              value={addTicker} onChange={e => setAddTicker(e.target.value.toUpperCase())} />
            <button type="submit" className="btn btn-sm btn-primary join-item" disabled={!addTicker.trim()}><Plus className="w-3.5 h-3.5" /> Add</button>
          </form>
        </div>
      )}

      {/* ── Evaluate parameters ── */}
      {rows.length > 0 && (
        <div className="bg-base-200/40 rounded-lg border border-white/[0.03] p-3 space-y-3">
          <div className="text-sm font-medium">Evaluate selected — A/B-graded naked calls &amp; cash-secured puts (≤ 4 per stock)</div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 items-end">
            <label className="form-control col-span-2">
              <span className="label-text text-[11px] text-base-content/60 mb-1">Min success (not exercised): <b className="text-secondary">{Math.round(evalP.min_prob * 100)}%</b></span>
              <input type="range" min={70} max={99} value={Math.round(evalP.min_prob * 100)} className="range range-secondary range-xs"
                onChange={e => setE('min_prob', Number(e.target.value) / 100)} />
            </label>
            <NumField label="Min premium ($/contract)" value={evalP.min_income} onChange={v => setE('min_income', v ?? 0)} step={5} />
            <NumField label="Expiry min (days)" value={evalP.min_dte} onChange={v => setE('min_dte', Math.max(1, v ?? 1))} />
            <NumField label="Expiry max (days)" value={evalP.max_dte} onChange={v => setE('max_dte', Math.max(1, v ?? 45))} />
            <label className="form-control">
              <span className="label-text text-[11px] text-base-content/60 mb-1">Earnings before expiry</span>
              <div className="join">
                <button type="button" className={`btn btn-xs join-item ${evalP.earnings === 'exclude' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setE('earnings', 'exclude')}>Exclude</button>
                <button type="button" className={`btn btn-xs join-item ${evalP.earnings === 'include' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setE('earnings', 'include')}>Include</button>
              </div>
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-4 text-xs">
            <span className="text-base-content/60">Grades:</span>
            {['A', 'B', 'C'].map(g => (
              <label key={g} className="flex items-center gap-1.5 cursor-pointer">
                <input type="checkbox" className="checkbox checkbox-xs" checked={(evalP.grades ?? []).includes(g)}
                  onChange={e => setE('grades', e.target.checked ? [...(evalP.grades ?? []), g] : (evalP.grades ?? []).filter(x => x !== g))} />
                {g}
              </label>
            ))}
            <label className="flex items-center gap-1.5 cursor-pointer" title="Across a print before expiry, discount walls the earnings gap can leap and penalise strikes inside ~1.5× the event move">
              <input type="checkbox" className="checkbox checkbox-xs checkbox-warning" checked={!!evalP.earnings_aware} onChange={e => setE('earnings_aware', e.target.checked)} />
              Earnings-aware grading
            </label>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-3">
            {evalP.min_dte > evalP.max_dte && <span className="text-xs text-error mr-auto">Expiry min must be ≤ max.</span>}
            {selectedList.length > 20 && (
              <span className="text-[11px] text-base-content/50 mr-auto flex items-center gap-1">
                <Info className="w-3 h-3" /> ~{Math.ceil(selectedList.length * 10 / 60)} min for {selectedList.length} names — results stream in as each finishes; you can stop any time.
              </span>
            )}
            <button type="button" className="btn btn-sm btn-primary gap-1.5"
              disabled={!selectedList.length || !(evalP.grades ?? []).length || evalP.min_dte > evalP.max_dte}
              onClick={evaluate}>
              <Play className="w-4 h-4" /> Evaluate {selectedList.length || ''} {selectedList.length === 1 ? 'stock' : 'stocks'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
