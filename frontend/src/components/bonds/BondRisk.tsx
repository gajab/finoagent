import React, { useMemo } from 'react';
import { Activity, Gauge, ShieldAlert } from 'lucide-react';
import type { BondPortfolio } from '../../types';
import { AXIS, CREDIT_COLORS, Card, Chart, Donut, Note, Stat, TOOLTIP, num, pct, pnlClass, tenorLabel, usd } from './bondUi';

export default function BondRisk({ data }: { data: BondPortfolio }) {
  const s = data.summary;
  const sc = data.scenarios;

  const shockChart = useMemo(() => ({
    tooltip: { ...TOOLTIP, trigger: 'axis', valueFormatter: (v: number) => usd(v) },
    grid: { left: 8, right: 8, top: 16, bottom: 4, containLabel: true },
    xAxis: { type: 'category', data: sc.parallel.map(p => `${p.shift_bp > 0 ? '+' : ''}${p.shift_bp}bp`), ...AXIS },
    yAxis: { type: 'value', ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => usd(v, { compact: true }) } },
    series: [{ type: 'bar', name: 'P&L', data: sc.parallel.map(p => ({ value: p.pnl, itemStyle: { color: p.pnl >= 0 ? '#34d399' : '#f87171' } })),
      label: { show: true, position: 'top', color: '#94a3b8', fontSize: 9, formatter: (p: { value: number }) => usd(p.value, { compact: true }) } }],
  }), [sc.parallel]);

  const krdChart = useMemo(() => ({
    tooltip: { ...TOOLTIP, trigger: 'axis', valueFormatter: (v: number) => usd(v, { cents: true }) },
    grid: { left: 8, right: 8, top: 16, bottom: 4, containLabel: true },
    xAxis: { type: 'category', data: data.key_rate_dv01.map(k => tenorLabel(k.tenor)), ...AXIS },
    yAxis: { type: 'value', ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => `$${v}` } },
    series: [{ type: 'bar', name: 'DV01', data: data.key_rate_dv01.map(k => k.dv01), itemStyle: { color: '#60a5fa', borderRadius: [3, 3, 0, 0] } }],
  }), [data.key_rate_dv01]);

  const up100 = sc.parallel.find(p => p.shift_bp === 100);
  const dn100 = sc.parallel.find(p => p.shift_bp === -100);
  const convexityGain = (up100 && dn100) ? dn100.pnl + up100.pnl : null;
  const hz = data.profile.horizon_years;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-6">
        <Stat label="Effective duration" value={`${num(s.eff_duration, 2)}y`} hint="≈ % price move per 1% rate change" />
        <Stat label="Convexity" value={num(s.convexity, 1)} sub={convexityGain != null ? `±100bp asymmetry ${usd(convexityGain, { sign: true })}` : undefined} />
        <Stat label="DV01" value={usd(s.dv01, { cents: true })} sub="per 1bp parallel" />
        <Stat label="+100bp" value={<span className="text-rose-400">{usd(up100?.pnl, { compact: true })}</span>} sub={pct(up100?.pnl_pct, 2, true)} />
        <Stat label="−100bp" value={<span className="text-emerald-400">{usd(dn100?.pnl, { compact: true, sign: true })}</span>} sub={pct(dn100?.pnl_pct, 2, true)} />
        <Stat label="Horizon match" value={hz ? `${num(s.eff_duration, 1)}y vs ${hz}y` : 'set horizon'} sub={hz ? (Math.abs((s.eff_duration ?? 0) - hz) <= 1.5 ? 'immunized-ish' : ((s.eff_duration ?? 0) > hz ? 'price risk' : 'reinvestment risk')) : 'Tax & profile tab'} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card icon={<Activity className="h-4 w-4 text-rose-400" />} title="Parallel rate shocks" subtitle="Instant, full reprice of every bond to worst (calls honoured); funds via duration + convexity">
          <Chart option={shockChart} height={250} />
        </Card>
        <Card icon={<Gauge className="h-4 w-4 text-sky-400" />} title="Where your rate risk sits" subtitle="Key-rate DV01: $ change per 1bp move at each tenor — sums exactly to total DV01">
          <Chart option={krdChart} height={250} />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Curve twists" subtitle="Non-parallel moves via key-rate DV01">
          <table className="w-full text-xs">
            <tbody>
              {sc.twists.map(t => (
                <tr key={t.name} className="border-b border-white/[0.03]">
                  <td className="py-1.5"><div className="font-medium">{t.name}</div><div className="text-[10px] text-base-content/45">{t.description}</div></td>
                  <td className={`py-1.5 text-right font-semibold tabular-nums ${pnlClass(t.pnl)}`}>{usd(t.pnl, { sign: true })}</td>
                  <td className={`w-16 py-1.5 text-right tabular-nums ${pnlClass(t.pnl)}`}>{pct(t.pnl_pct, 2, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card icon={<ShieldAlert className="h-4 w-4 text-amber-400" />} title="Credit spread stress" subtitle="Spread widening with Treasury yields unchanged (spread duration ≈ duration)">
          <table className="w-full text-xs">
            <tbody>
              {sc.credit.map(t => (
                <tr key={t.name} className="border-b border-white/[0.03]">
                  <td className="py-1.5"><div className="font-medium">{t.name}</div><div className="text-[10px] text-base-content/45">{t.description}</div></td>
                  <td className={`py-1.5 text-right font-semibold tabular-nums ${pnlClass(t.pnl)}`}>{usd(t.pnl, { sign: true })}</td>
                  <td className={`w-16 py-1.5 text-right tabular-nums ${pnlClass(t.pnl)}`}>{pct(t.pnl_pct, 2, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card title="Credit quality"><Donut data={data.allocation.by_credit} colors={CREDIT_COLORS} height={170} /></Card>
        <Card title="Tax character"><Donut data={data.allocation.by_tax} height={170} /></Card>
        <Card title="Account"><Donut data={data.allocation.by_account} height={170} /></Card>
        <Card title="Top issuers (non-government)">
          {data.allocation.by_issuer.length ? (
            <ul className="space-y-1.5">
              {data.allocation.by_issuer.map(x => (
                <li key={x.key} className="text-[11px]">
                  <div className="flex justify-between gap-2"><span className="truncate text-base-content/75">{x.key}</span><span className="tabular-nums">{x.pct.toFixed(1)}%</span></div>
                  <div className="mt-0.5 h-1 rounded bg-base-300/60"><div className={`h-1 rounded ${x.pct > 10 ? 'bg-amber-400' : 'bg-sky-400/70'}`} style={{ width: `${Math.min(100, x.pct * 3)}%` }} /></div>
                </li>
              ))}
            </ul>
          ) : <p className="py-8 text-center text-xs text-base-content/40">Only government / fund exposure.</p>}
        </Card>
      </div>
      <Note>{sc.note}</Note>
    </div>
  );
}
