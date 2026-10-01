import React from 'react';
import { ArrowRight, Lock, Shuffle } from 'lucide-react';
import type { BondPlanFunds } from '../../types';
import { Card, Note, Stat, num, pct, pnlClass, usd } from './bondUi';

const ACCT: Record<string, string> = { taxable: 'Taxable', ira: 'IRA', roth: 'Roth', '401k': '401(k)', '403b': '403(b)', hsa: 'HSA', '529': '529' };

// How the planner spends bond ETFs / mutual funds (which never mature) to fill the gap years.
export default function BondFundDrawdown({ funds, onLockIn }: {
  funds: BondPlanFunds;
  onLockIn?: (amount: number, firstYear: number, lastYear: number) => void;
}) {
  const b = funds.before, a = funds.after;
  const impact = funds.rate_impact_1pct;
  const lock = funds.lock_in;
  return (
    <Card icon={<Shuffle className="h-4 w-4 text-violet-400" />} title="Bond funds → gap years"
      subtitle="Bond ETFs and mutual funds never mature, so the plan sells them down only in years your bonds fall short — choosing which fund to sell in which year.">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <Stat label="Funded ratio" value={<span>{pct(b.funded_ratio_pct, 1)} <ArrowRight className="inline h-3 w-3" /> <span className="text-emerald-400">{pct(a.funded_ratio_pct, 1)}</span></span>}
          sub={a.shortfall_years.length ? `still short: ${a.shortfall_years.join(', ')}` : 'every goal year covered'} />
        <Stat label="Funds to sell" value={usd(funds.total_sold, { compact: true })} sub={`${funds.schedule.length} sale${funds.schedule.length === 1 ? '' : 's'} over ${new Set(funds.schedule.map(s => s.year)).size} years`} />
        <Stat label="Tax on those sales" value={<span className={funds.tax_paid < 0 ? 'text-emerald-400' : ''}>{usd(funds.tax_paid, { compact: true, sign: funds.tax_paid < 0 })}</span>}
          sub={funds.tax_paid < 0 ? 'losses harvested — tax saved' : 'gains + IRA withdrawals'} />
        <Stat label="Rate sensitivity" value={`±${usd(Math.abs(impact), { compact: true })}`}
          sub={impact > 0 ? 'per 1%: rates falling hurts' : impact < 0 ? 'per 1%: rates rising hurts' : 'duration-matched'}
          hint="How much the plan's fund sales gain/lose if rates move 1% today and stay there (duration vs years until each need)" />
        <Stat label="Funds left at end" value={usd(funds.remaining_value_end, { compact: true })} sub="after the last goal year" />
      </div>

      {funds.schedule.length > 0 ? (
        <div className="mt-4 overflow-x-auto">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-base-content/45">Sale schedule — {funds.method}</div>
          <table className="w-full text-xs">
            <thead className="border-y border-white/[0.05] text-[10px] uppercase tracking-wide text-base-content/45">
              <tr>{['Year', 'Sell', 'Account', 'Amount', 'Tax', 'You receive', 'Duration vs need', 'Why this fund'].map(h => <th key={h} className="px-2 py-1.5 text-left font-medium">{h}</th>)}</tr>
            </thead>
            <tbody>
              {funds.schedule.map((s, i) => (
                <tr key={`${s.year}-${s.holding_id}-${i}`} className="border-b border-white/[0.03] align-top">
                  <td className="px-2 py-1.5 font-semibold tabular-nums">{s.year}</td>
                  <td className="px-2 py-1.5"><span className="font-medium">{s.ticker ?? s.label}</span></td>
                  <td className="px-2 py-1.5 text-base-content/60">{ACCT[s.account_type] ?? s.account_type}</td>
                  <td className="px-2 py-1.5 tabular-nums">{usd(s.gross)}</td>
                  <td className={`px-2 py-1.5 tabular-nums ${s.tax < 0 ? 'text-emerald-400' : s.tax > 0 ? 'text-rose-300' : 'text-base-content/50'}`}>{usd(s.tax, { sign: s.tax < 0 })}</td>
                  <td className="px-2 py-1.5 font-semibold tabular-nums">{usd(s.net)}</td>
                  <td className="px-2 py-1.5 tabular-nums">
                    {num(s.duration, 1)}y vs {num(s.years_to_need, 1)}y
                    <div className={`text-[10px] ${s.mismatch_years <= 2 ? 'text-emerald-400/80' : s.mismatch_years <= 5 ? 'text-amber-300/80' : 'text-rose-300/80'}`}>
                      {s.mismatch_years <= 2 ? 'well matched' : `${num(s.mismatch_years, 1)}y apart`}
                    </div>
                  </td>
                  <td className="max-w-[320px] px-2 py-1.5 text-[10.5px] leading-snug text-base-content/55">{s.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-4 text-xs text-base-content/50">{b.shortfall_years.length ? 'Your funds can’t help with these gaps.' : 'No gaps — your bonds and fund distributions already cover every goal year, so nothing needs to be sold.'}</p>
      )}

      <div className="mt-4 overflow-x-auto">
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-base-content/45">Your bond funds</div>
        <table className="w-full text-xs">
          <thead className="text-[10px] uppercase tracking-wide text-base-content/45">
            <tr>{['Fund', 'Account', 'Value', 'Yield', 'Duration', 'Gain', 'Tax if sold', 'Left at end'].map(h => <th key={h} className="px-2 py-1 text-left font-medium">{h}</th>)}</tr>
          </thead>
          <tbody>
            {funds.available.map(f => (
              <tr key={f.id} className="border-t border-white/[0.03]">
                <td className="px-2 py-1 font-medium">{f.ticker ?? f.label}</td>
                <td className="px-2 py-1 text-base-content/60">{ACCT[f.account_type] ?? f.account_type}</td>
                <td className="px-2 py-1 tabular-nums">{usd(f.value, { compact: true })}</td>
                <td className="px-2 py-1 tabular-nums">{pct(f.yield_pct)}
                  {f.accumulates
                    ? <div className="text-[9.5px] text-teal-300/70" title="Pays nothing until sold — the planner grows it in price and taxes the gain when sold">builds in price</div>
                    : f.cash_yield_pct != null && Math.abs(f.cash_yield_pct - f.yield_pct) >= 0.05
                      ? <div className="text-[9.5px] text-base-content/40" title="Cash it distributes; the rest builds up in the price">{pct(f.cash_yield_pct)} cash</div> : null}
                </td>
                <td className="px-2 py-1 tabular-nums" title={f.duration_source ?? ''}>{num(f.duration, 1)}y{f.duration_estimated ? <span className="text-amber-300/80"> ~</span> : ''}</td>
                <td className={`px-2 py-1 tabular-nums ${pnlClass(f.gain_pct)}`}>{f.cost_known ? pct(f.gain_pct, 1, true) : <span className="text-base-content/40">no cost basis</span>}</td>
                <td className="px-2 py-1 tabular-nums">{pct(f.sale_tax_pct, 1)}</td>
                <td className="px-2 py-1 tabular-nums text-base-content/70">{usd(f.remaining_end, { compact: true })}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {lock && (
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-sky-500/25 bg-sky-500/5 p-3">
          <Lock className="h-4 w-4 shrink-0 text-sky-300" />
          <p className="flex-1 text-[11.5px] leading-relaxed text-base-content/70">{lock.note}</p>
          {onLockIn && (
            <button className="btn btn-xs btn-outline btn-info"
              onClick={() => onLockIn(Math.ceil(lock.treasury_cost_today / 1000) * 1000, Math.min(...lock.years), Math.max(...lock.years))}>
              Build that Treasury ladder
            </button>
          )}
        </div>
      )}
      <div className="mt-3 space-y-0.5">
        <Note>How a fund is picked for a gap year: the lowest total of (1) rate risk — 1% per year between the fund's duration and when the money is needed (a fund held about its duration locks in its yield), (2) tax you trigger by selling now (a loss saves tax), and (3) tax-sheltered growth you give up by spending IRA/Roth money early. Solved across all years at once, selling only in gap years and never more than that year needs.</Note>
        <Note>Durations are measured from each fund's last year of daily returns against Treasury yield moves (vendor data for bond funds is unreliable). A “~” means low confidence. Fund prices are assumed flat — total return ≈ distribution yield.</Note>
      </div>
    </Card>
  );
}
