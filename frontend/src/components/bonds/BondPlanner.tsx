import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { CalendarArrowUp, Flag, Hourglass, Loader2, Plus, Save, Shuffle, Target, Trash2 } from 'lucide-react';
import { fetchBondPlan, saveBondProfile } from '../../api';
import type { BondFilters, BondGoal, BondLadderParams, BondPlan, BondProfile, BondWithdrawalMode } from '../../types';
import { AXIS, Card, Chart, Empty, ErrorBox, LEGEND, Loading, Note, Seg, Stat, TOOLTIP, inputCls, pct, usd } from './bondUi';
import BondFundDrawdown from './BondFundDrawdown';
import BondReinvestPlan, { REINVEST_COLOR } from './BondReinvestPlan';

const TEMPLATES: { label: string; goal: BondGoal }[] = [
  { label: 'Retirement income floor', goal: { name: 'Retirement income', year: new Date().getFullYear() + 5, end_year: new Date().getFullYear() + 25, amount: 40000, inflation_adjusted: true } },
  { label: 'College tuition', goal: { name: 'College', year: new Date().getFullYear() + 8, end_year: new Date().getFullYear() + 11, amount: 35000, inflation_adjusted: true } },
  { label: 'Home down payment', goal: { name: 'Home down payment', year: new Date().getFullYear() + 3, amount: 100000, inflation_adjusted: false } },
];

export default function BondPlanner({ profile, filters, onChanged, onBuildLadder }: {
  profile: BondProfile;
  filters?: BondFilters;
  onChanged: () => void;
  onBuildLadder: (prefill: Partial<BondLadderParams>, tips: boolean) => void;
}) {
  const [goals, setGoals] = useState<BondGoal[]>(profile.goals ?? []);
  const [plan, setPlan] = useState<BondPlan | null>(null);
  const [afterTax, setAfterTax] = useState(true);
  const [useFunds, setUseFunds] = useState(true);
  const [reinvest, setReinvest] = useState(true);
  // profile.settings is the source of truth for birth year / withdrawal mode; kept locally so a goals save
  // never overwrites a just-saved birth year with the page's stale copy
  const [settings, setSettings] = useState<Record<string, unknown>>(profile.settings ?? {});
  const savedBirth = settings.birth_year ? String(settings.birth_year) : '';
  const [birthYear, setBirthYear] = useState(savedBirth);
  const [mode, setMode] = useState<BondWithdrawalMode>(
    (settings.withdrawal_mode as BondWithdrawalMode) || (settings.birth_year ? 'age' : 'after'));
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  useEffect(() => { setGoals(profile.goals ?? []); }, [profile.goals]);
  const load = useCallback(() => {
    setLoading(true); setErr(null);
    fetchBondPlan({ afterTax, useFunds, reinvest, withdrawalMode: mode, filters })
      .then(setPlan).catch(e => setErr(e instanceof Error ? e.message : 'Failed')).finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [afterTax, useFunds, reinvest, mode, JSON.stringify(filters ?? null), JSON.stringify(settings)]);
  useEffect(load, [load, profile]);

  const persistSettings = async (patch: Record<string, unknown>) => {
    const next = { ...settings, ...patch };
    try { await saveBondProfile({ ...profile, goals: profile.goals, settings: next }); setSettings(next); }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed'); }
  };
  const chooseMode = (m: BondWithdrawalMode) => { setMode(m); persistSettings({ withdrawal_mode: m }); };
  const commitBirthYear = () => {
    const y = Number(birthYear);
    if (birthYear === savedBirth || (birthYear && (!Number.isInteger(y) || y < 1920 || y > now))) return;
    persistSettings({ birth_year: birthYear ? y : null, withdrawal_mode: birthYear ? 'age' : mode });
    if (birthYear) setMode('age');
  };

  const update = (i: number, patch: Partial<BondGoal>) => { setGoals(g => g.map((x, j) => (j === i ? { ...x, ...patch } : x))); setDirty(true); };
  const save = async () => {
    setSaving(true);
    try { await saveBondProfile({ ...profile, goals, settings }); setDirty(false); onChanged(); } catch (e) { setErr(e instanceof Error ? e.message : 'Save failed'); } finally { setSaving(false); }
  };

  const chart = useMemo(() => {
    if (!plan) return null;
    const ys = plan.years.filter(y => y.need > 0 || y.inflow > 0 || (y.reinvest_out ?? 0) > 0);
    // attribute each year's covered amount: the Treasury set aside for THIS year first, then fund sales (the plan
    // sizes them to what's still missing — together they never exceed the need), then bonds & cash
    const reinvPart = (y: typeof ys[number]) => Math.min(y.reinvest_in ?? 0, y.covered);
    const fundPart = (y: typeof ys[number]) => Math.min(y.fund_sales ?? 0, Math.max(0, y.covered - reinvPart(y)));
    const early = plan.withdrawal?.early_until != null && plan.withdrawal.applies
      ? ys.filter(y => y.early_withdrawal).map(y => String(y.year)) : [];
    return {
      tooltip: { ...TOOLTIP, trigger: 'axis', valueFormatter: (v: number) => usd(v) },
      legend: LEGEND, grid: { left: 8, right: 8, top: 30, bottom: 4, containLabel: true },
      xAxis: { type: 'category', data: ys.map(y => String(y.year)), ...AXIS },
      yAxis: { type: 'value', ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => usd(v, { compact: true }) } },
      series: [
        { name: 'Covered by bonds & cash', type: 'bar', stack: 'n', data: ys.map(y => Math.max(0, y.covered - fundPart(y) - reinvPart(y))), itemStyle: { color: '#34d399' },
          markArea: early.length ? { silent: true, itemStyle: { color: 'rgba(251,191,36,0.07)' },
            label: { show: true, position: 'insideTop', color: '#fbbf24', fontSize: 9, formatter: 'before 59½' },
            data: [[{ xAxis: early[0] }, { xAxis: early[early.length - 1] }]] } : undefined },
        { name: 'Covered by reinvested surplus', type: 'bar', stack: 'n', data: ys.map(reinvPart), itemStyle: { color: REINVEST_COLOR } },
        { name: 'Covered by selling funds', type: 'bar', stack: 'n', data: ys.map(fundPart), itemStyle: { color: '#a78bfa' } },
        { name: 'Shortfall', type: 'bar', stack: 'n', data: ys.map(y => y.shortfall), itemStyle: { color: '#f87171' } },
        { name: 'Set aside for a later year', type: 'bar', stack: 'aside', data: ys.map(y => y.reinvest_out ?? 0), itemStyle: { color: REINVEST_COLOR, opacity: 0.35, borderColor: REINVEST_COLOR, borderType: 'dashed', borderWidth: 1 } },
        { name: `Bond & fund income${afterTax ? ' (after tax)' : ''}`, type: 'line', data: ys.map(y => y.inflow), itemStyle: { color: '#60a5fa' }, symbol: 'circle', symbolSize: 4 },
        { name: 'Surplus carried (T-bills)', type: 'line', data: ys.map(y => y.surplus_carried), itemStyle: { color: '#94a3b8' }, symbol: 'none', lineStyle: { type: 'dashed' } },
      ],
    };
  }, [plan, afterTax]);

  const gapYears = plan?.shortfall_years ?? [];
  const now = new Date().getFullYear();
  const realGoals = goals.some(g => g.inflation_adjusted);

  return (
    <div className="space-y-4">
      <Card icon={<Flag className="h-4 w-4 text-primary" />} title="Your goals"
        subtitle="One-time needs (a house) or recurring income (retirement, tuition). Inflation-adjusted goals grow with CPI and are best matched with TIPS."
        right={<div className="flex gap-2">
          <div className="dropdown dropdown-end">
            <button tabIndex={0} className="btn btn-sm btn-ghost"><Plus className="h-3.5 w-3.5" /> Template</button>
            <ul tabIndex={0} className="dropdown-content menu z-[50] w-56 rounded-xl border border-white/10 bg-base-100 p-1 shadow-xl">
              {TEMPLATES.map(t => <li key={t.label}><button onClick={() => { setGoals(g => [...g, { ...t.goal }]); setDirty(true); }}>{t.label}</button></li>)}
            </ul>
          </div>
          <button className="btn btn-sm btn-outline" onClick={() => { setGoals(g => [...g, { name: 'New goal', year: now + 5, amount: 25000, inflation_adjusted: false }]); setDirty(true); }}><Plus className="h-3.5 w-3.5" /> Goal</button>
          <button className="btn btn-sm btn-primary" disabled={!dirty || saving} onClick={save}>{saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save goals</button>
        </div>}>
        {goals.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-[10px] uppercase text-base-content/45">
                <tr>{['Goal', 'From year', 'Through year', 'Amount / yr ($)', "Today's $ (inflates)", ''].map(h => <th key={h} className="px-2 py-1.5 text-left font-medium">{h}</th>)}</tr>
              </thead>
              <tbody>
                {goals.map((g, i) => (
                  <tr key={i} className="border-t border-white/[0.03]">
                    <td className="px-2 py-1"><input className={inputCls} value={g.name} onChange={e => update(i, { name: e.target.value })} /></td>
                    <td className="w-28 px-2 py-1"><input type="number" className={inputCls} value={g.year} onChange={e => update(i, { year: Number(e.target.value) })} /></td>
                    <td className="w-28 px-2 py-1"><input type="number" className={inputCls} value={g.end_year ?? ''} placeholder="one-time" onChange={e => update(i, { end_year: e.target.value ? Number(e.target.value) : null })} /></td>
                    <td className="w-36 px-2 py-1"><input type="number" className={inputCls} value={g.amount} onChange={e => update(i, { amount: Number(e.target.value) })} /></td>
                    <td className="px-2 py-1"><input type="checkbox" className="toggle toggle-xs toggle-success" checked={g.inflation_adjusted} onChange={e => update(i, { inflation_adjusted: e.target.checked })} /></td>
                    <td className="px-2 py-1 text-right"><button className="btn btn-ghost btn-xs text-rose-400" onClick={() => { setGoals(x => x.filter((_, j) => j !== i)); setDirty(true); }}><Trash2 className="h-3.5 w-3.5" /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty icon={<Target className="h-8 w-8" />} title="No goals yet" body="Add what your bonds need to pay for — we'll check whether your cash flows cover it, year by year, and price the gap." />}
      </Card>

      {err && <ErrorBox message={err} onRetry={load} />}
      {loading && !plan ? <Loading label="Matching cash flows to goals…" /> : plan && goals.length > 0 && !dirty && (
        <>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-2xl border border-white/[0.06] bg-base-100/60 px-4 py-2.5 text-[11px] text-base-content/70">
            <span className="font-semibold text-base-content/60">Plan options</span>
            <label className="flex items-center gap-1.5" title="Off: surplus still earns interest, rolled in 1-year T-bills. On: it buys Treasuries maturing in the years that will spend it — locking today's longer rates. Same money, slightly higher rate.">
              <CalendarArrowUp className="h-3.5 w-3.5" style={{ color: REINVEST_COLOR }} />
              <input type="checkbox" className="toggle toggle-xs" style={reinvest ? { backgroundColor: REINVEST_COLOR, borderColor: REINVEST_COLOR } : undefined}
                checked={reinvest} onChange={e => setReinvest(e.target.checked)} />
              Lock surplus into Treasuries (vs T-bills) <span className="rounded bg-emerald-500/15 px-1 text-[9px] font-semibold uppercase text-emerald-300">recommended</span>
            </label>
            {plan.funds && (
              <label className="flex items-center gap-1.5" title="Sell bond ETFs / mutual funds in years your bonds fall short">
                <Shuffle className="h-3.5 w-3.5 text-violet-400" />
                <input type="checkbox" className="toggle toggle-xs toggle-primary" checked={useFunds} onChange={e => setUseFunds(e.target.checked)} />
                Sell bond funds for gap years
              </label>
            )}
            {plan.withdrawal.applies && (
              <span className="flex flex-wrap items-center gap-1.5" title="IRA/401(k): 10% penalty before 59½ on top of income tax. Roth: earnings taxed + 10% before 59½; contributions free.">
                <Hourglass className="h-3.5 w-3.5 text-amber-400" /> IRA / 401(k) / Roth withdrawals:
                <Seg value={mode} onChange={v => chooseMode(v as BondWithdrawalMode)} options={[
                  { value: 'age', label: 'By my age' }, { value: 'before', label: 'Before 59½' }, { value: 'after', label: 'After 59½' }]} />
                {mode === 'age' && (
                  <input className="input input-xs input-bordered w-24 bg-base-200/60" inputMode="numeric" placeholder="birth year"
                    value={birthYear} onChange={e => setBirthYear(e.target.value.replace(/\D/g, '').slice(0, 4))}
                    onBlur={commitBirthYear} onKeyDown={e => e.key === 'Enter' && commitBirthYear()} />
                )}
              </span>
            )}
            <span className="ml-auto"><Seg value={afterTax ? 'at' : 'pre'} onChange={v => setAfterTax(v === 'at')} options={[{ value: 'at', label: 'After tax' }, { value: 'pre', label: 'Pre-tax' }]} /></span>
          </div>
          <div className={`grid grid-cols-2 gap-2 ${plan.withdrawal.applies ? 'md:grid-cols-5' : 'md:grid-cols-4'}`}>
            <Stat label="Funded ratio" value={pct(plan.funded_ratio_pct, 1)}
              sub={`${usd(plan.pv_covered, { compact: true })} of ${usd(plan.pv_needs, { compact: true })} covered${afterTax ? ' after tax' : ''} (today's $)`}
              hint="Present value of the goal years your bonds & funds pay for, ÷ present value of all goal years (both at Treasury rates)"
              accent={(plan.funded_ratio_pct ?? 0) >= 99 ? 'text-emerald-400' : (plan.funded_ratio_pct ?? 0) >= 80 ? 'text-amber-400' : 'text-rose-400'} />
            <Stat label="PV of goals" value={usd(plan.pv_needs, { compact: true })} sub={`inflation ${plan.inflation_source}`}
              hint="What every goal year is worth today, at Treasury rates — the spending itself, after tax" />
            <Stat label="Shortfall years" value={gapYears.length ? `${gapYears[0]}–${gapYears[gapYears.length - 1]}` : 'none'} sub={`${gapYears.length} year(s)`} />
            <Stat label="Cost to fully fund" value={usd(plan.cost_to_fund_shortfalls.total, { compact: true })}
              sub={<>{usd(plan.cost_to_fund_shortfalls.treasury, { compact: true })} Treasuries · {usd(plan.cost_to_fund_shortfalls.tips, { compact: true })} TIPS
                {afterTax && plan.cost_to_fund_shortfalls.pre_tax_total != null && plan.cost_to_fund_shortfalls.total > 0 && (
                  <div>in a taxable account · {usd(plan.cost_to_fund_shortfalls.pre_tax_total, { compact: true })} in a Roth</div>
                )}</>}
              hint={plan.cost_to_fund_shortfalls.basis ? `Today's price of zero-coupon Treasuries / TIPS paying each shortfall year — ${plan.cost_to_fund_shortfalls.basis}` : undefined} />
            {plan.withdrawal.applies && (
              <Stat label="Retirement-account access" value={plan.withdrawal.penalty_free_from ? `penalty-free ${plan.withdrawal.penalty_free_from}` : (plan.withdrawal.mode === 'age' ? 'add birth year' : 'no penalty')}
                sub={plan.withdrawal.early_cost_total ? `early-withdrawal cost ${usd(plan.withdrawal.early_cost_total, { compact: true })}` : plan.withdrawal.mode === 'age' && !plan.withdrawal.birth_year ? 'treated as after 59½' : 'no early withdrawals'}
                accent={plan.withdrawal.early_cost_total ? 'text-amber-400' : undefined} />
            )}
          </div>
          <Card title="Needs vs bond cash, year by year"
            subtitle={reinvest ? 'Teal = surplus from an earlier year, invested in a Treasury that matures this year (dashed = set aside this year). Each year spends its own bond cash first.'
              : 'Surpluses carry forward in T-bills (1-year Treasury yield, after tax)'}>
            {chart && <Chart option={chart} height={300} />}
            {afterTax && (plan.taxes_total ?? 0) > 0 && gapYears.length > 0 && (
              <div className="mt-3 rounded-xl border border-amber-500/20 bg-amber-500/5 p-3 text-[11.5px] leading-relaxed text-amber-100/85">
                <b>Why there's a gap{(plan.book_value ?? 0) > plan.pv_needs ? <> when your bonds ({usd(plan.book_value, { compact: true })}) are worth more than the goal ({usd(plan.pv_needs, { compact: true })})</> : ''}:</b>{' '}
                market values are <i>before</i> tax, while the goal is money you spend <i>after</i> tax. Over the plan, taxes take{' '}
                <b>{usd(plan.taxes_total, { compact: true })}</b> ({usd(plan.taxes_pv, { compact: true })} in today's dollars) — interest, IRA/401(k)
                withdrawals taxed as income, gains — so your book covers {usd(plan.pv_covered, { compact: true })} of the {usd(plan.pv_needs, { compact: true })}.
                {' '}Switch to <b>Pre-tax</b> to see it without taxes; setting lower <b>after-retirement tax rates</b> (Tax tab) shrinks the gap.
              </div>
            )}
            {gapYears.length > 0 && (
              <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-rose-500/20 bg-rose-500/5 p-3">
                <span className="text-xs text-rose-200">Close the gap: a ladder maturing {gapYears[0]}–{gapYears[gapYears.length - 1]} costs about {usd(plan.cost_to_fund_shortfalls.total)} today.</span>
                <button className="btn btn-xs btn-primary" onClick={() => onBuildLadder({
                  amount: Math.ceil(plan.cost_to_fund_shortfalls.total / 1000) * 1000,
                  start_years: Math.max(0.5, gapYears[0] - now), end_years: Math.max(1, gapYears[gapYears.length - 1] - now), instrument: 'treasury', weighting: 'level_income',
                }, false)}>Build Treasury ladder</button>
                {realGoals && <button className="btn btn-xs btn-success btn-outline" onClick={() => onBuildLadder({ start_years: gapYears[0] - now, end_years: gapYears[gapYears.length - 1] - now }, true)}>Build TIPS ladder</button>}
              </div>
            )}
            <div className="mt-2 space-y-0.5">{plan.notes.map((n, i) => <Note key={i}>{n}</Note>)}</div>
          </Card>
          {reinvest ? <BondReinvestPlan data={plan.reinvest} /> : (plan.reinvest.after.funded_ratio_pct ?? 0) > (plan.reinvest.before.funded_ratio_pct ?? 0) && (
            <p className="text-center text-xs text-base-content/50">Surplus is rolling in T-bills. Turn on “Lock surplus into Treasuries” — funded ratio would go from {pct(plan.reinvest.before.funded_ratio_pct, 1)} to {pct(plan.reinvest.after.funded_ratio_pct, 1)}.</p>
          )}
          {plan.funds && useFunds && (
            <BondFundDrawdown funds={plan.funds}
              onLockIn={(amount, y0, y1) => onBuildLadder({ amount, start_years: Math.max(0.5, y0 - now), end_years: Math.max(1, y1 - now), instrument: 'treasury', weighting: 'level_income' }, false)} />
          )}
          {plan.funds && !useFunds && (
            <p className="text-center text-xs text-base-content/50">Holding your bond funds (distributions only). Turn on “Sell bond funds for gap years” to see which fund to sell in which year — funded ratio would go from {pct(plan.funds.before.funded_ratio_pct, 1)} to {pct(plan.funds.after.funded_ratio_pct, 1)}.</p>
          )}
        </>
      )}
      {dirty && goals.length > 0 && <p className="text-center text-xs text-amber-300/80">Save your goals to recompute the plan.</p>}
    </div>
  );
}
