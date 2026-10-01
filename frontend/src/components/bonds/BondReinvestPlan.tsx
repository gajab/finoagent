import React from 'react';
import { ArrowRight, CalendarArrowUp } from 'lucide-react';
import type { BondPlanReinvest } from '../../types';
import { Card, Note, Stat, pct, usd } from './bondUi';

export const REINVEST_COLOR = '#2dd4bf';

// Surplus years (more cash from maturities + interest than the goals need) → Treasuries maturing in the later
// years that will spend it. Same optimizer as the fund sell-down; only where it beats rolling T-bills.
export default function BondReinvestPlan({ data }: { data: BondPlanReinvest }) {
  const b = data.before, a = data.after;
  const plan = data.plan;
  const rates = plan.map(r => r.rate_pct).filter(r => r != null);
  return (
    <Card icon={<CalendarArrowUp className="h-4 w-4" style={{ color: REINVEST_COLOR }} />} title="Surplus years → future needs"
      subtitle="Some years your bonds pay out more than you need. Instead of letting it sit in T-bills, buy Treasuries that mature exactly in the later years that will spend it.">
      {plan.length ? (
        <>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
            <Stat label="Funded ratio" value={<span>{pct(b.funded_ratio_pct, 1)} <ArrowRight className="inline h-3 w-3" /> <span style={{ color: REINVEST_COLOR }}>{pct(a.funded_ratio_pct, 1)}</span></span>}
              sub={a.shortfall_years.length ? `still short: ${a.shortfall_years.join(', ')}` : 'every goal year covered'} />
            {b.shortfall_total != null && a.shortfall_total != null && (
              <Stat label="Total shortfall" value={<span>{usd(b.shortfall_total, { compact: true })} <ArrowRight className="inline h-3 w-3" /> <span style={{ color: REINVEST_COLOR }}>{usd(a.shortfall_total, { compact: true })}</span></span>}
                sub={`${usd(a.shortfall_total - b.shortfall_total, { sign: true })} · needs left unpaid, all years`} />
            )}
            <Stat label="Set aside" value={usd(data.total_set_aside, { compact: true })} sub={`${plan.length} purchase${plan.length === 1 ? '' : 's'}`} />
            <Stat label="Arrives in gap years" value={usd(data.total_arriving, { compact: true })} sub="principal + interest" />
            <Stat label="Extra vs T-bills" value={<span className="text-emerald-400">{usd(data.extra_vs_tbills, { compact: true, sign: true })}</span>}
              sub={`T-bills ≈ ${pct(data.tbill_rate_pct)} after tax`} />
          </div>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="border-y border-white/[0.05] text-[10px] uppercase tracking-wide text-base-content/45">
                <tr>{['When', 'Set aside', 'Buy', 'Est. yield', 'Arrives', 'Pays for', 'vs T-bills'].map(h => <th key={h} className="px-2 py-1.5 text-left font-medium">{h}</th>)}</tr>
              </thead>
              <tbody>
                {plan.map((r, i) => (
                  <tr key={i} className="border-b border-white/[0.03]">
                    <td className="px-2 py-1.5 font-semibold tabular-nums">{r.from_year}</td>
                    <td className="px-2 py-1.5 tabular-nums">{usd(r.amount)}</td>
                    <td className="px-2 py-1.5">
                      <span className="rounded-md px-1.5 py-0.5 text-[10px] font-semibold" style={{ color: REINVEST_COLOR, backgroundColor: `${REINVEST_COLOR}1f`, border: `1px solid ${REINVEST_COLOR}55` }}>
                        Treasury · {r.tenor}y
                      </span>
                      <span className="ml-1.5 text-base-content/50">maturing {r.to_year}</span>
                    </td>
                    <td className="px-2 py-1.5 tabular-nums">{pct(r.rate_pct)}</td>
                    <td className="px-2 py-1.5 font-semibold tabular-nums">{usd(r.value_at_maturity)}</td>
                    <td className="px-2 py-1.5 tabular-nums">{r.to_year}</td>
                    <td className="px-2 py-1.5 tabular-nums text-emerald-400">{usd(r.vs_tbills, { sign: true })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="text-xs text-base-content/50">
          {b.shortfall_years.length
            ? 'No year has bond cash left over before a later need — nothing to move forward.'
            : 'No later year needs money, so there is nothing to invest surplus for.'}
        </p>
      )}
      <div className="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] leading-relaxed text-amber-100/80">
        <b>What this toggle changes — and what it can't.</b> With it off your surplus is <b>not idle</b>: it rolls in 1-year
        T-bills at {pct(data.tbill_rate_pct)} after tax (the grey dashed line). On, the same {usd(data.total_set_aside, { compact: true })} buys
        Treasuries maturing in the years that spend it{rates.length ? ` at ${pct(Math.min(...rates))}–${pct(Math.max(...rates))} after tax` : ''}.
        Same money, a slightly higher rate: <b>{usd(data.extra_vs_tbills, { sign: true })}</b> in total — the bars move from green to teal but
        barely grow.
        {a.shortfall_years.length > 0 && <> Covering another whole year takes new money, not a better rate: {a.shortfall_years.length > 1
          ? `${a.shortfall_years[0]}–${a.shortfall_years[a.shortfall_years.length - 1]}` : a.shortfall_years[0]} need about <b>{usd(a.cost_to_fund_shortfalls?.total, { compact: true })}</b> more
          today (or lower spending, or earlier access to retirement money).</>}
      </div>
      <div className="mt-3 space-y-0.5">
        <Note>Yields: {data.rate_basis}. You buy each Treasury in the year shown, so the actual yield then will differ — revisit the plan each year.</Note>
        <Note>Planned only where it beats rolling T-bills and never at the expense of a nearer goal; a Treasury held to maturity has no price risk for the year it pays for.</Note>
      </div>
    </Card>
  );
}
