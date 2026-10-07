/**
 * MyTradesBeta — the Beta layout for Derivative Trades.
 *
 *   KPI strip        → book totals that exclude unpriced positions (classic counts them, so one unpriced SPX
 *                      condor can drag the headline to a fake −$29k)
 *   Attention lane   → only what needs a decision: Act · No quote · Harvest
 *   Table | Board    → one dense, urgency-sorted list (or a triage board) instead of seven stacked groups,
 *                      with the verdict sentence on every row
 *   Inspector        → the "Decide" level for the selected position (BetaTradeInspector)
 *
 * Data, endpoints and persistence are the classic ones (fetchActiveTrades · fetchTradeLivePnl ·
 * saveTradePnlSnapshot); the Closed and Paper tabs reuse the classic ledger and paper trader unchanged.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, RefreshCw, AlertCircle, X, List, LayoutGrid, CheckCircle2, ChevronDown } from 'lucide-react';
import type { SavedStrategyItem, LivePnlResponse, ClosedMonthSummary } from '../api';
import { fetchActiveTrades, fetchTradeLivePnl, saveTradePnlSnapshot, fetchClosedLedger, appendTradeTransaction } from '../api';
import { fmtMoney, fmtPct } from '../lib/tradeFormat';
import {
  tradePurpose, PURPOSE_META, PURPOSE_ORDER, tradeStructure, daysHeld, effectivePnl, deployedCapital,
  trimPnl, ClosedLedger,
} from '../components/trades/MyTradesV2';
import BetaBookRisk from './BetaBookRisk';
import PaperTraderPanel from '../components/trades/PaperTraderPanel';
import UpdatePositionModal from '../components/trades/UpdatePositionModal';
import CloseTradeModal from '../components/trades/CloseTradeModal';
import BetaTradeInspector from './BetaTradeInspector';
import SplitPane from './SplitPane';
import { StateChip, Kpi, timeAgo } from './BetaChrome';
import {
  deriveTradeState, quoteInfo, summarizeBook, urgencyCompare, STATE_META, STATE_ORDER,
  type ActionState, type TradeState,
} from './betaState';

type Tab = 'active' | 'closed' | 'paper';
type View = 'table' | 'board';
type SortKey = 'urgency' | 'expiry' | 'pnl' | 'ticker';
type Filter = ActionState | 'all';

const LS_VIEW = 'finoagent.beta.trades.view';
const readView = (): View => { try { return localStorage.getItem(LS_VIEW) === 'board' ? 'board' : 'table'; } catch { return 'table'; } };

const signedMoney = (n: number | null | undefined) => fmtMoney(n, { signed: true });
const pnlTone = (n: number | null | undefined) => (n == null ? 'text-base-content/40' : n >= 0 ? 'text-success' : 'text-error');

/** Share of the trade's life already used: held / (held + days left). Honest — both numbers are known. */
function timeUsedPct(trade: SavedStrategyItem, dte: number | null): number {
  if (dte == null) return 0;
  const held = daysHeld(trade.entry_date);
  const total = held + dte;
  return total > 0 ? Math.max(0, Math.min(100, (held / total) * 100)) : 0;
}

export default function MyTradesBeta({ initialSelectedId = null }: { initialSelectedId?: number | null }) {
  const [tab, setTab] = useState<Tab>('active');
  const [view, setViewState] = useState<View>(readView);
  const setView = (v: View) => { setViewState(v); try { localStorage.setItem(LS_VIEW, v); } catch { /* private mode */ } };
  const [trades, setTrades] = useState<SavedStrategyItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [pnlMap, setPnlMap] = useState<Record<number, LivePnlResponse>>({});
  const [pnlLoading, setPnlLoading] = useState<Record<number, boolean>>({});
  const [quoteSource, setQuoteSource] = useState<'yfinance' | 'ibkr'>('yfinance');
  const [lastRefreshAt, setLastRefreshAt] = useState<string | null>(null);
  const [refreshingAll, setRefreshingAll] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(initialSelectedId);
  const [filter, setFilter] = useState<Filter>('all');
  const [sortBy, setSortBy] = useState<SortKey>('urgency');
  const [groupByPurpose, setGroupByPurpose] = useState(true);
  const [updateTrade, setUpdateTrade] = useState<SavedStrategyItem | null>(null);
  const [closeTrade, setCloseTrade] = useState<SavedStrategyItem | null>(null);

  const tradesRef = useRef<SavedStrategyItem[]>([]);
  tradesRef.current = trades;

  // ── data (same endpoints as classic) ──────────────────────────────────────
  const loadActive = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const data = await fetchActiveTrades('active', false);
      setTrades(data);
      // Seed from each trade's LAST persisted snapshot so the list paints instantly with last-known numbers.
      const seeded: Record<number, LivePnlResponse> = {};
      let latest: string | null = null;
      for (const t of data) {
        const lp = (t.parameters as any)?.last_pnl;
        if (lp) seeded[t.id] = { ...lp, _cached: true } as LivePnlResponse;
        const at = (t.parameters as any)?.last_pnl_at;
        if (at && (!latest || at > latest)) latest = at;
      }
      // Keep any live P&L already fetched in this session; only fill the gaps from snapshots.
      setPnlMap(prev => {
        const next: Record<number, LivePnlResponse> = { ...seeded };
        for (const [id, p] of Object.entries(prev)) if (p && !(p as any)._cached) next[Number(id)] = p;
        return next;
      });
      setLastRefreshAt(prev => prev ?? latest);
    } catch (e: any) {
      setErr(e?.message || 'Failed to load trades');
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { loadActive(); }, [loadActive]);

  const refreshOne = useCallback(async (id: number) => {
    setPnlLoading(p => ({ ...p, [id]: true }));
    try {
      const data = await fetchTradeLivePnl(id, quoteSource);
      setPnlMap(p => ({ ...p, [id]: data }));
      const t = tradesRef.current.find(x => x.id === id);
      const gap = t ? quoteInfo(t, data).status === 'gap' : false;
      // Same snapshot classic persists, plus a flag so the NEXT landing knows this position was unpriced.
      saveTradePnlSnapshot(id, { ...trimPnl(data), quote_gap: gap }).catch(() => { /* best effort */ });
      setLastRefreshAt(new Date().toISOString());
    } catch (e: any) {
      const t = tradesRef.current.find(x => x.id === id);
      setErr(`P&L refresh failed for ${t?.ticker ?? id}: ${e?.message || 'error'}`);
    } finally {
      setPnlLoading(p => ({ ...p, [id]: false }));
    }
  }, [quoteSource]);

  const refreshAll = async () => {
    if (refreshingAll) return;
    setRefreshingAll(true);
    try {
      const ids = tradesRef.current.map(t => t.id);
      const BATCH = 4;   // bounded concurrency — don't hammer the quote provider (same as classic)
      for (let i = 0; i < ids.length; i += BATCH) await Promise.all(ids.slice(i, i + BATCH).map(refreshOne));
    } finally { setRefreshingAll(false); }
  };

  // Selecting a position fetches its live detail if all we have is the saved snapshot.
  useEffect(() => {
    if (selectedId == null) return;
    const p = pnlMap[selectedId];
    if ((!p || (p as any)._cached) && !pnlLoading[selectedId] && trades.some(t => t.id === selectedId)) refreshOne(selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, trades.length]);

  // Esc closes the inspector.
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape' && !updateTrade && !closeTrade) setSelectedId(null); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [updateTrade, closeTrade]);

  // ── derived ───────────────────────────────────────────────────────────────
  const states = useMemo(() => {
    const m: Record<number, TradeState> = {};
    for (const t of trades) m[t.id] = deriveTradeState(t, pnlMap[t.id]);
    return m;
  }, [trades, pnlMap]);
  const summary = useMemo(() => summarizeBook(trades, pnlMap, states), [trades, pnlMap, states]);
  const deployed = useMemo(() => trades.reduce((s, t) => s + deployedCapital(t, pnlMap[t.id]), 0), [trades, pnlMap]);
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: trades.length };
    for (const s of STATE_ORDER) c[s] = 0;
    for (const t of trades) c[states[t.id]?.state ?? 'pending']++;
    return c;
  }, [trades, states]);

  const shown = useMemo(() => {
    const list = filter === 'all' ? trades : trades.filter(t => states[t.id]?.state === filter);
    const n = (v: number | null | undefined) => (v == null ? -Infinity : v);
    const cmp: Record<SortKey, (a: SavedStrategyItem, b: SavedStrategyItem) => number> = {
      urgency: (a, b) => urgencyCompare(a, b, states),
      expiry: (a, b) => (states[a.id]?.dte ?? Infinity) - (states[b.id]?.dte ?? Infinity),
      pnl: (a, b) => n(effectivePnl(pnlMap[b.id], false)) - n(effectivePnl(pnlMap[a.id], false)),
      ticker: (a, b) => a.ticker.localeCompare(b.ticker),
    };
    return [...list].sort(cmp[sortBy]);
  }, [trades, states, pnlMap, filter, sortBy]);

  const attention = useMemo(
    () => trades.filter(t => ['act', 'noquote', 'harvest'].includes(states[t.id]?.state))
      .sort((a, b) => urgencyCompare(a, b, states)),
    [trades, states]);

  const selected = selectedId != null ? trades.find(t => t.id === selectedId) ?? null : null;

  const onChanged = useCallback(async (id?: number | null) => {
    if (id != null) setPnlMap(p => { const n = { ...p }; delete n[id]; return n; });
    await loadActive();
    if (id != null) refreshOne(id);
  }, [loadActive, refreshOne]);

  // ── Closed tab (reuses the classic ledger) ────────────────────────────────
  const [closed, setClosed] = useState<{ trades: SavedStrategyItem[]; frozen: ClosedMonthSummary[]; month: string | null } | null>(null);
  const [closedLoading, setClosedLoading] = useState(false);
  const [showRoll, setShowRoll] = useState(false);
  const [closedNonce, setClosedNonce] = useState(0);
  useEffect(() => {
    if (tab !== 'closed') return;
    let alive = true;
    setClosedLoading(true);
    fetchClosedLedger(showRoll)
      .then(led => { if (alive) setClosed({ trades: led.trades, frozen: led.frozen_months, month: led.current_month }); })
      .catch(e => { if (alive) setErr(e?.message || 'Failed to load the closed ledger'); })
      .finally(() => { if (alive) setClosedLoading(false); });
    return () => { alive = false; };
  }, [tab, showRoll, closedNonce]);
  const closedPnl = useMemo(() => {
    const m: Record<number, LivePnlResponse> = {};
    for (const t of closed?.trades ?? []) { const lp = (t.parameters as any)?.last_pnl; if (lp) m[t.id] = { ...lp, _cached: true } as LivePnlResponse; }
    return m;
  }, [closed]);

  // ── render ────────────────────────────────────────────────────────────────
  const groups = useMemo(() => {
    if (!groupByPurpose) return null;
    return PURPOSE_ORDER.map(p => ({ p, rows: shown.filter(t => tradePurpose(t) === p) })).filter(g => g.rows.length > 0);
  }, [shown, groupByPurpose]);

  const tabBtn = (id: Tab, label: string) => (
    <button key={id} onClick={() => setTab(id)}
      className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${tab === id ? 'bg-primary/20 text-primary font-semibold' : 'text-base-content/60 hover:text-base-content hover:bg-white/[0.04]'}`}>{label}</button>
  );

  return (
    <div className="space-y-4">
      {/* tabs + freshness */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-1 rounded-xl bg-base-200/50 p-1">
          {tabBtn('active', `Active${trades.length ? ` ${trades.length}` : ''}`)}
          {tabBtn('closed', 'Closed')}
          {tabBtn('paper', 'Paper')}
        </div>
        {tab === 'active' && (
          <div className="ml-auto flex items-center gap-2 text-xs text-base-content/50">
            {lastRefreshAt && !refreshingAll && <span>Numbers as of {timeAgo(lastRefreshAt)}</span>}
            <select className="select select-xs select-bordered" value={quoteSource} onChange={e => setQuoteSource(e.target.value as 'yfinance' | 'ibkr')} aria-label="Quote source">
              <option value="yfinance">Yahoo</option><option value="ibkr">IBKR</option>
            </select>
            <button className="btn btn-sm btn-outline border-white/15 gap-1.5" onClick={refreshAll} disabled={refreshingAll || loading}>
              {refreshingAll ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              {refreshingAll ? 'Refreshing…' : 'Refresh all'}
            </button>
          </div>
        )}
      </div>

      {err && (
        <div className="alert alert-error text-sm py-2">
          <AlertCircle className="w-4 h-4 shrink-0" /><span className="flex-1">{err}</span>
          <button className="btn btn-ghost btn-xs" onClick={() => setErr(null)} aria-label="Dismiss"><X className="w-3 h-3" /></button>
        </div>
      )}

      {tab === 'paper' && <PaperTraderPanel quoteSource={quoteSource} />}

      {tab === 'closed' && (
        <div className="space-y-2">
          <label className="flex items-center justify-end gap-1.5 text-xs cursor-pointer text-base-content/60 hover:text-base-content/90"
            title="Rolled trades are still live, so their per-roll realized P&L is hidden by default.">
            <input type="checkbox" className="checkbox checkbox-xs" checked={showRoll} onChange={e => setShowRoll(e.target.checked)} />
            Show rolled trades' partial gains
          </label>
          {closedLoading && !closed ? <div className="flex justify-center py-12"><Loader2 className="w-7 h-7 animate-spin text-base-content/20" /></div>
            : closed && (closed.trades.length > 0 || closed.frozen.length > 0)
              ? <ClosedLedger trades={closed.trades} pnlMap={closedPnl} frozenMonths={closed.frozen} currentMonth={closed.month} includeRolls={showRoll} onDeleteTrade={() => setClosedNonce(n => n + 1)} />
              : <div className="text-center py-16 text-base-content/40 text-sm">No closed trades yet.</div>}
        </div>
      )}

      {tab === 'active' && (
        <>
          {/* KPI strip */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
            <Kpi label="Open P&L" value={loading ? '…' : signedMoney(summary.openPnl)} tone={pnlTone(summary.openPnl)}
              sub={summary.excluded > 0 ? `${summary.priced} priced · ${summary.excluded} excluded` : `${summary.priced} positions priced`}
              title="Sum of unrealized P&L over positions with a trustworthy price. Unpriced or still-loading positions are excluded, not counted as a loss." />
            <Kpi label="Deployed" value={fmtMoney(deployed, { compact: true })} sub={`${summary.positions} positions`} />
            <Kpi label="Needs action" value={summary.actCount} tone={summary.actCount > 0 ? 'text-error' : 'text-base-content/60'}
              sub={summary.noQuoteCount > 0 ? `+ ${summary.noQuoteCount} unpriced` : 'tested or losing'} />
            <Kpi label="Harvest" value={summary.harvestCount} tone={summary.harvestCount > 0 ? 'text-info' : 'text-base-content/60'} sub="profit to take" />
            <Kpi label="Expiring ≤ 7d" value={summary.expiringSoon} tone={summary.expiringSoon > 0 ? 'text-warning' : 'text-base-content/60'} sub="decide soon" />
          </div>

          {/* book risk (the classic desk, collapsed by default) */}
          {!loading && trades.length > 0 && (
            <BetaBookRisk quoteSource={quoteSource} onManageTrade={(id) => setSelectedId(id)} />
          )}

          {/* attention lane */}
          {!loading && trades.length > 0 && (
            attention.length > 0 ? (
              <div className="rounded-xl border border-white/[0.07] bg-base-200/30 divide-y divide-white/[0.05]">
                <div className="px-3 py-2 text-xs font-semibold text-base-content/70">Needs a decision <span className="font-normal text-base-content/45">· {attention.length}</span></div>
                {attention.slice(0, 6).map(t => {
                  const st = states[t.id];
                  return (
                    <button key={t.id} onClick={() => setSelectedId(t.id)}
                      className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-white/[0.03] transition-colors">
                      <span className={`h-2 w-2 rounded-full shrink-0 ${STATE_META[st.state].dot}`} />
                      <span className="font-semibold text-sm w-16 shrink-0">{t.ticker}</span>
                      <span className="text-xs text-base-content/50 w-28 shrink-0 truncate hidden sm:block">{tradeStructure(t).label}</span>
                      <span className="text-xs text-base-content/70 flex-1 min-w-0 truncate" title={st.headline}>{st.headline}</span>
                      <span className={`text-xs font-semibold shrink-0 ${STATE_META[st.state].text}`}>{st.state === 'act' ? 'Defend' : st.state === 'harvest' ? 'Take profit' : 'Check'}</span>
                    </button>
                  );
                })}
                {attention.length > 6 && <div className="px-3 py-1.5 text-[11px] text-base-content/45">+ {attention.length - 6} more — use the filters below.</div>}
              </div>
            ) : (
              <div className="rounded-xl border border-success/20 bg-success/[0.05] px-3 py-2 text-xs text-success/90 flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4" /> Nothing needs a decision right now.
                {summary.excluded > 0 && <span className="text-base-content/50">({summary.excluded} position{summary.excluded === 1 ? '' : 's'} still loading — press Refresh all.)</span>}
              </div>
            )
          )}

          {/* controls */}
          <div className="flex items-center gap-2 flex-wrap">
            <div className="flex items-center gap-1 flex-wrap">
              {(['all', 'act', 'noquote', 'watch', 'harvest', 'hold'] as Filter[]).filter(f => f === 'all' || counts[f] > 0 || filter === f).map(f => (
                <button key={f} onClick={() => setFilter(f)}
                  className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${filter === f ? 'border-primary/50 bg-primary/15 text-primary font-semibold' : 'border-white/10 text-base-content/60 hover:border-white/25'}`}>
                  {f === 'all' ? 'All' : STATE_META[f].label} <span className="text-base-content/45">{counts[f]}</span>
                </button>
              ))}
            </div>
            <div className="ml-auto flex items-center gap-2">
              <label className="flex items-center gap-1.5 text-xs text-base-content/60">
                Sort
                <select className="select select-xs select-bordered" value={sortBy} onChange={e => setSortBy(e.target.value as SortKey)}>
                  <option value="urgency">Urgency</option><option value="expiry">Expiry</option><option value="pnl">P&amp;L</option><option value="ticker">Ticker</option>
                </select>
              </label>
              {view === 'table' && (
                <label className="flex items-center gap-1.5 text-xs text-base-content/60 cursor-pointer">
                  <input type="checkbox" className="checkbox checkbox-xs" checked={groupByPurpose} onChange={e => setGroupByPurpose(e.target.checked)} /> Group by purpose
                </label>
              )}
              <div className="flex rounded-lg border border-white/10 overflow-hidden" role="group" aria-label="View">
                <button className={`px-2.5 py-1.5 ${view === 'table' ? 'bg-primary/20 text-primary' : 'text-base-content/55 hover:bg-white/[0.04]'}`} onClick={() => setView('table')} aria-label="Table view" title="Table"><List className="w-4 h-4" /></button>
                <button className={`px-2.5 py-1.5 border-l border-white/10 ${view === 'board' ? 'bg-primary/20 text-primary' : 'text-base-content/55 hover:bg-white/[0.04]'}`} onClick={() => setView('board')} aria-label="Board view" title="Triage board"><LayoutGrid className="w-4 h-4" /></button>
              </div>
            </div>
          </div>

          {/* list + inspector */}
          {loading ? (
            <div className="space-y-2">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="h-12 rounded-lg bg-base-200/40 animate-pulse" />)}</div>
          ) : trades.length === 0 ? (
            <div className="text-center py-16 text-base-content/40">
              <p className="font-medium">No active trades</p>
              <p className="text-xs mt-1">Use Log Trade or Paste Order above to record your first trade.</p>
            </div>
          ) : (
            // Positions | inspector with a draggable divider — widen the inspector while digging into one trade.
            <SplitPane storageKey="finoagent.beta.split.trades" defaultLeft={50}>
              <div className="min-w-0">
                {shown.length === 0 ? <div className="text-center py-10 text-sm text-base-content/45">No positions in this state.</div>
                  : view === 'table'
                    ? <PositionTable groups={groups} rows={shown} states={states} pnlMap={pnlMap} selectedId={selectedId} onSelect={setSelectedId} />
                    : <Board rows={shown} states={states} pnlMap={pnlMap} selectedId={selectedId} onSelect={setSelectedId} />}
              </div>
              {selected && (
                <div className="fixed inset-0 z-50 overflow-y-auto bg-base-100 p-3 lg:static lg:z-auto lg:bg-transparent lg:p-0 lg:sticky lg:top-16 lg:max-h-[calc(100vh-5rem)] lg:overflow-y-auto">
                  <BetaTradeInspector
                    trade={selected} pnl={pnlMap[selected.id]} state={states[selected.id]} quoteSource={quoteSource}
                    loading={!!pnlLoading[selected.id]} onRefresh={() => refreshOne(selected.id)}
                    onClose={() => setSelectedId(null)} onChanged={() => onChanged(selected.id)}
                    onCloseTrade={() => setCloseTrade(selected)} onUpdatePosition={() => setUpdateTrade(selected)}
                  />
                </div>
              )}
            </SplitPane>
          )}
        </>
      )}

      {updateTrade && (
        <UpdatePositionModal open onClose={() => setUpdateTrade(null)} trade={updateTrade}
          onTransactionSaved={() => { const id = updateTrade.id; setUpdateTrade(null); onChanged(id); }}
          onSave={async data => { await appendTradeTransaction(updateTrade.id, data); }} />
      )}
      {closeTrade && (
        <CloseTradeModal trade={closeTrade} pnl={pnlMap[closeTrade.id]} preselectLeg={null}
          onClose={() => setCloseTrade(null)}
          onClosed={() => { const id = closeTrade.id; setCloseTrade(null); setSelectedId(null); onChanged(id); }} />
      )}
    </div>
  );
}

// ── table ────────────────────────────────────────────────────────────────────

function PnlCell({ trade, st, pnl }: { trade: SavedStrategyItem; st: TradeState; pnl?: LivePnlResponse }) {
  if (st.state === 'noquote') return <span className="text-xs text-base-content/45" title={st.headline}>unpriced</span>;
  const v = effectivePnl(pnl, false);
  if (v == null) return <span className="text-base-content/30">—</span>;
  return (
    <div className="text-right leading-tight">
      <div className={`font-semibold tabular-nums ${pnlTone(v)}`}>{signedMoney(v)}</div>
      {pnl?.pnl_pct != null && <div className={`text-[11px] tabular-nums ${pnlTone(v)} opacity-75`}>{fmtPct(pnl.pnl_pct, { signed: true })}</div>}
    </div>
  );
}

function DteCell({ trade, st }: { trade: SavedStrategyItem; st: TradeState }) {
  if (st.dte == null) return <span className="text-base-content/30">—</span>;
  const urgent = st.dte <= 7;
  return (
    <div className="min-w-[56px]">
      <div className={`text-sm tabular-nums ${urgent ? 'text-warning font-semibold' : ''}`}>{st.dte}d</div>
      <div className="h-1 rounded-full bg-base-content/10 mt-1 overflow-hidden" title={`${timeUsedPct(trade, st.dte).toFixed(0)}% of the trade's life used`}>
        <div className={`h-full rounded-full ${urgent ? 'bg-warning' : 'bg-base-content/40'}`} style={{ width: `${timeUsedPct(trade, st.dte)}%` }} />
      </div>
    </div>
  );
}

function PositionTable({ groups, rows, states, pnlMap, selectedId, onSelect }: {
  groups: { p: keyof typeof PURPOSE_META; rows: SavedStrategyItem[] }[] | null;
  rows: SavedStrategyItem[]; states: Record<number, TradeState>; pnlMap: Record<number, LivePnlResponse>;
  selectedId: number | null; onSelect: (id: number) => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const renderRow = (t: SavedStrategyItem) => {
    const st = states[t.id]; const pnl = pnlMap[t.id];
    const sel = t.id === selectedId;
    return (
      <tr key={t.id} id={`beta-trade-${t.id}`} tabIndex={0}
        onClick={() => onSelect(t.id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(t.id); } }}
        className={`cursor-pointer border-t border-white/[0.05] transition-colors outline-none focus-visible:bg-white/[0.05] ${sel ? 'bg-primary/[0.09]' : 'hover:bg-white/[0.03]'}`}>
        <td className="pl-3 pr-2 py-2.5 whitespace-nowrap"><StateChip state={st.state} /></td>
        <td className="px-2 py-2.5 overflow-hidden">
          <div className="flex items-baseline gap-2">
            <span className="font-semibold">{t.ticker}</span>
            <span className="text-xs text-base-content/55 truncate">{tradeStructure(t).label}</span>
            {t.roll && (t.roll.count || 0) > 0 && <span className="text-[11px] text-warning" title="Rolled — one continuing campaign">↻{t.roll.count}</span>}
          </div>
          <div className="text-xs text-base-content/45 truncate" title={st.headline}>{st.headline}</div>
        </td>
        <td className="px-2 py-2.5 whitespace-nowrap"><DteCell trade={t} st={st} /></td>
        <td className="px-2 py-2.5 whitespace-nowrap text-right"><PnlCell trade={t} st={st} pnl={pnl} /></td>
        <td className="pl-2 pr-3 py-2.5 whitespace-nowrap hidden md:table-cell">
          {st.capturedPct != null && st.capturedPct >= 0 && st.state !== 'noquote'
            ? <div className="min-w-[44px]"><div className="text-xs tabular-nums text-base-content/70">{st.capturedPct.toFixed(0)}%</div>
                <div className="h-1 rounded-full bg-base-content/10 mt-1 overflow-hidden"><div className="h-full rounded-full bg-base-content/40" style={{ width: `${Math.max(0, Math.min(100, st.capturedPct))}%` }} /></div></div>
            : <span className="text-base-content/25" title={st.capturedPct != null && st.capturedPct < 0 ? `Under water: ${st.capturedPct.toFixed(0)}% of max profit` : undefined}>—</span>}
        </td>
      </tr>
    );
  };

  return (
    <div className="rounded-xl border border-white/[0.07] bg-base-200/20 overflow-hidden">
      <table className="w-full text-sm table-fixed">
        <colgroup>
          <col style={{ width: 104 }} /><col /><col style={{ width: 84 }} /><col style={{ width: 112 }} /><col className="hidden md:table-column" style={{ width: 76 }} />
        </colgroup>
        <thead>
          <tr className="text-[11px] text-base-content/45 text-left">
            <th className="pl-3 pr-2 py-2 font-normal">State</th><th className="px-2 py-2 font-normal">Position</th>
            <th className="px-2 py-2 font-normal">Expiry</th><th className="px-2 py-2 font-normal text-right">P&amp;L</th>
            <th className="pl-2 pr-3 py-2 font-normal hidden md:table-cell">Kept</th>
          </tr>
        </thead>
        <tbody>
          {groups
            ? groups.map(g => {
                const meta = PURPOSE_META[g.p];
                const priced = g.rows.filter(t => !['noquote', 'pending'].includes(states[t.id]?.state));
                const sum = priced.reduce((s, t) => s + (effectivePnl(pnlMap[t.id], false) ?? 0), 0);
                const open = !collapsed.has(g.p);
                return (
                  <React.Fragment key={g.p}>
                    <tr className="bg-base-200/60 border-t border-white/[0.06] cursor-pointer select-none"
                      onClick={() => setCollapsed(c => { const n = new Set(c); n.has(g.p) ? n.delete(g.p) : n.add(g.p); return n; })}>
                      <td colSpan={5} className="px-3 py-1.5">
                        <div className="flex items-center gap-2 text-xs">
                          <ChevronDown className={`w-3.5 h-3.5 text-base-content/40 transition-transform ${open ? '' : '-rotate-90'}`} />
                          <span className="text-base-content/60">{meta.icon}</span>
                          <span className="font-semibold">{meta.label}</span>
                          <span className="text-base-content/45">{g.rows.length} position{g.rows.length === 1 ? '' : 's'}</span>
                          <span className="ml-auto flex items-center gap-2">
                            {priced.length < g.rows.length && <span className="text-base-content/40">{g.rows.length - priced.length} unpriced</span>}
                            <span className={`font-semibold tabular-nums ${pnlTone(sum)}`}>{signedMoney(sum)}</span>
                          </span>
                        </div>
                      </td>
                    </tr>
                    {open && g.rows.map(renderRow)}
                  </React.Fragment>
                );
              })
            : rows.map(renderRow)}
        </tbody>
      </table>
    </div>
  );
}

// ── board ────────────────────────────────────────────────────────────────────

const LANES: ActionState[] = ['act', 'noquote', 'watch', 'harvest', 'hold'];

function Board({ rows, states, pnlMap, selectedId, onSelect }: {
  rows: SavedStrategyItem[]; states: Record<number, TradeState>; pnlMap: Record<number, LivePnlResponse>;
  selectedId: number | null; onSelect: (id: number) => void;
}) {
  const byLane = (lane: ActionState) => rows.filter(t => (states[t.id]?.state === 'pending' ? 'hold' : states[t.id]?.state) === lane);
  const lanes = LANES.filter(l => byLane(l).length > 0 || ['act', 'watch', 'harvest', 'hold'].includes(l));
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}>
      {lanes.map(lane => {
        const m = STATE_META[lane]; const list = byLane(lane);
        return (
          <div key={lane} className="min-w-0">
            <div className="flex items-center gap-2 mb-2 text-sm font-semibold">
              <span className={`h-2 w-2 rounded-full ${m.dot}`} />{lane === 'act' ? 'Act now' : lane === 'hold' ? 'On track' : m.label}
              <span className="text-xs font-normal text-base-content/45">{list.length}</span>
            </div>
            <div className="space-y-2">
              {list.length === 0 && <div className="rounded-lg border border-dashed border-white/10 px-3 py-4 text-xs text-base-content/35 text-center">Nothing here</div>}
              {list.map(t => {
                const st = states[t.id]; const v = effectivePnl(pnlMap[t.id], false);
                return (
                  <button key={t.id} onClick={() => onSelect(t.id)}
                    className={`w-full text-left rounded-xl border p-3 transition-colors ${t.id === selectedId ? 'border-primary/60 bg-primary/[0.08]' : `${m.border} bg-base-200/40 hover:bg-base-200/70`}`}>
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="font-semibold">{t.ticker}</span>
                      {st.state === 'noquote' ? <span className="text-xs text-base-content/45">unpriced</span>
                        : <span className={`font-semibold tabular-nums text-sm ${pnlTone(v)}`}>{signedMoney(v)}</span>}
                    </div>
                    <div className="text-xs text-base-content/50">{tradeStructure(t).label}{st.dte != null ? ` · ${st.dte}d left` : ''}</div>
                    {st.dte != null && (
                      <div className="h-1 rounded-full bg-base-content/10 mt-2 overflow-hidden">
                        <div className={`h-full rounded-full ${m.bar}`} style={{ width: `${timeUsedPct(t, st.dte)}%` }} />
                      </div>
                    )}
                    <div className="text-xs text-base-content/65 mt-2 leading-snug line-clamp-2" title={st.headline}>{st.headline}</div>
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
