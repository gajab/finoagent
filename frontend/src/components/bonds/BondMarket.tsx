import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Gavel, Landmark, LineChart, Percent } from 'lucide-react';
import { fetchBondMarket } from '../../api';
import type { BondMarket as BM } from '../../types';
import BondCalculator from './BondCalculator';
import { AXIS, Card, Chart, ErrorBox, LEGEND, Loading, Note, Stat, TOOLTIP, bp, fmtDate, num, pct, tenorLabel, usd } from './bondUi';

export default function BondMarket() {
  const [m, setM] = useState<BM | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { fetchBondMarket().then(setM).catch(e => setErr(e instanceof Error ? e.message : 'Failed')); }, []);

  const curveChart = useMemo(() => m && ({
    tooltip: { ...TOOLTIP, trigger: 'axis', valueFormatter: (v: number) => (v == null ? '—' : `${Number(v).toFixed(2)}%`) },
    legend: LEGEND, grid: { left: 8, right: 12, top: 30, bottom: 4, containLabel: true },
    xAxis: { type: 'log', logBase: 2, min: 1 / 12, max: 30, ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => tenorLabel(v) } },
    yAxis: { type: 'value', scale: true, ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => `${v}%` } },
    series: [
      { name: `Today (${m.as_of ?? ''})`, type: 'line', data: m.nominal_curve.map(p => [p.tenor, p.yield_pct]), itemStyle: { color: '#60a5fa' }, lineStyle: { width: 2.5 }, symbolSize: 5 },
      { name: '1M ago', type: 'line', data: m.nominal_curve_1m.map(p => [p.tenor, p.yield_pct]), itemStyle: { color: '#94a3b8' }, lineStyle: { type: 'dashed' }, symbol: 'none' },
      { name: '1Y ago', type: 'line', data: m.nominal_curve_1y.map(p => [p.tenor, p.yield_pct]), itemStyle: { color: '#475569' }, lineStyle: { type: 'dotted' }, symbol: 'none' },
      { name: 'Real (TIPS)', type: 'line', data: m.real_curve.map(p => [p.tenor, p.yield_pct]), itemStyle: { color: '#34d399' }, symbolSize: 5 },
    ],
  }), [m]);

  if (err) return <ErrorBox message={err} />;
  if (!m) return <Loading label="Loading live curves, spreads and Treasury prices…" />;
  const shape = m.curve_shape;
  const rc = m.rate_context;
  const bill = m.yield_menu.find(r => r.tenor === 1)?.treasury;
  const dep12 = m.deposit_rates?.['12mo']?.rate_pct;
  const sp = m.credit_spreads;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <Stat label="2s10s" value={bp(shape['2s10s_bp'])} sub={shape.inverted_2s10s ? 'inverted' : 'normal'} />
        <Stat label="3m10y" value={bp(shape['3m10y_bp'])} sub={shape.inverted_3m10y ? 'inverted' : 'normal'} />
        <Stat label="10y nominal" value={pct(rc.nominal_10y?.value_pct)} sub={rc.nominal_10y?.avg_20y_pct != null ? `20y avg ${pct(rc.nominal_10y.avg_20y_pct)}` : undefined} />
        <Stat label="10y real" value={pct(rc.real_10y?.value_pct)} sub={rc.real_10y?.avg_20y_pct != null ? `20y avg ${pct(rc.real_10y.avg_20y_pct)}` : undefined} accent="text-emerald-400" />
        <Stat label="5y breakeven" value={pct(rc.be_5y?.value_pct)} sub="market inflation" />
        <Stat label="Fed funds" value={pct(rc.fed_funds?.value_pct)} sub={`SOFR ${pct(rc.sofr?.value_pct)}`} />
        <Stat label="IG spread" value={sp?.by_rating.IG?.oas_bp != null ? `${sp.by_rating.IG.oas_bp}bp` : '—'} sub={sp?.context.IG?.percentile_3y != null ? `${sp.context.IG.percentile_3y}th pct (3y)` : undefined} />
        <Stat label="HY spread" value={sp?.by_rating.HY?.oas_bp != null ? `${sp.by_rating.HY.oas_bp}bp` : '—'} sub={sp?.context.HY?.percentile_3y != null ? `${sp.context.HY.percentile_3y}th pct (3y)` : undefined} />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2" icon={<LineChart className="h-4 w-4 text-primary" />} title="Treasury yield curve" subtitle="Official par curves (nominal + real) — today vs 1 month and 1 year ago">
          {curveChart && <Chart option={curveChart} height={300} />}
        </Card>
        <Card icon={<Landmark className="h-4 w-4 text-pink-400" />} title="Is your cash lazy?" subtitle="National average bank CD rates (FDIC via FRED) vs a 1-year T-bill">
          <div className="space-y-2">
            {(['6mo', '12mo', '60mo'] as const).map(k => (
              <div key={k} className="flex justify-between rounded-lg bg-base-200/50 px-3 py-2 text-xs"><span className="text-base-content/60">Avg bank CD {k}</span><span className="font-semibold tabular-nums">{pct(m.deposit_rates?.[k]?.rate_pct)}</span></div>
            ))}
            <div className="flex justify-between rounded-lg bg-primary/10 px-3 py-2 text-xs text-primary"><span>1-year Treasury</span><span className="font-semibold tabular-nums">{pct(bill)}</span></div>
            {bill != null && dep12 != null && <p className="text-[11px] text-base-content/60">On $100k the average 12-month bank CD leaves <b className="text-rose-300">{usd((bill - dep12) * 1000)}</b>/yr on the table vs a T-bill — and T-bill interest is state-tax-free.</p>}
          </div>
        </Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card icon={<Percent className="h-4 w-4 text-amber-400" />} title="Credit spreads by rating" subtitle={sp?.source} pad={false}>
          <table className="w-full text-xs"><tbody>
            {['AAA', 'AA', 'A', 'BBB', 'BB', 'B', 'CCC'].map(r => (
              <tr key={r} className="border-b border-white/[0.03]"><td className="px-3 py-1.5 font-medium">{r}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{sp?.by_rating[r]?.oas_bp != null ? `${sp.by_rating[r].oas_bp}bp` : '—'}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">{pct(sp?.by_rating[r]?.effective_yield_pct)}</td></tr>
            ))}
          </tbody></table>
          <div className="flex flex-wrap gap-3 px-3 py-2 text-[10px] text-base-content/50">{m.breakevens.map(b => <span key={b.tenor}>{tenorLabel(b.tenor)} breakeven {pct(b.breakeven_pct)}</span>)}</div>
        </Card>
        <Card title="Today's yield menu (pre-tax)" subtitle="Treasury = official curve; others estimated — see the Tax tab for after-tax" pad={false}>
          <div className="overflow-x-auto"><table className="w-full text-xs">
            <thead className="text-[10px] uppercase text-base-content/45"><tr>{['Tenor', 'UST', 'CD', 'Agency', 'Muni', 'AA', 'A', 'BBB', 'TIPS real'].map(h => <th key={h} className="px-2 py-1.5 text-right font-medium first:text-left">{h}</th>)}</tr></thead>
            <tbody>{m.yield_menu.map(r => (
              <tr key={r.tenor} className="border-t border-white/[0.03]"><td className="px-2 py-1 font-semibold">{tenorLabel(r.tenor)}</td>
                {[r.treasury, r.cd, r.agency, r.muni, r.corporate_aa, r.corporate_a, r.corporate_bbb, r.tips].map((v, i) => <td key={i} className="px-2 py-1 text-right tabular-nums">{num(v, 2)}</td>)}</tr>
            ))}</tbody>
          </table></div>
        </Card>
      </div>
      <BondCalculator />
      <Card icon={<Gavel className="h-4 w-4 text-sky-400" />} title="Recent Treasury auctions" subtitle="FiscalData auctions with today's FedInvest price and yield" pad={false}>
        <div className="overflow-x-auto"><table className="w-full text-xs">
          <thead className="text-[10px] uppercase text-base-content/45"><tr>{['Auction', 'CUSIP', 'Type', 'Term', 'Maturity', 'Coupon', 'Auction yld', 'Price', 'Yield now'].map(h => <th key={h} className="px-2 py-1.5 text-left font-medium">{h}</th>)}</tr></thead>
          <tbody>{m.recent_auctions.map(a => (
            <tr key={a.cusip} className="border-t border-white/[0.03]"><td className="px-2 py-1">{fmtDate(a.last_auction_date)}</td><td className="px-2 py-1 font-mono text-[10px]">{a.cusip}</td>
              <td className="px-2 py-1 uppercase">{a.type}</td><td className="px-2 py-1">{a.original_term ?? '—'}</td><td className="px-2 py-1">{fmtDate(a.maturity)}</td>
              <td className="px-2 py-1">{pct(a.coupon_pct, 3)}</td><td className="px-2 py-1">{pct(a.last_auction_yield_pct, 3)}</td><td className="px-2 py-1">{num(a.price, 3)}</td>
              <td className="px-2 py-1">{pct(a.ytm_pct, 3)}{a.type === 'tips' ? ' real' : ''}</td></tr>
          ))}</tbody>
        </table></div>
        <div className="flex items-center justify-between gap-2 px-4 py-3">
          <Note>Sources: Treasury.gov par curves, TreasuryDirect FedInvest, FiscalData, FRED (ICE BofA spreads, FDIC deposit rates, breakevens).</Note>
          <Link to="/market?section=debt" className="btn btn-ghost btn-xs whitespace-nowrap">Timing a bond ETF? Debt Radar →</Link>
        </div>
      </Card>
    </div>
  );
}
