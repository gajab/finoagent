import React, { useEffect, useMemo, useState } from 'react';
import { Waves } from 'lucide-react';
import { fetchBondCashflow } from '../../api';
import type { BondCashFlow as CF, BondFilters, BondKind } from '../../types';
import { AXIS, Card, Chart, ErrorBox, FLOW_COLORS, KIND_META, KindBadge, LEGEND, Loading, Note, Seg, Stat, TOOLTIP, fmtDate, usd } from './bondUi';
import { filtersActive } from './bondFilters';

type Mode = 'nominal' | 'after_tax' | 'real' | 'by_kind';
const GAIN = '#fbbf24';
const LOSS = '#f87171';

export default function BondCashFlow({ initial, filters }: { initial: CF; filters?: BondFilters }) {
  const [cf, setCf] = useState<CF>(initial);
  const [mode, setMode] = useState<Mode>('nominal');
  const [assumeCalls, setAssumeCalls] = useState(false);
  const [includeWatch, setIncludeWatch] = useState(false);
  const [years, setYears] = useState(30);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const fkey = JSON.stringify(filters ?? null);

  // The overview payload has the yearly rollup but not the event list (or filters) — always fetch.
  useEffect(() => {
    setLoading(true); setErr(null);
    fetchBondCashflow({ years, fundYears: Math.min(years, 10), assumeCalls, includeWatch, filters })
      .then(setCf).catch(e => setErr(e instanceof Error ? e.message : 'Failed')).finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assumeCalls, includeWatch, years, fkey]);

  const yearly = cf.yearly;
  const hasGains = yearly.some(y => (y.gain ?? 0) > 0 || (y.premium_loss ?? 0) > 0);
  const option = useMemo(() => {
    const x = yearly.map(y => String(y.year));
    const base = {
      tooltip: { ...TOOLTIP, trigger: 'axis', valueFormatter: (v: number) => usd(v) },
      legend: LEGEND, grid: { left: 8, right: 8, top: 30, bottom: 4, containLabel: true },
      xAxis: { type: 'category', data: x, ...AXIS },
      yAxis: { type: 'value', ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => usd(v, { compact: true }) } },
    };
    if (mode === 'by_kind') {
      const kinds = Array.from(new Set(yearly.flatMap(y => Object.keys(y.by_kind)))) as BondKind[];
      return { ...base, series: kinds.map(k => ({ name: KIND_META[k]?.label ?? k, type: 'bar', stack: 'k', data: yearly.map(y => y.by_kind[k] ?? 0), itemStyle: { color: KIND_META[k]?.color } })) };
    }
    if (mode === 'after_tax') {
      return { ...base, series: [
        { name: 'After-tax cash', type: 'bar', stack: 'x', data: yearly.map(y => y.after_tax), itemStyle: { color: '#34d399' } },
        { name: 'Tax (interest, gains & phantom income)', type: 'bar', stack: 'x', data: yearly.map(y => Math.max(0, y.total - y.after_tax)), itemStyle: { color: LOSS } },
      ] };
    }
    if (mode === 'real') {
      return { ...base, series: [
        { name: `Today's dollars (${cf.inflation_pct}%${cf.inflation_long_pct != null && cf.inflation_long_pct !== cf.inflation_pct ? ` → ${cf.inflation_long_pct}%` : ''} inflation)`, type: 'bar', data: yearly.map(y => y.real_total), itemStyle: { color: '#a78bfa' } },
        { name: 'Nominal', type: 'line', data: yearly.map(y => y.total), itemStyle: { color: '#94a3b8' }, symbol: 'none', lineStyle: { type: 'dashed' } },
      ] };
    }
    // Principal is SPLIT (cost returned + gain) — the two stack to exactly the principal, never on top of it.
    return { ...base, series: [
      { name: 'Coupons', type: 'bar', stack: 'cf', data: yearly.map(y => y.coupon), itemStyle: { color: FLOW_COLORS.coupon } },
      { name: 'Distributions', type: 'bar', stack: 'cf', data: yearly.map(y => y.distribution), itemStyle: { color: FLOW_COLORS.distribution } },
      { name: 'Principal: your cost back', type: 'bar', stack: 'cf', data: yearly.map(y => y.capital_returned ?? y.principal), itemStyle: { color: FLOW_COLORS.principal } },
      ...(hasGains ? [
        { name: 'Principal: gain over cost', type: 'bar', stack: 'cf', data: yearly.map(y => y.gain ?? 0), itemStyle: { color: GAIN } },
        { name: 'Paid above what comes back (premium / fund loss)', type: 'bar', stack: 'loss', data: yearly.map(y => -(y.premium_loss ?? 0)), itemStyle: { color: LOSS, opacity: 0.55 } },
      ] : []),
    ] };
  }, [yearly, mode, cf.inflation_pct, hasGains]);

  const monthly = useMemo(() => ({
    tooltip: { ...TOOLTIP, trigger: 'axis', valueFormatter: (v: number) => usd(v) },
    legend: LEGEND, grid: { left: 8, right: 8, top: 30, bottom: 4, containLabel: true },
    xAxis: { type: 'category', data: cf.monthly.map(m => m.month.slice(2)), ...AXIS },
    yAxis: { type: 'value', ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => usd(v, { compact: true }) } },
    series: [
      { name: 'Income', type: 'bar', stack: 'm', data: cf.monthly.map(m => m.coupon + m.distribution), itemStyle: { color: FLOW_COLORS.coupon } },
      { name: 'Cost back', type: 'bar', stack: 'm', data: cf.monthly.map(m => m.capital_returned ?? m.principal), itemStyle: { color: FLOW_COLORS.principal } },
      { name: 'Gain', type: 'bar', stack: 'm', data: cf.monthly.map(m => m.gain ?? 0), itemStyle: { color: GAIN } },
    ],
  }), [cf.monthly]);

  const events = (cf.events ?? cf.next_12m.events).filter(e => !search || e.label.toLowerCase().includes(search.toLowerCase()));
  const totalIncome = yearly.reduce((a, y) => a + y.coupon + y.distribution, 0);
  const totalPrincipal = yearly.reduce((a, y) => a + y.principal, 0);
  const g = cf.gains;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <Stat label="Next 12m income" value={usd(cf.next_12m.income)} sub={`${usd(cf.next_12m.income_after_tax)} after tax`} />
        <Stat label="Next 12m principal back" value={usd(cf.next_12m.principal)} sub={cf.next_12m.gain ? `incl. ${usd(cf.next_12m.gain)} gain over cost` : 'to reinvest or spend'} />
        <Stat label={`Income over ${yearly.length}y`} value={usd(totalIncome, { compact: true })} sub={`+ ${usd(totalPrincipal, { compact: true })} principal`} />
        <Stat label="Gains at maturity" value={<span className="text-amber-300">{usd(g?.total_gain, { compact: true })}</span>}
          sub={g ? `tax ${usd(g.tax_on_gains, { compact: true })}${g.total_premium_loss ? ` · premium lost ${usd(g.total_premium_loss, { compact: true })}` : ''}` : undefined}
          hint="Discount bonds pay back more than you paid: that extra is part of the principal (not added on top). Premium bonds pay back less — the premium is written off against coupons for tax." />
        <Stat label="Phantom-income tax" value={usd(g?.phantom_tax, { compact: true })} sub="TIPS inflation / zero-coupon accretion, taxed yearly"
          hint="Taxed every year though no cash arrives until maturity — included in the after-tax view" />
      </div>

      <Card icon={<Waves className="h-4 w-4 text-primary" />} title="Cash flow by year"
        subtitle={filters && filtersActive(filters) ? 'Filtered holdings — every coupon, distribution and maturity' : 'Every coupon, distribution and maturity from your holdings'}
        right={<div className="flex flex-wrap items-center justify-end gap-2">
          <Seg value={mode} onChange={v => setMode(v as Mode)} options={[
            { value: 'nominal', label: 'Nominal' }, { value: 'after_tax', label: 'After tax' },
            { value: 'real', label: "Today's $" }, { value: 'by_kind', label: 'By type' }]} />
          <Seg value={String(years)} onChange={v => setYears(Number(v))} options={[{ value: '10', label: '10y' }, { value: '20', label: '20y' }, { value: '30', label: '30y' }, { value: '40', label: '40y' }]} />
        </div>}>
        <div className="mb-2 flex flex-wrap gap-4 text-[11px] text-base-content/60">
          <label className="flex items-center gap-1.5"><input type="checkbox" className="checkbox checkbox-xs" checked={assumeCalls} onChange={e => setAssumeCalls(e.target.checked)} /> Assume likely calls are exercised</label>
          <label className="flex items-center gap-1.5"><input type="checkbox" className="checkbox checkbox-xs" checked={includeWatch} onChange={e => setIncludeWatch(e.target.checked)} /> Include watchlist (planned buys)</label>
        </div>
        {err && <ErrorBox message={err} />}
        {loading ? <Loading label="Projecting cash flows…" /> : <Chart option={option} height={300} />}
        <div className="mt-2 space-y-0.5">
          {cf.notes.map((n, i) => <Note key={i}>{n}</Note>)}
          {g && g.unknown_cost.length > 0 && <Note>Add a cost basis to see the gain on: {g.unknown_cost.join(', ')}.</Note>}
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-2" title="Next 24 months" subtitle="Income vs principal returning (cost back + gain)">
          <Chart option={monthly} height={260} />
        </Card>
        <Card className="lg:col-span-3" title="Payment schedule" subtitle={`${cf.events_count} scheduled payments`}
          right={<input className="input input-xs input-bordered w-36 bg-base-200/60" placeholder="Search holding…" value={search} onChange={e => setSearch(e.target.value)} />}>
          <div className="max-h-[300px] overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-base-100 text-[10px] uppercase text-base-content/45">
                <tr><th className="py-1 text-left font-medium">Date</th><th className="text-left font-medium">Holding</th><th className="text-left font-medium">Type</th>
                  <th className="text-right font-medium">Amount</th><th className="text-right font-medium">After tax</th></tr>
              </thead>
              <tbody>
                {events.slice(0, 500).map((e, i) => (
                  <tr key={i} className="border-t border-white/[0.03] align-top">
                    <td className="whitespace-nowrap py-1 tabular-nums text-base-content/60">{fmtDate(e.date)}</td>
                    <td className="py-1"><span className="flex items-center gap-1.5"><KindBadge kind={e.kind} /><span className="truncate">{e.label}</span></span></td>
                    <td className="py-1 text-[10px] uppercase text-base-content/45">
                      {e.type === 'tax' ? 'phantom tax' : e.type}{e.projected ? '*' : ''}
                      {(e.type === 'principal' || e.type === 'call') && e.gain_known && (
                        <div className="normal-case text-base-content/50">
                          cost {usd(e.capital)}{e.gain ? <> + <span className="text-amber-300">gain {usd(e.gain)}</span></> : ''}
                          {e.premium_loss ? <> · <span className="text-rose-300/80">{e.kind === 'etf' || e.kind === 'mutual_fund' ? `loss ${usd(e.premium_loss)} vs cost` : `premium ${usd(e.premium_loss)} not returned`}</span></> : ''}
                        </div>
                      )}
                      {e.type === 'tax' && e.phantom_income != null && <div className="normal-case text-base-content/50">on {usd(e.phantom_income)} accreted, no cash</div>}
                    </td>
                    <td className="py-1 text-right font-medium tabular-nums">{e.type === 'tax' ? '—' : usd(e.amount)}</td>
                    <td className={`py-1 text-right tabular-nums ${e.after_tax < 0 ? 'text-rose-300' : 'text-emerald-400/80'}`}>
                      {usd(e.after_tax)}
                      {!!e.tax_on_gain && <div className="text-[10px] text-base-content/40">−{usd(e.tax_on_gain)} tax on gain</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  );
}
