import React, { useMemo } from 'react';
import { CalendarClock, Lightbulb, PieChart, TrendingUp } from 'lucide-react';
import type { BondPortfolio } from '../../types';
import {
  AXIS, CREDIT_COLORS, Card, Chart, Donut, FLOW_COLORS, KindBadge, LABEL_COLOR, LEGEND, SeverityBadge, Stat, TOOLTIP,
  fmtDate, num, pct, pnlClass, usd,
  YieldBreakdown,
} from './bondUi';

export default function BondOverview({ data, onGoto }: { data: BondPortfolio; onGoto: (tab: string) => void }) {
  const s = data.summary;
  const cf = data.cash_flow;

  const monthly = useMemo(() => {
    const months = cf.monthly.slice(0, 12);
    return {
      tooltip: { ...TOOLTIP, trigger: 'axis', valueFormatter: (v: number) => usd(v) },
      legend: LEGEND,
      grid: { left: 8, right: 8, top: 28, bottom: 4, containLabel: true },
      xAxis: { type: 'category', data: months.map(m => m.month.slice(2)), ...AXIS },
      yAxis: { type: 'value', ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => usd(v, { compact: true }) } },
      series: [
        { name: 'Coupons', type: 'bar', stack: 'cf', data: months.map(m => m.coupon), itemStyle: { color: FLOW_COLORS.coupon } },
        { name: 'Distributions', type: 'bar', stack: 'cf', data: months.map(m => m.distribution), itemStyle: { color: FLOW_COLORS.distribution } },
        { name: 'Principal', type: 'bar', stack: 'cf', data: months.map(m => m.principal), itemStyle: { color: FLOW_COLORS.principal } },
      ],
    };
  }, [cf.monthly]);

  const upcoming = cf.next_12m.events.slice(0, 10);
  const recs = data.recommendations.slice(0, 5);
  const shock = (bpv: number) => data.scenarios.parallel.find(p => p.shift_bp === bpv);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
        <Stat label="Market value" value={usd(s.market_value, { compact: true })} sub={`${s.positions} positions${s.watchlist ? ` · ${s.watchlist} watching` : ''}`} />
        <Stat label="Unrealized P&L" value={<span className={pnlClass(s.unrealized_pnl)}>{usd(s.unrealized_pnl, { compact: true, sign: true })}</span>}
          sub={`cost ${usd(s.cost_basis, { compact: true })}`} />
        <Stat label="Total yield" value={pct(s.total_yield_pct ?? s.ytw_pct)}
          sub={<YieldBreakdown parts={s.yield_parts} compact />}
          hint="What the book earns a year if held to maturity at today's prices: coupons & distributions + the gain from discount bonds paying back 100 (and BOXX-type funds growing in price) + TIPS inflation. Funds use their estimated yield to maturity, or yours." />
        <Stat label="After-tax yield" value={pct(s.after_tax_yield_pct)} sub={`TEY ${pct(s.tey_pct)}`} accent="text-emerald-400"
          hint="What you keep after your federal/state rates; TEY = the fully-taxable yield that nets the same" />
        <Stat label="Annual income" value={usd(s.annual_income, { compact: true })}
          sub={<>{usd(s.annual_income_after_tax, { compact: true })} after tax{s.annual_total_return ? <> · {usd(s.annual_total_return, { compact: true })} total/yr</> : null}</>}
          hint="Cash income (coupons + distributions). Total/yr adds what builds up toward maturity (discounts pulling to par, accumulating funds)." />
        <Stat label="Duration" value={`${num(s.eff_duration, 2)}y`} sub={`avg maturity ${num(s.years_to_maturity, 1)}y`} hint="Effective duration to worst — % price change per 1% rate move" />
        <Stat label="DV01" value={usd(s.dv01, { cents: false })} sub="$ per 0.01% rate move" hint="Sum of every position's dollar value of a basis point" />
        <Stat label="Rates +1%" value={<span className="text-rose-400">{usd(shock(100)?.pnl, { compact: true })}</span>}
          sub={`−1%: ${usd(shock(-100)?.pnl, { compact: true, sign: true })}`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2" icon={<CalendarClock className="h-4 w-4 text-primary" />} title="Next 12 months of cash"
          subtitle={`${usd(cf.next_12m.income)} income (${usd(cf.next_12m.income_after_tax)} after tax) + ${usd(cf.next_12m.principal)} principal back`}
          right={<button className="btn btn-ghost btn-xs" onClick={() => onGoto('cashflow')}>Full projection →</button>}>
          <Chart option={monthly} height={240} />
        </Card>
        <Card icon={<Lightbulb className="h-4 w-4 text-warning" />} title="What to do next"
          subtitle="Deterministic checks on your book — every number is computed"
          right={<button className="btn btn-ghost btn-xs" onClick={() => onGoto('insights')}>All ({data.recommendations.length}) →</button>}>
          {recs.length ? (
            <ul className="space-y-2">
              {recs.map(r => (
                <li key={r.id} className="rounded-xl border border-white/[0.05] bg-base-200/40 p-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <SeverityBadge severity={r.severity} />
                    {r.impact_usd ? <span className="text-[10px] tabular-nums text-base-content/50">~{usd(r.impact_usd, { compact: true })}{r.category === 'risk' ? '' : '/yr'}</span> : null}
                  </div>
                  <div className="mt-1 text-xs font-medium leading-snug text-base-content/85">{r.title}</div>
                </li>
              ))}
            </ul>
          ) : <p className="py-6 text-center text-xs text-base-content/45">Nothing flagged — your book looks clean.</p>}
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Card icon={<PieChart className="h-4 w-4 text-secondary" />} title="By type"><Donut data={data.allocation.by_kind} colors={LABEL_COLOR} /></Card>
        <Card title="By credit quality" subtitle="Funds split by their reported credit mix"><Donut data={data.allocation.by_credit} colors={CREDIT_COLORS} /></Card>
        <Card title="By maturity"><Donut data={data.allocation.by_maturity} /></Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card icon={<CalendarClock className="h-4 w-4 text-sky-400" />} title="Payment calendar" subtitle="Your next scheduled coupons, distributions and maturities">
          {upcoming.length ? (
            <ul className="divide-y divide-white/[0.04]">
              {upcoming.map((e, i) => (
                <li key={i} className="flex items-center justify-between gap-3 py-1.5 text-xs">
                  <span className="w-24 shrink-0 tabular-nums text-base-content/55">{fmtDate(e.date)}</span>
                  <span className="flex min-w-0 flex-1 items-center gap-2"><KindBadge kind={e.kind} /><span className="truncate text-base-content/80">{e.label}</span></span>
                  <span className="text-[10px] uppercase text-base-content/40">{e.type}{e.projected ? '*' : ''}</span>
                  <span className="w-20 text-right font-semibold tabular-nums">{usd(e.amount)}</span>
                </li>
              ))}
            </ul>
          ) : <p className="py-6 text-center text-xs text-base-content/45">No payments in the next year.</p>}
          {upcoming.some(e => e.projected) && <p className="mt-2 text-[10px] text-base-content/40">* fund distributions projected at trailing yield</p>}
        </Card>
        <Card icon={<TrendingUp className="h-4 w-4 text-rose-400" />} title="If rates move today" subtitle="Instant parallel shift — full repricing (to worst)"
          right={<button className="btn btn-ghost btn-xs" onClick={() => onGoto('risk')}>Risk lab →</button>}>
          <div className="grid grid-cols-4 gap-2">
            {data.scenarios.parallel.map(p => (
              <div key={p.shift_bp} className="rounded-lg bg-base-200/50 px-2 py-1.5 text-center">
                <div className="text-[10px] text-base-content/45">{p.shift_bp > 0 ? '+' : ''}{p.shift_bp}bp</div>
                <div className={`text-xs font-semibold tabular-nums ${pnlClass(p.pnl)}`}>{usd(p.pnl, { compact: true, sign: true })}</div>
                <div className={`text-[10px] tabular-nums ${pnlClass(p.pnl)}`}>{pct(p.pnl_pct, 1, true)}</div>
              </div>
            ))}
          </div>
          <p className="mt-2 text-[10.5px] text-base-content/45">Prices as of {data.fedinvest_as_of ? `FedInvest ${data.fedinvest_as_of}` : data.as_of}; {s.estimated_marks_pct ? `${num(s.estimated_marks_pct, 0)}% of value uses estimated marks.` : 'all marks are market or manual.'}</p>
        </Card>
      </div>
    </div>
  );
}
