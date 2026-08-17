/**
 * BookTailRisk — institutional short-vol / tail-risk desk at the top of My Trades.
 *
 * Income selling is concave (short gamma/vega). This aggregates the OPTION-LAYER
 * greeks + beta-weighted (SPY-equivalent) delta, FULLY REPRICES the book under crash
 * scenarios, runs a fat-tailed 1-month Monte-Carlo VaR/CVaR, flags concentration, and
 * offers a MENU of index (SPX, European) tail hedges ranked by risk-reduction-per-$
 * and Spitznagel CAGR lift — plus a plain-language verdict + actions.
 */
import { useState } from 'react';
import { Loader2, ShieldAlert, AlertTriangle, TrendingDown, Layers, Umbrella, ChevronDown, ChevronUp, Compass, CheckCircle2, Sparkles } from 'lucide-react';
import { fetchBookTailRisk, fetchBookHedgeAdvice } from '../../api';
import type { BookTailRiskResult } from '../../api';
import CollapsibleSection from './CollapsibleSection';

const money = (v: number | null | undefined, d = 0) =>
  v == null ? '—' : `${v < 0 ? '−' : ''}$${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })}`;

const LEVEL: Record<string, { tone: string; bg: string }> = {
  Dangerous: { tone: 'text-error', bg: 'border-error/30 bg-error/[0.06]' },
  Elevated: { tone: 'text-error', bg: 'border-error/25 bg-error/[0.05]' },
  Moderate: { tone: 'text-warning', bg: 'border-warning/25 bg-warning/[0.05]' },
  Contained: { tone: 'text-success', bg: 'border-success/25 bg-success/[0.05]' },
};

export default function BookTailRisk({ quoteSource }: { quoteSource: string }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<BookTailRiskResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [assignOpen, setAssignOpen] = useState(false);   // scenario lab — collapsed by default
  const [advice, setAdvice] = useState<string | null>(null);        // LLM hedging strategy
  const [adviceLoading, setAdviceLoading] = useState(false);
  const [adviceErr, setAdviceErr] = useState<string | null>(null);

  const run = async () => {
    setOpen(true); setLoading(true); setErr(null);
    try {
      const r = await fetchBookTailRisk(quoteSource);
      if (r.error) setErr(r.error); else setData(r);
    } catch (e: any) { setErr(e?.message || 'Failed'); }
    finally { setLoading(false); }
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
      <button onClick={() => (data || loading ? setOpen(o => !o) : run())}
        className="w-full flex items-center gap-2.5 px-4 py-3 hover:bg-warning/[0.06] transition-colors text-left">
        <div className="w-7 h-7 rounded-lg bg-warning/15 flex items-center justify-center text-warning shrink-0"><ShieldAlert className="w-4 h-4" /></div>
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-sm text-warning">Manage Book</div>
          <div className="text-[10px] text-base-content/40">Beta-weighted greeks · two-sided stress · 1-mo CVaR · assignment lab · ranked hedges</div>
        </div>
        {loading ? <Loader2 className="w-4 h-4 animate-spin text-warning" />
          : data ? (open ? <ChevronUp className="w-4 h-4 text-base-content/30" /> : <ChevronDown className="w-4 h-4 text-base-content/30" />)
          : <span className="btn btn-warning btn-xs">Analyze book</span>}
      </button>

      {open && (loading || data || err) && (
        <div className="border-t border-warning/10 p-3 space-y-3">
          {err && <div className="text-xs text-error flex items-center gap-1"><AlertTriangle className="w-3.5 h-3.5" />{err}</div>}
          {loading && !data && <div className="text-xs text-base-content/40 flex items-center gap-2 py-2"><Loader2 className="w-4 h-4 animate-spin" />Repricing the book across crash scenarios and Monte-Carlo tails…</div>}

          {data && (
            <>
              {/* Verdict */}
              {v && (
                <div className={`rounded-xl border p-2.5 ${lv.bg}`}>
                  <div className="flex items-center gap-2">
                    <span className={`text-xs font-bold uppercase tracking-wider ${lv.tone}`}>{v.level}</span>
                    <span className="text-[11px] text-base-content/70">{v.summary}</span>
                  </div>
                </div>
              )}

              {/* Aggregate exposure — greeks (Γ/Vega/Θ), directional (β-Δ), then income (carry) vs tail (CVaR) */}
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
                <Tile label="Net Gamma" value={data.net_gamma?.toFixed(2) ?? '—'} tone={(data.net_gamma ?? 0) < 0 ? 'error' : 'success'} sub={data.short_vol ? 'SHORT vol (concave)' : 'long vol'} />
                <Tile label="Net Vega" value={money(data.net_vega)} tone={(data.net_vega ?? 0) < 0 ? 'error' : 'success'} sub="$/+1 vol-pt" />
                <Tile label="Net Θ / day" value={money(data.net_theta)} tone={(data.net_theta ?? 0) >= 0 ? 'success' : 'error'}
                  sub={data.theta_net_liq_pct != null ? `${data.theta_net_liq_pct >= 0 ? '+' : ''}${data.theta_net_liq_pct}%/day of cap` : 'daily decay'} />
                <Tile label="β-Δ (SPY-equiv)" value={data.beta_delta_spy != null ? `${data.beta_delta_spy >= 0 ? '+' : ''}${data.beta_delta_spy}` : '—'} tone="base" sub={`${money(data.beta_delta_notional)} notl · β ${data.avg_beta}`} />
                <Tile label="Carry · θ/Net-Liq" value={data.carry_yield_pct != null ? `${data.carry_yield_pct}%/yr` : '—'} tone="success" sub="annualized · ~25%/yr healthy" />
                <Tile label="CVaR 95%" value={money(data.cvar_95)} tone="warning"
                  sub={data.cvar_capital_pct != null ? `${data.cvar_capital_pct}% of cap · 1mo` : (data.horizon || 'expected shortfall')} />
              </div>

              {/* Book capital — spell out the "% of cap" denominator so it isn't a mystery */}
              {data.book_capital != null && (
                <div className="text-[10px] text-base-content/50 -mt-1 flex flex-wrap items-baseline gap-x-1.5" title={data.capital_basis}>
                  <span className="text-base-content/40 uppercase tracking-wider text-[9px]">Book capital</span>
                  <b className="text-base-content/75">{money(data.book_capital)}</b>
                  <span className="cursor-help text-base-content/40">— the “% of cap” base: Σ committed capital (short-put strikes ×100 + short-call/other strike notional), the capital put to work. <b>Not</b> your whole account net-liq. Hover for detail.</span>
                </div>
              )}

              {/* Income-vs-tail reality check — how many days of carry one 1-month tail erases */}
              {data.carry_yield_pct != null && (data.cvar_95 ?? 0) > 0 && (data.net_theta ?? 0) > 0 && (
                <div className="text-[10px] text-base-content/50 -mt-1">
                  Reality check · a single 1-month CVaR tail ({money(data.cvar_95)}) erases
                  {' '}<b className="text-warning">{Math.round((data.cvar_95 as number) / (data.net_theta as number))} days</b> of decay income —
                  the concave trade-off you're hedging below.
                </div>
              )}

              {/* Stress scenarios (full reprice) — TWO-SIDED: short gamma loses either way */}
              <div>
                <div className="text-[9px] uppercase tracking-wider text-base-content/40 mb-1 flex items-center gap-1"><TrendingDown className="w-3 h-3" /> Stress scenarios · downside &amp; melt-up · full reprice (β-weighted{data.avg_beta ? ` · book β ${data.avg_beta}` : ''})</div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {data.crash_scenarios?.map(c => {
                    const up = c.move_pct > 0;
                    return (
                    <div key={c.label} className={`rounded-lg border p-2 text-center ${up ? 'border-warning/25 bg-warning/[0.05]' : 'border-error/15 bg-error/[0.04]'}`}>
                      <div className="text-[9px] uppercase text-base-content/40">{c.label}</div>
                      <div className={`text-sm font-bold mt-0.5 ${up ? 'text-warning' : 'text-error'}`}>{money(c.pnl)}</div>
                      {c.pct_of_capital != null && <div className="text-[9px] text-base-content/40">{c.pct_of_capital}% of capital</div>}
                    </div>
                  ); })}
                </div>
                <p className="text-[9px] text-base-content/40 mt-1">Short gamma loses on a big move in <b>either</b> direction — the amber melt-up rows are the upside risk a downside-only crash table hides. Full reprice (not a delta-gamma approximation).</p>
              </div>

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
                    <p className="text-[10px] text-base-content/40">Sends only the computed book numbers (greeks, β, CVaR, crash P&Ls, hedge menu) to the model — it reasons over them for the best whole-book hedge, doing no arithmetic of its own.</p>
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

function Tile({ label, value, sub, tone = 'base' }: { label: string; value: string; sub?: string; tone?: 'error' | 'success' | 'warning' | 'base' }) {
  const c = tone === 'error' ? 'text-error' : tone === 'success' ? 'text-success' : tone === 'warning' ? 'text-warning' : 'text-base-content/80';
  return (
    <div className="rounded-lg bg-base-300/25 p-2 text-center">
      <div className="text-[9px] uppercase tracking-wider text-base-content/40">{label}</div>
      <div className={`text-sm font-bold mt-0.5 ${c}`}>{value}</div>
      {sub && <div className="text-[9px] text-base-content/40 mt-0.5">{sub}</div>}
    </div>
  );
}
