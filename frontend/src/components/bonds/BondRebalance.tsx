import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, BookmarkPlus, Check, Loader2, Lock, Play, Scale, Unlock } from 'lucide-react';
import { createBondHolding, fetchBondRebalance } from '../../api';
import type { BondGapOutcome, BondGapScenarioKey, BondPortfolio, BondRebalance as Rebal, BondRebalanceBuy, BondRebalanceParams } from '../../types';
import { ACCOUNTS, Card, ErrorBox, Field, KindBadge, Note, Seg, fmtDate, inputCls, pct, pnlClass, selectCls, usd } from './bondUi';
import { ResilienceCard } from './BondGapPlan';

const SC: BondGapScenarioKey[] = ['low', 'base', 'high', 'debase'];
const CREDIT_OPTS = [{ value: 'govt', label: 'Government / FDIC only' }, { value: 'AA', label: 'AA or better (adds agencies, munis)' }, { value: 'A', label: 'A or better' }, { value: 'BBB', label: 'BBB or better' }];
const ACCT_LABEL: Record<string, string> = Object.fromEntries(ACCOUNTS.map(a => [a.value, a.label]));
const money = (s: string): number => { const v = Number(String(s).replace(/[$,\s]/g, '')); return Number.isFinite(v) ? v : 0; };
const tone = (f: number | null | undefined) => (f == null ? '' : f >= 99.5 ? 'text-emerald-400' : f >= 90 ? 'text-amber-300' : 'text-rose-400');

function Delta({ label, before, after, fmt, better, hint }: {
  label: string; before: number | null | undefined; after: number | null | undefined; fmt: (v: number | null | undefined) => string; better?: 'up' | 'down'; hint?: string;
}) {
  const moved = before != null && after != null && Math.abs(after - before) > 1e-6;
  const good = !moved || !better ? null : (better === 'up' ? after! > before! : after! < before!);
  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/40 px-3 py-2" title={hint}>
      <div className="text-[10px] uppercase tracking-wide text-base-content/45">{label}</div>
      <div className="mt-0.5 flex items-center gap-1.5 text-sm font-semibold tabular-nums">
        <span className="text-base-content/55">{fmt(before)}</span><ArrowRight className="h-3 w-3 text-base-content/35" />
        <span className={good == null ? '' : good ? 'text-emerald-400' : 'text-amber-300'}>{fmt(after)}</span>
      </div>
    </div>
  );
}

function World({ b, a }: { b: BondGapOutcome; a: BondGapOutcome }) {
  const d = b.unfunded_today_dollars - a.unfunded_today_dollars;
  return (
    <td className="px-2 py-2 align-top tabular-nums">
      <div><span className={`font-semibold ${tone(b.funded_ratio_pct)}`}>{pct(b.funded_ratio_pct, 1)}</span>
        <span className="mx-1 text-base-content/35">→</span><span className={`font-semibold ${tone(a.funded_ratio_pct)}`}>{pct(a.funded_ratio_pct, 1)}</span></div>
      <div className="text-[10px] text-base-content/50">
        {a.unfunded_today_dollars < 500 ? 'fully funded' : `${usd(a.unfunded_today_dollars, { compact: true })} short`}
        {Math.abs(d) >= 1000 && <span className={d > 0 ? 'text-emerald-400' : 'text-rose-300'}> ({d > 0 ? '−' : '+'}{usd(Math.abs(d), { compact: true })})</span>}
      </div>
    </td>
  );
}

// Balance the whole book: every holding, in every account, is kept / sold / swapped (plus any new money) so the
// plan is funded at the best after-tax outcome with limited turnover. One LP; the planner judges the result.
export default function BondRebalance({ data, onChanged }: { data: BondPortfolio; onChanged: () => void }) {
  const [newMoney, setNewMoney] = useState('');
  const [newAcct, setNewAcct] = useState('taxable');
  const [turnover, setTurnover] = useState('25');
  const [robust, setRobust] = useState<NonNullable<BondRebalanceParams['robustness']>>('both');
  const [tipsMax, setTipsMax] = useState('50');
  const [bondsMode, setBondsMode] = useState('25');
  const [intl, setIntl] = useState('0');
  const [credit, setCredit] = useState<NonNullable<BondRebalanceParams['min_credit']>>('AA');
  const [keep, setKeep] = useState<number[]>([]);
  const [res, setRes] = useState<Rebal | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [added, setAdded] = useState<Record<string, boolean>>({});
  const [adding, setAdding] = useState(false);
  const req = useRef(0);
  const started = useRef(false);

  const run = useCallback(async (keepIds: number[] = keep) => {
    const id = ++req.current;
    setLoading(true); setErr(null);
    try {
      const r = await fetchBondRebalance({ new_money: money(newMoney), new_money_account: newAcct, max_turnover_pct: Number(turnover),
        robustness: robust, min_credit: credit, keep_ids: keepIds, tips_max_pct: Number(tipsMax), bond_turnover_pct: Number(bondsMode),
        intl_pct: Number(intl) });
      if (id === req.current) { setRes(r); setAdded({}); }
    } catch (e) {
      if (id === req.current) setErr(e instanceof Error ? e.message : 'Could not rebalance');
    } finally {
      if (id === req.current) setLoading(false);
    }
  }, [newMoney, newAcct, turnover, robust, credit, keep, tipsMax, bondsMode, intl]);
  useEffect(() => { if (!started.current) { started.current = true; run(); } }, [run]);

  const toggleKeep = (id: number) => { const next = keep.includes(id) ? keep.filter(x => x !== id) : [...keep, id]; setKeep(next); run(next); };
  const accounts = useMemo(() => Array.from(new Set([...(res?.sells ?? []).map(s => s.account_type), ...(res?.buys ?? []).map(b => b.account_type)])), [res]);
  const bkey = (l: BondRebalanceBuy) => `${l.account_type}|${l.security?.cusip ?? l.ticker ?? l.label}|${l.year}`;
  const watchable = (res?.buys ?? []).filter(l => l.security);
  const addToWatch = async (ls: BondRebalanceBuy[]) => {
    setAdding(true); setErr(null);
    try {
      for (const l of ls) {
        const s = l.security!;
        await createBondHolding({ kind: l.kind, status: 'watch', cusip: s.cusip, label: `${l.kind === 'tips' ? 'TIPS' : 'UST'} ${s.coupon_pct}% ${s.maturity} (rebalance)`,
          face_value: l.face, coupon_rate: s.coupon_pct, coupon_freq: s.type === 'bill' ? 0 : 2, maturity_date: s.maturity, account_type: l.account_type });
        setAdded(a => ({ ...a, [bkey(l)]: true }));
      }
      onChanged();
    } catch (e) { setErr(e instanceof Error ? e.message : 'Could not add to the watchlist'); } finally { setAdding(false); }
  };

  const t = res?.totals;
  const hold = res?.verdict === 'hold';
  return (
    <div className="space-y-4">
      <Card icon={<Scale className="h-4 w-4 text-primary" />} title="Balance the whole book"
        subtitle="Looks at every holding in every account (plus any money you add) and asks: keep it, or is there something that funds your plan better after tax? It changes only what pays for its own tax and trading cost."
        right={<button className="btn btn-primary btn-sm" onClick={() => run()} disabled={loading}>{loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} {res ? 'Re-run' : 'Run'}</button>}>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Field label="New money to add (optional)" hint="Invested in the account you pick; everything else stays in its own account">
            <div className="flex gap-2">
              <input className={inputCls} inputMode="decimal" placeholder="0" value={newMoney} onChange={e => setNewMoney(e.target.value)} />
              <select className={selectCls} value={newAcct} onChange={e => setNewAcct(e.target.value)}>{ACCOUNTS.map(a => <option key={a.value} value={a.value}>{a.label}</option>)}</select>
            </div>
          </Field>
          <Field label="How much of the book may change" hint="A cap on what's sold. Lower = fewer, only the most valuable swaps">
            <Seg value={turnover} onChange={setTurnover} options={[{ value: '10', label: '10%' }, { value: '25', label: '25%' }, { value: '50', label: '50%' }, { value: '100', label: 'No limit' }]} />
          </Field>
          <Field label="Must hold up in" hint="Both ways = deflation, expected AND high inflation, each valued in today's purchasing power. All four adds dollar debasement">
            <Seg value={robust} onChange={v => setRobust(v as typeof robust)} options={[{ value: 'expected', label: 'Expected only' }, { value: 'both', label: 'Both ways' }, { value: 'all', label: 'All four' }]} />
          </Field>
          <Field label="Credit limit for new bonds">
            <select className={selectCls} value={credit} onChange={e => setCredit(e.target.value as typeof credit)}>{CREDIT_OPTS.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}</select>
          </Field>
          <Field label="Inflation-linked share of what's bought" hint="A cap on TIPS, so the book isn't a one-way bet: fixed-dollar bonds win if inflation is low or negative, TIPS if it's high">
            <Seg value={tipsMax} onChange={setTipsMax} options={[{ value: '25', label: '≤ 25%' }, { value: '50', label: '≤ 50%' }, { value: '75', label: '≤ 75%' }, { value: '100', label: 'No cap' }]} />
          </Field>
          <Field label="What may be sold" hint="Funds go first: they're cheap to trade and never mature. Individual bonds get only a part of the change limit">
            <Seg value={bondsMode} onChange={setBondsMode} options={[{ value: '0', label: 'Funds only' }, { value: '25', label: 'Funds first' }, { value: '100', label: 'Anything' }]} />
          </Field>
          <Field label="Non-dollar bonds (currency diversification)" hint="An unhedged international bond-fund sleeve: gains if the dollar weakens, loses if it strengthens">
            <Seg value={intl} onChange={setIntl} options={[{ value: '0', label: 'None' }, { value: '5', label: '5% of book' }, { value: '10', label: '10%' }]} />
          </Field>
        </div>
        {keep.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-base-content/60">
            <Lock className="h-3 w-3" /> Kept as is:
            {(res?.kept ?? keep.map(id => ({ holding_id: id, label: data.holdings.find(h => h.id === id)?.label ?? `#${id}` }))).map(k => (
              <button key={k.holding_id} className="rounded-full border border-white/10 px-2 py-0.5 hover:border-rose-400/40 hover:text-rose-300" onClick={() => toggleKeep(k.holding_id)} title="Allow it to be sold again">{k.label} ×</button>
            ))}
          </div>
        )}
        {err && <div className="mt-3"><ErrorBox message={err} onRetry={() => run()} /></div>}
        {loading && <p className="mt-3 flex items-center gap-2 text-xs text-base-content/55"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Optimizing every holding against your plan, then checking the result in four inflation worlds…</p>}
      </Card>

      {res?.verdict === 'no_goals' && <p className="text-center text-xs text-base-content/55">{res.notes[0]}</p>}
      {res && res.outcomes && res.verdict !== 'no_goals' && (
        <div className={`space-y-4 transition-opacity ${loading ? 'opacity-60' : ''}`}>
          <div className={`rounded-2xl border px-4 py-3 ${hold ? 'border-white/[0.08] bg-base-100/60' : 'border-emerald-500/25 bg-emerald-500/[0.06]'}`}>
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className={`text-sm font-bold ${hold ? '' : 'text-emerald-300'}`}>{hold ? 'Hold — nothing worth changing' : `Rebalance: sell ${t?.positions_sold} position${t?.positions_sold === 1 ? '' : 's'}, buy ${t?.lines_bought} line${t?.lines_bought === 1 ? '' : 's'}`}</span>
              {!hold && t && <span className="text-xs text-base-content/60">{usd(t.sold, { compact: true })} sold ({pct(t.turnover_pct, 1)} of the book){res.new_money > 0 ? ` + ${usd(res.new_money, { compact: true })} new` : ''} · tax now {usd(t.tax_now)} · trading cost ≈ {usd(t.trading_cost)}</span>}
            </div>
            <ul className="mt-2 list-disc space-y-1 pl-4 text-[11.5px] leading-relaxed text-base-content/75">{res.why.map((w, i) => <li key={i}>{w}</li>)}</ul>
          </div>

          <Card title="Your plan: now → after rebalancing" subtitle={`Checked by the planner in every inflation world (optimized for: ${res.worlds_optimized.join(', ')})`} pad={false}>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="border-y border-white/[0.05] text-[10px] uppercase tracking-wide text-base-content/45">
                  <tr>{SC.map(k => { const s = res.scenarios.find(x => x.key === k); return <th key={k} className="px-2 py-2 text-left font-medium" title={s?.story}>{s?.label}</th>; })}</tr>
                </thead>
                <tbody><tr>{SC.map(k => <World key={k} b={res.outcomes!.before[k]} a={res.outcomes!.after[k]} />)}</tr></tbody>
              </table>
            </div>
            {res.book && (
              <div className="grid grid-cols-2 gap-2 px-4 py-3 md:grid-cols-5">
                <Delta label="After-tax yield" before={res.book.before.after_tax_yield_pct} after={res.book.after.after_tax_yield_pct} fmt={v => pct(v)} better="up" />
                <Delta label="Total yield" before={res.book.before.total_yield_pct} after={res.book.after.total_yield_pct} fmt={v => pct(v)} better="up" />
                <Delta label="Duration" before={res.book.before.eff_duration} after={res.book.after.eff_duration} fmt={v => (v == null ? '—' : `${v.toFixed(2)}y`)}
                  hint="Longer isn't riskier here when each bond matures in the year you spend it — but its price moves more if you sold early" />
                <Delta label="Positions" before={res.book.before.positions} after={res.book.after.positions} fmt={v => (v == null ? '—' : String(v))} />
                <Delta label="Left at the end" before={res.end_wealth?.before} after={res.end_wealth?.after} fmt={v => usd(v, { compact: true })} better="up"
                  hint="After-tax money remaining after the last goal year, at expected inflation" />
              </div>
            )}
          </Card>

          {!hold && accounts.map(a => {
            const ss = res.sells.filter(s => s.account_type === a), bb = res.buys.filter(b => b.account_type === a);
            const cash = res.cash_left?.find(c => c.account_type === a);
            const acc = res.by_account?.find(x => x.account_type === a);
            return (
              <Card key={a} title={`${ACCT_LABEL[a] ?? a.toUpperCase()}`}
                subtitle={`${usd(acc?.value ?? 0, { compact: true })} in this account · sell ${usd(acc?.sold ?? 0, { compact: true })} → buy ${usd(acc?.bought ?? 0, { compact: true })}${acc?.new_money ? ` (incl. ${usd(acc.new_money, { compact: true })} new money)` : ''}${a === 'taxable' ? '' : ' · no tax on trades inside this account'}`} pad={false}>
                <div className="grid gap-0 lg:grid-cols-2">
                  <div className="border-b border-white/[0.05] lg:border-b-0 lg:border-r">
                    <div className="px-3 pt-2 text-[10px] font-semibold uppercase tracking-wide text-rose-300/80">Sell</div>
                    {ss.length === 0 ? <p className="px-3 py-3 text-xs text-base-content/45">Nothing — only the new money is invested here.</p> : (
                      <table className="w-full text-xs">
                        <tbody>
                          {ss.map(s => (
                            <tr key={s.holding_id} className="border-t border-white/[0.03] align-top">
                              <td className="px-3 py-1.5">
                                <div className="flex items-center gap-1.5"><KindBadge kind={s.kind} /><span className="font-medium">{s.ticker ?? s.label}</span>
                                  {s.fraction_pct < 99.9 && <span className="text-[10px] text-base-content/45">{pct(s.fraction_pct, 0)} of it</span>}</div>
                                <div className="mt-0.5 text-[10.5px] leading-snug text-base-content/55">{s.reason}</div>
                              </td>
                              <td className="px-2 py-1.5 text-right tabular-nums"><div className="font-semibold">{usd(s.amount)}</div>
                                <div className="text-[10px] text-base-content/45">yields {pct(s.after_tax_yield_pct)}</div></td>
                              <td className="px-2 py-1.5 text-right tabular-nums text-[10.5px]">
                                {Math.abs(s.gain) >= 1 && <div className={pnlClass(s.gain)}>{usd(s.gain, { sign: true })}</div>}
                                {Math.abs(s.tax_now) >= 1 && <div className="text-base-content/45">tax {usd(s.tax_now, { sign: true })}</div>}
                              </td>
                              <td className="px-2 py-1.5 text-right">
                                <button className="btn btn-ghost btn-xs" title="Keep this holding — re-run without selling it" disabled={loading} onClick={() => toggleKeep(s.holding_id)}><Unlock className="h-3 w-3" /> keep</button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                  <div>
                    <div className="px-3 pt-2 text-[10px] font-semibold uppercase tracking-wide text-emerald-300/80">Buy</div>
                    <table className="w-full text-xs">
                      <tbody>
                        {bb.map(l => (
                          <tr key={bkey(l)} className="border-t border-white/[0.03] align-top">
                            <td className="px-3 py-1.5">
                              <div className="flex items-center gap-1.5"><KindBadge kind={l.kind} /><span className="font-medium">{l.leg === 'intl' ? l.ticker : l.label}</span>
                                <span className="text-[10px] text-base-content/45">{l.leg === 'intl' ? 'non-dollar sleeve' : `for ${l.years.length > 1 ? `${l.years[0]}–${l.years[l.years.length - 1]}` : l.years[0]}`}</span></div>
                              <div className="mt-0.5 text-[10.5px] leading-snug text-base-content/55">
                                {l.leg === 'intl' ? <>{l.label} — {l.what}; value moves with the dollar</> : l.security ? <><span className="font-mono text-primary">{l.security.cusip}</span> · {l.security.coupon_pct}% due {fmtDate(l.security.maturity)} · price {l.security.price}{l.security.real ? ' (real)' : ''}
                                  {added[bkey(l)] && <span className="ml-1 text-emerald-400"><Check className="inline h-3 w-3" /> watchlist</span>}</>
                                  : <>{l.label}, non-callable, maturing {l.year}</>}
                                {l.etf && l.leg !== 'intl' && <> · {l.kind === 'corporate' || l.kind === 'muni' ? <>best as the {l.year} ETF <b>{l.etf}</b> (hundreds of issuers)</> : <>or ETF <b>{l.etf}</b></>}</>}
                              </div>
                            </td>
                            <td className="px-2 py-1.5 text-right tabular-nums"><div className="font-semibold">{usd(l.amount)}</div>
                              <div className="text-[10px] text-emerald-400/90">yields {pct(l.after_tax_pct)}</div></td>
                          </tr>
                        ))}
                        {cash && <tr className="border-t border-white/[0.03]"><td className="px-3 py-1.5 text-base-content/55">Left in cash / T-bills</td><td className="px-2 py-1.5 text-right tabular-nums">{usd(cash.amount)}</td></tr>}
                      </tbody>
                    </table>
                  </div>
                </div>
              </Card>
            );
          })}
          {!hold && watchable.length > 0 && (
            <div className="flex justify-end">
              <button className="btn btn-outline btn-xs" disabled={adding || watchable.every(l => added[bkey(l)])} onClick={() => addToWatch(watchable.filter(l => !added[bkey(l)]))}>
                {adding ? <Loader2 className="h-3 w-3 animate-spin" /> : <BookmarkPlus className="h-3 w-3" />} Add the Treasuries/TIPS to my watchlist
              </button>
            </div>
          )}
          <div className="grid gap-4 lg:grid-cols-5">
            {res.fund_alternatives && res.fund_alternatives.length > 0 && (
              <Card className="lg:col-span-3" title="Your funds vs what else that account could hold"
                subtitle="Each fund's yield against the best bond of similar maturity in the same account — the first candidates to sell are at the top" pad={false}>
                <div className="max-h-[320px] overflow-auto">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-base-100 text-[10px] uppercase tracking-wide text-base-content/45">
                      <tr>{['Fund', 'Value', 'Yields', 'Best alternative', 'Difference', 'Plan'].map(h => <th key={h} className="px-2 py-1.5 text-left font-medium">{h}</th>)}</tr>
                    </thead>
                    <tbody>
                      {res.fund_alternatives.map(f => (
                        <tr key={f.holding_id} className="border-t border-white/[0.03]">
                          <td className="px-2 py-1.5"><span className="font-medium">{f.ticker ?? f.label}</span> <span className="text-[10px] text-base-content/45">{ACCT_LABEL[f.account_type] ?? f.account_type}</span></td>
                          <td className="px-2 py-1.5 tabular-nums">{usd(f.value, { compact: true })}</td>
                          <td className="px-2 py-1.5 tabular-nums" title={f.basis}>{pct(f.yield_pct)}</td>
                          <td className="px-2 py-1.5 tabular-nums">{f.alt_label ? <>{pct(f.alt_yield_pct)} <span className="text-[10px] text-base-content/45">{f.alt_label} ~{f.alt_years}y</span></> : '—'}</td>
                          <td className={`px-2 py-1.5 font-medium tabular-nums ${(f.gap_pct ?? 0) > 0.1 ? 'text-amber-300' : (f.gap_pct ?? 0) < -0.1 ? 'text-emerald-400' : ''}`}>{pct(f.gap_pct, 2, true)}</td>
                          <td className="px-2 py-1.5">{f.sold_pct > 0 ? <span className="text-rose-300">sell {pct(f.sold_pct, 0)}</span> : f.kept ? <span className="text-base-content/50"><Lock className="inline h-3 w-3" /> kept</span> : <span className="text-base-content/50">keep</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="px-4 py-2"><Note>Yields are after tax in a taxable account and as earned inside a retirement account. A fund's "yield" is its estimated yield to maturity net of fees (or its distributions); funds with credit, leverage or currency risk can yield more than the alternative for that reason.</Note></div>
              </Card>
            )}
            {res.allocation && (
              <Card className="lg:col-span-2" title="Mix by type: now → after" subtitle="Share of the whole book">
                {(() => {
                  const tot = (l: { value: number }[]) => l.reduce((a, x) => a + x.value, 0) || 1;
                  const tb = tot(res.allocation.before), ta = tot(res.allocation.after);
                  const keys = Array.from(new Set([...res.allocation.after.map(x => x.key), ...res.allocation.before.map(x => x.key)]));
                  const val = (l: { key: string; value: number }[], k: string) => l.find(x => x.key === k)?.value ?? 0;
                  return keys.map(k => {
                    const b = (100 * val(res.allocation!.before, k)) / tb, a = (100 * val(res.allocation!.after, k)) / ta;
                    return (
                      <div key={k} className="py-1 text-[11px]">
                        <div className="flex justify-between"><span className="text-base-content/65">{k}</span>
                          <span className="tabular-nums">{pct(b, 0)}{Math.abs(a - b) >= 0.5 && <span className={a > b ? 'text-emerald-400' : 'text-rose-300'}> → {pct(a, 0)}</span>}</span></div>
                        <div className="relative h-1.5 rounded bg-base-300/60">
                          <div className="absolute h-1.5 rounded bg-slate-500/70" style={{ width: `${Math.min(100, b)}%` }} />
                          <div className="absolute h-1.5 rounded bg-sky-400/80" style={{ width: `${Math.min(100, a)}%`, opacity: 0.75 }} />
                        </div>
                      </div>
                    );
                  });
                })()}
              </Card>
            )}
          </div>
          {res.roll_at_maturity && res.roll_at_maturity.length > 0 && (
            <Card title={`Don't sell — roll at maturity (${res.roll_at_maturity.length})`}
              subtitle={`Bonds in retirement accounts that mature before you can tap them (${res.roll_until}). When each matures, reinvest it inside the account in a bond maturing in the years that money is needed.`}>
              <div className="flex flex-wrap gap-1.5">
                {res.roll_at_maturity.map(r => (
                  <span key={r.holding_id} className="rounded-full border border-white/[0.08] px-2 py-0.5 text-[11px] text-base-content/65">
                    {r.label} <span className="text-base-content/40">· {ACCT_LABEL[r.account_type] ?? r.account_type} · {fmtDate(r.maturity)} · {usd(r.value, { compact: true })}</span>
                  </span>
                ))}
              </div>
            </Card>
          )}
          {res.resilience && <ResilienceCard before={res.resilience.before} after={hold ? undefined : res.resilience.after} />}
          <div className="space-y-0.5">
            {res.considered && <Note>Considered {res.considered.holdings} holdings and {res.considered.candidates} possible purchases; {res.considered.kept_out} left out (bank CDs, or maturing after your last goal).</Note>}
            {res.notes.map((n, i) => <Note key={i}>{n}</Note>)}
          </div>
        </div>
      )}
    </div>
  );
}
