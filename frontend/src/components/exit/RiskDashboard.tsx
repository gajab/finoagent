import React from 'react';
import { AlertTriangle, Activity, BarChart3, TrendingDown, Shield, Target, Info } from 'lucide-react';
import type { ExitRiskData, ExitLiquidityData } from '../../types';

/* Inline tooltip for metric labels */
function Tip({ text }: { text: string }) {
  return (
    <span className="relative group/tip inline-flex ml-0.5">
      <Info className="w-3 h-3 text-base-content/25 cursor-help hover:text-base-content/50 transition-colors" />
      <span className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-2 rounded-xl bg-neutral/95 backdrop-blur-sm text-neutral-content text-[10px] whitespace-normal opacity-0 group-hover/tip:opacity-100 transition-opacity duration-200 z-50 shadow-xl shadow-black/30 max-w-[240px] text-center leading-relaxed border border-white/[0.06]">
        {text}
      </span>
    </span>
  );
}

interface Props {
  risk: ExitRiskData;
  liquidity: ExitLiquidityData;
}

function fmt(val: number | null | undefined): string {
  if (val === null || val === undefined) return 'N/A';
  const abs = Math.abs(val);
  const sign = val < 0 ? '-' : '';
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

function fmtVol(val: number | null | undefined): string {
  if (val === null || val === undefined) return 'N/A';
  const abs = Math.abs(val);
  if (abs >= 1e6) return `${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(abs / 1e3).toFixed(0)}K`;
  return abs.toFixed(0);
}

function ratioColor(val: number | null, goodThreshold: number, badThreshold: number, higherIsBetter = true): string {
  if (val === null || val === undefined) return 'text-base-content/50';
  if (higherIsBetter) {
    if (val >= goodThreshold) return 'text-success';
    if (val >= badThreshold) return 'text-warning';
    return 'text-error';
  }
  if (val <= goodThreshold) return 'text-success';
  if (val <= badThreshold) return 'text-warning';
  return 'text-error';
}

export function RiskDashboard({ risk, liquidity }: Props) {
  return (
    <div className="space-y-4">
      {/* Core Risk Metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3">
        <div className="metric-card text-left">
          <div className="text-xs text-base-content/50 flex items-center gap-1 mb-1.5">
            <Activity className="w-3 h-3" /> Beta <Tip text="Measures stock sensitivity to market moves. 1.0 = moves with market, >1.5 = amplified risk" />
          </div>
          <div className={`text-xl font-black tabular-nums ${risk.beta != null && risk.beta > 1.5 ? 'text-error' : risk.beta != null && risk.beta > 1 ? 'text-warning' : 'text-success'}`}>
            {risk.beta != null ? risk.beta.toFixed(2) : 'N/A'}
          </div>
          <div className="text-[10px] text-base-content/30 mt-0.5">{risk.beta_label}</div>
        </div>

        <div className="metric-card text-left">
          <div className="text-xs text-base-content/50 flex items-center gap-1 mb-1.5">
            <BarChart3 className="w-3 h-3" /> Volatility <Tip text="Annualized price swing. >40% is high, <20% is stable" />
          </div>
          <div className={`text-xl font-black tabular-nums ${risk.annualized_volatility != null && risk.annualized_volatility > 0.5 ? 'text-error' : risk.annualized_volatility != null && risk.annualized_volatility > 0.3 ? 'text-warning' : 'text-success'}`}>
            {risk.annualized_volatility != null ? `${(risk.annualized_volatility * 100).toFixed(1)}%` : 'N/A'}
          </div>
        </div>

        <div className="metric-card text-left">
          <div className="text-xs text-base-content/50 flex items-center gap-1 mb-1.5">
            <TrendingDown className="w-3 h-3" /> Max DD <Tip text="Largest peak-to-trough decline in the past year" />
          </div>
          <div className="text-xl font-black text-error tabular-nums">
            {risk.max_drawdown_1y != null ? `${(risk.max_drawdown_1y * 100).toFixed(1)}%` : 'N/A'}
          </div>
          {risk.max_drawdown_duration_days != null && (
            <div className="text-[10px] text-base-content/30 mt-0.5">{risk.max_drawdown_duration_days} days</div>
          )}
        </div>

        <div className="metric-card text-left">
          <div className="text-xs text-base-content/50 flex items-center gap-1 mb-1.5">
            Sharpe <Tip text="Return per unit of total risk. >1.0 good, >2.0 excellent, <0 losing money" />
          </div>
          <div className={`text-xl font-black tabular-nums ${ratioColor(risk.sharpe_ratio_1y, 1.0, 0.5)}`}>
            {risk.sharpe_ratio_1y != null ? risk.sharpe_ratio_1y.toFixed(2) : 'N/A'}
          </div>
        </div>

        <div className="metric-card text-left">
          <div className="text-xs text-base-content/50 flex items-center gap-1 mb-1.5">
            Sortino <Tip text="Like Sharpe but only penalizes downside risk. >1.5 is good" />
          </div>
          <div className={`text-xl font-black tabular-nums ${ratioColor(risk.sortino_ratio_1y, 1.5, 0.8)}`}>
            {risk.sortino_ratio_1y != null ? risk.sortino_ratio_1y.toFixed(2) : 'N/A'}
          </div>
        </div>
      </div>

      {/* Advanced Risk Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="metric-card text-left">
          <div className="text-xs text-base-content/50 flex items-center gap-1 mb-1">
            Calmar <Tip text="Annual return / max drawdown. >1.0 = returns exceed worst loss" />
          </div>
          <div className={`text-sm font-bold tabular-nums ${ratioColor(risk.calmar_ratio, 1.0, 0.3)}`}>
            {risk.calmar_ratio != null ? risk.calmar_ratio.toFixed(2) : 'N/A'}
          </div>
          <div className="text-[10px] text-base-content/25">Return / Max DD</div>
        </div>

        <div className="metric-card text-left">
          <div className="text-xs text-base-content/50 flex items-center gap-1 mb-1">
            Ulcer Index <Tip text="Measures depth and duration of drawdowns. <5% excellent, >15% signals chronic pain" />
          </div>
          <div className={`text-sm font-bold tabular-nums ${ratioColor(risk.ulcer_index, 5, 15, false)}`}>
            {risk.ulcer_index != null ? `${risk.ulcer_index}%` : 'N/A'}
          </div>
          <div className="text-[10px] text-base-content/25">RMS Drawdown</div>
        </div>

        <div className="metric-card text-left">
          <div className="text-xs text-base-content/50 flex items-center gap-1 mb-1">
            Skewness <Tip text="Return asymmetry. Negative = more extreme losses, Positive = more extreme gains" />
          </div>
          <div className={`text-sm font-bold tabular-nums ${risk.skewness != null ? (risk.skewness < -0.5 ? 'text-error' : risk.skewness > 0.5 ? 'text-success' : '') : 'text-base-content/50'}`}>
            {risk.skewness != null ? risk.skewness.toFixed(3) : 'N/A'}
          </div>
          <div className="text-[10px] text-base-content/25">{risk.skewness != null ? (risk.skewness < -0.5 ? 'Left-tail heavy' : risk.skewness > 0.5 ? 'Right-tail heavy' : 'Symmetric') : ''}</div>
        </div>

        <div className="metric-card text-left">
          <div className="text-xs text-base-content/50 flex items-center gap-1 mb-1">
            Kurtosis <Tip text="Tail thickness. >3 = fat tails (more black swan events). 0 = normal" />
          </div>
          <div className={`text-sm font-bold tabular-nums ${risk.kurtosis != null && risk.kurtosis > 3 ? 'text-error' : ''}`}>
            {risk.kurtosis != null ? risk.kurtosis.toFixed(3) : 'N/A'}
          </div>
          <div className="text-[10px] text-base-content/25">{risk.kurtosis != null ? (risk.kurtosis > 3 ? 'Fat tails' : risk.kurtosis > 0 ? 'Moderate tails' : 'Thin tails') : ''}</div>
        </div>
      </div>

      {/* Volatility Breakdown + Win/Loss */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="glass-card p-4">
          <h5 className="text-xs font-bold mb-3 flex items-center gap-1.5 text-base-content/60">
            <BarChart3 className="w-3.5 h-3.5 text-info" />
            Volatility Breakdown
          </h5>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="text-[10px] text-base-content/40 flex items-center gap-0.5 mb-0.5">Upside Vol <Tip text="Volatility from positive returns — the 'good' volatility" /></div>
              <div className="text-sm font-bold text-success tabular-nums">
                {risk.upside_volatility != null ? `${(risk.upside_volatility * 100).toFixed(1)}%` : 'N/A'}
              </div>
            </div>
            <div>
              <div className="text-[10px] text-base-content/40 flex items-center gap-0.5 mb-0.5">Downside Vol <Tip text="Volatility from negative returns — the risk you care about" /></div>
              <div className="text-sm font-bold text-error tabular-nums">
                {risk.downside_volatility != null ? `${(risk.downside_volatility * 100).toFixed(1)}%` : 'N/A'}
              </div>
            </div>
            <div>
              <div className="text-[10px] text-base-content/40 flex items-center gap-0.5 mb-0.5">Tail Risk <Tip text="CVaR/VaR ratio. >1.5 = losses cluster in the tail (fat tails)" /></div>
              <div className={`text-sm font-bold tabular-nums ${risk.tail_risk_ratio != null && risk.tail_risk_ratio > 1.5 ? 'text-error' : ''}`}>
                {risk.tail_risk_ratio != null ? `${risk.tail_risk_ratio}x` : 'N/A'}
              </div>
            </div>
            <div>
              <div className="text-[10px] text-base-content/40 flex items-center gap-0.5 mb-0.5">Gain/Pain <Tip text="Sum of gains / sum of losses. >1.0 = gains exceed losses" /></div>
              <div className={`text-sm font-bold tabular-nums ${ratioColor(risk.gain_to_pain_ratio, 1.0, 0.5)}`}>
                {risk.gain_to_pain_ratio != null ? risk.gain_to_pain_ratio.toFixed(2) : 'N/A'}
              </div>
            </div>
          </div>
        </div>

        <div className="glass-card p-4">
          <h5 className="text-xs font-bold mb-3 flex items-center gap-1.5 text-base-content/60">
            <Target className="w-3.5 h-3.5 text-warning" />
            Win / Loss Profile
          </h5>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <div className="text-[10px] text-base-content/40 mb-0.5">Win Rate</div>
              <div className={`text-sm font-bold tabular-nums ${risk.win_rate != null && risk.win_rate > 52 ? 'text-success' : 'text-error'}`}>
                {risk.win_rate != null ? `${risk.win_rate}%` : 'N/A'}
              </div>
            </div>
            <div>
              <div className="text-[10px] text-base-content/40 mb-0.5">Avg Win</div>
              <div className="text-sm font-bold text-success tabular-nums">
                {risk.avg_win_pct != null ? `+${risk.avg_win_pct}%` : 'N/A'}
              </div>
            </div>
            <div>
              <div className="text-[10px] text-base-content/40 mb-0.5">Avg Loss</div>
              <div className="text-sm font-bold text-error tabular-nums">
                {risk.avg_loss_pct != null ? `${risk.avg_loss_pct}%` : 'N/A'}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* VaR & CVaR + Liquidity */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {/* VaR & CVaR */}
        <div className="glass-card p-4">
          <h5 className="text-xs font-bold mb-3 flex items-center gap-1.5 text-base-content/60">
            <AlertTriangle className="w-3.5 h-3.5 text-error" />
            Value at Risk & Expected Shortfall
          </h5>
          <div className="overflow-x-auto">
            <table className="table-pro text-xs">
              <thead>
                <tr>
                  <th className="!py-2 !px-3"></th>
                  <th className="!py-2 !px-3">
                    <span className="flex items-center gap-0.5">
                      VaR (95%)
                      <Tip text="Value at Risk: Maximum expected loss with 95% confidence. There's a 5% chance daily loss exceeds this amount." />
                    </span>
                  </th>
                  <th className="!py-2 !px-3">
                    <span className="flex items-center gap-0.5">
                      CVaR / ES
                      <Tip text="Conditional VaR (Expected Shortfall): Average loss in the worst 5% of scenarios. Always worse than VaR — shows tail risk severity." />
                    </span>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="font-medium !px-3">Daily %</td>
                  <td className="text-error font-bold !px-3 tabular-nums">
                    {risk.var_95_daily != null ? `${(risk.var_95_daily * 100).toFixed(2)}%` : 'N/A'}
                  </td>
                  <td className="text-error font-bold !px-3 tabular-nums">
                    {risk.cvar_95_daily != null ? `${(risk.cvar_95_daily * 100).toFixed(2)}%` : 'N/A'}
                  </td>
                </tr>
                <tr>
                  <td className="font-medium !px-3">Monthly %</td>
                  <td className="text-error font-bold !px-3 tabular-nums">
                    {risk.var_95_monthly != null ? `${(risk.var_95_monthly * 100).toFixed(2)}%` : 'N/A'}
                  </td>
                  <td className="text-error font-bold !px-3 tabular-nums">
                    {risk.cvar_95_monthly != null ? `${(risk.cvar_95_monthly * 100).toFixed(2)}%` : 'N/A'}
                  </td>
                </tr>
                <tr className="border-t border-white/[0.06]">
                  <td className="font-medium !px-3">Position $</td>
                  <td className="text-error font-bold !px-3 tabular-nums">
                    {fmt(risk.position_var_95_daily)} / {fmt(risk.position_var_95_monthly)}
                  </td>
                  <td className="text-error font-bold !px-3 tabular-nums">
                    {fmt(risk.position_cvar_95_daily)} / {fmt(risk.position_cvar_95_monthly)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        {/* Liquidity */}
        <div className="glass-card p-4">
          <h5 className="text-xs font-bold mb-3 flex items-center gap-1.5 text-base-content/60">
            <Shield className="w-3.5 h-3.5" />
            Liquidity Assessment
          </h5>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="text-[10px] text-base-content/40 mb-0.5">Avg Daily Vol</div>
              <div className="text-sm font-bold tabular-nums">{fmtVol(liquidity.avg_daily_volume_20d)}</div>
            </div>
            <div>
              <div className="text-[10px] text-base-content/40 mb-0.5">Avg $ Volume</div>
              <div className="text-sm font-bold tabular-nums">{fmt(liquidity.avg_daily_dollar_volume)}</div>
            </div>
            <div>
              <div className="text-[10px] text-base-content/40 mb-0.5">Days to Liquidate</div>
              <div className={`text-sm font-bold tabular-nums ${liquidity.days_to_liquidate != null && liquidity.days_to_liquidate > 5 ? 'text-error' : 'text-success'}`}>
                {liquidity.days_to_liquidate != null ? `${liquidity.days_to_liquidate.toFixed(1)} days` : 'N/A'}
              </div>
            </div>
            <div>
              <div className="text-[10px] text-base-content/40 mb-0.5">Rating</div>
              <span className={`badge badge-sm mt-0.5 font-semibold ${
                liquidity.liquidity_rating === 'Excellent' ? 'badge-success' :
                liquidity.liquidity_rating === 'Good' ? 'badge-info' :
                liquidity.liquidity_rating === 'Fair' ? 'badge-warning' : 'badge-error'
              }`}>
                {liquidity.liquidity_rating}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
