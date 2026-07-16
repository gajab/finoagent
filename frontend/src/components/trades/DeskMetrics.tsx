/**
 * DeskMetrics — the shared Trader / PM / Risk metric grids.
 *
 * One source of truth for how the desk numbers are laid out, reused by BOTH the
 * live-position LifecyclePanel (My Trades) and the pre-trade PreTradeAdvisor
 * (Dual Direction Buffer / Hedging / Derivative Income). Callers pass a plain
 * data object; these components only render.
 */
import { fmtMoney } from '../../lib/tradeFormat';

export function num(v: number | null | undefined, digits = 2, suffix = ''): string {
  return v == null ? '—' : `${v.toFixed(digits)}${suffix}`;
}

export function Metric({ label, value, hint, color = '' }: { label: string; value: string; hint?: string; color?: string }) {
  return (
    <div className="bg-base-300/20 rounded-lg p-2 text-center" title={hint}>
      <div className="text-[9px] uppercase text-base-content/30 tracking-wider">{label}</div>
      <div className={`text-xs font-semibold mt-0.5 ${color}`}>{value}</div>
    </div>
  );
}

export interface TraderMetricsData {
  net_delta?: number | null; net_gamma?: number | null; net_vega?: number | null; net_theta?: number | null;
  net_vanna?: number | null; net_charm?: number | null; net_volga?: number | null; avg_iv_pct?: number | null;
}

export function TraderGrid({ t }: { t: TraderMetricsData }) {
  return (
    <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
      <Metric label="Net Δ Delta" value={num(t.net_delta, 1)} hint="Directional exposure (share-equivalents)" color={(t.net_delta ?? 0) >= 0 ? 'text-success/80' : 'text-error/80'} />
      <Metric label="Γ Gamma" value={num(t.net_gamma, 3)} hint="Δ change per +$1 in the underlying" />
      <Metric label="ν Vega" value={fmtMoney(t.net_vega ?? 0)} hint="$ P&L per +1 vol-point" />
      <Metric label="Θ Theta/d" value={fmtMoney(t.net_theta ?? 0)} hint="$ decay per calendar day" color={(t.net_theta ?? 0) >= 0 ? 'text-success/80' : 'text-warning/80'} />
      <Metric label="Vanna" value={num(t.net_vanna, 2)} hint="Δ drift per +1 vol-point (∂Δ/∂σ)" />
      <Metric label="Charm" value={num(t.net_charm, 3)} hint="Δ decay per +1 day (∂Δ/∂t)" />
      <Metric label="Volga" value={num(t.net_volga, 2)} hint="Vega change per +1 vol-point (∂ν/∂σ)" />
      <Metric label="Impl. Vol" value={num(t.avg_iv_pct, 1, '%')} hint="Average implied vol across legs" />
    </div>
  );
}

export interface PmMetricsData {
  omega?: number | null; sortino?: number | null; calmar?: number | null; expected_return_pct?: number | null;
  pop?: number | null; expected_value?: number | null; kelly_fraction?: number | null; downside_dev_pct?: number | null;
}

export function PmGrid({ pm }: { pm: PmMetricsData }) {
  return (
    <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
      <Metric label="Omega" value={num(pm.omega, 2)} hint="Prob-weighted gains ÷ losses (>1 = favorable)" color={(pm.omega ?? 0) >= 1 ? 'text-success/80' : 'text-error/80'} />
      <Metric label="Sortino" value={num(pm.sortino, 2)} hint="Return per unit of downside deviation" color={(pm.sortino ?? 0) >= 1 ? 'text-success/80' : ''} />
      <Metric label="Calmar" value={num(pm.calmar, 2)} hint="Annualized return ÷ worst-case drawdown" />
      <Metric label="Exp. Return" value={num(pm.expected_return_pct, 1, '%')} hint="Option-implied expected return on capital" color={(pm.expected_return_pct ?? 0) >= 0 ? 'text-success/80' : 'text-error/80'} />
      <Metric label="Prob of Profit" value={num(pm.pop ?? null, 1, '%')} hint="Chance the trade is profitable at expiry" />
      <Metric label="Expected Value" value={pm.expected_value != null ? fmtMoney(pm.expected_value) : '—'} hint="Probability-weighted $ outcome" color={(pm.expected_value ?? 0) >= 0 ? 'text-success/80' : 'text-error/80'} />
      <Metric label="Kelly" value={pm.kelly_fraction != null ? `${(pm.kelly_fraction * 100).toFixed(1)}%` : '—'} hint="Kelly-optimal position fraction" />
      <Metric label="Downside σ" value={num(pm.downside_dev_pct, 1, '%')} hint="Downside deviation of returns" />
    </div>
  );
}

export interface RiskMetricsData {
  var_95?: number | null; cvar_95?: number | null; max_loss?: number | null; max_profit?: number | null; capital?: number | null;
}

export function RiskGrid({ r }: { r: RiskMetricsData }) {
  const tailPct = r.cvar_95 != null && r.capital ? (r.cvar_95 / r.capital) * 100 : null;
  return (
    <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
      <Metric label="VaR 95%" value={r.var_95 != null ? fmtMoney(r.var_95) : '—'} hint="1-in-20 loss over the horizon (position)" color="text-warning/80" />
      <Metric label="CVaR 95%" value={r.cvar_95 != null ? fmtMoney(r.cvar_95) : '—'} hint="Expected shortfall — average of the worst 5% outcomes" color="text-error/80" />
      <Metric label="Tail / Capital" value={tailPct != null ? `${tailPct.toFixed(0)}%` : '—'} hint="CVaR95 as a fraction of capital committed" color={tailPct != null && tailPct <= 25 ? 'text-success/80' : 'text-warning/80'} />
      <Metric label="Max Profit" value={r.max_profit != null ? fmtMoney(r.max_profit) : '—'} hint="Best-case payoff" color="text-success/80" />
      <Metric label="Max Loss" value={r.max_loss != null ? fmtMoney(r.max_loss) : '—'} hint="Worst-case payoff (deep tail)" color="text-error/80" />
      <Metric label="Capital" value={r.capital != null ? fmtMoney(r.capital) : '—'} hint="Total capital committed" />
    </div>
  );
}
