/**
 * PaperTraderPanel — the Paper Trader tab of the Derivative Trades page.
 *
 * A journal of paper trades placed (one click) from the Income Desk. Each row shows cost basis,
 * current market value + P&L, and the desk score WHEN PLACED vs NOW. Expanding a row lays the
 * full Quant Analysis side by side: the snapshot taken at placement, and the same engine's read
 * today (+ the holder Management Analysis) — so the user can see, across many trades, where the
 * system's read held up and where it drifted.
 *
 * Laziness (product requirement): the list renders from cached columns only. All repricing +
 * quant recompute is per-trade, on expand / the row's Refresh / the page-level Refresh all
 * (bounded concurrency) — never one heavy request for the whole book.
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  Loader2, RefreshCw, ChevronDown, ChevronUp, Trash2, Check, AlertCircle,
  Briefcase, X, ArrowRight, Cpu, Layers,
} from 'lucide-react';
import {
  fetchPaperTrades, fetchPaperTrade, refreshPaperTrade, closePaperTrade, deletePaperTrade,
} from '../../api';
import type { PaperTradeListItem, PaperTradeDetail, PaperTradeCurrent } from '../../api';
import { fmtMoney, fmtDate } from '../../lib/tradeFormat';
import { QuantAnalysisSection } from '../DeskReview';
import { LegsTable } from '../DerivativeIncome';
import CollapsibleSection from './CollapsibleSection';
import ManagementAnalysis from './ManagementAnalysis';

function timeAgo(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

const gradeCls = (g?: string | null) => {
  const u = (g || '').toUpperCase();
  if (u.startsWith('A') || u.startsWith('B')) return 'text-success';
  if (u.startsWith('C') || u.startsWith('D')) return 'text-warning';
  if (u.startsWith('F')) return 'text-error';
  return 'text-base-content/60';
};

const pnlCls = (n?: number | null) => (n == null ? 'text-base-content/50' : n >= 0 ? 'text-success' : 'text-error');
const signedMoney = (n?: number | null) => (n == null ? '—' : `${n >= 0 ? '+' : '−'}${fmtMoney(Math.abs(n))}`);
const signedPct = (n?: number | null) => (n == null ? '' : `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`);

// A compact score chip: number colored by grade, with the letter grade beneath.
function ScoreChip({ score, grade, dim }: { score?: number | null; grade?: string | null; dim?: boolean }) {
  if (score == null) return <span className="text-base-content/30">—</span>;
  return (
    <span className={`inline-flex items-baseline gap-1 ${dim ? 'opacity-70' : ''}`}>
      <span className={`font-mono font-semibold ${gradeCls(grade)}`}>{Math.round(score)}</span>
      {grade && <span className="text-[9px] text-base-content/40">{grade}</span>}
    </span>
  );
}

export default function PaperTraderPanel({ quoteSource = 'yfinance' }: { quoteSource?: string }) {
  const [filter, setFilter] = useState<'open' | 'closed' | 'all'>('open');
  const [items, setItems] = useState<PaperTradeListItem[]>([]);
  const [counts, setCounts] = useState<{ open: number; closed: number; total: number }>({ open: 0, closed: 0, total: 0 });
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [detailMap, setDetailMap] = useState<Record<number, PaperTradeDetail>>({});
  const [currentMap, setCurrentMap] = useState<Record<number, PaperTradeCurrent>>({});
  const [busy, setBusy] = useState<Record<number, boolean>>({});           // per-row refresh in-flight
  const [detailLoading, setDetailLoading] = useState<Record<number, boolean>>({});
  const [refreshingAll, setRefreshingAll] = useState(false);
  const [lastRefreshAt, setLastRefreshAt] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await fetchPaperTrades(filter);
      setItems(r.items); setCounts(r.counts);
      let latest: string | null = null;
      for (const it of r.items) if (it.last_eval_at && (!latest || it.last_eval_at > latest)) latest = it.last_eval_at;
      setLastRefreshAt(latest);
    } catch (e: any) { setErr(e?.message || 'Failed to load paper trades'); }
    finally { setLoading(false); }
  }, [filter]);
  useEffect(() => { load(); }, [load]);

  const patchItem = (id: number, patch: Partial<PaperTradeListItem>) =>
    setItems(prev => prev.map(it => (it.id === id ? { ...it, ...patch } : it)));

  const refreshOne = async (id: number) => {
    setBusy(p => ({ ...p, [id]: true }));
    try {
      const cur = await refreshPaperTrade(id, quoteSource);
      setCurrentMap(p => ({ ...p, [id]: cur }));
      patchItem(id, {
        last_pnl: cur.pnl?.unrealized_pnl ?? null,
        current_value: cur.pnl?.current_value ?? null,
        last_spot: cur.pnl?.current_spot ?? null,
        last_desk_score: cur.desk_score ?? null,
        last_algo_grade: cur.algo_grade ?? null,
        last_eval_at: new Date().toISOString(),
      });
      setLastRefreshAt(new Date().toISOString());
    } catch (e: any) { setErr(`Refresh failed for #${id}: ${e?.message || 'error'}`); }
    finally { setBusy(p => ({ ...p, [id]: false })); }
  };

  const loadDetail = async (id: number): Promise<PaperTradeDetail | null> => {
    setDetailLoading(p => ({ ...p, [id]: true }));
    try {
      const d = await fetchPaperTrade(id);
      setDetailMap(p => ({ ...p, [id]: d }));
      if (d.last_eval) setCurrentMap(p => (p[id] ? p : { ...p, [id]: d.last_eval! }));
      return d;
    } catch (e: any) { setErr(`Load failed for #${id}: ${e?.message || 'error'}`); return null; }
    finally { setDetailLoading(p => ({ ...p, [id]: false })); }
  };

  const toggle = async (id: number) => {
    const willOpen = !expanded.has(id);
    setExpanded(prev => { const n = new Set(prev); if (willOpen) n.add(id); else n.delete(id); return n; });
    if (willOpen && !detailMap[id]) {
      const d = await loadDetail(id);
      // Auto-load the CURRENT read on first expand when nothing is cached yet.
      if (d && !d.last_eval && !currentMap[id]) refreshOne(id);
    }
  };

  // Refresh EVERY row (bounded concurrency so we don't hammer the quote provider).
  const refreshAll = async () => {
    if (refreshingAll || !items.length) return;
    setRefreshingAll(true);
    try {
      const ids = items.map(i => i.id);
      const BATCH = 4;
      for (let i = 0; i < ids.length; i += BATCH) await Promise.all(ids.slice(i, i + BATCH).map(refreshOne));
    } finally { setRefreshingAll(false); }
  };

  const doClose = async (id: number) => {
    setBusy(p => ({ ...p, [id]: true }));
    try { await closePaperTrade(id, quoteSource); await load(); }
    catch (e: any) { setErr(`Close failed for #${id}: ${e?.message || 'error'}`); }
    finally { setBusy(p => ({ ...p, [id]: false })); }
  };

  const doDelete = async (id: number) => {
    try {
      await deletePaperTrade(id);
      setItems(prev => prev.filter(i => i.id !== id));
      setExpanded(prev => { const n = new Set(prev); n.delete(id); return n; });
    } catch (e: any) { setErr(`Delete failed for #${id}: ${e?.message || 'error'}`); }
    finally { setConfirmDelete(null); }
  };

  return (
    <div className="space-y-3">
      {/* Toolbar: sub-filter + refresh all */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="tabs tabs-boxed bg-base-200/50 p-0.5">
          {(['open', 'closed', 'all'] as const).map(f => (
            <button key={f} className={`tab tab-xs ${filter === f ? 'tab-active' : ''}`} onClick={() => setFilter(f)}>
              {f === 'open' ? `Open (${counts.open})` : f === 'closed' ? `Closed (${counts.closed})` : `All (${counts.total})`}
            </button>
          ))}
        </div>
        {items.length > 0 && (
          <div className="flex items-center gap-3 text-xs text-base-content/50 flex-wrap">
            <span>{items.length} paper trade{items.length !== 1 ? 's' : ''}</span>
            <span className="opacity-30">·</span>
            <button className="btn btn-ghost btn-xs gap-1 h-6 min-h-0" onClick={refreshAll} disabled={refreshingAll}
              title="Re-price every paper trade and recompute its current quant read">
              {refreshingAll ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
              {refreshingAll ? 'Refreshing…' : 'Refresh all'}
            </button>
            {lastRefreshAt && !refreshingAll && <span className="text-[10px] text-base-content/35">updated {timeAgo(lastRefreshAt)}</span>}
          </div>
        )}
      </div>

      {err && (
        <div className="alert alert-error text-sm py-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span className="flex-1">{err}</span>
          <button className="btn btn-ghost btn-xs" onClick={() => setErr(null)}><X className="w-3 h-3" /></button>
        </div>
      )}

      {loading && (
        <div className="flex justify-center py-12"><Loader2 className="w-7 h-7 animate-spin text-base-content/20" /></div>
      )}

      {!loading && items.length === 0 && (
        <div className="text-center py-16 text-base-content/30">
          <Briefcase className="w-12 h-12 mx-auto mb-3 opacity-20" />
          <p className="font-medium">No {filter === 'all' ? '' : filter} paper trades</p>
          <p className="text-xs mt-1">On the Income Desk, expand an opportunity and hit <b>Trade</b> to place one here.</p>
        </div>
      )}

      {!loading && items.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-white/[0.06]">
          <table className="table table-xs text-xs w-full">
            <thead>
              <tr className="text-[10px] uppercase tracking-wider text-base-content/40">
                <th></th>
                <th>Trade</th>
                <th>Placed</th>
                <th className="text-right">DTE</th>
                <th className="text-right">Cost basis</th>
                <th className="text-right">Current</th>
                <th className="text-right">Unrealized P&amp;L</th>
                <th className="text-center" title="Desk score when placed → now">Score · placed→now</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map(it => {
                const open = expanded.has(it.id);
                const detail = detailMap[it.id];
                const cur = currentMap[it.id];
                const pnl = it.status === 'closed' ? it.close_pnl : it.last_pnl;
                return (
                  <React.Fragment key={it.id}>
                    <tr className="hover:bg-base-200/30 cursor-pointer" onClick={() => toggle(it.id)}>
                      <td>{open ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}</td>
                      <td>
                        <div className="font-semibold">{it.ticker}</div>
                        <div className="text-[10px] text-base-content/50">{it.label || it.structure}</div>
                        {it.status === 'closed' && <span className="badge badge-ghost badge-xs mt-0.5 text-[9px]">closed</span>}
                      </td>
                      <td className="whitespace-nowrap text-base-content/60">{fmtDate(it.created_at)}</td>
                      <td className="text-right font-mono">{it.dte ?? '—'}</td>
                      <td className="text-right font-mono" title="Net credit received at placement">{fmtMoney(it.cost_basis)}</td>
                      <td className="text-right font-mono">{it.current_value != null ? fmtMoney(it.current_value) : <span className="text-base-content/30">—</span>}</td>
                      <td className={`text-right font-mono font-semibold ${pnlCls(pnl)}`}>
                        {pnl != null ? signedMoney(pnl) : <span className="text-base-content/30">tap ▸</span>}
                      </td>
                      <td className="text-center whitespace-nowrap">
                        <ScoreChip score={it.placed_desk_score} grade={it.placed_algo_grade} dim />
                        <ArrowRight className="w-3 h-3 inline mx-1 text-base-content/30" />
                        {it.last_desk_score != null
                          ? <ScoreChip score={it.last_desk_score} grade={it.last_algo_grade} />
                          : <span className="text-base-content/30">—</span>}
                      </td>
                      <td onClick={e => e.stopPropagation()}>
                        <div className="flex items-center gap-1 justify-end">
                          <button className="btn btn-ghost btn-xs px-1" title="Re-price + recompute current quant"
                            onClick={() => refreshOne(it.id)} disabled={busy[it.id]}>
                            {busy[it.id] ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                          </button>
                          {it.status === 'open' && (
                            <button className="btn btn-ghost btn-xs px-1 text-warning/80" title="Bank P&L and archive to Closed"
                              onClick={() => doClose(it.id)} disabled={busy[it.id]}>
                              <Check className="w-3.5 h-3.5" />
                            </button>
                          )}
                          {confirmDelete === it.id ? (
                            <>
                              <button className="btn btn-error btn-xs px-1" onClick={() => doDelete(it.id)} title="Confirm delete"><Check className="w-3.5 h-3.5" /></button>
                              <button className="btn btn-ghost btn-xs px-1" onClick={() => setConfirmDelete(null)} title="Cancel"><X className="w-3.5 h-3.5" /></button>
                            </>
                          ) : (
                            <button className="btn btn-ghost btn-xs px-1 text-base-content/40 hover:text-error"
                              title="Delete paper trade" onClick={() => setConfirmDelete(it.id)}>
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                    {open && (
                      <tr className="bg-secondary/[0.04]">
                        <td colSpan={9} className="!p-0">
                          <div className="m-2 rounded-lg border border-secondary/25 bg-base-100/40 p-3">
                            <ExpandedPaperTrade
                              item={it} detail={detail} current={cur}
                              detailLoading={!!detailLoading[it.id]} refreshing={!!busy[it.id]}
                              onRefresh={() => refreshOne(it.id)}
                            />
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Expanded view: placed-vs-now, side by side ─────────────────────────────────

function DeltaStrip({ item, current }: { item: PaperTradeListItem; current?: PaperTradeCurrent }) {
  const p = current?.pnl;
  const spotFrom = p?.entry_spot ?? item.entry_spot;
  const spotTo = p?.current_spot ?? item.last_spot;
  const scoreFrom = item.placed_desk_score;
  const scoreTo = current?.desk_score ?? item.last_desk_score;
  const dScore = (scoreFrom != null && scoreTo != null) ? Math.round(scoreTo) - Math.round(scoreFrom) : null;
  const pnl = item.status === 'closed' ? item.close_pnl : (p?.unrealized_pnl ?? item.last_pnl);
  const pnlPct = p?.unrealized_pct;
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[11px] rounded-lg bg-base-200/30 px-3 py-2 mb-3">
      <span className="text-base-content/50">Placed <b className="text-base-content/80">{fmtDate(item.created_at)}</b></span>
      {spotFrom != null && spotTo != null && (
        <span className="text-base-content/50">Spot <b className="font-mono text-base-content/80">${spotFrom.toFixed(2)}</b>
          <ArrowRight className="w-3 h-3 inline mx-1 text-base-content/30" />
          <b className="font-mono text-base-content/80">${spotTo.toFixed(2)}</b>
          {p?.spot_change_pct != null && <span className={`ml-1 ${pnlCls(p.spot_change_pct)}`}>{signedPct(p.spot_change_pct)}</span>}
        </span>
      )}
      <span className="text-base-content/50">Desk score
        <b className={`ml-1 font-mono ${gradeCls(item.placed_algo_grade)}`}>{scoreFrom != null ? Math.round(scoreFrom) : '—'}</b>
        <ArrowRight className="w-3 h-3 inline mx-1 text-base-content/30" />
        <b className={`font-mono ${gradeCls(current?.algo_grade ?? item.last_algo_grade)}`}>{scoreTo != null ? Math.round(scoreTo) : '—'}</b>
        {dScore != null && dScore !== 0 && <span className={`ml-1 ${dScore >= 0 ? 'text-success' : 'text-error'}`}>({dScore >= 0 ? '+' : ''}{dScore})</span>}
      </span>
      <span className={`font-semibold ${pnlCls(pnl)}`}>{item.status === 'closed' ? 'Realized' : 'Unrealized'} {signedMoney(pnl)}
        {pnlPct != null && <span className="ml-1 opacity-80">({signedPct(pnlPct)})</span>}</span>
    </div>
  );
}

function ExpandedPaperTrade({ item, detail, current, detailLoading, refreshing, onRefresh }: {
  item: PaperTradeListItem;
  detail?: PaperTradeDetail;
  current?: PaperTradeCurrent;
  detailLoading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const placed = detail?.placed_snapshot;
  // The exact legs of the trade, as placed (strike/type/action + entry bid/ask/mid/greeks).
  const legs = (detail?.legs && detail.legs.length ? detail.legs : placed?.legs) || [];
  return (
    <div>
      <DeltaStrip item={item} current={current} />

      {/* The actual trade legs, as placed. */}
      {legs.length > 0 ? (
        <div className="mb-3">
          <CollapsibleSection title="Trade Legs" accent="base-content"
            icon={<Layers className="w-3 h-3" />} subtitle={`${legs.length} leg${legs.length !== 1 ? 's' : ''} · as placed`} defaultOpen>
            <LegsTable legs={legs} />
          </CollapsibleSection>
        </div>
      ) : detailLoading ? (
        <div className="flex items-center gap-2 text-xs text-base-content/50 py-2 mb-1"><Loader2 className="w-4 h-4 animate-spin" /> Loading trade legs…</div>
      ) : null}

      <div className="grid md:grid-cols-2 gap-3">
        {/* WHEN PLACED — the snapshot taken at placement (never recomputed). */}
        <div>
          <div className="text-[10px] uppercase tracking-wider text-base-content/40 mb-1.5 flex items-center gap-1.5">
            <Briefcase className="w-3 h-3" /> When placed · {fmtDate(item.created_at)}
          </div>
          {detailLoading && !placed ? (
            <div className="flex items-center gap-2 text-xs text-base-content/50 py-3"><Loader2 className="w-4 h-4 animate-spin" /> Loading the placed snapshot…</div>
          ) : placed ? (
            <QuantAnalysisSection t={placed} q={placed.desk_metrics?.quant} defaultOpen title="Quant Analysis · as placed"
              subtitle="the read at placement — frozen" />
          ) : (
            <div className="text-[11px] text-base-content/40 py-2">No placement snapshot stored.</div>
          )}
        </div>

        {/* NOW — the same engine re-run on the exact legs (+ holder Management Analysis). */}
        <div>
          <div className="text-[10px] uppercase tracking-wider text-base-content/40 mb-1.5 flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5"><Cpu className="w-3 h-3" /> Now{current?.pnl?.dte_remaining != null ? ` · ${current.pnl.dte_remaining} DTE left` : ''}</span>
            <button className="btn btn-ghost btn-xs gap-1 h-5 min-h-0 text-[10px]" onClick={onRefresh} disabled={refreshing}>
              {refreshing ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
              {refreshing ? 'Repricing…' : 'Refresh'}
            </button>
          </div>
          {refreshing && !current ? (
            <div className="flex items-center gap-2 text-xs text-base-content/50 py-3"><Loader2 className="w-4 h-4 animate-spin" /> Re-pricing the legs & recomputing the desk read…</div>
          ) : current && current.matched === false ? (
            <div className="text-[11px] text-base-content/50 flex items-start gap-1.5 py-2">
              <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0 text-base-content/40" />
              <span>{current.error || 'Could not re-price this trade from the current chain.'}</span>
            </div>
          ) : current ? (
            <div className="space-y-2">
              {current.management_analysis && <ManagementAnalysis ma={current.management_analysis} qp={current.qp} />}
              {current.opp && <QuantAnalysisSection t={current.opp} q={current.opp?.desk_metrics?.quant} defaultOpen
                title="Quant Analysis · now" subtitle="same engine, re-priced today" />}
            </div>
          ) : (
            <button className="btn btn-outline btn-secondary btn-xs gap-1.5" onClick={onRefresh}>
              <Cpu className="w-3.5 h-3.5" /> Load the current read
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
