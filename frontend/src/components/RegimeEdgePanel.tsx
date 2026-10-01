import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, ChevronDown, ChevronUp, Loader2, RefreshCw, Eye, Zap, ArrowUpRight, ArrowDownRight } from 'lucide-react';
import { fetchRegimeEdge, analyzeTa } from '../api';
import type { RegimeEdgeData, RegimeEdgeStats, RegimeKey, EdgeVerdict } from '../types';
import IndicatorAIConsole from './IndicatorAIConsole';
import { useLocalState } from './taShared';

const HORIZONS = [5, 10, 20];
const segCls = (a: boolean) => `px-2 py-0.5 rounded-md text-[10px] font-semibold transition ${a ? 'bg-primary/20 text-primary' : 'text-base-content/45 hover:text-base-content'}`;

// verdict → cell tint + text tone (the composite judgment, not raw win rate)
const EDGE_CELL: Record<EdgeVerdict, string> = {
  confirmed: 'bg-success/10 border-success/25',
  negative: 'bg-error/10 border-error/25',
  weak: 'bg-base-200/30 border-white/[0.06]',
  insufficient: 'bg-base-200/15 border-white/[0.04] opacity-55',
};
const EDGE_TONE: Record<EdgeVerdict, string> = {
  confirmed: 'text-success', negative: 'text-error', weak: 'text-base-content/75', insufficient: 'text-base-content/40',
};
const EDGE_LABEL: Record<EdgeVerdict, string> = { confirmed: 'Edge', negative: 'Avoid', weak: 'Thin', insufficient: 'n/a' };

const fmtPct = (w: number | null) => (w == null ? '—' : `${Math.round(w)}%`);
const fmtR = (e: number | null) => (e == null ? '—' : `${e > 0 ? '+' : ''}${e}R`);

function Cell({ stats, verdict, live }: { stats: RegimeEdgeStats | null; verdict: EdgeVerdict; live: boolean }) {
  if (!stats) return (
    <td className="px-1.5 py-1 text-center align-middle">
      <div className="rounded-md border border-white/[0.04] bg-base-200/10 py-1.5 text-[10px] text-base-content/30">—</div>
    </td>
  );
  return (
    <td className="px-1.5 py-1 text-center align-middle">
      <div className={`rounded-md border px-1.5 py-1 ${EDGE_CELL[verdict]} ${live ? 'ring-1 ring-primary/50' : ''}`}>
        <div className={`text-[12px] font-black tabular-nums leading-none ${EDGE_TONE[verdict]}`}>{fmtPct(stats.win_rate)}</div>
        <div className={`text-[10.5px] font-bold tabular-nums leading-tight mt-0.5 ${(stats.expectancy ?? 0) > 0 ? 'text-success/90' : (stats.expectancy ?? 0) < 0 ? 'text-error/90' : 'text-base-content/60'}`}>{fmtR(stats.expectancy)}</div>
        <div className={`text-[8.5px] tabular-nums mt-0.5 ${stats.low_confidence ? 'italic text-warning/70' : 'text-base-content/40'}`}>n={stats.n}{stats.t_stat != null ? ` · t${stats.t_stat}` : ''}</div>
      </div>
    </td>
  );
}

export default function RegimeEdgePanel({ ticker, defaultOpen = false }: { ticker: string; defaultOpen?: boolean }) {
  const [expanded, setExpanded] = useState(defaultOpen);
  const [horizon, setHorizon] = useLocalState<number>('ta:regedge:h', 10);
  const [data, setData] = useState<RegimeEdgeData | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true); setErr(null);
    fetchRegimeEdge(ticker, horizon)
      .then(r => setData(r.regime_edge))
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : 'Failed to load regime edge'))
      .finally(() => setLoading(false));
  }, [ticker, horizon]);

  useEffect(() => { setData(null); setErr(null); }, [ticker]);
  useEffect(() => { if (expanded) load(); }, [expanded, horizon, load]);

  const cur = data?.current_regime;
  const selectionJson = useMemo(() => {
    if (!data) return {};
    return {
      ticker,
      current_regime: data.current_regime,
      barrier: data.barrier, horizon: data.horizon,
      signals: data.signals.map(s => ({
        signal: s.label, direction: s.direction, firing_now: s.firing_now,
        current_regime_edge: s.current_edge, current_regime_stats: s.current,
        by_regime: s.by_regime,
      })),
    };
  }, [ticker, data]);

  return (
    <div className="bg-base-300 rounded-xl overflow-hidden shadow-xl border border-white/[0.05]">
      <button type="button" onClick={() => setExpanded(o => !o)} className="w-full flex items-center justify-between px-4 py-3 bg-gradient-to-r from-base-200/60 to-transparent">
        <div className="flex items-center gap-2 text-left">
          <Activity className="w-4 h-4 text-secondary" />
          <div>
            <h4 className="text-sm font-semibold text-base-content/90">Regime edge — does this signal work in THIS tape?</h4>
            <p className="text-[10px] text-base-content/50">Historical win rate &amp; expectancy for each signal, split by the market regime (Trending · Transitional · Choppy) it fired in — so a breakout that only works when trending is exposed. Deterministic backtest on this name.</p>
          </div>
        </div>
        {expanded ? <ChevronUp className="w-4 h-4 shrink-0" /> : <ChevronDown className="w-4 h-4 shrink-0" />}
      </button>

      {expanded && (
        <div className="p-3 border-t border-white/[0.05] space-y-2">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            {cur && (
              <div className="flex items-center gap-1.5 flex-wrap text-[11px]">
                <span className="text-base-content/50">Current regime</span>
                <span className="badge badge-sm badge-primary font-bold">{cur.label}</span>
                <span className="text-base-content/40 tabular-nums">Hurst {cur.hurst ?? '—'} · ER {cur.efficiency_ratio ?? '—'}</span>
                {cur.absolute_regime && cur.absolute_regime !== cur.regime && (
                  <span className="text-[9px] text-base-content/35">(classifier: {cur.absolute_regime})</span>
                )}
              </div>
            )}
            <div className="flex items-center gap-1">
              <span className="text-[9px] text-base-content/40 uppercase tracking-wide">Hold</span>
              <div className="flex gap-0.5 bg-base-300/60 rounded-lg p-0.5">
                {HORIZONS.map(h => <button key={h} onClick={() => setHorizon(h)} className={segCls(horizon === h)}>{h}b</button>)}
              </div>
              {data && <button className="btn btn-ghost btn-xs" onClick={load} title="Refresh"><RefreshCw className="w-3.5 h-3.5" /></button>}
            </div>
          </div>

          {loading && !data && <div className="flex items-center gap-2 text-sm text-base-content/60 py-10 justify-center"><Loader2 className="w-4 h-4 animate-spin" /> Backtesting signals across regimes…</div>}
          {err && !loading && <div className="alert alert-error text-xs flex items-center justify-between"><span>{err}</span><button className="btn btn-ghost btn-xs" onClick={load}><RefreshCw className="w-3.5 h-3.5" /> Retry</button></div>}

          {data && cur && (
            <>
              <div className="overflow-x-auto rounded-lg border border-white/[0.06] bg-base-200/20">
                <table className="w-full border-collapse text-left min-w-[520px]">
                  <thead>
                    <tr className="text-[9px] uppercase tracking-wide text-base-content/45">
                      <th className="px-2 py-1.5 font-semibold">Signal</th>
                      {data.regimes.map(rg => (
                        <th key={rg} className={`px-1.5 py-1.5 font-semibold text-center ${rg === cur.regime ? 'text-primary' : ''}`}>
                          {data.regime_labels[rg]}
                          {rg === cur.regime && <span className="ml-1 inline-block w-1.5 h-1.5 rounded-full bg-primary align-middle" title="current regime" />}
                        </th>
                      ))}
                      <th className="px-1.5 py-1.5 font-semibold text-center text-base-content/35">All</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.signals.map(s => (
                      <tr key={s.key} className="border-t border-white/[0.04]">
                        <td className="px-2 py-1 align-middle">
                          <div className="flex items-center gap-1 text-[11.5px] font-semibold text-base-content/85">
                            {s.direction === 'long' ? <ArrowUpRight className="w-3 h-3 text-success/70" /> : <ArrowDownRight className="w-3 h-3 text-error/70" />}
                            {s.label}
                            {s.firing_now && (
                              <span className={`badge badge-xs gap-0.5 ${s.current_edge === 'confirmed' ? 'badge-success' : s.current_edge === 'negative' ? 'badge-error' : 'badge-ghost'}`} title="firing on the latest bar">
                                <Zap className="w-2.5 h-2.5" /> now
                              </span>
                            )}
                          </div>
                          <div className="text-[9px] text-base-content/40 leading-tight mt-0.5 max-w-[210px]">{s.thesis}</div>
                        </td>
                        {data.regimes.map(rg => (
                          <Cell key={rg} stats={s.by_regime[rg]} verdict={s.edge_by_regime[rg]} live={rg === cur.regime} />
                        ))}
                        <td className="px-1.5 py-1 text-center align-middle">
                          {s.overall ? (
                            <div className="text-[10.5px] tabular-nums text-base-content/55 leading-tight">
                              <div className="font-bold">{fmtPct(s.overall.win_rate)}</div>
                              <div>{fmtR(s.overall.expectancy)}</div>
                              <div className="text-[8.5px] text-base-content/35">n={s.overall.n}</div>
                            </div>
                          ) : <span className="text-base-content/25 text-[10px]">—</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="flex items-center gap-x-3 gap-y-0.5 flex-wrap text-[9px] text-base-content/40">
                <span>Cell: <b className="text-base-content/60">win% · expectancy(R) · n·t-stat</b></span>
                <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-success/25 border border-success/40 inline-block" /> edge confirmed</span>
                <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-error/25 border border-error/40 inline-block" /> loses here</span>
                <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm ring-1 ring-primary/50 inline-block" /> current regime</span>
                <span>R = multiple of ATR risk · {data.barrier.target_atr}R target / {data.barrier.stop_atr}R stop · {data.horizon}-bar hold</span>
              </div>

              {!!data.read?.length && (
                <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2.5">
                  <div className="text-[10px] uppercase tracking-wider text-base-content/45 mb-1 flex items-center gap-1"><Eye className="w-3 h-3" /> What the regime says about your signals</div>
                  <ul className="list-disc pl-4 text-[11px] text-base-content/70 space-y-0.5 leading-snug">
                    {data.read.map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                </div>
              )}

              {data.meta?.note && <p className="text-[9px] text-base-content/35 leading-snug">{data.meta.note}</p>}

              <IndicatorAIConsole
                selectionJson={selectionJson}
                chips={[{ key: 'regedge', label: `Regime edge · ${cur.label}`, tone: 'text-secondary' }]}
                analyzeFn={(sel, msgs) => analyzeTa(ticker, sel, msgs)}
                emptyHint="Ask the AI which signals to trust in the current regime."
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}
