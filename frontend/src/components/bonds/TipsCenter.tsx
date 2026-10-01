import React, { useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Eye, Loader2, Save, ShieldCheck, TrendingUp } from 'lucide-react';
import { adoptBondLadder, buildTipsLadder, fetchBondTips, saveBondLadder } from '../../api';
import type { BondCatalogue, TipsLadderResult } from '../../types';
import { ACCOUNTS, AXIS, Card, Chart, ErrorBox, Field, LEGEND, Loading, Note, Seg, Stat, TOOLTIP, fmtDate, inputCls, num, pct, selectCls, usd } from './bondUi';

export default function TipsCenter({ onChanged }: { onChanged: () => void }) {
  const [cat, setCat] = useState<BondCatalogue | null>(null);
  const [catErr, setCatErr] = useState<string | null>(null);
  const [mode, setMode] = useState<'income' | 'budget'>('income');
  const thisYear = new Date().getFullYear();
  const [params, setParams] = useState({ annual_income: 40000, amount: 500000, first_year: thisYear + 1, last_year: thisYear + 20, account_type: 'ira' });
  const [lad, setLad] = useState<TipsLadderResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<number | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [sort, setSort] = useState<'maturity' | 'real_yield_pct' | 'breakeven_pct'>('maturity');

  useEffect(() => { fetchBondTips().then(setCat).catch(e => setCatErr(e instanceof Error ? e.message : 'Failed')); }, []);

  const rows = useMemo(() => {
    const r = [...(cat?.rows ?? [])];
    return r.sort((a, b) => sort === 'maturity' ? a.maturity.localeCompare(b.maturity) : ((b[sort] ?? -99) as number) - ((a[sort] ?? -99) as number));
  }, [cat, sort]);

  const curve = useMemo(() => {
    const pts = (cat?.rows ?? []).filter(r => r.real_yield_pct != null && r.years > 0.4);
    return {
      tooltip: { ...TOOLTIP, trigger: 'item', formatter: (p: { data: [number, number, string, number | null] }) => `${p.data[2]}<br/>real ${p.data[1].toFixed(3)}%${p.data[3] != null ? ` · breakeven ${p.data[3].toFixed(2)}%` : ''}` },
      legend: LEGEND, grid: { left: 8, right: 12, top: 30, bottom: 4, containLabel: true },
      xAxis: { type: 'value', name: 'years', nameTextStyle: { color: '#64748b', fontSize: 9 }, ...AXIS },
      yAxis: [{ type: 'value', ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => `${v}%` } }],
      series: [
        { name: 'Real yield', type: 'scatter', symbolSize: 7, itemStyle: { color: '#34d399' }, data: pts.map(r => [r.years, r.real_yield_pct, `${r.cusip} · ${fmtDate(r.maturity)}`, r.breakeven_pct ?? null]) },
        { name: 'Breakeven inflation', type: 'scatter', symbolSize: 5, itemStyle: { color: '#f59e0b' }, data: pts.filter(r => r.breakeven_pct != null).map(r => [r.years, r.breakeven_pct, `${r.cusip} breakeven`, null]) },
      ],
    };
  }, [cat]);

  const build = async () => {
    setLoading(true); setErr(null); setSavedId(null); setMsg(null);
    try {
      setLad(await buildTipsLadder({ mode, ...params, ...(mode === 'income' ? { amount: null } : {}) }));
    } catch (e) { setErr(e instanceof Error ? e.message : 'Build failed'); } finally { setLoading(false); }
  };
  const save = async () => {
    if (!lad) return;
    const s = await saveBondLadder({ name: `TIPS income ${lad.first_year}–${lad.last_year}`, ladder_type: 'tips', params: lad.params, plan: lad });
    setSavedId(s.id); setMsg('Saved. Adopt it as bought or as a plan:');
  };
  const adopt = async (status: 'held' | 'watch') => {
    if (!savedId) return;
    const r = await adoptBondLadder(savedId, status);
    setMsg(`${r.created} TIPS ${status === 'held' ? 'added to your holdings' : 'added to your watchlist'} and linked to the ladder.`);
    onChanged();
  };

  const incomeChart = useMemo(() => lad && ({
    tooltip: { ...TOOLTIP, trigger: 'axis', valueFormatter: (v: number) => usd(v) },
    legend: LEGEND, grid: { left: 8, right: 8, top: 30, bottom: 4, containLabel: true },
    xAxis: { type: 'category', data: lad.yearly.map(y => String(y.year)), ...AXIS },
    yAxis: { type: 'value', ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => usd(v, { compact: true }) } },
    series: [
      { name: "Real income (today's $)", type: 'bar', data: lad.yearly.map(y => ({ value: y.real_income, itemStyle: { color: y.gap_year ? '#f59e0b' : '#34d399' } })) },
      { name: `Nominal at ${lad.inflation_pct}% inflation`, type: 'line', data: lad.yearly.map(y => y.nominal_income), itemStyle: { color: '#94a3b8' }, symbol: 'none', lineStyle: { type: 'dashed' } },
    ],
  }), [lad]);

  return (
    <div className="space-y-4">
      <Card icon={<ShieldCheck className="h-4 w-4 text-emerald-400" />} title="TIPS income ladder"
        subtitle="Lock in an inflation-proof income floor: each year a TIPS matures and pays you the same REAL amount, rising with CPI.">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
          <Field label="Mode">
            <Seg value={mode} onChange={v => setMode(v as 'income' | 'budget')} options={[{ value: 'income', label: 'Target income' }, { value: 'budget', label: 'Budget' }]} />
          </Field>
          {mode === 'income'
            ? <Field label="Real income / yr ($)"><input type="number" className={inputCls} value={params.annual_income} onChange={e => setParams({ ...params, annual_income: Number(e.target.value) })} /></Field>
            : <Field label="Budget ($)"><input type="number" className={inputCls} value={params.amount} onChange={e => setParams({ ...params, amount: Number(e.target.value) })} /></Field>}
          <Field label="First year"><input type="number" className={inputCls} value={params.first_year} onChange={e => setParams({ ...params, first_year: Number(e.target.value) })} /></Field>
          <Field label="Last year"><input type="number" className={inputCls} value={params.last_year} onChange={e => setParams({ ...params, last_year: Number(e.target.value) })} /></Field>
          <Field label="Account">
            <select className={selectCls} value={params.account_type} onChange={e => setParams({ ...params, account_type: e.target.value })}>
              {ACCOUNTS.map(a => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
          </Field>
          <div className="flex items-end"><button className="btn btn-primary btn-sm w-full" onClick={build} disabled={loading}>{loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />} Build</button></div>
        </div>
        {err && <div className="mt-3"><ErrorBox message={err} /></div>}
      </Card>

      {lad && (
        <>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
            <Stat label="Real income / yr" value={usd(lad.annual_real_income)} sub={`${lad.first_year}–${lad.last_year}`} accent="text-emerald-400" />
            <Stat label="Cost today" value={usd(lad.total_cost, { compact: true })} sub={`${lad.rungs.length} TIPS`} />
            <Stat label="Real yield" value={pct(lad.real_yield_pct)} sub="cost-weighted, above inflation" />
            <Stat label="Inflation assumption" value={lad.inflation_long_pct != null && lad.inflation_long_pct !== lad.inflation_pct ? `${pct(lad.inflation_pct)} → ${pct(lad.inflation_long_pct)}` : pct(lad.inflation_pct)} sub={lad.inflation_source} />
            <Stat label="Gap years" value={lad.gaps.length ? lad.gaps.map(g => g.year).join(', ') : 'none'} sub={lad.gaps.length ? 'bracket-funded' : 'every year has a TIPS'} />
          </div>
          <div className="grid gap-4 lg:grid-cols-5">
            <Card className="lg:col-span-2" title="Income by year" subtitle="Green = matured TIPS + coupons; amber = gap year funded by bracket holdings">
              {incomeChart && <Chart option={incomeChart} height={260} />}
            </Card>
            <Card className="lg:col-span-3" title="Buy list" subtitle={`FedInvest prices ${lad.as_of}`} pad={false}
              right={<div className="flex items-center gap-2">
                {msg && <span className="text-[11px] text-emerald-400">{msg}</span>}
                {!savedId ? <button className="btn btn-sm btn-outline" onClick={save}><Save className="h-3.5 w-3.5" /> Save</button> : <>
                  <button className="btn btn-sm btn-success btn-outline" onClick={() => adopt('held')}><CheckCircle2 className="h-3.5 w-3.5" /> I bought these</button>
                  <button className="btn btn-sm btn-outline" onClick={() => adopt('watch')}><Eye className="h-3.5 w-3.5" /> Track as plan</button></>}
              </div>}>
              <div className="max-h-[320px] overflow-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-base-100 text-[10px] uppercase text-base-content/45">
                    <tr>{['Year', 'CUSIP', 'Maturity', 'Coupon', 'Real yld', 'Index ratio', 'Face to buy', 'Cost', 'Gap hedge'].map(h => <th key={h} className="px-2 py-1.5 text-left font-medium">{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    {lad.rungs.map(r => (
                      <tr key={r.cusip} className="border-t border-white/[0.03]">
                        <td className="px-2 py-1 font-medium">{r.year}</td>
                        <td className="px-2 py-1 font-mono text-[10px] text-base-content/60">{r.cusip}</td>
                        <td className="px-2 py-1 tabular-nums">{fmtDate(r.maturity)}</td>
                        <td className="px-2 py-1 tabular-nums">{pct(r.coupon_pct, 3)}</td>
                        <td className="px-2 py-1 tabular-nums text-emerald-400/90">{pct(r.real_yield_pct, 3)}</td>
                        <td className="px-2 py-1 tabular-nums">{num(r.index_ratio, 5)}</td>
                        <td className="px-2 py-1 tabular-nums">{usd(r.face_to_buy)}</td>
                        <td className="px-2 py-1 font-medium tabular-nums">{usd(r.cost)}</td>
                        <td className="px-2 py-1 tabular-nums text-amber-400/80">{r.gap_hedge ? usd(r.gap_hedge) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="space-y-0.5 px-4 py-3">{lad.notes.map((n, i) => <Note key={i}>{n}</Note>)}
                {lad.gaps.map(g => <Note key={g.year}>{g.year}: funded {Object.entries(g.split ?? {}).map(([y, w]) => `${Math.round(w * 100)}% via ${y}`).join(' + ')}</Note>)}
              </div>
            </Card>
          </div>
        </>
      )}

      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-2" icon={<TrendingUp className="h-4 w-4 text-emerald-400" />} title="TIPS real yield curve"
          subtitle="Every outstanding TIPS priced at FedInvest end-of-day — real yield (green) and breakeven inflation vs nominal Treasuries (amber)">
          {catErr ? <ErrorBox message={catErr} /> : cat ? <Chart option={curve} height={300} /> : <Loading />}
        </Card>
        <Card className="lg:col-span-3" title="TIPS catalogue" subtitle={cat ? `${cat.count} TIPS · priced ${cat.as_of} · gaps: ${(cat.gap_years ?? []).join(', ') || 'none'}` : 'Loading…'}
          right={<Seg value={sort} onChange={v => setSort(v as typeof sort)} options={[{ value: 'maturity', label: 'Maturity' }, { value: 'real_yield_pct', label: 'Real yield' }, { value: 'breakeven_pct', label: 'Breakeven' }]} />} pad={false}>
          <div className="max-h-[330px] overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-base-100 text-[10px] uppercase text-base-content/45">
                <tr>{['CUSIP', 'Maturity', 'Coupon', 'Price (real)', 'Real yield', 'Index ratio', 'Adj. price', 'Breakeven', 'Duration'].map(h => <th key={h} className="px-2 py-1.5 text-left font-medium">{h}</th>)}</tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.cusip} className="border-t border-white/[0.03] hover:bg-base-200/40">
                    <td className="px-2 py-1 font-mono text-[10px] text-base-content/60">{r.cusip}</td>
                    <td className="whitespace-nowrap px-2 py-1 tabular-nums">{fmtDate(r.maturity)}</td>
                    <td className="px-2 py-1 tabular-nums">{pct(r.coupon_pct, 3)}</td>
                    <td className="px-2 py-1 tabular-nums">{num(r.price, 3)}</td>
                    <td className="px-2 py-1 font-medium tabular-nums text-emerald-400/90">{pct(r.real_yield_pct, 3)}</td>
                    <td className="px-2 py-1 tabular-nums">{num(r.index_ratio ?? null, 5)}{r.index_ratio_projected ? '*' : ''}</td>
                    <td className="px-2 py-1 tabular-nums">{num(r.adjusted_price ?? null, 3)}</td>
                    <td className="px-2 py-1 tabular-nums text-amber-300/90">{pct(r.breakeven_pct ?? null)}</td>
                    <td className="px-2 py-1 tabular-nums">{num(r.mod_duration, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="px-4 py-2"><Note>Real yield = return above CPI-U if held to maturity. Breakeven = nominal Treasury yield at the same maturity minus the real yield: if inflation beats it, TIPS win. * index ratio projected (CPI not yet published).</Note></div>
        </Card>
      </div>
    </div>
  );
}
