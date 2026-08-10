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
import { Loader2, ShieldAlert, AlertTriangle, TrendingDown, Layers, Umbrella, ChevronDown, ChevronUp, Compass, CheckCircle2 } from 'lucide-react';
import { fetchBookTailRisk } from '../../api';
import type { BookTailRiskResult } from '../../api';

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

  const run = async () => {
    setOpen(true); setLoading(true); setErr(null);
    try {
      const r = await fetchBookTailRisk(quoteSource);
      if (r.error) setErr(r.error); else setData(r);
    } catch (e: any) { setErr(e?.message || 'Failed'); }
    finally { setLoading(false); }
  };

  const v = data?.verdict;
  const lv = v ? (LEVEL[v.level] || LEVEL.Moderate) : LEVEL.Moderate;

  return (
    <div className="rounded-2xl border border-warning/20 bg-warning/[0.03] overflow-hidden">
      <button onClick={() => (data || loading ? setOpen(o => !o) : run())}
        className="w-full flex items-center gap-2.5 px-4 py-3 hover:bg-warning/[0.06] transition-colors text-left">
        <div className="w-7 h-7 rounded-lg bg-warning/15 flex items-center justify-center text-warning shrink-0"><ShieldAlert className="w-4 h-4" /></div>
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-sm text-warning">Book Tail Risk · short-vol desk</div>
          <div className="text-[10px] text-base-content/40">Beta-weighted greeks · full-reprice crash · 1-mo CVaR · ranked index hedges</div>
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

              {/* Income-vs-tail reality check — how many days of carry one 1-month tail erases */}
              {data.carry_yield_pct != null && (data.cvar_95 ?? 0) > 0 && (data.net_theta ?? 0) > 0 && (
                <div className="text-[10px] text-base-content/50 -mt-1">
                  Reality check · a single 1-month CVaR tail ({money(data.cvar_95)}) erases
                  {' '}<b className="text-warning">{Math.round((data.cvar_95 as number) / (data.net_theta as number))} days</b> of decay income —
                  the concave trade-off you're hedging below.
                </div>
              )}

              {/* Crash scenarios (full reprice) */}
              <div>
                <div className="text-[9px] uppercase tracking-wider text-base-content/40 mb-1 flex items-center gap-1"><TrendingDown className="w-3 h-3" /> Crash scenarios · full reprice (β-weighted{data.avg_beta ? ` · book β ${data.avg_beta}` : ''})</div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {data.crash_scenarios?.map(c => (
                    <div key={c.label} className="rounded-lg border border-error/15 bg-error/[0.04] p-2 text-center">
                      <div className="text-[9px] uppercase text-base-content/40">{c.label}</div>
                      <div className="text-sm font-bold text-error mt-0.5">{money(c.pnl)}</div>
                      {c.pct_of_capital != null && <div className="text-[9px] text-base-content/40">{c.pct_of_capital}% of capital</div>}
                    </div>
                  ))}
                </div>
              </div>

              {/* Concentration */}
              {(data.concentration?.length ?? 0) > 0 && (
                <div>
                  <div className="text-[9px] uppercase tracking-wider text-base-content/40 mb-1 flex items-center gap-1"><Layers className="w-3 h-3" /> Concentration · by underlying</div>
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
                </div>
              )}

              {/* Hedge menu — ranked alternatives: SPX puts (linear) + VIX calls (convex black-swan) */}
              {(data.hedge_menu?.length ?? 0) > 0 && (
                <div className="rounded-xl border border-info/20 bg-info/[0.03] p-2.5 space-y-2">
                  <div className="text-[10px] uppercase tracking-wider text-info/80 font-semibold flex items-center gap-1">
                    <Umbrella className="w-3.5 h-3.5" /> Tail-hedge menu · SPX puts + VIX black-swan
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

              {/* Actions */}
              {(v?.actions?.length ?? 0) > 0 && (
                <div className="rounded-xl border border-white/10 p-2.5">
                  <div className="text-[9px] uppercase tracking-wider text-base-content/40 mb-1 flex items-center gap-1"><Compass className="w-3 h-3" /> What to do</div>
                  <ul className="space-y-1">
                    {v!.actions.map((a, i) => <li key={i} className="text-[11px] text-base-content/80 flex items-start gap-1.5"><span className="text-secondary mt-0.5">›</span>{a}</li>)}
                  </ul>
                </div>
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
