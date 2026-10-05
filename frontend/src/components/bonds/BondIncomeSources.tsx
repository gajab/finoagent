import React, { useEffect, useMemo, useState } from 'react';
import { HandCoins, Loader2, Plus, Save, Trash2, UserPlus } from 'lucide-react';
import type { BondIncomeSource, BondPlan, BondPlanIncomePerson, BondSSPerson, BondSocialSecurity } from '../../types';
import { Card, Field, Note, Stat, inputCls, pct, selectCls, usd } from './bondUi';

export const INCOME_COLOR = '#fbbf24';
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const numOrNull = (v: string): number | null => (v === '' || !Number.isFinite(Number(v)) ? null : Number(v));
const ym = (s: string | null | undefined) => { if (!s) return '—'; const [y, m] = s.split('-'); return `${MONTHS[Number(m) - 1] ?? ''} ${y}`; };
const ageLabel = (a: number) => { const y = Math.floor(a + 1e-9), m = Math.round((a - y) * 12); return m ? `${y}y ${m}m` : `${y}`; };

type Draft = { you: BondSSPerson; spouse: BondSSPerson | null; taxable_pct: number | null; state_taxed: boolean; sources: BondIncomeSource[]; birth_year: number | null };
const fromSettings = (settings: Record<string, unknown>): Draft => {
  const ss = (settings.social_security ?? {}) as BondSocialSecurity;
  return {
    you: { claim_age: 67, basis: 'fra', through_age: 95, birth_month: 6, ...(ss.you ?? {}) },
    spouse: ss.spouse ? { claim_age: 67, basis: 'fra', through_age: 95, birth_month: 6, ...ss.spouse } : null,
    taxable_pct: ss.taxable_pct ?? null, state_taxed: !!ss.state_taxed,
    sources: ((settings.income_sources ?? []) as BondIncomeSource[]).map(s => ({ ...s })),
    birth_year: settings.birth_year ? Number(settings.birth_year) : null,
  };
};

function PersonFields({ title, p, birthYear, onBirthYear, onChange, info, onRemove }: {
  title: string; p: BondSSPerson; birthYear: number | null; onBirthYear: (v: number | null) => void;
  onChange: (patch: Partial<BondSSPerson>) => void; info?: BondPlanIncomePerson; onRemove?: () => void;
}) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/30 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] font-semibold text-base-content/75">{title}</span>
        {onRemove && <button type="button" className="btn btn-ghost btn-xs text-rose-300" onClick={onRemove}><Trash2 className="h-3 w-3" /> remove</button>}
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
        <Field label="Birth year"><input type="number" className={inputCls} value={birthYear ?? ''} placeholder="1965" onChange={e => onBirthYear(numOrNull(e.target.value))} /></Field>
        <Field label="Birth month">
          <select className={selectCls} value={p.birth_month ?? 6} onChange={e => onChange({ birth_month: Number(e.target.value) })}>
            {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
          </select>
        </Field>
        <Field label="Claim at age" hint="62 (earliest) to 70 (largest); halves OK">
          <input type="number" min={62} max={70} step={0.5} className={inputCls} value={p.claim_age ?? ''} onChange={e => onChange({ claim_age: numOrNull(e.target.value) })} />
        </Field>
        <Field label="Monthly benefit ($)" hint="Today's dollars — from your SSA statement (ssa.gov/myaccount)">
          <input type="number" className={inputCls} value={p.monthly_benefit ?? ''} placeholder="3200" onChange={e => onChange({ monthly_benefit: numOrNull(e.target.value) })} />
        </Field>
        <Field label="That amount is" hint="We adjust for the age you claim at">
          <select className={selectCls} value={p.basis ?? 'fra'} onChange={e => onChange({ basis: e.target.value as 'fra' | 'claim' })}>
            <option value="fra">at full retirement age</option>
            <option value="claim">at my claiming age</option>
          </select>
        </Field>
        <Field label="Plan through age" hint="How long benefits are paid in this plan">
          <input type="number" className={inputCls} value={p.through_age ?? ''} placeholder="95" onChange={e => onChange({ through_age: numOrNull(e.target.value) })} />
        </Field>
      </div>
      {info && (
        <div className="mt-2 text-[11px] leading-relaxed text-base-content/60">
          Starts <b className="text-base-content/85">{ym(info.start)}</b> at <b style={{ color: INCOME_COLOR }}>{usd(info.own_monthly)}/mo</b> in today's dollars
          {' '}({pct(info.pct_of_fra, 0)} of the {usd(info.pia_monthly)} full-retirement benefit; full retirement age {info.fra_label}).
          {info.spousal_monthly > 0 && <> Plus a <b>{usd(info.spousal_monthly)}/mo spousal top-up</b> from {ym(info.spousal_start)} (half the other's full benefit, less your own).</>}
          {' '}Paid through {info.through_year}; rises with inflation.
        </div>
      )}
    </div>
  );
}

// Social Security (you + spouse) and other income (pension, annuity, rental…). They're spent first each year —
// the bonds and funds only have to cover what's left.
export default function BondIncomeSources({ settings, plan, onSave }: {
  settings: Record<string, unknown>; plan: BondPlan | null; onSave: (patch: Record<string, unknown>) => Promise<void>;
}) {
  const [d, setD] = useState<Draft>(() => fromSettings(settings));
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const key = JSON.stringify([settings.social_security ?? null, settings.income_sources ?? null, settings.birth_year ?? null]);
  useEffect(() => { setD(fromSettings(settings)); setDirty(false); }, [key]);   // eslint-disable-line react-hooks/exhaustive-deps
  const set = (patch: Partial<Draft>) => { setD(cur => ({ ...cur, ...patch })); setDirty(true); };
  const now = new Date().getFullYear();
  const income = plan?.income;
  const hasYou = (d.you.monthly_benefit ?? 0) > 0;

  const save = async () => {
    setSaving(true);
    const { birth_year: _by, ...youRest } = d.you;      // your birth year lives in one place (also drives the 59½ rule)
    void _by;
    const ss: BondSocialSecurity = { you: hasYou || d.spouse ? youRest : null, spouse: d.spouse, taxable_pct: d.taxable_pct, state_taxed: d.state_taxed };
    try {
      await onSave({
        social_security: (ss.you || ss.spouse) ? ss : null,
        income_sources: d.sources.filter(s => (s.amount ?? 0) > 0),
        ...(d.birth_year !== (settings.birth_year ? Number(settings.birth_year) : null) ? { birth_year: d.birth_year } : {}),
      });
      setDirty(false);
    } finally { setSaving(false); }
  };
  const setSource = (i: number, patch: Partial<BondIncomeSource>) => set({ sources: d.sources.map((s, j) => (j === i ? { ...s, ...patch } : s)) });

  const cmp = income?.claim_comparison ?? [];
  const best = useMemo(() => cmp.length ? cmp.reduce((a, b) => ((b.funded_ratio_pct ?? 0) > (a.funded_ratio_pct ?? 0) + 0.05 ? b : a)) : null, [cmp]);
  const share = income && plan && plan.pv_needs > 0 ? (100 * income.pv_net) / plan.pv_needs : null;
  const firstSS = income?.by_year.find(r => r.social_security > 0);

  return (
    <Card icon={<HandCoins className="h-4 w-4" style={{ color: INCOME_COLOR }} />} title="Income sources"
      subtitle="Social Security, pensions and other income are spent first each year — your bonds only have to cover what's left."
      right={<button className="btn btn-sm btn-primary" disabled={!dirty || saving} onClick={save}>{saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save income</button>}>
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-base-content/45">Social Security</div>
      <div className="grid gap-3 lg:grid-cols-2">
        <PersonFields title="You" p={d.you} birthYear={d.birth_year} onBirthYear={v => set({ birth_year: v })}
          onChange={patch => set({ you: { ...d.you, ...patch } })} info={dirty ? undefined : income?.people.you} />
        {d.spouse ? (
          <PersonFields title="Spouse" p={d.spouse} birthYear={d.spouse.birth_year ?? null} onBirthYear={v => set({ spouse: { ...d.spouse!, birth_year: v } })}
            onChange={patch => set({ spouse: { ...d.spouse!, ...patch } })} info={dirty ? undefined : income?.people.spouse} onRemove={() => set({ spouse: null })} />
        ) : (
          <button type="button" onClick={() => set({ spouse: { claim_age: 67, basis: 'fra', through_age: 95, birth_month: 6, monthly_benefit: null, birth_year: null } })}
            className="flex min-h-[96px] items-center justify-center gap-2 rounded-xl border border-dashed border-white/[0.12] text-xs text-base-content/55 hover:border-primary/40 hover:text-primary">
            <UserPlus className="h-4 w-4" /> Add spouse — own benefit, spousal top-up and survivor benefit
          </button>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-end gap-4 text-[11px] text-base-content/65">
        <label className="flex items-center gap-2">
          % of benefits federally taxable
          <input type="number" min={0} max={85} className="input input-xs input-bordered w-20 bg-base-200/60" value={d.taxable_pct ?? ''} placeholder="auto"
            onChange={e => set({ taxable_pct: numOrNull(e.target.value) })} />
          <span className="text-base-content/40">blank = IRS test on this plan's income (0–85%)</span>
        </label>
        <label className="flex items-center gap-1.5"><input type="checkbox" className="checkbox checkbox-xs" checked={d.state_taxed} onChange={e => set({ state_taxed: e.target.checked })} /> My state taxes Social Security</label>
      </div>

      <div className="mb-1 mt-4 flex items-center justify-between">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-base-content/45">Other income — pension, annuity, rental, part-time work</span>
        <button type="button" className="btn btn-ghost btn-xs" onClick={() => set({ sources: [...d.sources, { name: 'Pension', amount: null, start_year: now + 1, end_year: null, cola: false, taxable_pct: 100 }] })}><Plus className="h-3 w-3" /> Add</button>
      </div>
      {d.sources.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-[10px] uppercase tracking-wide text-base-content/40">
              <tr>{['Name', '$ per year', 'From', 'Until', 'Rises with inflation', 'Taxable %', ''].map(h => <th key={h} className="px-1.5 py-1 text-left font-medium">{h}</th>)}</tr>
            </thead>
            <tbody>
              {d.sources.map((s, i) => (
                <tr key={i}>
                  <td className="px-1.5 py-1"><input className={inputCls} value={s.name} onChange={e => setSource(i, { name: e.target.value })} /></td>
                  <td className="px-1.5 py-1"><input type="number" className={inputCls} value={s.amount ?? ''} placeholder="24000" onChange={e => setSource(i, { amount: numOrNull(e.target.value) })} /></td>
                  <td className="px-1.5 py-1"><input type="number" className={inputCls} value={s.start_year ?? ''} onChange={e => setSource(i, { start_year: numOrNull(e.target.value) })} /></td>
                  <td className="px-1.5 py-1"><input type="number" className={inputCls} value={s.end_year ?? ''} placeholder="for life" onChange={e => setSource(i, { end_year: numOrNull(e.target.value) })} /></td>
                  <td className="px-1.5 py-1"><input type="checkbox" className="toggle toggle-xs toggle-success" checked={s.cola} onChange={e => setSource(i, { cola: e.target.checked })} title="On: the amount is in today's dollars and grows with inflation. Off: a fixed dollar amount (most pensions/annuities)" /></td>
                  <td className="px-1.5 py-1"><input type="number" min={0} max={100} className={inputCls} value={s.taxable_pct ?? ''} placeholder="100" onChange={e => setSource(i, { taxable_pct: numOrNull(e.target.value) })} /></td>
                  <td className="px-1.5 py-1"><button type="button" className="btn btn-ghost btn-xs text-rose-300" onClick={() => set({ sources: d.sources.filter((_, j) => j !== i) })}><Trash2 className="h-3 w-3" /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="text-[11px] text-base-content/40">None added.</p>}

      {dirty && <p className="mt-3 text-[11px] text-amber-300/80">Save to see it in the plan.</p>}
      {!dirty && income?.configured && plan && (
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <Stat label="Income over the plan" value={usd(income.total_net, { compact: true })} sub={`${usd(income.total_gross, { compact: true })} before ${usd(income.total_tax, { compact: true })} tax`} />
            <Stat label="Share of your goals" value={pct(share, 0)} sub={`${usd(income.pv_net, { compact: true })} of ${usd(plan.pv_needs, { compact: true })} in today's dollars`}
              accent="text-amber-300" hint="Present value of this income ÷ present value of all goal years — the bonds need to cover the rest" />
            <Stat label="Social Security starts" value={firstSS ? String(firstSS.year) : '—'} sub={firstSS ? `${usd(firstSS.social_security, { compact: true })} that year` : 'not set up'} />
            <Stat label="Benefits taxed (federal)" value={(() => { const r = income.by_year.filter(x => x.ss_taxable_pct != null).pop(); return r ? pct(r.ss_taxable_pct, 0) : '—'; })()}
              sub={income.ss_taxable_override != null ? 'your setting' : `IRS test, ${income.filing} filer`}
              hint="Share of benefits counted as taxable income: 0%, up to 50% or up to 85% depending on your other income" />
          </div>
          {cmp.length > 0 && (
            <div className="overflow-x-auto rounded-xl border border-white/[0.06]">
              <div className="border-b border-white/[0.05] px-3 py-2 text-[11px] font-semibold text-base-content/70">When should you claim? — the same plan at each age (spouse unchanged)</div>
              <table className="w-full text-xs">
                <thead className="text-[10px] uppercase tracking-wide text-base-content/45">
                  <tr>{['Claim at', 'Starts', 'Monthly (today\'s $)', 'Lifetime benefits (household)', 'Funded ratio', 'Unfunded', 'First short year'].map(h => <th key={h} className="px-2 py-1.5 text-left font-medium">{h}</th>)}</tr>
                </thead>
                <tbody>
                  {cmp.map(c => (
                    <tr key={c.claim_age} className={`border-t border-white/[0.03] ${c.chosen ? 'bg-amber-500/[0.06]' : ''}`}>
                      <td className="px-2 py-1.5 font-semibold">{ageLabel(c.claim_age)}{c.chosen && <span className="ml-1.5 rounded bg-amber-500/20 px-1 text-[9px] uppercase text-amber-300">your plan</span>}
                        {best && !income.comparison_horizon_short && best.claim_age === c.claim_age && !c.chosen && <span className="ml-1.5 rounded bg-emerald-500/15 px-1 text-[9px] uppercase text-emerald-300">best funded</span>}</td>
                      <td className="px-2 py-1.5 tabular-nums">{c.start_year}</td>
                      <td className="px-2 py-1.5 tabular-nums">{usd(c.monthly_today)} <span className="text-base-content/40">({pct(c.pct_of_fra, 0)})</span></td>
                      <td className="px-2 py-1.5 tabular-nums">{usd(c.household_lifetime_today_dollars, { compact: true })}</td>
                      <td className="px-2 py-1.5 font-medium tabular-nums">{pct(c.funded_ratio_pct, 1)}</td>
                      <td className="px-2 py-1.5 tabular-nums">{c.shortfall_total > 0.5 ? usd(c.shortfall_total, { compact: true }) : <span className="text-emerald-400">none</span>}</td>
                      <td className="px-2 py-1.5 tabular-nums">{c.first_shortfall ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {income.comparison_horizon_short && (
                <div className="mx-3 mt-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-100/85">
                  <b>Read this table with care:</b> your goals stop in {income.goals_end_year}, but benefits run to {income.benefits_through_year}.
                  The funded ratio only sees the years with goals, which flatters claiming early. Extend your income goal to your plan-through age for a fair comparison.
                </div>
              )}
              <div className="px-3 py-2"><Note>Waiting pays more per month for life (and for a surviving spouse) but your bonds must bridge the years until then; claiming early does the reverse. Lifetime benefits assume you live through the age you set.</Note></div>
            </div>
          )}
          <div className="space-y-0.5">{income.notes.map((n, i) => <Note key={i}>{n}</Note>)}</div>
        </div>
      )}
    </Card>
  );
}
