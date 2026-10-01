import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, BarChart3, CheckCircle2, Eye, Layers, Loader2, Play, Save, Trash2 } from 'lucide-react';
import {
  adoptBondLadder, buildBondLadder, deleteBondLadder, fetchBondLadder, fetchBondLadders, saveBondLadder, simulateBondLadder,
} from '../../api';
import type { BondLadderParams, BondLadderResult, BondLadderSaved, BondSimulation, BondKind } from '../../types';
import {
  ACCOUNTS, AXIS, Card, Chart, Empty, ErrorBox, FLOW_COLORS, Field, KIND_META, KindBadge, LEGEND, Loading, Note, Seg, Stat, TOOLTIP,
  fmtDate, inputCls, num, pct, selectCls, usd,
} from './bondUi';

const INSTRUMENTS = [
  { value: 'best_after_tax', label: 'Best after-tax (auto)', hint: 'Per rung, whichever instrument nets you the most after YOUR taxes' },
  { value: 'treasury', label: 'Treasuries', hint: 'Real outstanding CUSIPs at FedInvest prices · state-tax-free' },
  { value: 'cd', label: 'Brokered CDs', hint: 'FDIC-insured; state-taxable' },
  { value: 'muni', label: 'Municipal bonds', hint: 'Federal-tax-free (estimated AA yields)' },
  { value: 'agency', label: 'Agencies', hint: 'Slight pickup over Treasuries; often callable' },
  { value: 'corporate', label: 'Corporates', hint: 'Adds credit risk for more yield' },
  { value: 'etf_treasury', label: 'iBonds Treasury ETFs', hint: 'One ETF per year — tradable any day' },
  { value: 'etf_corporate', label: 'iBonds Corporate ETFs', hint: 'Diversified IG corporate rungs' },
  { value: 'etf_muni', label: 'iBonds Muni ETFs', hint: 'Diversified muni rungs' },
  { value: 'etf_tips', label: 'iBonds TIPS ETFs', hint: 'Inflation-protected rungs (Oct maturities)' },
];

const STATUS_STYLE: Record<string, string> = {
  funded: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  partial: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  planned: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  missing: 'bg-rose-500/10 text-rose-300 border-rose-500/25',
  matured: 'bg-slate-500/15 text-slate-300 border-slate-500/30',
};

const DEFAULTS: BondLadderParams = {
  amount: 100000, start_years: 1, end_years: 10, frequency: 'annual', instrument: 'best_after_tax',
  weighting: 'equal', account_type: 'taxable', allow_credit: false, corporate_rating: 'A',
};

export function LadderPreview({ ladder }: { ladder: BondLadderResult }) {
  const [sim, setSim] = useState<BondSimulation | null>(null);
  const [simLoading, setSimLoading] = useState(false);
  const [simErr, setSimErr] = useState<string | null>(null);
  const s = ladder.summary;
  useEffect(() => { setSim(null); }, [ladder]);

  const rungChart = useMemo(() => ({
    tooltip: { ...TOOLTIP, trigger: 'axis' },
    legend: LEGEND, grid: { left: 8, right: 8, top: 30, bottom: 4, containLabel: true },
    xAxis: { type: 'category', data: ladder.rungs.map(r => (r.maturity ?? r.target_date).slice(0, 7)), ...AXIS },
    yAxis: [
      { type: 'value', ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => usd(v, { compact: true }) } },
      { type: 'value', ...AXIS, splitLine: { show: false }, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => `${v}%` } },
    ],
    series: [
      { name: 'Invested', type: 'bar', data: ladder.rungs.map(r => ({ value: Math.round(r.cost), itemStyle: { color: KIND_META[r.kind as BondKind]?.color ?? '#60a5fa' } })),
        tooltip: { valueFormatter: (v: number) => usd(v) } },
      { name: 'Yield', type: 'line', yAxisIndex: 1, data: ladder.rungs.map(r => r.yield_pct), itemStyle: { color: '#e2e8f0' }, symbolSize: 5 },
      { name: 'After-tax', type: 'line', yAxisIndex: 1, data: ladder.rungs.map(r => r.after_tax_pct), itemStyle: { color: '#34d399' }, symbolSize: 5 },
    ],
  }), [ladder]);

  const cashChart = useMemo(() => {
    const ys = ladder.cash_flow.yearly.filter(y => y.total > 0);
    return {
      tooltip: { ...TOOLTIP, trigger: 'axis', valueFormatter: (v: number) => usd(v) },
      legend: LEGEND, grid: { left: 8, right: 8, top: 30, bottom: 4, containLabel: true },
      xAxis: { type: 'category', data: ys.map(y => String(y.year)), ...AXIS },
      yAxis: { type: 'value', ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => usd(v, { compact: true }) } },
      series: [
        { name: 'Interest', type: 'bar', stack: 'c', data: ys.map(y => y.coupon + y.distribution), itemStyle: { color: FLOW_COLORS.coupon } },
        { name: 'Principal', type: 'bar', stack: 'c', data: ys.map(y => y.principal), itemStyle: { color: FLOW_COLORS.principal } },
      ],
    };
  }, [ladder]);

  const simChart = useMemo(() => {
    if (!sim) return null;
    const colors: Record<string, string> = { flat: '#94a3b8', up100: '#f87171', down100: '#60a5fa', forwards: '#f59e0b' };
    const keys = Object.keys(sim.scenarios) as (keyof BondSimulation['scenarios'])[];
    return {
      tooltip: { ...TOOLTIP, trigger: 'axis', valueFormatter: (v: number) => usd(v) },
      legend: LEGEND, grid: { left: 8, right: 8, top: 30, bottom: 4, containLabel: true },
      xAxis: { type: 'category', data: sim.scenarios.flat.map(p => `Y${p.year}`), ...AXIS },
      yAxis: { type: 'value', ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => usd(v, { compact: true }) } },
      series: keys.map(k => ({ name: sim.labels[k], type: 'line', smooth: true, symbol: 'none', data: sim.scenarios[k].map(p => p.income), itemStyle: { color: colors[k] }, lineStyle: { width: 2 } })),
    };
  }, [sim]);

  const runSim = async () => {
    setSimLoading(true); setSimErr(null);
    try { setSim(await simulateBondLadder(ladder, 15)); } catch (e) { setSimErr(e instanceof Error ? e.message : 'Simulation failed'); } finally { setSimLoading(false); }
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-6">
        <Stat label="Invested" value={usd(s.invested, { compact: true })} sub={s.cash_left > 1 ? `${usd(s.cash_left)} left as cash` : `${s.rungs} rungs`} />
        <Stat label="Yield" value={pct(s.yield_pct)} sub="cost-weighted" />
        <Stat label="After-tax" value={pct(s.after_tax_yield_pct)} sub={`TEY ${pct(s.tey_pct)}`} accent="text-emerald-400" />
        <Stat label="Income / yr" value={usd(s.annual_income, { compact: true })} sub={`${usd(s.annual_income_after_tax, { compact: true })} after tax`} />
        <Stat label="Duration" value={`${num(s.eff_duration, 2)}y`} sub={`avg life ${num(s.avg_years, 1)}y`} />
        <Stat label="Maturities" value={`${(s.first_maturity ?? '').slice(0, 4)}–${(s.last_maturity ?? '').slice(0, 4)}`} sub={`DV01 ${usd(s.dv01)}`} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Rungs" subtitle="Dollars per rung (colored by instrument) with yield & after-tax yield"><Chart option={rungChart} height={260} /></Card>
        <Card title="Cash this ladder pays you" subtitle="Interest + principal by year (no reinvestment)"><Chart option={cashChart} height={260} /></Card>
      </div>
      <Card title="Buy list" subtitle="What to buy for each rung — with the reason it was chosen" pad={false}>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="border-y border-white/[0.05] text-[10px] uppercase tracking-wide text-base-content/45">
              <tr>{['#', 'Maturity', 'Instrument', 'ID', 'Yield', 'After-tax', 'Buy', 'Face / shares', 'Income/yr', 'Why'].map(h => <th key={h} className="px-2 py-2 text-left font-medium">{h}</th>)}</tr>
            </thead>
            <tbody>
              {ladder.rungs.map(r => (
                <tr key={r.index} className="border-b border-white/[0.03] align-top">
                  <td className="px-2 py-1.5 text-base-content/45">{r.index}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 tabular-nums">{fmtDate(r.maturity ?? r.target_date)}</td>
                  <td className="px-2 py-1.5"><span className="flex items-center gap-1.5"><KindBadge kind={r.kind as BondKind} /><span className="whitespace-nowrap">{r.label}</span></span></td>
                  <td className="px-2 py-1.5 font-mono text-[10px] text-base-content/60">{r.cusip ?? r.ticker ?? '—'}</td>
                  <td className="px-2 py-1.5 tabular-nums">{pct(r.yield_pct)}{r.distribution_yield_pct != null && <div className="text-[9px] text-base-content/40">dist {pct(r.distribution_yield_pct)}</div>}</td>
                  <td className="px-2 py-1.5 tabular-nums text-emerald-400/90">{pct(r.after_tax_pct)}</td>
                  <td className="px-2 py-1.5 font-medium tabular-nums">{usd(r.cost)}</td>
                  <td className="px-2 py-1.5 tabular-nums">{r.face != null ? usd(r.face) : r.quantity != null ? `${r.quantity} sh` : '—'}</td>
                  <td className="px-2 py-1.5 tabular-nums">{usd(r.annual_income)}</td>
                  <td className="max-w-[260px] px-2 py-1.5 text-[10px] leading-snug text-base-content/50">
                    {r.basis}
                    {r.alternatives.length > 1 && (
                      <div className="mt-0.5 text-base-content/40">vs {r.alternatives.slice(1, 4).map(a => `${a.label} ${pct(a.after_tax_pct)}`).join(' · ')}</div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="space-y-0.5 px-4 py-3">{ladder.notes.map((n, i) => <Note key={i}>{n}</Note>)}</div>
      </Card>
      <Card icon={<Activity className="h-4 w-4 text-amber-400" />} title="Rolling it forward — reinvestment risk"
        subtitle="Each maturing rung is reinvested at the far end. How does your income evolve if rates stay, rise, fall, or follow market forwards?"
        right={<button className="btn btn-sm btn-outline" onClick={runSim} disabled={simLoading}>{simLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} Simulate 15y</button>}>
        {simErr && <ErrorBox message={simErr} />}
        {simChart ? (
          <>
            <Chart option={simChart} height={260} />
            <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4">
              {(Object.keys(sim!.scenarios) as (keyof BondSimulation['scenarios'])[]).map(k => {
                const path = sim!.scenarios[k];
                const last = path[path.length - 1];
                return <Stat key={k} label={sim!.labels[k]} value={usd(last?.income, { compact: true })} sub={`ladder yield ${pct(last?.ladder_yield_pct)} in Y${last?.year}`} />;
              })}
            </div>
          </>
        ) : !simLoading && <p className="py-6 text-center text-xs text-base-content/45">Run the simulation to see how income drifts as rungs roll.</p>}
      </Card>
    </div>
  );
}

function SavedLadder({ id, onChanged, onClose }: { id: number; onChanged: () => void; onClose: () => void }) {
  const [lad, setLad] = useState<BondLadderSaved | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { fetchBondLadder(id).then(setLad).catch(e => setErr(e instanceof Error ? e.message : 'Failed')); }, [id]);
  useEffect(load, [load]);
  if (err) return <ErrorBox message={err} />;
  if (!lad) return <Loading />;
  const sd = lad.status_detail;
  const adopt = async (status: 'held' | 'watch') => {
    setBusy(true);
    try { await adoptBondLadder(lad.id, status); load(); onChanged(); } catch (e) { setErr(e instanceof Error ? e.message : 'Failed'); } finally { setBusy(false); }
  };
  const remove = async () => {
    const delPlanned = window.confirm('Also delete the watchlist (planned) rungs of this ladder?\nOK = delete them, Cancel = keep them as unlinked holdings.');
    if (!window.confirm(`Delete ladder "${lad.name}"?`)) return;
    await deleteBondLadder(lad.id, delPlanned); onChanged(); onClose();
  };
  const isNominal = lad.ladder_type !== 'tips';
  return (
    <div className="space-y-4">
      <Card title={lad.name} subtitle={`${lad.ladder_type === 'tips' ? 'TIPS real-income ladder' : 'Bond ladder'} · saved ${fmtDate(lad.created_at)} · ${lad.status === 'active' ? 'tracking holdings' : 'plan only'}`}
        right={<>
          <button className="btn btn-sm btn-success btn-outline" disabled={busy} onClick={() => adopt('held')} title="Create holdings for every rung as bought"><CheckCircle2 className="h-3.5 w-3.5" /> I bought these</button>
          <button className="btn btn-sm btn-outline" disabled={busy} onClick={() => adopt('watch')} title="Put every rung on your watchlist"><Eye className="h-3.5 w-3.5" /> Track as plan</button>
          <button className="btn btn-sm btn-ghost text-rose-400" onClick={remove}><Trash2 className="h-3.5 w-3.5" /></button>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>Close</button>
        </>}>
        {sd && (
          <>
            <div className="mb-3 flex items-center gap-3">
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-base-300/60"><div className="h-2 rounded-full bg-emerald-400" style={{ width: `${sd.funded_pct}%` }} /></div>
              <span className="text-xs font-semibold tabular-nums">{sd.funded_pct}% funded</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {sd.rungs.map(r => (
                <div key={`${r.index}-${r.maturity}`} className={`rounded-lg border px-2 py-1 text-[10px] ${STATUS_STYLE[r.status]}`} title={`${r.label ?? ''} · planned ${usd(r.planned)} · held ${usd(r.held)}${r.on_watchlist ? ` · watchlist ${usd(r.on_watchlist)}` : ''}`}>
                  <div className="font-semibold">{(r.maturity ?? '').slice(0, 7)}</div>
                  <div className="uppercase tracking-wide">{r.status}{r.maturing_soon ? ' · soon' : ''}</div>
                </div>
              ))}
            </div>
          </>
        )}
        {lad.actual?.summary && (
          <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
            <Stat label="Held value" value={usd(lad.actual.summary.market_value, { compact: true })} />
            <Stat label="Yield (held)" value={pct((lad.actual.summary as { total_yield_pct?: number | null }).total_yield_pct ?? lad.actual.summary.ytw_pct)} />
            <Stat label="After-tax (held)" value={pct(lad.actual.summary.after_tax_yield_pct)} />
            <Stat label="Duration (held)" value={`${num(lad.actual.summary.eff_duration, 2)}y`} />
          </div>
        )}
      </Card>
      {isNominal && lad.plan && 'rungs' in lad.plan && (lad.plan as BondLadderResult).cash_flow && <LadderPreview ladder={lad.plan as BondLadderResult} />}
    </div>
  );
}

export default function BondLadders({ onChanged, prefill }: { onChanged: () => void; prefill?: Partial<BondLadderParams> | null }) {
  const [p, setP] = useState<BondLadderParams>({ ...DEFAULTS, ...(prefill ?? {}) });
  const [ladder, setLadder] = useState<BondLadderResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<BondLadderSaved[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const [saveName, setSaveName] = useState('');
  const [savingMsg, setSavingMsg] = useState<string | null>(null);

  const loadSaved = useCallback(() => { fetchBondLadders().then(setSaved).catch(() => undefined); }, []);
  useEffect(loadSaved, [loadSaved]);
  useEffect(() => { if (prefill) setP(prev => ({ ...prev, ...prefill })); }, [prefill]);

  const set = (patch: Partial<BondLadderParams>) => setP(prev => ({ ...prev, ...patch }));
  const build = async () => {
    setLoading(true); setErr(null); setSavingMsg(null);
    try {
      const r = await buildBondLadder(p);
      setLadder(r);
      setSaveName(`${INSTRUMENTS.find(i => i.value === p.instrument)?.label ?? 'Ladder'} ${new Date().getFullYear() + Math.round(p.start_years)}–${new Date().getFullYear() + Math.round(p.end_years)}`);
    } catch (e) { setErr(e instanceof Error ? e.message : 'Build failed'); } finally { setLoading(false); }
  };
  const save = async () => {
    if (!ladder) return;
    try {
      const s = await saveBondLadder({ name: saveName || 'My ladder', ladder_type: 'nominal', params: ladder.params, plan: ladder });
      setSavingMsg(`Saved "${s.name}". Open it below to adopt the rungs.`);
      loadSaved();
    } catch (e) { setErr(e instanceof Error ? e.message : 'Save failed'); }
  };
  const inst = INSTRUMENTS.find(i => i.value === p.instrument);

  if (openId != null) return <SavedLadder id={openId} onChanged={() => { onChanged(); loadSaved(); }} onClose={() => { setOpenId(null); loadSaved(); }} />;

  return (
    <div className="space-y-4">
      <Card icon={<Layers className="h-4 w-4 text-primary" />} title="Build a ladder"
        subtitle="Split money across staggered maturities: steady income, cash every year, and less reinvestment risk than one big maturity.">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
          <Field label="Amount ($)"><input type="number" className={inputCls} value={p.amount} onChange={e => set({ amount: Number(e.target.value) })} /></Field>
          <Field label="First rung (years)"><input type="number" step="0.25" min="0.25" className={inputCls} value={p.start_years} onChange={e => set({ start_years: Number(e.target.value) })} /></Field>
          <Field label="Last rung (years)"><input type="number" step="0.5" min="0.5" max="30" className={inputCls} value={p.end_years} onChange={e => set({ end_years: Number(e.target.value) })} /></Field>
          <Field label="Rung every">
            <select className={selectCls} value={p.frequency} onChange={e => set({ frequency: e.target.value as BondLadderParams['frequency'] })}>
              <option value="annual">Year</option><option value="semiannual">6 months</option><option value="quarterly">Quarter</option><option value="monthly">Month</option>
            </select>
          </Field>
          <Field label="Instrument" className="col-span-2">
            <select className={selectCls} value={p.instrument} onChange={e => set({ instrument: e.target.value })}>
              {INSTRUMENTS.map(i => <option key={i.value} value={i.value}>{i.label}</option>)}
            </select>
          </Field>
          <Field label="Sizing">
            <select className={selectCls} value={p.weighting} onChange={e => set({ weighting: e.target.value as BondLadderParams['weighting'] })}>
              <option value="equal">Equal $ per rung</option><option value="level_income">Level income (equal cash / yr)</option>
            </select>
          </Field>
          <Field label="Account">
            <select className={selectCls} value={p.account_type} onChange={e => set({ account_type: e.target.value })}>
              {ACCOUNTS.map(a => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
          </Field>
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-4 text-[11px] text-base-content/60">
            <span>{inst?.hint}</span>
            {(p.instrument === 'best_after_tax' || p.instrument === 'corporate') && (
              <>
                {p.instrument === 'best_after_tax' && (
                  <label className="flex items-center gap-1.5"><input type="checkbox" className="checkbox checkbox-xs" checked={!!p.allow_credit} onChange={e => set({ allow_credit: e.target.checked })} /> Allow corporate credit risk</label>
                )}
                <span className="flex items-center gap-1.5">Corp rating
                  <Seg value={p.corporate_rating ?? 'A'} onChange={v => set({ corporate_rating: v })} options={['AA', 'A', 'BBB'].map(x => ({ value: x, label: x }))} />
                </span>
              </>
            )}
          </div>
          <button className="btn btn-primary btn-sm" onClick={build} disabled={loading}>{loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <BarChart3 className="h-3.5 w-3.5" />} Build ladder</button>
        </div>
        {err && <div className="mt-3"><ErrorBox message={err} /></div>}
      </Card>

      {loading && <Loading label="Pricing rungs off live curves & Treasury prices…" />}
      {ladder && !loading && (
        <>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {savingMsg && <span className="text-xs text-emerald-400">{savingMsg}</span>}
            <input className="input input-sm input-bordered w-64 bg-base-200/60" value={saveName} onChange={e => setSaveName(e.target.value)} placeholder="Ladder name" />
            <button className="btn btn-sm btn-outline" onClick={save}><Save className="h-3.5 w-3.5" /> Save plan</button>
          </div>
          <LadderPreview ladder={ladder} />
        </>
      )}

      <Card title="Saved ladders" subtitle="Plans you've built — open one to see which rungs you own, adopt it, or track it as a plan">
        {saved.length ? (
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {saved.map(l => (
              <button key={l.id} onClick={() => setOpenId(l.id)} className="rounded-xl border border-white/[0.06] bg-base-200/40 p-3 text-left transition hover:border-primary/40">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold">{l.name}</span>
                  <span className={`rounded-full border px-2 py-0.5 text-[9px] uppercase ${l.status === 'active' ? STATUS_STYLE.funded : STATUS_STYLE.planned}`}>{l.status}</span>
                </div>
                <div className="mt-1 text-[11px] text-base-content/50">
                  {l.ladder_type === 'tips' ? 'TIPS real income' : 'Nominal'} · {String(l.summary?.rungs ?? '—')} rungs · {usd(Number(l.summary?.invested ?? 0), { compact: true })}
                  {l.summary?.yield_pct != null && ` · ${pct(Number(l.summary.yield_pct))}`}
                  {l.summary?.annual_real_income != null && ` · ${usd(Number(l.summary.annual_real_income), { compact: true })}/yr real`}
                </div>
              </button>
            ))}
          </div>
        ) : <Empty title="No saved ladders yet" body="Build one above (or a TIPS income ladder on the TIPS tab) and save it to track rung-by-rung." />}
      </Card>
    </div>
  );
}
