import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BookmarkPlus, Check, Globe2, Loader2, Play, ShieldCheck, SlidersHorizontal, Target } from 'lucide-react';
import { createBondHolding, fetchBondGapPlan } from '../../api';
import type { BondGapLine, BondGapOutcome, BondGapParams, BondGapPlan as GapPlan, BondGapScenarioKey, BondGapStrategy, BondResilience } from '../../types';
import { ACCOUNTS, AXIS, Card, Chart, ErrorBox, Field, KindBadge, LEGEND, Note, Stat, TOOLTIP, fmtDate, inputCls, pct, selectCls, usd } from './bondUi';

const SC_ORDER: BondGapScenarioKey[] = ['low', 'base', 'high', 'debase'];
const CREDIT_OPTS = [{ value: 'govt', label: 'Government / FDIC only' }, { value: 'AA', label: 'AA or better (adds agencies, munis)' }, { value: 'A', label: 'A or better' }, { value: 'BBB', label: 'BBB or better' }];
const numOrNull = (v: string): number | null => { const x = Number(String(v).replace(/[$,\s]/g, '')); return v === '' || !Number.isFinite(x) ? null : x; };
const tone = (f: number | null | undefined) => (f == null ? '' : f >= 99.5 ? 'text-emerald-400' : f >= 90 ? 'text-amber-300' : 'text-rose-400');

function Cell({ o }: { o: BondGapOutcome | undefined }) {
  if (!o) return <td className="px-2 py-2">—</td>;
  const full = o.unfunded_today_dollars < 500;
  return (
    <td className="px-2 py-2 align-top tabular-nums" title={full ? 'Every goal year covered' : `Short ${o.shortfall_years} year(s), first ${o.first_shortfall}. Closing this gap with TIPS would cost about ${usd(o.tips_to_close)} today.`}>
      <div className={`font-semibold ${tone(o.funded_ratio_pct)}`}>{pct(o.funded_ratio_pct, 1)}</div>
      <div className="text-[10px] text-base-content/50">{full ? 'fully funded' : `${usd(o.unfunded_today_dollars, { compact: true })} short · from ${o.first_shortfall}`}</div>
    </td>
  );
}

function ResilienceRow({ label, before, after, fmt, hint }: { label: string; before: number; after?: number; fmt?: (v: number) => string; hint: string }) {
  const f = fmt ?? ((v: number) => pct(v, 1));
  return (
    <div className="flex items-center justify-between gap-2 py-1 text-[11.5px]" title={hint}>
      <span className="text-base-content/60">{label}</span>
      <span className="tabular-nums"><b>{f(before)}</b>{after != null && Math.abs(after - before) >= 0.05 && <span className="text-emerald-400"> → {f(after)}</span>}</span>
    </div>
  );
}

export function ResilienceCard({ before: rb, after: ra }: { before: BondResilience; after?: BondResilience }) {
  return (
    <Card icon={<Globe2 className="h-4 w-4 text-sky-400" />} title="Diversification & dollar-debasement check"
      subtitle={ra ? 'Your book now → after the selected recommendation' : 'Your book now'}>
      <div className="grid gap-x-6 md:grid-cols-2">
        <div>
          <ResilienceRow label="Inflation-linked (TIPS)" before={rb.inflation_linked_pct} after={ra?.inflation_linked_pct} hint="Keeps pace with CPI — the main protection against a weaker dollar inside a bond book" />
          <ResilienceRow label="Non-dollar bonds" before={rb.non_usd_pct} after={ra?.non_usd_pct} hint="Bonds paid in other currencies gain if the dollar falls (and lose if it rises)" />
          <ResilienceRow label="Short / floating rate" before={rb.floating_or_short_pct} after={ra?.floating_or_short_pct} hint="Reprices quickly when rates rise — helps only if rates keep up with inflation" />
          <ResilienceRow label="In funds" before={rb.funds_pct} after={ra?.funds_pct} hint="Funds never mature: their value moves with rates until you sell" />
          <ResilienceRow label={`Largest position (${rb.largest_position.name})`} before={rb.largest_position.pct} after={ra && ra.largest_position.name === rb.largest_position.name ? ra.largest_position.pct : undefined} hint="Concentration in one holding" />
          {rb.largest_corporate_issuer && <ResilienceRow label={`Largest company (${rb.largest_corporate_issuer.name})`} before={rb.largest_corporate_issuer.pct} hint="One issuer's bonds added together" />}
        </div>
        <div className="space-y-1 pt-1">
          {rb.segments.map(s => (
            <div key={s.key} className="text-[11px]">
              <div className="flex justify-between text-base-content/60"><span>{s.key}</span><span className="tabular-nums">{pct(s.pct, 0)}</span></div>
              <div className="h-1.5 rounded bg-base-300/60"><div className="h-1.5 rounded bg-sky-400/70" style={{ width: `${Math.min(100, s.pct)}%` }} /></div>
            </div>
          ))}
        </div>
      </div>
      {rb.flags.length > 0 && <ul className="mt-3 space-y-1">{rb.flags.map((f, i) => <li key={i} className="text-[11px] leading-snug text-amber-200/80">• {f}</li>)}</ul>}
    </Card>
  );
}

// Purchases sized to the plan's shortfall years, in three flavours (nominal / TIPS / half-half), each re-run
// through the planner in four inflation worlds. Deterministic; the recommended one has the smallest worst case.
export default function BondGapPlan({ onChanged, onGotoPlanner }: { onChanged: () => void; onGotoPlanner: () => void }) {
  const [budget, setBudget] = useState('');
  const [account, setAccount] = useState('taxable');
  const [credit, setCredit] = useState<NonNullable<BondGapParams['min_credit']>>('AA');
  const [low, setLow] = useState('-1');
  const [high, setHigh] = useState('5');
  const [deb, setDeb] = useState('6');
  const [advanced, setAdvanced] = useState(false);
  const [data, setData] = useState<GapPlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [pick, setPick] = useState<BondGapStrategy['key'] | null>(null);
  const [added, setAdded] = useState<Record<string, boolean>>({});
  const [adding, setAdding] = useState(false);
  const req = useRef(0);
  const started = useRef(false);

  const run = useCallback(async () => {
    const id = ++req.current;
    setLoading(true); setErr(null);
    try {
      const d = await fetchBondGapPlan({
        budget: numOrNull(budget), account_type: account, min_credit: credit,
        low_inflation: numOrNull(low) ?? -1, high_inflation: numOrNull(high) ?? 5, debase_inflation: numOrNull(deb) ?? 6,
      });
      if (id === req.current) { setData(d); setPick(d.recommended); setAdded({}); }
    } catch (e) {
      if (id === req.current) setErr(e instanceof Error ? e.message : 'Could not build the plan');
    } finally {
      if (id === req.current) setLoading(false);
    }
  }, [budget, account, credit, low, high, deb]);
  useEffect(() => { if (!started.current) { started.current = true; run(); } }, [run]);

  const strat = data?.strategies.find(s => s.key === pick) ?? data?.strategies[0];
  const scen = useMemo(() => SC_ORDER.map(k => data?.scenarios.find(s => s.key === k)).filter(Boolean) as GapPlan['scenarios'], [data]);
  const lineKey = (l: BondGapLine) => `${l.year}|${l.leg}|${l.label}`;
  const watchable = (strat?.lines ?? []).filter(l => l.security);

  const addToWatch = async (ls: BondGapLine[]) => {
    setAdding(true); setErr(null);
    try {
      for (const l of ls) {
        const s = l.security!;
        await createBondHolding({
          kind: l.kind, status: 'watch', cusip: s.cusip, label: `${l.kind === 'tips' ? 'TIPS' : 'UST'} ${s.coupon_pct}% ${s.maturity} (gap ${l.year})`,
          face_value: l.face, coupon_rate: s.coupon_pct, coupon_freq: s.type === 'bill' ? 0 : 2, maturity_date: s.maturity, account_type: account,
        });
        setAdded(a => ({ ...a, [lineKey(l)]: true }));
      }
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not add to the watchlist');
    } finally { setAdding(false); }
  };

  const chart = useMemo(() => {
    if (!strat) return null;
    const ys = strat.years.filter(y => y.shortfall_before > 0.5 || y.shortfall_after > 0.5 || y.need > 0);
    return {
      tooltip: { ...TOOLTIP, trigger: 'axis', valueFormatter: (v: number) => usd(v) },
      legend: LEGEND, grid: { left: 8, right: 8, top: 30, bottom: 4, containLabel: true },
      xAxis: { type: 'category', data: ys.map(y => String(y.year)), ...AXIS },
      yAxis: { type: 'value', ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => usd(v, { compact: true }) } },
      series: [
        { name: 'Covered today', type: 'bar', stack: 'n', data: ys.map(y => Math.max(0, y.need - y.shortfall_before)), itemStyle: { color: '#334155' } },
        { name: 'Filled by these purchases', type: 'bar', stack: 'n', data: ys.map(y => Math.max(0, y.shortfall_before - y.shortfall_after)), itemStyle: { color: '#34d399' } },
        { name: 'Still short', type: 'bar', stack: 'n', data: ys.map(y => y.shortfall_after), itemStyle: { color: '#f87171' } },
      ],
    };
  }, [strat]);

  const rb: BondResilience | undefined = data?.resilience.before;
  const ra: BondResilience | undefined = data?.resilience.after;

  return (
    <div className="space-y-4">
      <Card icon={<Target className="h-4 w-4 text-primary" />} title="Fund my plan — and stress-test it"
        subtitle="Buys sized to the exact years your plan is short (after your bonds, fund sales, Social Security and taxes), then every option is re-run through your whole plan in four inflation worlds."
        right={<button className="btn btn-primary btn-sm" onClick={run} disabled={loading}>{loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} {data ? 'Re-run' : 'Run'}</button>}>
        <div className="grid gap-3 md:grid-cols-3">
          <Field label="Money available" hint="Blank = whatever closes every gap. With a limit, the nearest gap years are funded first">
            <input className={inputCls} inputMode="decimal" value={budget} placeholder={data?.strategies.length ? `enough: ~${usd(Math.max(...data.strategies.map(s => s.cost)), { compact: true })}` : 'enough to close every gap'} onChange={e => setBudget(e.target.value)} />
          </Field>
          <Field label="Buy in account" hint="Sets each bond's tax: yearly in a taxable account (at your pre-/post-retirement rates), at withdrawal in an IRA, never in a Roth">
            <select className={selectCls} value={account} onChange={e => setAccount(e.target.value)}>{ACCOUNTS.map(x => <option key={x.value} value={x.value}>{x.label}</option>)}</select>
          </Field>
          <Field label="Credit limit for the nominal bonds" hint="The plan picks whichever of Treasury, CD, agency, muni or corporate leaves the most after tax within this limit">
            <select className={selectCls} value={credit} onChange={e => setCredit(e.target.value as typeof credit)}>{CREDIT_OPTS.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}</select>
          </Field>
        </div>
        <button type="button" className="btn btn-ghost btn-xs mt-2" onClick={() => setAdvanced(v => !v)}><SlidersHorizontal className="h-3 w-3" /> {advanced ? 'Hide' : 'Change'} the inflation worlds</button>
        {advanced && (
          <div className="mt-2 grid grid-cols-3 gap-3 rounded-xl border border-white/[0.06] bg-base-200/30 p-3 md:max-w-xl">
            <Field label="Low world % / yr" hint="Negative = deflation"><input type="number" step="0.5" className={inputCls} value={low} onChange={e => setLow(e.target.value)} /></Field>
            <Field label="High inflation % / yr"><input type="number" step="0.5" className={inputCls} value={high} onChange={e => setHigh(e.target.value)} /></Field>
            <Field label="Debasement % / yr" hint="Stays this high for decades; rates rise only half as much"><input type="number" step="0.5" className={inputCls} value={deb} onChange={e => setDeb(e.target.value)} /></Field>
          </div>
        )}
        {err && <div className="mt-3"><ErrorBox message={err} onRetry={run} /></div>}
        {loading && <p className="mt-3 flex items-center gap-2 text-xs text-base-content/55"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Running your whole plan about twenty times (3 strategies × 4 inflation worlds)… this takes 5–10 seconds.</p>}
      </Card>

      {data && !data.has_goals && (
        <p className="text-center text-xs text-base-content/55">Add your goals in the Planner first — this fills the years they come up short. <button className="link link-primary" onClick={onGotoPlanner}>Open the Planner</button></p>
      )}

      {data && data.has_goals && (
        <div className={`space-y-4 transition-opacity ${loading ? 'opacity-60' : ''}`}>
          <Card icon={<ShieldCheck className="h-4 w-4 text-emerald-400" />} title="How your plan holds up — today, and with each way of closing the gap"
            subtitle={`Funded ratio in each world; "short" is the unfunded spending in today's dollars. Expected inflation: ${data.inflation_source ?? `${data.inflation_pct}%`}.`} pad={false}>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="border-y border-white/[0.05] text-[10px] uppercase tracking-wide text-base-content/45">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">Plan</th>
                    <th className="px-2 py-2 text-left font-medium">Cost today</th>
                    {scen.map(s => <th key={s.key} className="px-2 py-2 text-left font-medium" title={`${s.story}\nInflation ${s.short}% (3y) then ${s.long}%; interest rates ${s.rate_shift >= 0 ? '+' : ''}${s.rate_shift.toFixed(1)}%.`}>{s.label}</th>)}
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b border-white/[0.03]">
                    <td className="px-3 py-2 font-semibold">Your plan as it is<div className="text-[10px] font-normal text-base-content/45">no new purchases</div></td>
                    <td className="px-2 py-2 tabular-nums text-base-content/50">—</td>
                    {scen.map(s => <Cell key={s.key} o={data.current.outcomes[s.key]} />)}
                  </tr>
                  {data.strategies.map(s => (
                    <tr key={s.key} onClick={() => setPick(s.key)}
                      className={`cursor-pointer border-b border-white/[0.03] ${pick === s.key ? 'bg-primary/[0.07]' : 'hover:bg-white/[0.02]'}`}>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap items-center gap-1.5 font-semibold">
                          <input type="radio" className="radio radio-xs radio-primary" checked={pick === s.key} onChange={() => setPick(s.key)} />
                          {s.label}
                          {data.recommended === s.key && <span className="rounded bg-emerald-500/15 px-1 text-[9px] font-semibold uppercase text-emerald-300">recommended</span>}
                        </div>
                        <div className="ml-5 max-w-[320px] text-[10px] font-normal leading-snug text-base-content/45">{s.blurb}</div>
                      </td>
                      <td className="px-2 py-2 align-top font-semibold tabular-nums">{usd(s.cost, { compact: true })}
                        {s.tips_cost > 0 && s.tips_cost < s.cost - 1 && <div className="text-[10px] font-normal text-base-content/45">{usd(s.tips_cost, { compact: true })} TIPS</div>}</td>
                      {scen.map(x => <Cell key={x.key} o={s.outcomes[x.key]} />)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="space-y-2 px-4 py-3">
              {data.why && data.why.length > 0 && (
                <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-[11.5px] leading-relaxed text-emerald-100/85">
                  <b>Why:</b> {data.why[0]}
                  <ul className="mt-1 list-disc space-y-0.5 pl-4 text-base-content/65">{data.why.slice(1).map((w, i) => <li key={i}>{w}</li>)}</ul>
                </div>
              )}
              {data.strategies.length === 0 && <p className="text-xs text-base-content/60">{data.notes[0]}</p>}
              <div className="grid gap-x-6 gap-y-1 text-[10.5px] leading-snug text-base-content/50 md:grid-cols-2">
                {scen.map(s => <div key={s.key}><b className="text-base-content/70">{s.label}:</b> {s.story}</div>)}
              </div>
              {(() => { const o = (strat ?? { outcomes: data.current.outcomes }).outcomes; const worst = SC_ORDER.reduce((a, k) => (o[k].tips_to_close > o[a].tips_to_close ? k : a), 'base' as BondGapScenarioKey);
                return o[worst].tips_to_close > 1000 ? (
                  <Note>Even after these purchases, {scen.find(s => s.key === worst)?.label.toLowerCase()} would leave a gap — your existing bonds pay fixed dollars. Closing it too would take about {usd(o[worst].tips_to_close, { compact: true })} more in TIPS today.</Note>
                ) : null; })()}
            </div>
          </Card>

          {strat && (
            <>
              <div className="grid gap-4 lg:grid-cols-5">
                <Card className="lg:col-span-3" title={`Buy list — ${strat.label}`}
                  subtitle={`${usd(strat.cost)} today · one rung per shortfall year, maturing that year${data.budget ? ` · limited to your ${usd(data.budget, { compact: true })}` : ''}`}
                  right={watchable.length > 0 && (
                    <button className="btn btn-outline btn-xs" disabled={adding || watchable.every(l => added[lineKey(l)])} onClick={() => addToWatch(watchable.filter(l => !added[lineKey(l)]))}>
                      {adding ? <Loader2 className="h-3 w-3 animate-spin" /> : <BookmarkPlus className="h-3 w-3" />} Add Treasuries/TIPS to watchlist
                    </button>
                  )} pad={false}>
                  <div className="max-h-[440px] overflow-auto">
                    <table className="w-full text-xs">
                      <thead className="sticky top-0 bg-base-100 text-[10px] uppercase tracking-wide text-base-content/45">
                        <tr>{['For', 'Buy', 'Amount', 'Yield → after tax', 'Exactly what', 'Why'].map(h => <th key={h} className="px-2 py-1.5 text-left font-medium">{h}</th>)}</tr>
                      </thead>
                      <tbody>
                        {strat.lines.map(l => (
                          <tr key={lineKey(l)} className="border-t border-white/[0.03] align-top">
                            <td className="px-2 py-1.5 font-semibold tabular-nums">{l.year}</td>
                            <td className="px-2 py-1.5"><span className="flex items-center gap-1.5"><KindBadge kind={l.kind} /><span className="font-medium">{l.label}</span></span></td>
                            <td className="px-2 py-1.5 font-semibold tabular-nums">{usd(l.amount)}<div className="text-[10px] font-normal text-base-content/45">→ {usd(l.spendable, { compact: true })} to spend</div></td>
                            <td className="px-2 py-1.5 tabular-nums">{pct(l.pre_tax_pct)} → <span className="text-emerald-400">{pct(l.after_tax_pct)}</span>
                              {l.real_yield_pct != null && <div className="text-[10px] text-base-content/45">real {pct(l.real_yield_pct)}</div>}</td>
                            <td className="max-w-[220px] px-2 py-1.5 text-[11px] leading-snug">
                              {l.security ? <><span className="font-mono text-primary">{l.security.cusip}</span> · {l.security.coupon_pct}% due {fmtDate(l.security.maturity)}
                                <div className="text-[10px] text-base-content/50">price {l.security.price}{l.security.real ? ' (real)' : ''}{added[lineKey(l)] ? <span className="ml-1 text-emerald-400"><Check className="inline h-3 w-3" /> watchlist</span> : null}</div></>
                                : <span className="text-base-content/65">{l.label}, non-callable, maturing {l.year}</span>}
                              {l.etf && <div className="text-[10px] text-base-content/50">or the {l.year} ETF: <b>{l.etf}</b> (diversified)</div>}
                            </td>
                            <td className="max-w-[240px] px-2 py-1.5 text-[10.5px] leading-snug text-base-content/60">{l.why}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
                <Card className="lg:col-span-2" title="Gap years: before and after" subtitle="At expected inflation">
                  {chart && <Chart option={chart} height={250} />}
                  {strat.fund_sales.length > 0 && (
                    <div className="mt-3">
                      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-base-content/45">When your funds are sold in this plan</div>
                      <div className="max-h-[120px] overflow-auto text-[11px]">
                        {Object.entries(strat.fund_sales.reduce<Record<number, { gross: number; names: string[] }>>((a, s) => {
                          (a[s.year] ??= { gross: 0, names: [] }); a[s.year].gross += s.gross; if (!a[s.year].names.includes(s.ticker ?? s.label)) a[s.year].names.push(s.ticker ?? s.label); return a;
                        }, {})).map(([y, v]) => (
                          <div key={y} className="flex justify-between gap-2 border-t border-white/[0.03] py-0.5"><span className="tabular-nums text-base-content/60">{y}</span><span className="truncate text-base-content/55">{v.names.join(', ')}</span><span className="tabular-nums">{usd(v.gross, { compact: true })}</span></div>
                        ))}
                      </div>
                      <button className="link link-primary mt-1 text-[11px]" onClick={onGotoPlanner}>Which fund, why, and the tax — in the Planner →</button>
                    </div>
                  )}
                </Card>
              </div>
            </>
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            {rb && <ResilienceCard before={rb} after={ra} />}
            {data.placement && (
              <Card title="Which account should hold it?" subtitle={`What $1 invested today becomes, to spend, in ${data.placement.year}`}>
                <table className="w-full text-xs">
                  <thead className="text-[10px] uppercase tracking-wide text-base-content/45"><tr>{['Account', 'Best nominal bond', 'TIPS'].map(h => <th key={h} className="px-2 py-1.5 text-left font-medium">{h}</th>)}</tr></thead>
                  <tbody>
                    {data.placement.rows.map(r => (
                      <tr key={r.account} className={`border-t border-white/[0.03] ${r.account === data.account_type || (r.account === 'ira' && ['401k', '403b'].includes(data.account_type)) ? 'bg-primary/[0.06]' : ''}`}>
                        <td className="px-2 py-1.5 font-medium">{r.label}</td>
                        <td className="px-2 py-1.5 tabular-nums">{r.nominal ? <>${r.nominal.spendable_per_dollar.toFixed(2)} <span className="text-base-content/45">· {r.nominal.label}</span></> : '—'}</td>
                        <td className="px-2 py-1.5 tabular-nums">{r.tips ? `$${r.tips.spendable_per_dollar.toFixed(2)}` : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="mt-2 space-y-0.5">
                  <Note>{data.placement.note}</Note>
                  {data.retired_tax && <Note>Taxes use your current rates until {data.retired_tax.from_year - 1}, then {data.retired_tax.federal_pct}% federal / {data.retired_tax.state_pct}% state.</Note>}
                </div>
              </Card>
            )}
          </div>
          {data.breakeven_pct != null && data.strategies.length > 0 && (
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Stat label="Gap to fill" value={usd(data.gaps.reduce((a, g) => a + g.shortfall, 0), { compact: true })} sub={`${data.gaps.length} year(s): ${data.gaps[0].year}–${data.gaps[data.gaps.length - 1].year}`} />
              <Stat label="Inflation-adjusted share" value={pct(data.real_share_pct, 0)} sub="of the gap rises with CPI" />
              <Stat label="TIPS break-even inflation" value={pct(data.breakeven_pct)} sub="above this, TIPS beat Treasuries" hint="Nominal Treasury yield vs TIPS real yield over these maturities" />
              <Stat label="You expect" value={pct(data.inflation_long_pct ?? data.inflation_pct)} sub="long-run (Tax tab)" />
            </div>
          )}
          <div className="space-y-0.5">{data.notes.map((n, i) => <Note key={i}>{n}</Note>)}</div>
        </div>
      )}
    </div>
  );
}
