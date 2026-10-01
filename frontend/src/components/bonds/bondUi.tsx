import React from 'react';
import ReactECharts from 'echarts-for-react';
import { AlertTriangle, Info, Loader2 } from 'lucide-react';
import type { BondAllocSlice, BondKind, BondRecommendation, BondYieldParts } from '../../types';

// ---------------------------------------------------------------------------
// Palette + labels
// ---------------------------------------------------------------------------
export const KIND_META: Record<BondKind, { label: string; short: string; color: string }> = {
  treasury:    { label: 'Treasury',        short: 'UST',  color: '#60a5fa' },
  tips:        { label: 'TIPS',            short: 'TIPS', color: '#34d399' },
  muni:        { label: 'Municipal',       short: 'MUNI', color: '#a78bfa' },
  corporate:   { label: 'Corporate',       short: 'CORP', color: '#f59e0b' },
  agency:      { label: 'Agency',          short: 'AGCY', color: '#22d3ee' },
  cd:          { label: 'CD',              short: 'CD',   color: '#f472b6' },
  etf:         { label: 'Bond ETF',        short: 'ETF',  color: '#94a3b8' },
  mutual_fund: { label: 'Bond fund',       short: 'FUND', color: '#fb923c' },
};
export const KIND_ORDER: BondKind[] = ['treasury', 'tips', 'muni', 'corporate', 'agency', 'cd', 'etf', 'mutual_fund'];
export const LABEL_COLOR: Record<string, string> = Object.fromEntries(
  Object.values(KIND_META).map(m => [m.label, m.color]),
);
export const FLOW_COLORS = { coupon: '#60a5fa', principal: '#34d399', distribution: '#a78bfa', call: '#f59e0b' };
export const CREDIT_COLORS: Record<string, string> = {
  GOVT: '#60a5fa', AAA: '#34d399', AA: '#4ade80', A: '#a3e635', BBB: '#facc15', BB: '#fb923c', B: '#f87171', CCC: '#ef4444', NR: '#64748b',
};
export const ACCOUNTS: { value: string; label: string }[] = [
  { value: 'taxable', label: 'Taxable' }, { value: 'ira', label: 'Traditional IRA' }, { value: 'roth', label: 'Roth IRA' },
  { value: '401k', label: '401(k)' }, { value: '403b', label: '403(b)' }, { value: 'hsa', label: 'HSA' }, { value: '529', label: '529' },
];
export const US_STATES = 'AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA PR RI SC SD TN TX UT VT VA WA WV WI WY'.split(' ');

// ---------------------------------------------------------------------------
// Formatters (null-safe — never .toFixed an optional field unguarded)
// ---------------------------------------------------------------------------
const isNum = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);

export function usd(n: number | null | undefined, opts: { compact?: boolean; cents?: boolean; sign?: boolean } = {}): string {
  if (!isNum(n)) return '—';
  const sign = opts.sign && n > 0 ? '+' : '';
  if (opts.compact && Math.abs(n) >= 1000) {
    const abs = Math.abs(n);
    const s = abs >= 1e9 ? `${(abs / 1e9).toFixed(2)}B` : abs >= 1e6 ? `${(abs / 1e6).toFixed(2)}M` : `${(abs / 1e3).toFixed(1)}k`;
    return `${n < 0 ? '−' : sign}$${s}`;
  }
  const v = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: opts.cents ? 2 : 0, maximumFractionDigits: opts.cents ? 2 : 0 });
  return `${n < 0 ? '−' : sign}$${v}`;
}

export function pct(n: number | null | undefined, d = 2, sign = false): string {
  if (!isNum(n)) return '—';
  return `${sign && n > 0 ? '+' : ''}${n.toFixed(d)}%`;
}

export function num(n: number | null | undefined, d = 2): string {
  return isNum(n) ? n.toFixed(d) : '—';
}

export function bp(n: number | null | undefined): string {
  return isNum(n) ? `${n > 0 ? '+' : ''}${Math.round(n)}bp` : '—';
}

export function tenorLabel(t: number): string {
  if (t < 1) return `${Math.round(t * 12)}M`;
  return `${Number.isInteger(t) ? t : t.toFixed(1)}Y`;
}

export function fmtDate(s: string | null | undefined): string {
  if (!s) return '—';
  const d = new Date(`${s.slice(0, 10)}T00:00:00`);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

export function pnlClass(n: number | null | undefined): string {
  if (!isNum(n) || n === 0) return 'text-base-content/70';
  return n > 0 ? 'text-emerald-400' : 'text-rose-400';
}

// ---------------------------------------------------------------------------
// Atoms
// ---------------------------------------------------------------------------
export const Card: React.FC<{
  title?: React.ReactNode; subtitle?: React.ReactNode; right?: React.ReactNode; className?: string;
  icon?: React.ReactNode; children: React.ReactNode; pad?: boolean;
}> = ({ title, subtitle, right, className = '', icon, children, pad = true }) => (
  <section className={`rounded-2xl border border-white/[0.06] bg-base-100/60 shadow-lg ${className}`}>
    {(title || right) && (
      <header className="flex items-start justify-between gap-3 px-4 pt-4 pb-2">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-base-content/90">{icon}{title}</h3>
          {subtitle && <p className="mt-0.5 text-[11px] leading-snug text-base-content/45">{subtitle}</p>}
        </div>
        {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
      </header>
    )}
    <div className={pad ? 'px-4 pb-4' : ''}>{children}</div>
  </section>
);

export const Stat: React.FC<{ label: string; value: React.ReactNode; sub?: React.ReactNode; accent?: string; hint?: string }> = ({
  label, value, sub, accent, hint,
}) => (
  <div className="rounded-xl border border-white/[0.05] bg-base-200/50 px-3 py-2.5" title={hint}>
    <div className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-base-content/45">
      {label}{hint && <Info className="h-2.5 w-2.5 opacity-50" />}
    </div>
    <div className={`mt-0.5 text-base font-bold tabular-nums ${accent ?? 'text-base-content'}`}>{value}</div>
    {sub && <div className="text-[10px] text-base-content/45">{sub}</div>}
  </div>
);

export const KindBadge: React.FC<{ kind: BondKind; className?: string }> = ({ kind, className = '' }) => {
  const m = KIND_META[kind] ?? { short: kind, color: '#94a3b8' };
  return (
    <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[9px] font-bold tracking-wide ${className}`}
      style={{ color: m.color, backgroundColor: `${m.color}1f`, border: `1px solid ${m.color}40` }}>
      {m.short}
    </span>
  );
};

const SEV: Record<BondRecommendation['severity'], string> = {
  high: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
  medium: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  low: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  info: 'bg-slate-500/15 text-slate-300 border-slate-500/30',
};
export const SeverityBadge: React.FC<{ severity: BondRecommendation['severity'] }> = ({ severity }) => (
  <span className={`rounded-full border px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${SEV[severity]}`}>{severity}</span>
);

export const Seg: React.FC<{ value: string; onChange: (v: string) => void; options: { value: string; label: React.ReactNode }[]; size?: 'xs' | 'sm' }> = ({
  value, onChange, options, size = 'xs',
}) => (
  <div className="inline-flex rounded-lg border border-white/[0.06] bg-base-200/60 p-0.5">
    {options.map(o => (
      <button key={o.value} type="button" onClick={() => onChange(o.value)}
        className={`rounded-md px-2.5 ${size === 'xs' ? 'py-1 text-[11px]' : 'py-1.5 text-xs'} font-medium transition-colors ${
          value === o.value ? 'bg-primary/20 text-primary' : 'text-base-content/55 hover:text-base-content'}`}>
        {o.label}
      </button>
    ))}
  </div>
);

export const Loading: React.FC<{ label?: string }> = ({ label = 'Loading…' }) => (
  <div className="flex items-center justify-center gap-2 py-16 text-sm text-base-content/50">
    <Loader2 className="h-4 w-4 animate-spin" /> {label}
  </div>
);

export const ErrorBox: React.FC<{ message: string; onRetry?: () => void }> = ({ message, onRetry }) => (
  <div className="flex items-start gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2.5 text-xs text-rose-200">
    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
    <div className="flex-1">{message}</div>
    {onRetry && <button onClick={onRetry} className="text-rose-100 underline">Retry</button>}
  </div>
);

export const Empty: React.FC<{ icon?: React.ReactNode; title: string; body?: React.ReactNode; action?: React.ReactNode }> = ({
  icon, title, body, action,
}) => (
  <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
    {icon && <div className="text-base-content/30">{icon}</div>}
    <div className="text-sm font-semibold text-base-content/80">{title}</div>
    {body && <div className="max-w-md text-xs text-base-content/50">{body}</div>}
    {action && <div className="mt-2">{action}</div>}
  </div>
);

// Total yield split into where it comes from: cash income + price gain to maturity (pull-to-par / fund accrual;
// negative = premium you'll lose) + TIPS inflation. The parts always sum to the total.
export const YIELD_PART_META = [
  { key: 'income_pct', label: 'income', color: '#60a5fa', help: 'Coupons and fund distributions you receive in cash' },
  { key: 'price_gain_pct', label: 'price gain', color: '#34d399', help: 'Bought below par → paid back at 100 at maturity; accumulating funds (BOXX) grow in price. Negative = premium paid above par' },
  { key: 'inflation_pct', label: 'inflation', color: '#f59e0b', help: 'TIPS principal grows with CPI (expected path)' },
] as const;

export const YieldBreakdown: React.FC<{ parts: BondYieldParts | null | undefined; compact?: boolean; className?: string }> = ({ parts, compact = false, className = '' }) => {
  if (!parts || !isNum(parts.total_pct)) return null;
  const shown = YIELD_PART_META.filter(m => isNum(parts[m.key]) && Math.abs(parts[m.key] as number) >= 0.005);
  const pos = shown.reduce((a, m) => a + Math.max(0, parts[m.key] as number), 0);
  return (
    <div className={className}>
      {!compact && pos > 0 && (
        <div className="mb-1 flex h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
          {shown.filter(m => (parts[m.key] as number) > 0).map(m => (
            <div key={m.key} style={{ width: `${(100 * (parts[m.key] as number)) / pos}%`, backgroundColor: m.color }} title={m.help} />
          ))}
        </div>
      )}
      <div className="flex flex-wrap gap-x-2 gap-y-0.5 text-[10.5px] text-base-content/55">
        {shown.map((m, i) => (
          <span key={m.key} title={m.help} className="tabular-nums">
            {i > 0 && <span className="mr-1 text-base-content/30">{(parts[m.key] as number) < 0 ? '−' : '+'}</span>}
            <span style={{ color: m.color }}>{i === 0 ? pct(parts[m.key]) : pct(Math.abs(parts[m.key] as number))}</span> {m.label}
          </span>
        ))}
      </div>
    </div>
  );
};

export const Note: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <p className="flex items-start gap-1.5 text-[10.5px] leading-snug text-base-content/45">
    <Info className="mt-0.5 h-3 w-3 shrink-0" /><span>{children}</span>
  </p>
);

export const Field: React.FC<{ label: string; hint?: string; children: React.ReactNode; className?: string }> = ({ label, hint, children, className = '' }) => (
  <label className={`flex flex-col gap-1 ${className}`}>
    <span className="text-[10px] font-medium uppercase tracking-wide text-base-content/50" title={hint}>{label}</span>
    {children}
  </label>
);

export const inputCls = 'input input-sm input-bordered w-full bg-base-200/60 text-sm';
export const selectCls = 'select select-sm select-bordered w-full bg-base-200/60 text-sm';

// ---------------------------------------------------------------------------
// Charts
// ---------------------------------------------------------------------------
export const AXIS = {
  axisLabel: { color: '#94a3b8', fontSize: 10 },
  axisLine: { lineStyle: { color: '#334155' } },
  splitLine: { lineStyle: { color: 'rgba(100,116,139,0.12)' } },
};
export const TOOLTIP = {
  confine: true, backgroundColor: 'rgba(15,23,42,0.95)', borderColor: '#334155', textStyle: { color: '#e2e8f0', fontSize: 11 },
};
export const LEGEND = { textStyle: { color: '#94a3b8', fontSize: 10 }, itemWidth: 10, itemHeight: 8, top: 0 };

export const Chart: React.FC<{ option: Record<string, unknown>; height?: number }> = ({ option, height = 260 }) => (
  <ReactECharts option={option} style={{ height, width: '100%' }} notMerge lazyUpdate />
);

export const Donut: React.FC<{ data: BondAllocSlice[]; colors?: Record<string, string>; height?: number; valueFmt?: (v: number) => string }> = ({
  data, colors = {}, height = 200, valueFmt = v => usd(v, { compact: true }),
}) => {
  if (!data?.length) return <div className="py-10 text-center text-xs text-base-content/40">No data</div>;
  const fallback = ['#60a5fa', '#34d399', '#a78bfa', '#f59e0b', '#22d3ee', '#f472b6', '#94a3b8', '#fb923c', '#facc15'];
  const option = {
    tooltip: { ...TOOLTIP, trigger: 'item', formatter: (p: { name: string; value: number; percent: number }) => `${p.name}<br/><b>${valueFmt(p.value)}</b> · ${p.percent.toFixed(1)}%` },
    series: [{
      type: 'pie', radius: ['58%', '82%'], center: ['50%', '50%'], avoidLabelOverlap: true,
      label: { show: false }, itemStyle: { borderColor: '#0f172a', borderWidth: 2 },
      data: data.map((d, i) => ({ name: d.key, value: d.value, itemStyle: { color: colors[d.key] ?? fallback[i % fallback.length] } })),
    }],
  };
  return (
    <div className="flex items-center gap-3">
      <div className="w-1/2 min-w-[120px]"><Chart option={option} height={height} /></div>
      <ul className="flex-1 space-y-1">
        {data.slice(0, 8).map((d, i) => (
          <li key={d.key} className="flex items-center justify-between gap-2 text-[11px]">
            <span className="flex min-w-0 items-center gap-1.5 text-base-content/70">
              <span className="h-2 w-2 shrink-0 rounded-sm" style={{ backgroundColor: colors[d.key] ?? fallback[i % fallback.length] }} />
              <span className="truncate">{d.key}</span>
            </span>
            <span className="tabular-nums text-base-content/85">{d.pct.toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
};
