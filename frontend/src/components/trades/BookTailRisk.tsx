/**
 * BookTailRisk — institutional short-vol / tail-risk desk at the top of My Trades.
 *
 * Income selling is concave (short gamma/vega). This aggregates the OPTION-LAYER
 * greeks + beta-weighted (SPY-equivalent) delta, FULLY REPRICES the book under crash
 * scenarios, runs a fat-tailed 1-month Monte-Carlo VaR/CVaR, flags concentration, and
 * offers a MENU of index (SPX, European) tail hedges ranked by risk-reduction-per-$
 * and Spitznagel CAGR lift — plus a plain-language verdict + actions.
 */
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, ShieldAlert, AlertTriangle, TrendingDown, Layers, Umbrella, ChevronDown, ChevronUp, Compass, CheckCircle2, Sparkles, RefreshCw, Grid3x3, Wrench, ArrowRight } from 'lucide-react';
import { fetchBookTailRisk, fetchBookHedgeAdvice } from '../../api';
import type { BookTailRiskResult } from '../../api';
import CollapsibleSection from './CollapsibleSection';

const money = (v: number | null | undefined, d = 0) =>
  v == null ? '—' : `${v < 0 ? '−' : ''}$${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })}`;

const ago = (iso?: string) => {
  if (!iso) return '';
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  return s < 60 ? 'just now' : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : `${Math.floor(s / 86400)}d ago`;
};

const LEVEL: Record<string, { tone: string; bg: string }> = {
  Dangerous: { tone: 'text-error', bg: 'border-error/30 bg-error/[0.06]' },
  Elevated: { tone: 'text-error', bg: 'border-error/25 bg-error/[0.05]' },
  Moderate: { tone: 'text-warning', bg: 'border-warning/25 bg-warning/[0.05]' },
  Contained: { tone: 'text-success', bg: 'border-success/25 bg-success/[0.05]' },
};

export default function BookTailRisk({ quoteSource }: { quoteSource: string }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);      // initial STORED-snapshot load
  const [refreshing, setRefreshing] = useState(false); // user-triggered live recompute
  const [data, setData] = useState<BookTailRiskResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [assignOpen, setAssignOpen] = useState(false);   // scenario lab — collapsed by default
  const [advice, setAdvice] = useState<string | null>(null);        // LLM hedging strategy
  const [adviceLoading, setAdviceLoading] = useState(false);
  const [adviceErr, setAdviceErr] = useState<string | null>(null);

  // On mount: load the STORED snapshot (no recompute) so the panel shows last-known numbers.
  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const r = await fetchBookTailRisk(quoteSource, false);
        if (alive && r.stored && !r.error) setData(r);
      } catch { /* no stored snapshot — user can Analyze */ }
      finally { if (alive) setLoading(false); }
    })();
    return () => { alive = false; };
  }, [quoteSource]);

  // Refresh = recompute from live quotes AND overwrite the stored snapshot in the DB.
  const refresh = async () => {
    setRefreshing(true); setErr(null); setOpen(true);
    try {
      const r = await fetchBookTailRisk(quoteSource, true);
      if (r.error) setErr(r.error); else { setData(r); setAdvice(null); }
    } catch (e: any) { setErr(e?.message || 'Failed'); }
    finally { setRefreshing(false); }
  };

  const askLlm = async () => {
    if (!data) return;
    setAdviceLoading(true); setAdviceErr(null);
    try {
      const r = await fetchBookHedgeAdvice(quoteSource);
      if (r.error) setAdviceErr(r.error); else setAdvice(r.advice || null);
    } catch (e: any) { setAdviceErr(e?.message || 'Failed to get hedging strategy'); }
    finally { setAdviceLoading(false); }
  };

  const v = data?.verdict;
  const lv = v ? (LEVEL[v.level] || LEVEL.Moderate) : LEVEL.Moderate;

  return (
    <div className="rounded-2xl border border-warning/20 bg-warning/[0.03] overflow-hidden">
      <div className="w-full flex items-center gap-2.5 px-4 py-3 hover:bg-warning/[0.06] transition-colors">
        <button onClick={() => (data ? setOpen(o => !o) : refresh())}
          className="flex items-center gap-2.5 flex-1 min-w-0 text-left">
          <div className="w-7 h-7 rounded-lg bg-warning/15 flex items-center justify-center text-warning shrink-0"><ShieldAlert className="w-4 h-4" /></div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="font-semibold text-sm text-warning">Manage Book</span>
              {data?.risk_scorecard && (() => {
                const s = data.risk_scorecard!;
                const cls = s.grade === 'At risk' ? 'badge-error' : s.grade === 'Watch' ? 'badge-warning' : 'badge-success';
                return (
                  <span className={`badge badge-xs ${cls} gap-1`}>
                    {s.grade}{s.n_breach > 0 ? ` · ${s.n_breach} breach${s.n_breach !== 1 ? 'es' : ''}` : s.n_warn > 0 ? ` · ${s.n_warn} watch` : ''}
                  </span>
                );
              })()}
            </div>
            <div className="text-[10px] text-base-content/40 truncate">
              {data?.computed_at
                ? `Updated ${ago(data.computed_at)} · click to ${open ? 'collapse' : 'view'}`
                : 'Institutional guardrails · two-sided stress · 1-mo CVaR · factor & correlation · cheapest fix per breach'}
            </div>
          </div>
        </button>
        {(loading && !data) ? <Loader2 className="w-4 h-4 animate-spin text-warning shrink-0" />
          : data ? (
            <>
              <button className="btn btn-ghost btn-xs gap-1 shrink-0" onClick={refresh} disabled={refreshing}
                title="Recompute from live quotes and save the latest to the DB">
                {refreshing ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                {refreshing ? 'Refreshing…' : 'Refresh'}
              </button>
              <button onClick={() => setOpen(o => !o)} className="shrink-0">
                {open ? <ChevronUp className="w-4 h-4 text-base-content/30" /> : <ChevronDown className="w-4 h-4 text-base-content/30" />}
              </button>
            </>
          ) : refreshing ? <Loader2 className="w-4 h-4 animate-spin text-warning shrink-0" />
          : <button className="btn btn-warning btn-xs shrink-0" onClick={refresh}>Analyze book</button>}
      </div>

      {open && (refreshing || data || err) && (
        <div className="border-t border-warning/10 p-3 space-y-3">
          {err && <div className="text-xs text-error flex items-center gap-1"><AlertTriangle className="w-3.5 h-3.5" />{err}</div>}
          {refreshing && !data && <div className="text-xs text-base-content/40 flex items-center gap-2 py-2"><Loader2 className="w-4 h-4 animate-spin" />Repricing the book across crash scenarios and Monte-Carlo tails…</div>}

          {data && (
            <>
              {/* Institutional risk scorecard — the lead intelligence: guardrails · your value vs limit · cheapest fix. */}
              {data.risk_scorecard && <RiskScorecard sc={data.risk_scorecard} />}
              {/* one-line plain read of the two-sided tail (terse) */}
              {v && <div className="text-[11px] text-base-content/55 -mt-1">{v.summary}</div>}

              {/* Book vitals — the current metrics in one tight strip (replaces the scattered tiles). */}
              <div className="flex flex-wrap gap-px rounded-lg overflow-hidden border border-white/[0.06]">
                <Vital label="Capital" value={money(data.book_capital)} title={data.capital_basis} />
                <Vital label="Carry" value={data.carry_yield_pct != null ? `${data.carry_yield_pct}%/yr` : '—'} tone="success" sub="θ yield" />
                <Vital label="Net θ / day" value={money(data.net_theta)} tone={(data.net_theta ?? 0) >= 0 ? 'success' : 'error'}
                  sub={data.theta_net_liq_pct != null ? `${data.theta_net_liq_pct}%/day` : undefined} />
                <Vital label="CVaR · 1mo" value={money(data.cvar_95)} tone="warning" sub={data.cvar_capital_pct != null ? `${data.cvar_capital_pct}% cap` : undefined} />
                <Vital label="β-Δ" value={data.beta_delta_spy != null ? `${data.beta_delta_spy >= 0 ? '+' : ''}${data.beta_delta_spy} SPY` : '—'} sub={data.avg_beta != null ? `β ${data.avg_beta}` : undefined} />
                <Vital label="Net Vega" value={money(data.net_vega)} tone={(data.net_vega ?? 0) < 0 ? 'error' : 'success'} sub="/ vol-pt" />
                <Vital label="Net Γ" value={data.net_gamma?.toFixed(2) ?? '—'} tone={(data.net_gamma ?? 0) < 0 ? 'error' : 'success'} sub={data.short_vol ? 'short vol' : 'long vol'} />
              </div>

              {/* Risk breakdown — the supporting evidence, tucked into one collapsible so the cockpit leads. */}
              <CollapsibleSection title="Risk breakdown · stress · by-name · factor" accent="warning" icon={<TrendingDown className="w-3.5 h-3.5" />}>
              <div className="space-y-3">
              {/* Stress scenarios (full reprice) — TWO-SIDED: short gamma loses either way */}
              <div>
                <div className="text-[9px] uppercase tracking-wider text-base-content/40 mb-1 flex items-center gap-1"><TrendingDown className="w-3 h-3" /> Stress scenarios · downside &amp; melt-up · full reprice (β-weighted{data.avg_beta ? ` · book β ${data.avg_beta}` : ''}{(data.stock_notional ?? 0) > 0 ? ' · incl. your shares' : ''})</div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {data.crash_scenarios?.map(c => {
                    const up = c.move_pct > 0;
                    const hasStock = c.stock_pnl != null && Math.abs(c.stock_pnl) >= 1;
                    return (
                    <div key={c.label} className={`rounded-lg border p-2 text-center ${up ? 'border-warning/25 bg-warning/[0.05]' : 'border-error/15 bg-error/[0.04]'}`}>
                      <div className="text-[9px] uppercase text-base-content/40">{c.label}</div>
                      <div className={`text-sm font-bold mt-0.5 ${up ? 'text-warning' : 'text-error'}`}>{money(c.pnl)}</div>
                      {c.pct_of_capital != null && <div className="text-[9px] text-base-content/40">{c.pct_of_capital}% of capital</div>}
                      {hasStock && (
                        <div className="text-[8px] text-base-content/35 mt-0.5" title="How the loss splits between the option overlay and the shares you hold behind it.">
                          {money(c.overlay_pnl)} opt · {money(c.stock_pnl)} shares
                        </div>
                      )}
                    </div>
                  ); })}
                </div>
                <p className="text-[9px] text-base-content/40 mt-1">
                  Full reprice (not a delta-gamma approximation){(data.stock_notional ?? 0) > 0
                    ? <> — and now revalues the <b>{money(data.stock_notional)} of shares</b> you hold behind covered calls/collars, not just the option overlay. That’s why a deeper crash is no longer <i>smaller</i>: the stock loss was previously invisible.</>
                    : <>. Short gamma loses on a big move in <b>either</b> direction — the amber melt-up tiles are the upside risk a downside-only table hides.</>}
                </p>
              </div>

              {/* Who is hurting me — per-name loss waterfall at a −20% month (whole position). */}
              {(data.loss_by_name?.length ?? 0) > 0 && (() => {
                const rows = data.loss_by_name!.filter(r => Math.abs(r.pnl) >= 1);
                const worst = Math.max(1, ...rows.map(r => Math.abs(r.pnl)));
                return (
                  <div>
                    <div className="text-[9px] uppercase tracking-wider text-base-content/40 mb-1 flex items-center gap-1"><Layers className="w-3 h-3" /> Where a −20% month hits — by name (whole position)</div>
                    <div className="space-y-1">
                      {rows.slice(0, 8).map(r => {
                        const loss = r.pnl < 0;
                        const w = Math.round((Math.abs(r.pnl) / worst) * 100);
                        return (
                          <div key={r.ticker} className="flex items-center gap-2 text-[11px]">
                            <span className="w-14 font-semibold shrink-0">{r.ticker}</span>
                            <div className="flex-1 h-3.5 rounded bg-base-300/30 overflow-hidden relative">
                              <div className={`h-full ${loss ? 'bg-error/50' : 'bg-success/50'}`} style={{ width: `${w}%` }} />
                            </div>
                            <span className={`w-20 text-right tabular-nums shrink-0 ${loss ? 'text-error' : 'text-success'}`}>{money(r.pnl)}</span>
                            <span className="w-24 text-right text-[9px] text-base-content/40 shrink-0 tabular-nums" title="Split: option overlay vs the shares behind it.">
                              {Math.abs(r.stock_pnl) >= 1 ? `${money(r.option_pnl)}o·${money(r.stock_pnl)}s` : 'options'}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                    <p className="text-[9px] text-base-content/40 mt-1">Your true single-name crash contributors — the concentration a book-level number hides. <b>o</b> = option overlay, <b>s</b> = shares.</p>
                  </div>
                );
              })()}

              {/* Risk array — spot × vol heatmap (the short-gamma valley + short-vega gradient). */}
              {(data.scenario_grid?.rows?.length ?? 0) > 0 && (
                <ScenarioGrid grid={data.scenario_grid!} capital={data.book_capital} />
              )}

              {/* Factor & correlation — the REAL concentration (deterministic: GICS + measured ρ). */}
              {data.factor_exposure && <FactorExposure fe={data.factor_exposure} />}
              </div>
              </CollapsibleSection>

              {/* Assignment / scenario lab — COLLAPSIBLE, collapsed by default */}
              {(data.assignment_ladder?.length ?? 0) > 0 && (
                <div className="rounded-lg border border-white/10">
                  <button onClick={() => setAssignOpen(o => !o)}
                    className="w-full flex items-center gap-1.5 px-2 py-1.5 text-left hover:bg-white/[0.03] transition-colors">
                    <Compass className="w-3 h-3 text-base-content/40 shrink-0" />
                    <span className="text-[9px] uppercase tracking-wider text-base-content/40">Assignment / scenario lab · capital if assigned</span>
                    {data.naked_assignment && data.naked_assignment.total > 0 && (
                      <span className="ml-auto text-[10px] text-base-content/55 whitespace-nowrap"
                        title={`Worst case if EVERY naked short is assigned at once. Puts ${money(data.naked_assignment.put_capital)} (cash to buy) + naked calls ${money(data.naked_assignment.call_capital)} (delivery notional). Covered calls & spread-protected legs excluded.`}>
                        all-naked-assigned <b className="text-warning">{money(data.naked_assignment.total)}</b>
                      </span>
                    )}
                    {assignOpen ? <ChevronUp className="w-3.5 h-3.5 text-base-content/30 shrink-0" /> : <ChevronDown className="w-3.5 h-3.5 text-base-content/30 shrink-0" />}
                  </button>
                  {assignOpen && (
                    <div className="px-2 pb-2">
                      {/* Headline — worst case if every naked short is assigned (covered excluded) */}
                      {data.naked_assignment && (data.naked_assignment.total > 0) && (
                        <div className="rounded-lg border border-warning/20 bg-warning/[0.04] p-2 mb-2 text-[10px] leading-snug">
                          <div className="flex flex-wrap items-baseline gap-x-2">
                            <span className="uppercase tracking-wider text-[9px] text-warning/80 font-semibold">If every naked short is assigned</span>
                            <b className="text-warning text-sm">{money(data.naked_assignment.total)}</b>
                          </div>
                          <div className="text-base-content/50 mt-0.5">
                            = {money(data.naked_assignment.put_capital)} to buy the {data.naked_assignment.n_naked_puts} naked short put{data.naked_assignment.n_naked_puts !== 1 ? 's' : ''} (strike ×100)
                            {data.naked_assignment.call_capital > 0 && <> + {money(data.naked_assignment.call_capital)} delivery notional on {data.naked_assignment.n_naked_calls} naked short call{data.naked_assignment.n_naked_calls !== 1 ? 's' : ''}</>}.
                            {' '}<span className="text-base-content/40">Covered calls &amp; spread-protected legs excluded. A naked call's true buy-to-cover can exceed its strike notional if the stock has already run (upside is unbounded).</span>
                          </div>
                          {/* Reconcile the three capital figures the user sees */}
                          {data.book_capital != null && (
                            <div className="text-[9px] text-base-content/40 mt-1 pt-1 border-t border-white/[0.06]">
                              This ≈ <b>Book capital {money(data.book_capital)}</b> (whole option book, Σ cash-secured/committed notional) — they differ only by the covered / spread-protected shorts excluded here.
                              The <b>“Deployed”</b> figure on a My Trades group is different by design: it’s that <b>filtered group’s</b> subset AND it’s the <b>Reg-T margin</b> (buying power on hold ≈ 20% of notional for naked shorts), not the full cash-secured notional — so it’s much smaller.
                            </div>
                          )}
                        </div>
                      )}
                      <div className="overflow-x-auto">
                        <table className="table table-xs w-full text-[11px]">
                          <thead><tr className="text-[8px] uppercase text-base-content/30">
                            <th>Market</th><th>Book P&amp;L</th><th title="Cash to take delivery on ITM short puts (strike ×100)">Put assignment $</th><th title="Intrinsic to deliver / buy back ITM short calls">Call cover $</th><th>ITM</th>
                          </tr></thead>
                          <tbody>
                            {data.assignment_ladder!.map((r, i) => {
                              const up = r.move_pct > 0;
                              return (
                              <tr key={i} className={Math.abs(r.move_pct) <= 5 ? 'bg-base-300/20' : ''}>
                                <td className={`font-mono ${up ? 'text-warning/80' : 'text-error/80'}`}>{r.move_pct > 0 ? '+' : ''}{r.move_pct}%</td>
                                <td className={`font-mono ${r.pnl >= 0 ? 'text-success/90' : 'text-error/90'}`}>{money(r.pnl)}</td>
                                <td className="font-mono text-warning/90">{r.put_assignment_capital > 0 ? money(r.put_assignment_capital) : '—'}</td>
                                <td className="font-mono text-warning/90">{r.call_cover_cost > 0 ? money(r.call_cover_cost) : '—'}</td>
                                <td className="text-base-content/50">{[r.puts_itm > 0 ? `${r.puts_itm}P` : '', r.calls_itm > 0 ? `${r.calls_itm}C` : ''].filter(Boolean).join(' ') || '—'}</td>
                              </tr>
                            ); })}
                          </tbody>
                        </table>
                      </div>
                      <p className="text-[9px] text-base-content/40 mt-1"><b>Put assignment</b> = cash to buy the shares you're put (strike ×100); <b>Call cover</b> = intrinsic to deliver/buy-back at that move. Only the binding wing is ITM at a given move — a strangle never demands both at once. Shaded row ≈ spot today.</p>
                    </div>
                  )}
                </div>
              )}

              {/* Concentration — collapsible, collapsed by default */}
              {(data.concentration?.length ?? 0) > 0 && (
                <CollapsibleSection title="Concentration · by underlying" accent="warning" icon={<Layers className="w-3 h-3" />}>
                  <div className="space-y-1">
                    {data.concentration!.map(c => (
                      <div key={c.ticker} className={`rounded-lg border p-2 ${c.flags.length ? 'border-warning/30 bg-warning/[0.05]' : 'border-white/10'}`}>
                        <div className="flex items-center gap-2 text-[11px]">
                          <span className="font-semibold">{c.ticker}</span>
                          {c.beta != null && <span className={`text-[10px] ${c.beta >= 1.3 ? 'text-warning' : 'text-base-content/40'}`}>β {c.beta}</span>}
                          <span className="text-base-content/40">{c.trades} trade{c.trades !== 1 ? 's' : ''} · {c.short_legs} short leg{c.short_legs !== 1 ? 's' : ''}</span>
                          <span className="ml-auto font-mono text-base-content/60">{c.gamma_share_pct}% of book Γ</span>
                        </div>
                        {c.flags.map((f, i) => <div key={i} className="text-[10px] text-warning/90 flex items-start gap-1 mt-1"><AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />{f}</div>)}
                      </div>
                    ))}
                  </div>
                </CollapsibleSection>
              )}

              {/* Hedge menu + What to do — joined in one collapsible, EXPANDED by default */}
              {((data.hedge_menu?.length ?? 0) > 0 || (v?.actions?.length ?? 0) > 0) && (
                <CollapsibleSection title="Tail-hedge menu + what to do" accent="info" icon={<Umbrella className="w-3.5 h-3.5" />} defaultOpen>
                <div className="space-y-2">

                {/* Start-here hero — ONE budget-sized recommendation with the crash number before→after. */}
                {(() => {
                  const menu = data.hedge_menu || [];
                  const rec = menu.find(c => c.recommended) || menu.find(c => c.cost_effective) || menu[0];
                  if (!rec) return null;
                  const before = Math.abs(data.crash_scenarios?.find(s => Math.abs(s.move_pct + 0.2) < 0.001)?.pnl ?? 0);
                  const after = Math.max(0, before - (rec.crash_payoff_20 || 0));
                  const carry = data.annual_income || 0;
                  const budgetPct = carry > 0 ? (rec.annual_bleed / carry) * 100 : null;
                  const lo = rec.long_strike ?? rec.long_put, sh = rec.short_strike ?? rec.short_put;
                  const isVixy = rec.instrument === 'VIXY';
                  const size = isVixy ? `${money(rec.sleeve_capital)} cash sleeve` : `${rec.contracts}× ${rec.instrument ?? 'SPX'} ${lo != null ? `${lo}${sh ? `/${sh}` : ''}` : ''}`.trim();
                  return (
                    <div className="rounded-xl border border-success/25 bg-success/[0.05] p-2.5">
                      <div className="flex items-center gap-1.5 mb-1">
                        <CheckCircle2 className="w-3.5 h-3.5 text-success shrink-0" />
                        <span className="text-[10px] uppercase tracking-wider text-success font-bold">Start here</span>
                        <span className="text-[11px] text-base-content/80 font-semibold">{rec.label}</span>
                        <span className="ml-auto text-[9px] text-base-content/45">{rec.dte_days ? `~${rec.dte_days}d` : 'signal-based'}</span>
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-center">
                        <div className="rounded-lg bg-base-300/25 p-1.5">
                          <div className="text-[8px] uppercase text-base-content/40">Buy</div>
                          <div className="text-[11px] font-semibold mt-0.5">{size}</div>
                        </div>
                        <div className="rounded-lg bg-base-300/25 p-1.5">
                          <div className="text-[8px] uppercase text-base-content/40">Cost</div>
                          <div className="text-[11px] font-semibold mt-0.5 text-warning">{money(rec.annual_bleed)}/yr{isVixy ? '*' : ''}</div>
                          {budgetPct != null && <div className="text-[8px] text-base-content/40">{budgetPct.toFixed(0)}% of carry</div>}
                        </div>
                        <div className="rounded-lg bg-base-300/25 p-1.5">
                          <div className="text-[8px] uppercase text-base-content/40">−20% month</div>
                          <div className="text-[11px] font-semibold mt-0.5"><span className="text-error">{money(-before)}</span> <span className="text-base-content/30">→</span> <span className="text-success">{money(-after)}</span></div>
                          {rec.offsets_pct != null && <div className="text-[8px] text-base-content/40">{rec.offsets_pct}% capped</div>}
                        </div>
                        <div className="rounded-lg bg-base-300/25 p-1.5">
                          <div className="text-[8px] uppercase text-base-content/40">Compounding</div>
                          <div className={`text-[11px] font-semibold mt-0.5 ${rec.cost_effective ? 'text-success' : 'text-base-content/60'}`}>{rec.cagr_lift_pct >= 0 ? '+' : ''}{rec.cagr_lift_pct}% CAGR</div>
                          <div className="text-[8px] text-base-content/40">{rec.cost_effective ? 'pays for itself' : 'pure insurance'}</div>
                        </div>
                      </div>
                      <p className="text-[9px] text-base-content/45 mt-1.5">
                        {rec.cost_effective
                          ? <>This one hedge <b>lifts</b> your compound growth while capping the crash — the rare case where protection is free. Size it once, roll it {data.assumptions?.hedge_rolls_per_year ? `~${data.assumptions.hedge_rolls_per_year}×/yr` : 'quarterly'}.</>
                          : <>Pure insurance: it costs {budgetPct != null ? `${budgetPct.toFixed(0)}% of your carry` : 'some yield'} to cap a −20% month from {money(-before)} to {money(-after)}. Add it only if avoiding that drawdown matters more than the yield — otherwise de-risk the concentrated names above first.</>}
                        {' '}The full menu below trades cost against protection.
                      </p>
                    </div>
                  );
                })()}

                {/* If the MELT-UP is the bigger tail, say so — the downside menu below won't fix it. */}
                {(() => {
                  const cs = data.crash_scenarios || [];
                  const wd = Math.min(0, ...cs.filter(s => s.move_pct < 0).map(s => s.pnl));
                  const wu = cs.filter(s => s.move_pct > 0).reduce<{ pnl: number; label: string } | null>((m, s) => (m == null || s.pnl < m.pnl ? { pnl: s.pnl, label: s.label } : m), null);
                  if (!wu || Math.abs(wu.pnl) <= Math.abs(wd)) return null;
                  return (
                    <div className="rounded-lg border border-warning/30 bg-warning/[0.06] p-2 text-[10px] text-base-content/80 flex items-start gap-1.5">
                      <AlertTriangle className="w-3.5 h-3.5 text-warning mt-0.5 shrink-0" />
                      <span>Your <b>bigger tail is the upside</b> — a {wu.label} loses {money(wu.pnl)} vs {money(wd)} for the worst downside. The hedges below protect the <b>downside</b>; for the squeeze, cap short calls / add call spreads or a small long-call (see “What to do”).</span>
                    </div>
                  );
                })()}

                {(data.hedge_menu?.length ?? 0) > 0 && (
                <div className="rounded-xl border border-info/15 bg-info/[0.02] p-2.5 space-y-2">
                  <div className="text-[10px] uppercase tracking-wider text-info/80 font-semibold flex items-center gap-1">
                    SPX puts + VIX black-swan
                    <span className="ml-auto normal-case text-[9px] text-base-content/40">~{data.hedge_menu![0].dte_days}d</span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="table table-xs w-full text-[11px]">
                      <thead><tr className="text-[8px] uppercase text-base-content/30">
                        <th>Hedge</th><th>Strikes</th><th>×</th><th>Cost/yr</th><th>Offsets −20%</th><th title="Book CVaR-95 reduction">CVaR cut</th><th title="Spitznagel: compound-growth lift">CAGR</th>
                      </tr></thead>
                      <tbody>
                        {data.hedge_menu!.map((c, i) => {
                          const lo = c.long_strike ?? c.long_put;
                          const sh = c.short_strike ?? c.short_put;
                          const badge = c.instrument === 'VIX' ? 'badge-secondary'
                            : c.instrument === 'VIXY' ? 'badge-accent' : 'badge-info';
                          const isVixy = c.instrument === 'VIXY';
                          return (
                          <tr key={i} className={c.recommended ? 'bg-success/[0.06]' : ''}>
                            <td className="whitespace-nowrap" title={isVixy ? c.signal : undefined}>
                              {c.recommended && <CheckCircle2 className="w-3 h-3 text-success inline mr-1" />}
                              <span className={`badge badge-xs mr-1 ${badge} badge-outline`}>{c.instrument ?? 'SPX'}</span>
                              {c.label}
                            </td>
                            <td className="font-mono">{isVixy ? `${money(c.sleeve_capital)} cash` : (lo != null ? `${lo}${sh ? `/${sh}` : ''}` : '—')}</td>
                            <td>{c.contracts ?? (isVixy ? 'sig' : '—')}</td>
                            <td className={isVixy ? 'text-success/90' : 'text-warning/90'}>{money(c.annual_bleed)}{isVixy ? '*' : ''}</td>
                            <td>{c.offsets_pct != null ? `${c.offsets_pct}%` : '—'}</td>
                            <td className="text-success/90">{money(c.cvar_reduction)}</td>
                            <td className={c.cagr_lift_pct >= 0 ? 'text-success font-semibold' : 'text-error'}>{c.cagr_lift_pct >= 0 ? '+' : ''}{c.cagr_lift_pct}%</td>
                          </tr>
                        ); })}
                      </tbody>
                    </table>
                  </div>
                  <p className="text-[9px] text-base-content/40 leading-snug">
                    Ranked by CVaR reduced per $/yr spent, then Spitznagel cost-vs-drag. <b className="text-info/80">SPX puts</b> = linear crash protection.
                    {' '}<b className="text-secondary/80">VIX call spreads</b> = cheap convexity that only pays when vol EXPLODES (a −20% month implies VIX ≈ {data.hedge_menu!.find(c => c.instrument === 'VIX')?.vix_at_minus20 ?? '—'}).
                    {' '}<b className="text-accent/80">Dynamic VIXY</b> = a cash sleeve deployed into VIXY ONLY when the VIX term structure inverts (backwardation),
                    so it avoids permanent roll-decay — <b>*near-zero cost</b>, but it ties up cash and its payoff is haircut ~35% for signal/gap lag.
                    A <b>positive CAGR</b> means capping the crash lifts compound growth; negative = pure insurance.
                  </p>
                </div>
                )}
                {data.hedge_note && <div className="text-[11px] text-base-content/60">{data.hedge_note}</div>}

                {/* What to do — deterministic actions */}
                {(v?.actions?.length ?? 0) > 0 && (
                  <div className="rounded-xl border border-white/10 p-2.5">
                    <div className="text-[9px] uppercase tracking-wider text-base-content/40 mb-1 flex items-center gap-1"><Compass className="w-3 h-3" /> What to do</div>
                    <ul className="space-y-1">
                      {v!.actions.map((a, i) => <li key={i} className="text-[11px] text-base-content/80 flex items-start gap-1.5"><span className="text-secondary mt-0.5">›</span>{a}</li>)}
                    </ul>
                  </div>
                )}

                {/* LLM — best hedging strategy for the WHOLE book (fed only the computed numbers) */}
                <div className="rounded-xl border border-secondary/20 bg-secondary/[0.03] p-2.5 space-y-2">
                  <div className="flex items-center gap-2">
                    <Sparkles className="w-3.5 h-3.5 text-secondary" />
                    <span className="text-[10px] uppercase tracking-wider text-secondary/80 font-semibold">AI hedging strategy · whole book</span>
                    <button className="btn btn-secondary btn-xs gap-1 ml-auto" disabled={adviceLoading} onClick={askLlm}>
                      {adviceLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
                      {adviceLoading ? 'Analyzing…' : advice ? 'Regenerate' : 'Get AI hedge plan'}
                    </button>
                  </div>
                  {adviceErr && <div className="text-[11px] text-error flex items-start gap-1"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />{adviceErr}</div>}
                  {advice && <div className="text-[11px] text-base-content/80 whitespace-pre-wrap leading-relaxed">{advice}</div>}
                  {!advice && !adviceErr && !adviceLoading && (
                    <p className="text-[10px] text-base-content/40">Sends only computed data — greeks, CVaR, crash P&Ls, the per-name loss waterfall, and the <b>deterministic factor read</b> (GICS sectors, measured-ρ correlated clusters, macro loadings) — plus the hedge menu. The model reasons over your real concentration to say what to trim/hedge; it does no arithmetic and can’t invent a correlation the data didn’t measure.</p>
                  )}
                </div>
                </div>
                </CollapsibleSection>
              )}

              <p className="text-[9px] text-base-content/35">
                {data.assumptions?.beta}; {data.assumptions?.tail}; market vol {data.assumptions?.mkt_vol_pct}%; VaR/CVaR over {data.horizon};
                crash prob {data.assumptions?.crash_prob_annual_pct}%/yr; hedge rolled {data.assumptions?.hedge_rolls_per_year}×/yr.
                {' '}θ/Net-Liq &amp; carry use committed capital ({money(data.book_capital)}, Σ cash-secured notional) as the net-liq base, and annualize decay at current pace.
                {' '}Option-layer greeks only. Model estimate — not an order.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}

type ScoreCard = NonNullable<BookTailRiskResult['risk_scorecard']>;
type FixTarget = NonNullable<NonNullable<ScoreCard['checks'][number]['fix']>['targets']>[number];

const GRADE = {
  'At risk': { pill: 'bg-error text-error-content', band: 'bg-error/[0.07]', border: 'border-error/25' },
  Watch: { pill: 'bg-warning text-warning-content', band: 'bg-warning/[0.07]', border: 'border-warning/25' },
  Sound: { pill: 'bg-success text-success-content', band: 'bg-success/[0.07]', border: 'border-success/25' },
} as const;

function RiskScorecard({ sc }: { sc: ScoreCard }) {
  const [showPass, setShowPass] = useState(false);
  const g = GRADE[sc.grade as keyof typeof GRADE] || GRADE.Watch;
  const flagged = sc.checks.filter(c => c.status !== 'pass');
  const passed = sc.checks.filter(c => c.status === 'pass');
  const total = sc.checks.length || 1;
  const pct = (n: number) => `${(n / total) * 100}%`;
  return (
    <div className={`rounded-xl border ${g.border} overflow-hidden bg-base-100/30`}>
      {/* Header band: grade + tallies */}
      <div className={`flex items-center gap-2.5 px-3 py-2 ${g.band}`}>
        <span className={`text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded ${g.pill}`}>{sc.grade}</span>
        <div className="flex items-center gap-2.5 text-[10px] text-base-content/55">
          {sc.n_breach > 0 && <span><b className="text-error">{sc.n_breach}</b> breached</span>}
          {sc.n_warn > 0 && <span><b className="text-warning">{sc.n_warn}</b> watch</span>}
          <span><b className="text-success">{passed.length}</b> ok</span>
        </div>
        <span className="ml-auto text-[8px] uppercase tracking-wider text-base-content/30 hidden sm:block">institutional guardrails</span>
      </div>
      {/* Proportion bar */}
      <div className="flex h-[3px]">
        <div className="bg-error" style={{ width: pct(sc.n_breach) }} />
        <div className="bg-warning" style={{ width: pct(sc.n_warn) }} />
        <div className="bg-success/50" style={{ width: pct(passed.length) }} />
      </div>
      {/* Rows */}
      <div className="p-2 space-y-1.5">
        {flagged.map(c => <ScoreRow key={c.key} c={c} />)}
        {passed.length > 0 && (
          <div className="pt-0.5">
            <button className="w-full text-[10px] text-base-content/40 hover:text-base-content/60 flex items-center gap-1 px-1"
              onClick={() => setShowPass(v => !v)}>
              <CheckCircle2 className="w-3 h-3 text-success/70" /> {passed.length} within limits
              {showPass ? <ChevronUp className="w-3 h-3 ml-auto" /> : <ChevronDown className="w-3 h-3 ml-auto" />}
            </button>
            {showPass && <div className="space-y-1.5 mt-1.5">{passed.map(c => <ScoreRow key={c.key} c={c} />)}</div>}
          </div>
        )}
      </div>
    </div>
  );
}

function ScoreRow({ c }: { c: ScoreCard['checks'][number] }) {
  const [open, setOpen] = useState(c.status === 'breach');   // breaches show their fix by default
  const hasFix = !!c.fix;
  const s = c.status;
  const accent = s === 'breach' ? 'border-error/60' : s === 'warn' ? 'border-warning/60' : 'border-success/40';
  const valTone = s === 'breach' ? 'text-error' : s === 'warn' ? 'text-warning' : 'text-base-content/70';
  return (
    <div className={`rounded-lg bg-base-300/20 border-l-2 ${accent} overflow-hidden`}>
      <div className={`flex items-center gap-2 px-2.5 py-1.5 ${hasFix ? 'cursor-pointer hover:bg-base-300/40 transition-colors' : ''}`}
        onClick={() => hasFix && setOpen(o => !o)} title={c.note}>
        <span className="text-[11px] font-medium flex-1 min-w-0 truncate text-base-content/80">{c.label}</span>
        <div className="text-right shrink-0 leading-tight">
          <div className={`text-[11px] font-semibold tabular-nums ${valTone}`}>{c.value_str}</div>
          <div className="text-[8px] text-base-content/35">limit {c.limit_str}</div>
        </div>
        {hasFix ? (open ? <ChevronUp className="w-3.5 h-3.5 text-base-content/30 shrink-0" /> : <ChevronDown className="w-3.5 h-3.5 text-base-content/30 shrink-0" />)
          : <span className="w-3.5 shrink-0" />}
      </div>
      {open && c.fix && <FixBody fix={c.fix} />}
    </div>
  );
}

function FixBody({ fix }: { fix: NonNullable<ScoreCard['checks'][number]['fix']> }) {
  const targets = fix.targets || [];
  return (
    <div className="px-2.5 pb-2 pt-1 space-y-1.5">
      <div className="text-[10px] leading-snug flex items-start gap-1.5">
        <Wrench className="w-3 h-3 text-success mt-0.5 shrink-0" />
        <span className="text-base-content/80"><b>{fix.headline}</b>{fix.effect && <span className="text-success/80"> — {fix.effect}</span>}</span>
      </div>
      {fix.cost && fix.cost !== 'each target priced below' && (
        <div className="text-[10px] text-base-content/50 ml-[18px]">{fix.cost}</div>
      )}
      {targets.length > 0 && <div className="space-y-1.5">{targets.map((t, i) => <TargetRow key={i} t={t} />)}</div>}
      {fix.alt && <div className="text-[9px] text-base-content/40 ml-[18px]">alt · {fix.alt}</div>}
    </div>
  );
}

function TargetRow({ t }: { t: FixTarget }) {
  const navigate = useNavigate();
  const rec = t.recommended;
  const isCap = rec.action === 'cap';
  const toEvaluate = () => {
    if (!rec.prefill) return;
    // EvaluateLeg carries no qty → expand each leg to one entry per contract (mirror RepairMenu).
    const legs = rec.prefill.legs.flatMap(l =>
      Array(Math.max(1, l.qty)).fill({ action: l.action, type: l.type, strike: l.strike, expiration: l.expiration }));
    try { sessionStorage.setItem('evaluatePrefill', JSON.stringify({ ticker: rec.prefill!.ticker, legs })); } catch { /* ignore */ }
    navigate('/strategies?mode=evaluate');
  };
  return (
    <div className="rounded-lg bg-base-100/50 border border-white/[0.06] px-2 py-1.5">
      <div className="flex items-center gap-1.5">
        <span className="font-semibold text-[11px] text-base-content/85">{t.ticker}</span>
        {t.structure && <span className="text-[9px] text-base-content/40">{t.structure.replace(/_/g, ' ')}</span>}
        <span className="ml-auto text-[9px] text-base-content/45 tabular-nums text-right shrink-0">{money(t.risk)} risk · {money(t.premium_left)} left</span>
      </div>
      <div className="mt-1 flex items-center gap-2">
        <span className={`badge badge-xs uppercase shrink-0 ${isCap ? 'badge-success' : 'badge-warning'}`}>{rec.action}</span>
        <div className="flex-1 min-w-0">
          <div className="text-[10px] text-base-content/85 font-medium truncate">{rec.legs}</div>
          <div className="text-[9px] text-base-content/55 tabular-nums">
            {money(rec.cost)} · <span className="text-error/90">{money(t.tail_before)}</span> → <span className="text-success">{money(rec.tail_after)}</span>
            {rec.premium_kept > 0 && <span className="text-base-content/40"> · keeps {money(rec.premium_kept)}</span>}
          </div>
        </div>
        {isCap && rec.prefill && (
          <button onClick={toEvaluate} title="Open this spread in Evaluate with exact live-chain pricing"
            className="btn btn-ghost btn-xs h-6 min-h-0 px-1.5 text-[9px] gap-0.5 text-info hover:bg-info/10 shrink-0">
            Evaluate <ArrowRight className="w-3 h-3" />
          </button>
        )}
      </div>
      {t.alt && <div className="mt-0.5 text-[9px] text-base-content/35">or {t.alt.action}: {t.alt.legs} · {money(t.alt.cost)}</div>}
    </div>
  );
}

function FactorExposure({ fe }: { fe: NonNullable<BookTailRiskResult['factor_exposure']> }) {
  const clusters = (fe.clusters || []).filter(c => c.tickers.length >= 2);
  const sectors = (fe.sectors || []).filter(s => s.capital > 0).slice(0, 6);
  const macro = fe.macro || [];
  if (!clusters.length && sectors.length <= 1 && !macro.length) return null;
  return (
    <div className="rounded-lg border border-white/10 p-2.5 space-y-2">
      <div className="text-[9px] uppercase tracking-wider text-base-content/40 flex items-center gap-1">
        <Layers className="w-3 h-3" /> Factor &amp; correlation · your real concentration
        <span className="ml-auto normal-case text-[8px] text-base-content/30">measured ρ · GICS — not opinion</span>
      </div>

      {/* Correlated clusters — names that MOVE TOGETHER = one bet, not diversification. */}
      {clusters.length > 0 && (
        <div className="space-y-1">
          {clusters.map((c, i) => (
            <div key={i} className="flex items-center gap-2 text-[11px] rounded bg-warning/[0.06] border border-warning/20 px-2 py-1">
              <AlertTriangle className="w-3 h-3 text-warning shrink-0" />
              <span className="font-semibold">{c.tickers.join(' · ')}</span>
              {c.avg_rho != null && <span className="text-warning/80 text-[10px]">move together ρ {c.avg_rho}</span>}
              <span className="ml-auto text-base-content/50 tabular-nums">{money(c.capital)} at work</span>
            </div>
          ))}
          <p className="text-[9px] text-base-content/40">These names are <b>one bet</b> — a shock to any of them hits the whole cluster at once. That’s the concentration a per-name view hides.</p>
        </div>
      )}

      {/* Sector buckets + macro loadings */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {sectors.length > 1 && (
          <div>
            <div className="text-[8px] uppercase tracking-wider text-base-content/35 mb-1">Capital by sector</div>
            <div className="space-y-0.5">
              {sectors.map(s => (
                <div key={s.sector} className="flex items-center gap-2 text-[10px]">
                  <span className="truncate flex-1">{s.sector}</span>
                  <span className="text-base-content/45">{s.n}</span>
                  <span className="tabular-nums text-base-content/70 w-16 text-right">{money(s.capital)}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        {macro.length > 0 && (
          <div>
            <div className="text-[8px] uppercase tracking-wider text-base-content/35 mb-1">Macro the book loads on (measured ρ)</div>
            <div className="flex flex-wrap gap-1">
              {macro.map(m => (
                <span key={m.factor} className={`badge badge-xs badge-outline ${Math.abs(m.rho) >= 0.6 ? 'badge-warning' : ''}`}
                  title={`Book return vs ${m.label}: measured ρ = ${m.rho}`}>
                  {m.label} {m.rho >= 0 ? '+' : ''}{m.rho}
                </span>
              ))}
            </div>
            <p className="text-[8px] text-base-content/35 mt-1">A move in these factors pushes the whole book one way — that’s your shared driver.</p>
          </div>
        )}
      </div>
    </div>
  );
}

function ScenarioGrid({ grid, capital }: { grid: NonNullable<BookTailRiskResult['scenario_grid']>; capital?: number }) {
  const maxAbs = Math.max(1, ...grid.rows.flatMap(r => r.cells.map(c => Math.abs(c.pnl))));
  const bg = (v: number) => {
    const t = Math.min(1, Math.abs(v) / maxAbs);
    if (v < 0) return `rgba(239,68,68,${0.08 + 0.55 * t})`;   // loss → red
    if (v > 0) return `rgba(34,197,94,${0.08 + 0.5 * t})`;    // gain → green
    return 'transparent';
  };
  return (
    <div>
      <div className="text-[9px] uppercase tracking-wider text-base-content/40 mb-1 flex items-center gap-1">
        <Grid3x3 className="w-3 h-3" /> Risk array · book P&amp;L by spot move × vol shock (full reprice{capital ? ', whole position' : ''})
      </div>
      <div className="overflow-x-auto">
        <table className="text-[10px] border-separate" style={{ borderSpacing: 2 }}>
          <thead>
            <tr>
              <th className="text-left font-normal text-base-content/40 pr-1 whitespace-nowrap">spot ↓ · vol →</th>
              {grid.vol_shocks.map(v => (
                <th key={v} className="font-normal text-base-content/45 px-1 text-center whitespace-nowrap">{v >= 0 ? '+' : ''}{v}v</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.rows.map(row => (
              <tr key={row.move_pct}>
                <td className="pr-1 text-base-content/55 whitespace-nowrap font-medium">{row.move_pct > 0 ? '+' : ''}{row.move_pct}%</td>
                {row.cells.map((c, i) => (
                  <td key={i} className="text-center tabular-nums rounded px-1 py-0.5 whitespace-nowrap"
                    style={{ background: bg(c.pnl), minWidth: 54 }}
                    title={c.pct != null ? `${c.pct}% of book capital` : undefined}>
                    <span className={c.pnl < 0 ? 'text-error' : c.pnl > 0 ? 'text-success' : 'text-base-content/40'}>{money(c.pnl)}</span>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[9px] text-base-content/40 mt-1">Every cell fully reprices the book at that spot × vol corner. Read <b>down a column</b> for gamma (a move either way), <b>across a row</b> for vega (loss as vol rises). The deepest-red cell is your worst corner — size hedges to it.</p>
    </div>
  );
}

function Vital({ label, value, sub, tone = 'base', title }: { label: string; value: string; sub?: string; tone?: 'error' | 'success' | 'warning' | 'base'; title?: string }) {
  const c = tone === 'error' ? 'text-error' : tone === 'success' ? 'text-success' : tone === 'warning' ? 'text-warning' : 'text-base-content/85';
  return (
    <div className="flex-1 min-w-[84px] bg-base-300/25 px-2.5 py-1.5" title={title}>
      <div className="text-[8px] uppercase tracking-wider text-base-content/40 whitespace-nowrap">{label}</div>
      <div className={`text-[12px] font-semibold tabular-nums leading-tight ${c}`}>{value}</div>
      {sub && <div className="text-[8px] text-base-content/35 whitespace-nowrap">{sub}</div>}
    </div>
  );
}

