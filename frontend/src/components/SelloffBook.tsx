import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { Link } from 'react-router-dom';
import { Briefcase, Loader2, Play, RefreshCw, AlertCircle, Info, ChevronDown, ChevronUp } from 'lucide-react';
import { fetchSelloffBook } from '../api';
import type { SelloffBookData, BookHorizon, BookDist } from '../types';

// Phase 3 of the Selloff Monitor: the user's open trades replayed through the page's own scenarios.

const usd = (v: number | null | undefined) => (v == null ? '—' : `${v < 0 ? '−' : '+'}$${Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`);
const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(d)}%`);
const tone = (v: number | null | undefined) => (v == null ? '' : v < 0 ? 'text-error' : 'text-success');
const PURPOSE: Record<string, string> = { income: 'Income', hedge: 'Hedge', trade: 'Trade', managed_floor: 'Floor', other: 'Other', untagged: 'Untagged' };

function quoteSource(): 'yfinance' | 'ibkr' {
  try { return localStorage.getItem('incomeDesk.quoteSource') === 'ibkr' ? 'ibkr' : 'yfinance'; } catch { return 'yfinance'; }
}

function Chip({ label, value, sub, cls }: { label: string; value: React.ReactNode; sub?: React.ReactNode; cls?: string }) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-base-200/30 px-3 py-2 min-w-0">
      <div className="text-[9px] uppercase tracking-wide text-base-content/40 truncate">{label}</div>
      <div className={`text-sm font-bold tabular-nums ${cls || 'text-base-content/85'}`}>{value}</div>
      {sub && <div className="text-[10px] text-base-content/45 truncate">{sub}</div>}
    </div>
  );
}

function DistRow({ label, d, sub }: { label: string; d: BookDist; sub?: string }) {
  return (
    <tr className="border-t border-white/[0.04]">
      <td className="py-1.5 pr-2 text-base-content/70">{label}{sub && <div className="text-[10px] text-base-content/40">{sub}</div>}</td>
      <td className={`py-1.5 pr-2 text-right ${tone(d.p10)}`}>{usd(d.p10)}</td>
      <td className={`py-1.5 pr-2 text-right ${tone(d.p50)}`}>{usd(d.p50)}</td>
      <td className={`py-1.5 pr-2 text-right ${tone(d.p90)}`}>{usd(d.p90)}</td>
      <td className="py-1.5 pr-2 text-right text-base-content/60">{d.p_loss.toFixed(0)}%</td>
      <td className="py-1.5 text-right text-error/80" title={`after ${d.worst_date}`}>{usd(d.worst)}</td>
    </tr>
  );
}

function useCurveOption(h: BookHorizon | undefined, curve: SelloffBookData['curve'], showCurve: boolean) {
  return useMemo(() => {
    if (!h) return null;
    const pts = h.history.points;
    const series: any[] = [{
      name: 'Historical outcomes', type: 'scatter', symbolSize: 7,
      data: pts.map(p => ({ value: [p.mkt_pct, p.pnl], date: p.date, vix: p.vix_ratio,
        itemStyle: { color: p.pnl < 0 ? 'rgba(248,113,113,0.9)' : 'rgba(52,211,153,0.9)' } })),
    }];
    if (showCurve && curve) {
      series.push({ name: `Book at +${curve.h} sessions`, type: 'line', showSymbol: false, smooth: false,
        data: curve.points.map(p => [p.mkt_pct, p.pnl]), lineStyle: { color: '#818cf8', width: 1.6 } });
    }
    for (const o of h.options) {
      series.push({ name: `Options ${o.q.toUpperCase()}`, type: 'scatter', symbol: 'diamond', symbolSize: 11,
        data: [[o.mkt_pct, o.pnl]], itemStyle: { color: '#f59e0b' } });
    }
    return {
      backgroundColor: 'transparent', animation: false,
      grid: { left: 64, right: 16, top: 16, bottom: 40 },
      tooltip: {
        trigger: 'item', backgroundColor: 'rgba(17,24,39,0.95)', borderColor: 'rgba(255,255,255,0.1)', textStyle: { color: '#e5e7eb', fontSize: 11 },
        formatter: (p: any) => {
          const [m, v] = p.value ?? p.data;
          const extra = p.data?.date ? `<br/>after ${p.data.date} · VIX ×${p.data.vix?.toFixed(2)}` : '';
          return `${p.seriesName}<br/>S&P ${pct(m, 2)} → book ${usd(v)}${extra}`;
        },
      },
      xAxis: { type: 'value', name: `S&P move over ${h.h} session${h.h > 1 ? 's' : ''} (%)`, nameLocation: 'middle', nameGap: 24,
        nameTextStyle: { color: '#9ca3af', fontSize: 10 }, axisLabel: { color: '#9ca3af', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } } },
      yAxis: { type: 'value', axisLabel: { color: '#9ca3af', fontSize: 10, formatter: (v: number) => usd(v) },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } } },
      series: [...series, { type: 'line', data: [], markLine: { silent: true, symbol: 'none', lineStyle: { color: 'rgba(255,255,255,0.25)' },
        data: [{ yAxis: 0 }, { xAxis: 0 }] } }],
    };
  }, [h, curve, showCurve]);
}

export default function SelloffBook({ marketSession }: { marketSession?: string }) {
  const [data, setData] = useState<SelloffBookData | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [hSel, setHSel] = useState<number>(5);
  const [withStock, setWithStock] = useState(false);
  const [showAssumptions, setShowAssumptions] = useState(false);
  const started = useRef(false);
  const qs = quoteSource();

  const load = useCallback((refresh: boolean) => {
    setLoading(true); setErr(null);
    fetchSelloffBook(qs, refresh)
      .then(r => { setData(r); if (r.focus_h) setHSel(r.focus_h); })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : 'Failed to run your book'))
      .finally(() => setLoading(false));
  }, [qs]);

  useEffect(() => {                                    // page load: the stored snapshot only (no live quotes)
    if (started.current) return;
    started.current = true;
    load(false);
  }, [load]);

  const h = data?.horizons?.find(x => x.h === hSel);
  const option = useCurveOption(h, data?.curve, hSel === data?.curve?.h);
  const stale = data?.available && marketSession && data.monitor_session && data.monitor_session !== marketSession;

  const runBtn = (
    <button className="btn btn-xs btn-primary btn-outline gap-1.5" onClick={() => load(true)} disabled={loading}>
      {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : data?.available ? <RefreshCw className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
      {data?.available ? 'Re-run with live quotes' : 'Run my open trades through these scenarios'}
    </button>
  );

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-start gap-2">
          <Briefcase className="w-4 h-4 text-primary mt-0.5" />
          <div>
            <h3 className="text-sm font-semibold">Your book under this tape</h3>
            <p className="text-[11px] text-base-content/45 leading-snug">
              Every open option trade replayed through this page's scenarios — the historical outcomes (with the VIX path that followed),
              the options-implied range and the tell levels. Uses live quotes ({qs}), like Manage Book's refresh.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {data?.available && <span className="text-[10px] text-base-content/40">{data.stored ? 'snapshot' : 'computed'} {data.as_of}</span>}
          {runBtn}
        </div>
      </div>

      {err && <div className="alert alert-error text-xs py-2"><AlertCircle className="w-4 h-4" />{err}</div>}
      {!data?.available && !loading && data && (
        <div className="text-xs text-base-content/50">{data.reason || 'Not run yet — this reprices each open trade from live option quotes.'}</div>
      )}
      {loading && !data?.available && (
        <div className="flex items-center gap-2 text-xs text-base-content/50 py-4"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Pricing your open trades and replaying the scenarios…</div>
      )}

      {data?.available && data.exposures && data.horizons && (
        <>
          {stale && <div className="alert alert-warning text-[11px] py-1.5"><AlertCircle className="w-3.5 h-3.5" />Run against the {data.monitor_session} market read — re-run to use the latest session.</div>}
          {data.indicative && <div className="text-[10px] text-warning/80">Quotes are indicative (market closed) — prices are last trades, implied vols re-solved from them.</div>}
          <ul className="space-y-1">
            {(data.read ?? []).map((l, i) => <li key={i} className="text-[12px] text-base-content/80 flex gap-2"><span className="text-primary/60">•</span><span>{l}</span></li>)}
          </ul>
          <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-2">
            <Chip label="β-weighted delta" value={data.exposures.beta_delta_spy != null ? `${data.exposures.beta_delta_spy > 0 ? '+' : ''}${data.exposures.beta_delta_spy.toLocaleString()} SPY` : '—'}
              sub={`${usd(data.exposures.dollar_delta_per_1pct)} per 1% S&P`} />
            <Chip label="Gamma (1% move)" value={usd(data.exposures.gamma_per_1pct_sq)} sub="convexity on a ±1% S&P move" cls={tone(data.exposures.gamma_per_1pct_sq)} />
            <Chip label="Theta" value={`${usd(data.exposures.theta_per_day)}/day`} cls={tone(data.exposures.theta_per_day)} />
            <Chip label="Vega" value={`${usd(data.exposures.vega_per_pt)}/pt`} sub="per 1 vol point" cls={tone(data.exposures.vega_per_pt)} />
            <Chip label="Committed capital" value={usd(data.exposures.capital).replace('+', '')} sub={`${data.exposures.short_legs} short legs`} />
            <Chip label="Trades analysed" value={`${data.coverage?.analysed ?? 0} of ${data.coverage?.requested ?? 0}`}
              sub={data.coverage?.skipped?.length ? `${data.coverage.skipped.length} without option quotes` : 'all open trades'} />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] text-base-content/50">Horizon:</span>
            {data.horizons.map(x => (
              <button key={x.h} onClick={() => setHSel(x.h)} className={`btn btn-xs ${hSel === x.h ? 'btn-primary' : 'btn-ghost'}`}>
                {x.h} session{x.h > 1 ? 's' : ''}
              </button>
            ))}
            {data.has_covered_stock && (
              <label className="ml-auto flex items-center gap-1.5 text-[11px] text-base-content/60 cursor-pointer">
                <input type="checkbox" className="checkbox checkbox-xs" checked={withStock} onChange={e => setWithStock(e.target.checked)} />
                Include losses on shares behind covered calls
              </label>
            )}
          </div>

          {h && (
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              <div className="overflow-x-auto">
                <table className="w-full text-[11px] tabular-nums">
                  <thead>
                    <tr className="text-[9px] uppercase tracking-wide text-base-content/40 text-left">
                      <th className="pb-1 pr-2 font-medium">Scenario set · +{h.h} session{h.h > 1 ? 's' : ''}</th>
                      <th className="pb-1 pr-2 font-medium text-right">Bad (P10)</th><th className="pb-1 pr-2 font-medium text-right">Median</th>
                      <th className="pb-1 pr-2 font-medium text-right">Good (P90)</th><th className="pb-1 pr-2 font-medium text-right">Losing</th>
                      <th className="pb-1 font-medium text-right">Worst</th>
                    </tr>
                  </thead>
                  <tbody>
                    <DistRow label={`History · ${h.history.n} outcomes`} sub="actual S&P move + VIX path"
                      d={withStock && h.history_with_stock ? h.history_with_stock : h.history} />
                  </tbody>
                </table>
                {h.options.length > 0 && (
                  <div className="mt-2">
                    <div className="text-[9px] uppercase tracking-wide text-base-content/40 mb-1">Options-implied S&P range</div>
                    <div className="flex flex-wrap gap-1.5">
                      {h.options.map(o => (
                        <span key={o.q} className="text-[11px] tabular-nums rounded border border-white/[0.06] bg-base-200/30 px-2 py-0.5">
                          {o.q.toUpperCase()} S&P {pct(o.mkt_pct, 2)} → <span className={tone(o.pnl)}>{usd(o.pnl)}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {data.levels && data.levels.filter(l => l.h === h.h).length > 0 && (
                  <div className="mt-2">
                    <div className="text-[9px] uppercase tracking-wide text-base-content/40 mb-1">Tell levels</div>
                    {data.levels.filter(l => l.h === h.h).map(l => (
                      <div key={l.id} className="text-[11px] tabular-nums flex justify-between border-t border-white/[0.04] py-1">
                        <span className="text-base-content/70">{l.label} <span className="text-base-content/40">S&P {pct(l.mkt_pct, 2)}</span></span>
                        <span className={tone(l.pnl)}>{usd(l.pnl)}</span>
                      </div>
                    ))}
                  </div>
                )}
                {h.vol_rule && (
                  <p className="text-[10px] text-base-content/40 mt-2">
                    Vol rule fitted on these analogs: VIX × e^({h.vol_rule.a} {h.vol_rule.b >= 0 ? '+' : '−'} {Math.abs(h.vol_rule.b)}·ln(1+move)), n={h.vol_rule.n}
                    {h.vol_rule.r2 != null && `, R²=${h.vol_rule.r2}`}.
                  </p>
                )}
              </div>
              <div>
                {option && <ReactECharts option={option} style={{ height: 280, width: '100%' }} notMerge lazyUpdate />}
                <p className="text-[10px] text-base-content/40">Dots = each historical outcome (red = book loses). {hSel === data.curve?.h ? 'Line = the book\'s P&L across S&P moves at this horizon. ' : ''}Amber = options-implied P10/P50/P90.</p>
              </div>
            </div>
          )}

          {data.trades && data.trades.length > 0 && (
            <div className="overflow-x-auto">
              <div className="text-[9px] uppercase tracking-wide text-base-content/40 mb-1">
                Trades in the bad case (S&P {pct(data.bad_case_mkt_pct, 1)}) and good case (S&P {pct(data.good_case_mkt_pct, 1)}) over {data.focus_h} sessions — worst first
              </div>
              <table className="w-full text-[11px] tabular-nums">
                <thead>
                  <tr className="text-[9px] uppercase tracking-wide text-base-content/35 text-left">
                    <th className="pb-1 pr-2 font-medium">Trade</th><th className="pb-1 pr-2 font-medium">Folder</th>
                    <th className="pb-1 pr-2 font-medium text-right">β</th><th className="pb-1 pr-2 font-medium text-right">Bad case</th>
                    <th className="pb-1 pr-2 font-medium text-right">Good case</th><th className="pb-1 pr-2 font-medium">Strikes breached (bad)</th>
                    <th className="pb-1 font-medium">Nearest short</th>
                  </tr>
                </thead>
                <tbody>
                  {data.trades.map(t => (
                    <tr key={`${t.trade_id}-${t.ticker}`} className="border-t border-white/[0.04]">
                      <td className="py-1 pr-2 text-base-content/80 whitespace-nowrap">{t.ticker} <span className="text-base-content/40">{t.name}</span></td>
                      <td className="py-1 pr-2 text-base-content/50">{PURPOSE[t.purpose] ?? t.purpose}</td>
                      <td className="py-1 pr-2 text-right text-base-content/60">{t.beta.toFixed(2)}</td>
                      <td className={`py-1 pr-2 text-right ${tone(t.pnl_bad)}`}>{usd(t.pnl_bad)}</td>
                      <td className={`py-1 pr-2 text-right ${tone(t.pnl_good)}`}>{usd(t.pnl_good)}</td>
                      <td className="py-1 pr-2">
                        {t.breached_bad?.length ? t.breached_bad.map(b => <span key={b} className="mr-1 px-1.5 py-0.5 rounded bg-red-500/20 text-error text-[10px]">{b}</span>) : <span className="text-base-content/30">—</span>}
                      </td>
                      <td className="py-1 text-base-content/60 whitespace-nowrap">
                        {t.nearest_short ? (
                          <span className={t.nearest_short.itm ? 'text-error' : (t.nearest_short.z ?? 9) < 1 ? 'text-warning' : ''}>
                            {t.nearest_short.strike}{t.nearest_short.right} · {pct(t.nearest_short.distance_pct, 1)}
                            {t.nearest_short.z != null && <span className="text-base-content/40"> · {t.nearest_short.itm ? 'ITM' : `${t.nearest_short.z.toFixed(1)}σ`}</span>}
                          </span>
                        ) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="flex flex-wrap items-center gap-3 mt-1.5 text-[10px] text-base-content/45">
                <span>σ = a {data.focus_h}-session move at that leg's own implied vol. Amber = inside 1σ.</span>
                <Link to="/my-trades" className="link link-primary">Open My Trades to manage or defend a position →</Link>
              </div>
            </div>
          )}

          {data.coverage?.skipped && data.coverage.skipped.length > 0 && (
            <p className="text-[10px] text-base-content/40">Not included: {data.coverage.skipped.map(s => `${s.ticker ?? '?'} (${s.reason})`).join(', ')}.</p>
          )}
          <button className="btn btn-xs btn-ghost gap-1 px-1" onClick={() => setShowAssumptions(v => !v)}>
            {showAssumptions ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />} How this is computed
          </button>
          {showAssumptions && (
            <ul className="text-[10px] text-base-content/45 space-y-0.5 pl-1">
              {(data.assumptions ?? []).map((a, i) => <li key={i} className="flex gap-1"><Info className="w-3 h-3 shrink-0 mt-px" />{a}</li>)}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
