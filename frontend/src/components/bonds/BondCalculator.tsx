import React, { useState } from 'react';
import { Calculator, Loader2 } from 'lucide-react';
import { calcBond } from '../../api';
import type { BondCalcResult, BondKind } from '../../types';
import { Card, ErrorBox, Field, KIND_META, Stat, US_STATES, bp, fmtDate, inputCls, num, pct, pnlClass, selectCls, tenorLabel, usd } from './bondUi';

export default function BondCalculator() {
  const [t, setT] = useState<Record<string, string | number | null>>({
    kind: 'corporate', coupon_rate: 5, coupon_freq: 2, maturity_date: `${new Date().getFullYear() + 7}-06-15`, price: 98.5,
    yield_pct: '', face_value: 10000, call_date: '', call_price: '', account_type: 'taxable', state: '',
  });
  const [mode, setMode] = useState<'price' | 'yield'>('price');
  const [res, setRes] = useState<BondCalcResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const set = (k: string, v: string | number | null) => setT(p => ({ ...p, [k]: v }));
  const run = async () => {
    setLoading(true); setErr(null);
    const body: Record<string, unknown> = { ...t };
    if (mode === 'price') delete body.yield_pct; else delete body.price;
    for (const k of Object.keys(body)) if (body[k] === '') body[k] = null;
    try { setRes(await calcBond(body)); } catch (e) { setErr(e instanceof Error ? e.message : 'Failed'); } finally { setLoading(false); }
  };
  return (
    <Card icon={<Calculator className="h-4 w-4 text-primary" />} title="Bond calculator"
      subtitle="Price ⇄ yield, yield-to-worst, duration, convexity, DV01, after-tax (your profile), scenarios and the cash-flow schedule">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Field label="Type">
          <select className={selectCls} value={String(t.kind)} onChange={e => set('kind', e.target.value)}>
            {(['treasury', 'tips', 'muni', 'corporate', 'agency', 'cd'] as BondKind[]).map(k => <option key={k} value={k}>{KIND_META[k].label}</option>)}
          </select>
        </Field>
        <Field label="Coupon %"><input type="number" step="0.001" className={inputCls} value={t.coupon_rate ?? ''} onChange={e => set('coupon_rate', e.target.value)} /></Field>
        <Field label="Pays">
          <select className={selectCls} value={String(t.coupon_freq)} onChange={e => set('coupon_freq', Number(e.target.value))}>
            <option value="2">Semiannual</option><option value="12">Monthly</option><option value="4">Quarterly</option><option value="1">Annual</option><option value="0">Zero</option>
          </select>
        </Field>
        <Field label="Maturity"><input type="date" className={inputCls} value={String(t.maturity_date ?? '')} onChange={e => set('maturity_date', e.target.value)} /></Field>
        <Field label="Face ($)"><input type="number" className={inputCls} value={t.face_value ?? ''} onChange={e => set('face_value', e.target.value)} /></Field>
        <Field label="Solve from">
          <div className="join w-full">
            <button className={`btn btn-sm join-item flex-1 ${mode === 'price' ? 'btn-primary' : ''}`} onClick={() => setMode('price')}>Price</button>
            <button className={`btn btn-sm join-item flex-1 ${mode === 'yield' ? 'btn-primary' : ''}`} onClick={() => setMode('yield')}>Yield</button>
          </div>
        </Field>
        {mode === 'price'
          ? <Field label="Clean price (per 100)"><input type="number" step="0.001" className={inputCls} value={t.price ?? ''} onChange={e => set('price', e.target.value)} /></Field>
          : <Field label="Yield %"><input type="number" step="0.001" className={inputCls} value={t.yield_pct ?? ''} onChange={e => set('yield_pct', e.target.value)} /></Field>}
        <Field label="Call date (opt.)"><input type="date" className={inputCls} value={String(t.call_date ?? '')} onChange={e => set('call_date', e.target.value)} /></Field>
        <Field label="Call price"><input type="number" className={inputCls} value={t.call_price ?? ''} placeholder="100" onChange={e => set('call_price', e.target.value)} /></Field>
        <Field label="Account">
          <select className={selectCls} value={String(t.account_type)} onChange={e => set('account_type', e.target.value)}><option value="taxable">Taxable</option><option value="ira">IRA</option></select>
        </Field>
        <Field label="Muni state">
          <select className={selectCls} value={String(t.state ?? '')} disabled={t.kind !== 'muni'} onChange={e => set('state', e.target.value)}>
            <option value="">—</option>{US_STATES.map(s => <option key={s}>{s}</option>)}
          </select>
        </Field>
        <div className="flex items-end"><button className="btn btn-primary btn-sm w-full" onClick={run} disabled={loading}>{loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Calculate'}</button></div>
      </div>
      {err && <div className="mt-3"><ErrorBox message={err} /></div>}
      {res && (
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
            <Stat label="Clean / dirty" value={num(res.clean_price, 3)} sub={`dirty ${num(res.dirty_price, 3)} · AI ${usd(res.accrued_usd, { cents: true })}`} />
            <Stat label="Cost" value={usd(res.cost_usd, { cents: true })} sub={`${num(res.years_to_maturity, 2)}y to maturity`} />
            <Stat label="YTM" value={pct(res.ytm_pct, 3)} sub={`current ${pct(res.current_yield_pct, 3)}`} />
            <Stat label="Yield to worst" value={pct(res.ytw_pct, 3)} sub={res.ytw_kind === 'call' ? `to call ${fmtDate(res.ytw_date)}` : 'to maturity'} accent={res.likely_called ? 'text-amber-400' : undefined} />
            <Stat label="After-tax" value={pct(res.tax.after_tax_yield_pct, 3)} sub={`TEY ${pct(res.tax.tey_pct, 3)}`} accent="text-emerald-400" />
            <Stat label="Duration" value={num(res.eff_duration, 3)} sub={`mod ${num(res.mod_duration, 3)} · mac ${num(res.mac_duration, 3)}`} />
            <Stat label="Convexity" value={num(res.eff_convexity, 2)} sub={`option-free ${num(res.convexity, 2)}`} />
            <Stat label="DV01" value={usd(res.dv01_usd, { cents: true })} sub={res.spread_to_treasury_bp != null ? `spread ${bp(res.spread_to_treasury_bp)}` : `UST ${pct(res.treasury_at_maturity_pct)}`} />
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase text-base-content/45">Instant rate shocks</div>
              <table className="w-full text-xs"><tbody>
                {res.scenarios.map(s => (
                  <tr key={s.shift_bp} className="border-t border-white/[0.03]">
                    <td className="py-1">{s.shift_bp > 0 ? '+' : ''}{s.shift_bp}bp</td>
                    <td className="py-1 text-right tabular-nums">{num(s.price, 3)}</td>
                    <td className={`py-1 text-right tabular-nums ${pnlClass(s.pnl)}`}>{usd(s.pnl, { sign: true })}</td>
                    <td className={`py-1 text-right tabular-nums ${pnlClass(s.pnl)}`}>{pct(s.pnl_pct, 2, true)}</td>
                    <td className="py-1 text-right text-[10px] tabular-nums text-base-content/40" title="Duration + convexity estimate">≈{pct(s.duration_estimate_pct, 2, true)}</td>
                  </tr>
                ))}
              </tbody></table>
              <div className="mt-2 text-[10px] text-base-content/45">1-year total return: {res.horizon_1y.map(h => `${h.shift_bp > 0 ? '+' : ''}${h.shift_bp}bp → ${pct(h.total_return_pct, 2, true)}`).join(' · ')}</div>
            </div>
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase text-base-content/45">Tax</div>
              <ul className="space-y-1 text-[11px] text-base-content/65">
                <li>Federal {res.tax.fed_taxable ? 'taxable' : 'exempt'} · State {res.tax.state_taxable ? 'taxable' : 'exempt'} · your rate {pct(res.tax.rate_pct, 1)}</li>
                {res.de_minimis_price != null && <li>De-minimis price if bought today: {num(res.de_minimis_price, 3)}</li>}
                {res.tax.notes.map((n, i) => <li key={i} className="text-[10px] text-base-content/45">• {n}</li>)}
              </ul>
              <div className="mt-2 text-[10px] font-semibold uppercase text-base-content/45">Key-rate durations</div>
              <div className="mt-1 flex flex-wrap gap-1">{res.krd.map(k => <span key={k.tenor} className="rounded bg-base-200/60 px-1.5 py-0.5 text-[10px]">{tenorLabel(k.tenor)} {k.duration.toFixed(2)}</span>)}</div>
            </div>
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase text-base-content/45">Cash flows ({res.cash_flows.length})</div>
              <div className="max-h-48 overflow-auto">
                <table className="w-full text-[11px]"><tbody>
                  {res.cash_flows.map((c, i) => (
                    <tr key={i} className="border-t border-white/[0.03]"><td className="py-0.5 tabular-nums text-base-content/55">{fmtDate(c.date)}</td>
                      <td className="py-0.5 text-right tabular-nums">{usd(c.coupon, { cents: true })}</td>
                      <td className="py-0.5 text-right tabular-nums text-emerald-400/80">{c.principal ? usd(c.principal) : ''}</td></tr>
                  ))}
                </tbody></table>
              </div>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}
