import React, { useEffect, useState } from 'react';
import { Calculator, Loader2, Receipt, Save } from 'lucide-react';
import { fetchYieldMenu, saveBondProfile } from '../../api';
import type { BondPortfolio, BondProfile, BondYieldMenu } from '../../types';
import { Card, ErrorBox, Field, KindBadge, Loading, Note, Seg, US_STATES, inputCls, pct, selectCls, tenorLabel, usd } from './bondUi';

const BRACKETS = [10, 12, 22, 24, 32, 35, 37];
const OWN_SETTINGS = ['inflation_long', 'retire_year', 'retired_federal_rate', 'retired_state_rate', 'retired_ltcg_rate'];

export function ProfileEditor({ profile, onSaved, compact = false }: { profile: BondProfile; onSaved: () => void; compact?: boolean }) {
  const [p, setP] = useState<BondProfile>(profile);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  useEffect(() => setP(profile), [profile]);
  const set = (patch: Partial<BondProfile>) => { setP(prev => ({ ...prev, ...patch })); setOk(false); };
  const st = (p.settings ?? {}) as Record<string, number | string | null | undefined>;
  const setS = (k: string, v: number | null) => set({ settings: { ...(p.settings ?? {}), [k]: v } });
  const numOrNull = (v: string) => (v === '' ? null : Number(v));
  const retireGoal = (p.goals ?? []).filter(g => /retire/i.test(g.name ?? '')).map(g => g.year).sort((a, b) => a - b)[0];
  const retiredSet = ['retired_federal_rate', 'retired_state_rate', 'retired_ltcg_rate'].some(k => st[k] != null && st[k] !== '');
  const save = async () => {
    setSaving(true); setErr(null);
    // send only the settings this editor owns — the server merges, so the planner's keys (Social Security,
    // income sources, birth year…) are never overwritten by this page's possibly stale copy
    const own = Object.fromEntries(OWN_SETTINGS.map(k => [k, (p.settings ?? {})[k] ?? null]));
    try { await saveBondProfile({ ...p, settings: own }); setOk(true); onSaved(); } catch (e) { setErr(e instanceof Error ? e.message : 'Save failed'); } finally { setSaving(false); }
  };
  return (
    <div>
      <div className={`grid gap-3 ${compact ? 'grid-cols-2 md:grid-cols-4' : 'grid-cols-2 md:grid-cols-4 xl:grid-cols-8'}`}>
        <Field label="Federal bracket">
          <select className={selectCls} value={p.federal_rate} onChange={e => set({ federal_rate: Number(e.target.value) })}>
            {BRACKETS.map(b => <option key={b} value={b}>{b}%</option>)}
            {!BRACKETS.includes(p.federal_rate) && <option value={p.federal_rate}>{p.federal_rate}%</option>}
          </select>
        </Field>
        <Field label="Your state">
          <select className={selectCls} value={p.state ?? ''} onChange={e => set({ state: e.target.value || null })}>
            <option value="">—</option>{US_STATES.map(s => <option key={s}>{s}</option>)}
          </select>
        </Field>
        <Field label="State + local rate %"><input type="number" step="0.1" className={inputCls} value={p.state_rate} onChange={e => set({ state_rate: Number(e.target.value) })} /></Field>
        <Field label="LTCG rate %"><input type="number" step="1" className={inputCls} value={p.ltcg_rate} onChange={e => set({ ltcg_rate: Number(e.target.value) })} /></Field>
        <Field label="Inflation next 3 yrs %" hint="Your expected CPI for the next 3 years. Blank = market 5y breakeven">
          <input type="number" step="0.1" className={inputCls} value={p.inflation_assumption ?? ''} placeholder="market" onChange={e => set({ inflation_assumption: e.target.value === '' ? null : Number(e.target.value) })} />
        </Field>
        <Field label="Inflation long-run avg %" hint="Average CPI after year 3 — drives long TIPS, inflation-adjusted goals. Blank = market 5y5y forward">
          <input type="number" step="0.1" className={inputCls} value={(st.inflation_long as number | null | undefined) ?? ''} placeholder="market"
            onChange={e => setS('inflation_long', numOrNull(e.target.value))} />
        </Field>
        <Field label="Horizon (years)" hint="When you'll need the money — used for duration matching">
          <input type="number" className={inputCls} value={p.horizon_years ?? ''} placeholder="—" onChange={e => set({ horizon_years: e.target.value === '' ? null : Number(e.target.value) })} />
        </Field>
        <label className="flex items-end gap-2 pb-2 text-xs text-base-content/70">
          <input type="checkbox" className="checkbox checkbox-xs" checked={p.niit} onChange={e => set({ niit: e.target.checked })} /> 3.8% NIIT
        </label>
        <div className="flex items-end gap-2">
          <button className="btn btn-primary btn-sm w-full" onClick={save} disabled={saving}>{saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save</button>
        </div>
      </div>
      <div className="mt-3 rounded-xl border border-white/[0.06] bg-base-200/30 p-3">
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
          <span className="text-[11px] font-semibold text-base-content/70">After you retire</span>
          <span className="text-[10.5px] text-base-content/45">
            {retiredSet ? `Used for cash flows, tax and the planner from ${st.retire_year || retireGoal || '—'}; today's yields use your current rates.`
              : 'Optional — usually lower brackets once the paycheck stops. Blank = same as now.'}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Field label="From year" hint={retireGoal ? `Blank = your "${(p.goals ?? []).find(g => g.year === retireGoal)?.name}" goal (${retireGoal})` : 'The first year you live off the portfolio'}>
            <input type="number" className={inputCls} value={(st.retire_year as number | null | undefined) ?? ''} placeholder={retireGoal ? String(retireGoal) : 'e.g. 2032'}
              onChange={e => setS('retire_year', numOrNull(e.target.value))} />
          </Field>
          <Field label="Federal bracket">
            <select className={selectCls} value={(st.retired_federal_rate as number | null | undefined) ?? ''} onChange={e => setS('retired_federal_rate', numOrNull(e.target.value))}>
              <option value="">same ({p.federal_rate}%)</option>
              {[0, ...BRACKETS].map(b => <option key={b} value={b}>{b}%</option>)}
            </select>
          </Field>
          <Field label="State + local %">
            <input type="number" step="0.1" className={inputCls} value={(st.retired_state_rate as number | null | undefined) ?? ''} placeholder={`same (${p.state_rate})`}
              onChange={e => setS('retired_state_rate', numOrNull(e.target.value))} />
          </Field>
          <Field label="LTCG %" hint="0% / 15% / 20% by taxable income">
            <select className={selectCls} value={(st.retired_ltcg_rate as number | null | undefined) ?? ''} onChange={e => setS('retired_ltcg_rate', numOrNull(e.target.value))}>
              <option value="">same ({p.ltcg_rate}%)</option>
              {[0, 15, 20].map(b => <option key={b} value={b}>{b}%</option>)}
            </select>
          </Field>
        </div>
      </div>
      {ok && <p className="mt-2 text-[11px] text-emerald-400">Saved — every after-tax number is now recomputed for you.</p>}
      {err && <div className="mt-2"><ErrorBox message={err} /></div>}
    </div>
  );
}

export default function BondTax({ data, onChanged }: { data: BondPortfolio; onChanged: () => void }) {
  const [acct, setAcct] = useState('taxable');
  const [menu, setMenu] = useState<BondYieldMenu | null>(null);
  const [menuErr, setMenuErr] = useState<string | null>(null);
  useEffect(() => {
    setMenu(null); setMenuErr(null);
    fetchYieldMenu(acct).then(setMenu).catch(e => setMenuErr(e instanceof Error ? e.message : 'Failed'));
  }, [acct, data.profile]);

  const live = data.holdings.filter(h => h.status === 'held' && !h.matured);
  const labels = menu?.rows[0]?.candidates.map(c => c.label) ?? [];
  const order = ['Treasury', 'Brokered CD', 'Agency', 'Muni (in-state)', 'Corporate AA', 'Corporate A', 'Corporate BBB', 'TIPS'].filter(l => labels.includes(l));

  return (
    <div className="space-y-4">
      <Card icon={<Receipt className="h-4 w-4 text-primary" />} title="Your tax profile" subtitle="Drives every after-tax yield, TEY and recommendation on the Bond Desk">
        <ProfileEditor profile={data.profile} onSaved={onChanged} />
      </Card>

      <Card icon={<Calculator className="h-4 w-4 text-emerald-400" />} title="After-tax yield menu"
        subtitle="Today's estimated yields per instrument and maturity — after YOUR taxes. The highlighted cell is the best no-credit-risk choice."
        right={<Seg value={acct} onChange={setAcct} options={[{ value: 'taxable', label: 'Taxable account' }, { value: 'ira', label: 'IRA / 401k' }]} />} pad={false}>
        {menuErr ? <div className="p-4"><ErrorBox message={menuErr} /></div> : !menu ? <Loading /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="border-y border-white/[0.05] text-[10px] uppercase tracking-wide text-base-content/45">
                <tr><th className="px-3 py-2 text-left font-medium">Maturity</th>{order.map(l => <th key={l} className="px-2 py-2 text-right font-medium">{l}</th>)}<th className="px-3 py-2 text-left font-medium">Best for you</th></tr>
              </thead>
              <tbody>
                {menu.rows.map(r => {
                  const byLabel = Object.fromEntries(r.candidates.map(c => [c.label, c]));
                  return (
                    <tr key={r.tenor} className="border-b border-white/[0.03]">
                      <td className="px-3 py-1.5 font-semibold">{tenorLabel(r.tenor)}</td>
                      {order.map(l => {
                        const c = byLabel[l];
                        const isBest = r.best_no_credit?.label === l;
                        return (
                          <td key={l} className={`px-2 py-1.5 text-right tabular-nums ${isBest ? 'rounded bg-emerald-500/15 font-semibold text-emerald-300' : ''}`}
                            title={c ? `${c.basis}\npre-tax ${pct(c.pre_tax_pct)} · tax ${pct(c.tax_rate_pct, 1)} · TEY ${pct(c.tey_pct)}` : ''}>
                            {c ? pct(c.after_tax_pct) : '—'}
                            {c && <div className="text-[9px] font-normal text-base-content/35">{pct(c.pre_tax_pct)}</div>}
                          </td>
                        );
                      })}
                      <td className="px-3 py-1.5 text-[11px]">
                        <span className="text-emerald-300">{r.best_no_credit?.label ?? '—'}</span>
                        {r.best && r.best.label !== r.best_no_credit?.label && <div className="text-[10px] text-amber-300/80">{r.best.label} +{((r.best.after_tax_pct ?? 0) - (r.best_no_credit?.after_tax_pct ?? 0)).toFixed(2)}% w/ credit risk</div>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="space-y-0.5 px-4 py-3">
              <Note>Big number = after-tax yield; small = pre-tax. TIPS shown as nominal-equivalent (real yield + expected inflation to that maturity: {menu.inflation_pct}% for 3 years{menu.inflation_long_pct != null && menu.inflation_long_pct !== menu.inflation_pct ? `, then ${menu.inflation_long_pct}%` : ''}), taxed federally including inflation accretion.</Note>
              <Note>Muni, CD, agency and corporate yields are curve-based estimates (muni: MUB-anchored ratio; corporates: ICE BofA OAS by rating); Treasuries use the official par curve.</Note>
            </div>
          </div>
        )}
      </Card>

      <div className="grid gap-4 xl:grid-cols-5">
        <Card className="xl:col-span-3" title="Tax treatment of each holding" pad={false}>
          <div className="max-h-[380px] overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-base-100 text-[10px] uppercase text-base-content/45">
                <tr>{['Holding', 'Account', 'Fed', 'State', 'Rate', 'Pre-tax', 'After-tax', 'TEY'].map(h => <th key={h} className="px-2 py-1.5 text-left font-medium">{h}</th>)}</tr>
              </thead>
              <tbody>
                {live.map(h => (
                  <tr key={h.id} className="border-t border-white/[0.03] align-top" title={(h.tax?.notes ?? []).join('\n')}>
                    <td className="px-2 py-1.5"><span className="flex items-center gap-1.5"><KindBadge kind={h.kind} /><span className="truncate">{h.label}</span></span>
                      {(h.tax?.notes ?? []).slice(0, 1).map((n, i) => <div key={i} className="mt-0.5 text-[10px] leading-snug text-base-content/40">{n}</div>)}</td>
                    <td className="px-2 py-1.5 uppercase text-base-content/60">{h.account_type}</td>
                    <td className="px-2 py-1.5">{h.tax?.fed_taxable ? 'tax' : <span className="text-emerald-400">free</span>}</td>
                    <td className="px-2 py-1.5">{h.tax?.state_taxable ? 'tax' : <span className="text-emerald-400">free</span>}</td>
                    <td className="px-2 py-1.5 tabular-nums">{pct(h.tax?.rate_pct, 1)}</td>
                    <td className="px-2 py-1.5 tabular-nums" title={h.kind === 'tips' ? `real ${pct(h.ytw_pct)} + inflation` : h.yield_basis}>{pct(h.total_yield_pct ?? h.ytw_pct)}</td>
                    <td className="px-2 py-1.5 font-medium tabular-nums text-emerald-400/90">{pct(h.tax?.after_tax_yield_pct)}</td>
                    <td className="px-2 py-1.5 tabular-nums">{pct(h.tax?.tey_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        <Card className="xl:col-span-2" title="Estimated tax on bond income" subtitle={`At ${data.tax.rates.federal_pct}% fed${data.tax.rates.niit_pct ? ` + ${data.tax.rates.niit_pct}% NIIT` : ''} + ${data.tax.rates.state_pct}% state`} pad={false}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-[10px] uppercase text-base-content/45">
                <tr>{['Year', 'Taxable int.', 'Tax-free', 'Phantom', 'Gains tax', 'Total tax', 'Eff.'].map(h => <th key={h} className="px-2 py-1.5 text-right font-medium first:text-left">{h}</th>)}</tr>
              </thead>
              <tbody>
                {data.tax.years.map(y => (
                  <tr key={y.year} className="border-t border-white/[0.03]">
                    <td className="px-2 py-1.5 font-medium">{y.year}{y.retired_rates && <span className="ml-1 text-[9px] text-teal-300/80" title="after-retirement rates">ret.</span>}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{usd(y.fed_taxable_interest, { compact: true })}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-emerald-400/80">{usd(y.tax_exempt_interest + y.sheltered_interest, { compact: true })}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums text-amber-300/80">{usd(y.phantom_income, { compact: true })}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums" title="Market discount / fund wind-up gains realized at maturity">{usd(y.tax_on_gains ?? 0, { compact: true })}</td>
                    <td className="px-2 py-1.5 text-right font-semibold tabular-nums text-rose-300">{usd(y.total_tax, { compact: true })}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{pct(y.effective_rate_pct, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="space-y-0.5 px-4 py-3">{data.tax.notes.map((n, i) => <Note key={i}>{n}</Note>)}</div>
          </div>
        </Card>
      </div>
    </div>
  );
}
