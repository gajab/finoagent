import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Heart, Loader2, AlertTriangle, ChevronDown, ChevronUp, RefreshCw,
  TrendingUp, TrendingDown, DollarSign, Shield, Droplets, Scale,
  BarChart3, Percent, Activity, Building2, Clock, Landmark,
} from 'lucide-react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  type ChartOptions,
} from 'chart.js';
import { Bar, Line } from 'react-chartjs-2';
import { fetchFinancialHealth } from '../api';
import type { FinancialHealthData, ReturnsOnCapital, TTMSnapshot } from '../types';

ChartJS.register(CategoryScale, LinearScale, BarElement, PointElement, LineElement, Title, Tooltip, Legend);

interface Props {
  ticker: string;
}

function fmt(val: number | null | undefined): string {
  if (val === null || val === undefined) return 'N/A';
  const abs = Math.abs(val);
  const sign = val < 0 ? '-' : '';
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toLocaleString()}`;
}

function fmtPct(val: number | null | undefined): string {
  if (val === null || val === undefined) return 'N/A';
  return `${val > 0 ? '+' : ''}${val.toFixed(1)}%`;
}

function scoreColor(score: number | null): string {
  if (score === null) return 'text-base-content/40';
  if (score >= 80) return 'text-success';
  if (score >= 60) return 'text-info';
  if (score >= 40) return 'text-warning';
  return 'text-error';
}

function scoreBg(score: number | null): string {
  if (score === null) return 'bg-base-content/10';
  if (score >= 80) return 'bg-success/15 border-success/30';
  if (score >= 60) return 'bg-info/15 border-info/30';
  if (score >= 40) return 'bg-warning/15 border-warning/30';
  return 'bg-error/15 border-error/30';
}

function ratioColor(val: number | null, goodAbove: number, warnAbove: number): string {
  if (val === null) return 'text-base-content/40';
  if (val >= goodAbove) return 'text-success';
  if (val >= warnAbove) return 'text-warning';
  return 'text-error';
}

export function FinancialHealth({ ticker }: Props) {
  const [data, setData] = useState<FinancialHealthData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showMargins, setShowMargins] = useState(false);
  const [showDebt, setShowDebt] = useState(false);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await fetchFinancialHealth(ticker);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  useEffect(() => { load(); }, [load]);

  // Margin chart data (with a trailing TTM point appended)
  const marginChart = useMemo(() => {
    if (!data?.revenue_trend.length) return null;
    const labels = data.revenue_trend.map(r => r.year);
    const gross = data.revenue_trend.map(r => r.gross_margin);
    const op = data.revenue_trend.map(r => r.operating_margin);
    const net = data.revenue_trend.map(r => r.net_margin);
    if (data.ttm) {
      labels.push('TTM');
      gross.push(data.ttm.gross_margin);
      op.push(data.ttm.operating_margin);
      net.push(data.ttm.net_margin);
    }
    return {
      labels,
      datasets: [
        { label: 'Gross Margin', data: gross, borderColor: 'rgb(34, 197, 94)', backgroundColor: 'rgba(34, 197, 94, 0.1)', fill: true, tension: 0.3, pointRadius: 4 },
        { label: 'Operating Margin', data: op, borderColor: 'rgb(99, 102, 241)', backgroundColor: 'rgba(99, 102, 241, 0.1)', fill: true, tension: 0.3, pointRadius: 4 },
        { label: 'Net Margin', data: net, borderColor: 'rgb(251, 191, 36)', backgroundColor: 'rgba(251, 191, 36, 0.1)', fill: true, tension: 0.3, pointRadius: 4 },
      ],
    };
  }, [data]);

  // Revenue & Earnings bar chart (with a trailing TTM point appended)
  const revenueChart = useMemo(() => {
    if (!data?.revenue_trend.length) return null;
    const labels = data.revenue_trend.map(r => r.year);
    const revenue = data.revenue_trend.map(r => (r.revenue ?? 0) / 1e9);
    const netIncome = data.revenue_trend.map(r => (r.net_income ?? 0) / 1e9);
    if (data.ttm) {
      labels.push('TTM');
      revenue.push((data.ttm.revenue ?? 0) / 1e9);
      netIncome.push((data.ttm.net_income ?? 0) / 1e9);
    }
    return {
      labels,
      datasets: [
        { label: 'Revenue', data: revenue, backgroundColor: 'rgba(99, 102, 241, 0.7)', borderRadius: 4 },
        { label: 'Net Income', data: netIncome, backgroundColor: 'rgba(34, 197, 94, 0.6)', borderRadius: 4 },
      ],
    };
  }, [data]);

  // Cash flow chart (with a trailing TTM point appended)
  const cfChart = useMemo(() => {
    if (!data?.cash_flow_trend.length) return null;
    const labels = data.cash_flow_trend.map(r => r.year);
    const ocf = data.cash_flow_trend.map(r => (r.operating_cf ?? 0) / 1e9);
    const fcf = data.cash_flow_trend.map(r => (r.free_cash_flow ?? 0) / 1e9);
    if (data.ttm) {
      labels.push('TTM');
      ocf.push((data.ttm.operating_cf ?? 0) / 1e9);
      fcf.push((data.ttm.free_cash_flow ?? 0) / 1e9);
    }
    return {
      labels,
      datasets: [
        { label: 'Operating CF', data: ocf, backgroundColor: 'rgba(99, 102, 241, 0.6)', borderRadius: 4 },
        {
          label: 'Free Cash Flow',
          data: fcf,
          backgroundColor: fcf.map(v => (v >= 0 ? 'rgba(34, 197, 94, 0.6)' : 'rgba(239, 68, 68, 0.6)')),
          borderRadius: 4,
        },
      ],
    };
  }, [data]);

  // EPS history bar chart for Earnings Consistency (with a trailing TTM point)
  const epsChart = useMemo(() => {
    if (!data?.eps_history.length) return { labels: [] as string[], datasets: [] as any[] };
    const labels = data.eps_history.map(e => e.year);
    const eps = data.eps_history.map(e => e.eps);
    if (data.ttm) {
      labels.push('TTM');
      eps.push(data.ttm.eps);
    }
    return {
      labels,
      datasets: [
        {
          label: 'Earnings Per Share (EPS)',
          data: eps,
          backgroundColor: eps.map(v => ((v || 0) >= 0 ? 'rgba(34, 197, 94, 0.7)' : 'rgba(239, 68, 68, 0.7)')),
          borderColor: eps.map(v => ((v || 0) >= 0 ? 'rgb(34, 197, 94)' : 'rgb(239, 68, 68)')),
          borderWidth: 1,
          borderRadius: 4,
        },
      ],
    };
  }, [data]);

  const lineOpts: ChartOptions<'line'> = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { position: 'top', labels: { color: '#999', boxWidth: 10, usePointStyle: true, padding: 10 } },
      tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${(ctx.parsed.y ?? 0).toFixed(1)}%` } },
    },
    scales: {
      x: { ticks: { color: '#999' }, grid: { display: false } },
      y: { ticks: { color: '#999', callback: v => `${Number(v).toFixed(0)}%` }, grid: { color: 'rgba(128,128,128,0.15)' } },
    },
  };

  const barOpts: ChartOptions<'bar'> = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { position: 'top', labels: { color: '#999', boxWidth: 10, usePointStyle: true, padding: 10 } },
      tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: $${(ctx.parsed.y ?? 0).toFixed(2)}B` } },
    },
    scales: {
      x: { ticks: { color: '#999' }, grid: { display: false } },
      y: { ticks: { color: '#999', callback: v => `$${Number(v).toFixed(1)}B` }, grid: { color: 'rgba(128,128,128,0.15)' } },
    },
  };

  return (
    <div className="glass-card">
      <div className="p-5">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h2 className="font-bold text-sm flex items-center gap-2">
            <Heart className="w-5 h-5 text-error" />
            Financial Health
          </h2>
          {data && (
            <button className="btn btn-ghost btn-xs gap-1" onClick={load}>
              <RefreshCw className="w-3 h-3" /> Refresh
            </button>
          )}
        </div>

        {loading && (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="w-8 h-8 animate-spin text-primary" />
            <span className="ml-3 text-base-content/60">Analyzing financial health...</span>
          </div>
        )}

        {error && !loading && (
          <div className="alert alert-error">
            <AlertTriangle className="w-5 h-5" />
            <span>{error}</span>
          </div>
        )}

        {data && !loading && (
          <div className="space-y-4 mt-2">
            {/* Health Score Banner */}
            <div className={`p-4 rounded-xl border ${scoreBg(data.health_score.overall)}`}>
              <div className="flex flex-col sm:flex-row items-center gap-4">
                <div className="text-center">
                  <div className="text-sm text-base-content/60 mb-1">Financial Health Score</div>
                  <div className={`text-4xl font-bold ${scoreColor(data.health_score.overall)}`}>
                    {data.health_score.overall !== null ? `${data.health_score.overall}` : 'N/A'}
                    <span className="text-lg text-base-content/40">/100</span>
                  </div>
                </div>
                <div className="flex-1 grid grid-cols-2 sm:grid-cols-3 gap-2">
                  {data.health_score.components.map((c, i) => (
                    <div key={i} className="bg-base-200/30 rounded-xl border border-white/[0.03] p-2 text-center">
                      <div className="text-[10px] text-base-content/40 truncate">{c.name}</div>
                      <div className={`text-sm font-bold ${scoreColor(c.score)}`}>{c.score}</div>
                      <div className="text-[9px] text-base-content/30 truncate">{c.detail}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Quick Stats Grid — valuation glance (ROE/ROA/ROIC live in Returns on Capital below) */}
            <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
              <StatCard label="P/B" value={data.quick_stats.price_to_book?.toFixed(2) ?? 'N/A'} />
              <StatCard label="EV/EBITDA" value={data.quick_stats.ev_to_ebitda?.toFixed(1) ?? 'N/A'} />
              <StatCard label="EV/Rev" value={data.quick_stats.ev_to_revenue?.toFixed(1) ?? 'N/A'} />
              <StatCard label="Rev/Share" value={data.quick_stats.revenue_per_share !== null ? `$${data.quick_stats.revenue_per_share.toFixed(2)}` : 'N/A'} />
              <StatCard label="Div Yield" value={data.quick_stats.dividend_yield > 0 ? `${data.quick_stats.dividend_yield.toFixed(2)}%` : '0%'} />
            </div>

            {/* Trailing Twelve Months (TTM) snapshot */}
            {data.ttm && <TTMSnapshotCard ttm={data.ttm} />}

            {/* Returns on Capital — ROE / ROA / ROIC with history */}
            {data.returns_on_capital && <ReturnsOnCapitalSection roc={data.returns_on_capital} />}

            {/* Revenue & Earnings + Margin Trends */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {revenueChart && (
                <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
                  <h4 className="text-sm font-semibold text-base-content/70 mb-2 flex items-center gap-1.5">
                    <BarChart3 className="w-4 h-4 text-primary" />
                    Revenue & Net Income
                  </h4>
                  <div className="h-44">
                    <Bar data={revenueChart} options={barOpts} />
                  </div>
                  {data.revenue_growth_rates.length > 0 && (
                    <div className="flex gap-2 mt-2">
                      {data.revenue_growth_rates.map((g, i) => (
                        <span key={i} className={`badge badge-xs ${g !== null && g > 0 ? 'badge-success' : 'badge-error'}`}>
                          {g !== null ? fmtPct(g) : 'N/A'} YoY
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {marginChart && (
                <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
                  <h4 className="text-sm font-semibold text-base-content/70 mb-2 flex items-center gap-1.5">
                    <Percent className="w-4 h-4 text-success" />
                    Margin Trends
                  </h4>
                  <div className="h-44">
                    <Line data={marginChart} options={lineOpts} />
                  </div>
                </div>
              )}
            </div>

            {/* Detailed Margin Table (expandable) */}
            {data.revenue_trend.length > 0 && (
              <div className="bg-base-200/40 rounded-xl border border-white/[0.03] overflow-hidden">
                <button
                  className="flex items-center justify-between w-full p-3 text-left hover:bg-base-content/5 transition-colors"
                  onClick={() => setShowMargins(!showMargins)}
                >
                  <span className="text-sm font-semibold text-base-content/70 flex items-center gap-1.5">
                    <Activity className="w-4 h-4 text-info" />
                    Detailed Income Statement & Margins
                  </span>
                  {showMargins ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                </button>
                {showMargins && (
                  <div className="overflow-x-auto px-3 pb-3">
                    <table className="table table-xs table-pro w-full">
                      <thead>
                        <tr className="text-base-content/40">
                          <th>Year</th>
                          <th className="text-right">Revenue</th>
                          <th className="text-right">Gross Profit</th>
                          <th className="text-right">Operating Inc</th>
                          <th className="text-right">Net Income</th>
                          <th className="text-right">EBITDA</th>
                          <th className="text-right">Gross %</th>
                          <th className="text-right">Op %</th>
                          <th className="text-right">Net %</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.ttm && (
                          <tr className="bg-primary/5">
                            <td className="font-bold text-primary" title={`Trailing twelve months through ${data.ttm.through}`}>TTM</td>
                            <td className="text-right font-semibold">{fmt(data.ttm.revenue)}</td>
                            <td className="text-right font-semibold">{fmt(data.ttm.gross_profit)}</td>
                            <td className="text-right font-semibold">{fmt(data.ttm.operating_income)}</td>
                            <td className={`text-right font-semibold ${(data.ttm.net_income ?? 0) < 0 ? 'text-error' : ''}`}>{fmt(data.ttm.net_income)}</td>
                            <td className="text-right font-semibold">{fmt(data.ttm.ebitda)}</td>
                            <td className="text-right font-semibold">{data.ttm.gross_margin !== null ? `${data.ttm.gross_margin.toFixed(1)}%` : 'N/A'}</td>
                            <td className="text-right font-semibold">{data.ttm.operating_margin !== null ? `${data.ttm.operating_margin.toFixed(1)}%` : 'N/A'}</td>
                            <td className={`text-right font-semibold ${(data.ttm.net_margin ?? 0) < 0 ? 'text-error' : ''}`}>{data.ttm.net_margin !== null ? `${data.ttm.net_margin.toFixed(1)}%` : 'N/A'}</td>
                          </tr>
                        )}
                        {data.revenue_trend.map((r, i) => (
                          <tr key={i}>
                            <td className="font-medium">{r.year}</td>
                            <td className="text-right">{fmt(r.revenue)}</td>
                            <td className="text-right">{fmt(r.gross_profit)}</td>
                            <td className="text-right">{fmt(r.operating_income)}</td>
                            <td className={`text-right ${(r.net_income ?? 0) < 0 ? 'text-error' : ''}`}>{fmt(r.net_income)}</td>
                            <td className="text-right">{fmt(r.ebitda)}</td>
                            <td className="text-right">{r.gross_margin !== null ? `${r.gross_margin.toFixed(1)}%` : 'N/A'}</td>
                            <td className="text-right">{r.operating_margin !== null ? `${r.operating_margin.toFixed(1)}%` : 'N/A'}</td>
                            <td className={`text-right ${(r.net_margin ?? 0) < 0 ? 'text-error' : ''}`}>
                              {r.net_margin !== null ? `${r.net_margin.toFixed(1)}%` : 'N/A'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {/* Cash Flow */}
            {cfChart && (
              <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
                <h4 className="text-sm font-semibold text-base-content/70 mb-2 flex items-center gap-1.5">
                  <DollarSign className="w-4 h-4 text-success" />
                  Cash Flow Trend
                </h4>
                <div className="h-44">
                  <Bar data={cfChart} options={barOpts} />
                </div>
                {data.fcf_stability && (
                  <div className="flex gap-3 mt-2 text-xs text-base-content/50">
                    <span>Avg FCF: {fmt(data.fcf_stability.mean)}</span>
                    {data.fcf_stability.cv !== null && (
                      <span>Variability (CV): {data.fcf_stability.cv.toFixed(2)} {data.fcf_stability.cv < 0.3 ? '(Stable)' : data.fcf_stability.cv < 0.7 ? '(Moderate)' : '(Volatile)'}</span>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Liquidity & Leverage side by side */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Liquidity */}
              {data.liquidity && (
                <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
                  <h4 className="text-sm font-semibold text-base-content/70 mb-3 flex items-center gap-1.5">
                    <Droplets className="w-4 h-4 text-info" />
                    Liquidity ({data.liquidity.year})
                  </h4>
                  <div className="grid grid-cols-3 gap-3">
                    <div className="text-center">
                      <div className="text-[10px] text-base-content/40">Current Ratio</div>
                      <div className={`text-xl font-bold ${ratioColor(data.liquidity.current_ratio, 1.5, 1.0)}`}>
                        {data.liquidity.current_ratio?.toFixed(2) ?? 'N/A'}
                      </div>
                      <div className="text-[9px] text-base-content/30">&gt; 1.5 = Strong</div>
                    </div>
                    <div className="text-center">
                      <div className="text-[10px] text-base-content/40">Quick Ratio</div>
                      <div className={`text-xl font-bold ${ratioColor(data.liquidity.quick_ratio, 1.0, 0.5)}`}>
                        {data.liquidity.quick_ratio?.toFixed(2) ?? 'N/A'}
                      </div>
                      <div className="text-[9px] text-base-content/30">&gt; 1.0 = Strong</div>
                    </div>
                    <div className="text-center">
                      <div className="text-[10px] text-base-content/40">Cash Ratio</div>
                      <div className={`text-xl font-bold ${ratioColor(data.liquidity.cash_ratio, 0.5, 0.2)}`}>
                        {data.liquidity.cash_ratio?.toFixed(2) ?? 'N/A'}
                      </div>
                      <div className="text-[9px] text-base-content/30">&gt; 0.5 = Strong</div>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2 mt-3 text-xs text-base-content/50">
                    <div>Current Assets: {fmt(data.liquidity.current_assets)}</div>
                    <div>Current Liabilities: {fmt(data.liquidity.current_liabilities)}</div>
                    <div>Cash: {fmt(data.liquidity.cash)}</div>
                    <div>Inventory: {fmt(data.liquidity.inventory)}</div>
                  </div>
                </div>
              )}

              {/* Leverage (latest year) */}
              {data.debt_data.length > 0 && (() => {
                const latest = data.debt_data[data.debt_data.length - 1];
                return (
                  <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
                    <h4 className="text-sm font-semibold text-base-content/70 mb-3 flex items-center gap-1.5">
                      <Scale className="w-4 h-4 text-warning" />
                      Leverage & Debt ({latest.year})
                    </h4>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="text-center">
                        <div className="text-[10px] text-base-content/40">Debt/Assets</div>
                        <div className={`text-lg font-bold ${latest.debt_to_assets_pct !== null && latest.debt_to_assets_pct < 30 ? 'text-success' : latest.debt_to_assets_pct !== null && latest.debt_to_assets_pct < 50 ? 'text-warning' : 'text-error'}`}>
                          {latest.debt_to_assets_pct !== null ? `${latest.debt_to_assets_pct.toFixed(1)}%` : 'N/A'}
                        </div>
                      </div>
                      <div className="text-center">
                        <div className="text-[10px] text-base-content/40">Debt/Equity</div>
                        <div className={`text-lg font-bold ${latest.debt_to_equity !== null && latest.debt_to_equity < 0.5 ? 'text-success' : latest.debt_to_equity !== null && latest.debt_to_equity < 1.5 ? 'text-warning' : 'text-error'}`}>
                          {latest.debt_to_equity !== null ? `${latest.debt_to_equity.toFixed(2)}x` : 'N/A'}
                        </div>
                      </div>
                      <div className="text-center">
                        <div className="text-[10px] text-base-content/40">Debt/EBITDA</div>
                        <div className={`text-lg font-bold ${latest.debt_to_ebitda !== null && latest.debt_to_ebitda < 2 ? 'text-success' : latest.debt_to_ebitda !== null && latest.debt_to_ebitda < 4 ? 'text-warning' : 'text-error'}`}>
                          {latest.debt_to_ebitda !== null ? `${latest.debt_to_ebitda.toFixed(1)}x` : 'N/A'}
                        </div>
                      </div>
                      <div className="text-center">
                        <div className="text-[10px] text-base-content/40">Interest Coverage</div>
                        <div className={`text-lg font-bold ${latest.interest_coverage !== null && latest.interest_coverage > 5 ? 'text-success' : latest.interest_coverage !== null && latest.interest_coverage > 2 ? 'text-warning' : 'text-error'}`}>
                          {latest.interest_coverage !== null ? `${latest.interest_coverage.toFixed(1)}x` : 'N/A'}
                        </div>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2 mt-3 text-xs text-base-content/50">
                      <div>Total Debt: {fmt(latest.total_debt)}</div>
                      <div>Cash: {fmt(latest.cash)}</div>
                      <div>Net Debt: {fmt(latest.net_debt)}</div>
                      <div>Total Equity: {fmt(latest.total_equity)}</div>
                    </div>
                  </div>
                );
              })()}
            </div>

            {/* Debt History (expandable) */}
            {data.debt_data.length > 1 && (
              <div className="bg-base-200/40 rounded-xl border border-white/[0.03] overflow-hidden">
                <button
                  className="flex items-center justify-between w-full p-3 text-left hover:bg-base-content/5 transition-colors"
                  onClick={() => setShowDebt(!showDebt)}
                >
                  <span className="text-sm font-semibold text-base-content/70 flex items-center gap-1.5">
                    <Shield className="w-4 h-4 text-warning" />
                    Debt & Leverage History
                  </span>
                  {showDebt ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                </button>
                {showDebt && (
                  <div className="overflow-x-auto px-3 pb-3">
                    <table className="table table-xs table-pro w-full">
                      <thead>
                        <tr className="text-base-content/40">
                          <th>Year</th>
                          <th className="text-right">Total Debt</th>
                          <th className="text-right">Cash</th>
                          <th className="text-right">Net Debt</th>
                          <th className="text-right">D/Assets</th>
                          <th className="text-right">D/Equity</th>
                          <th className="text-right">D/EBITDA</th>
                          <th className="text-right">Int Coverage</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.debt_data.map((d, i) => (
                          <tr key={i}>
                            <td className="font-medium">{d.year}</td>
                            <td className="text-right">{fmt(d.total_debt)}</td>
                            <td className="text-right">{fmt(d.cash)}</td>
                            <td className={`text-right ${d.net_debt > 0 ? 'text-error' : 'text-success'}`}>{fmt(d.net_debt)}</td>
                            <td className="text-right">{d.debt_to_assets_pct !== null ? `${d.debt_to_assets_pct.toFixed(1)}%` : 'N/A'}</td>
                            <td className="text-right">{d.debt_to_equity !== null ? `${d.debt_to_equity.toFixed(2)}x` : 'N/A'}</td>
                            <td className="text-right">{d.debt_to_ebitda !== null ? `${d.debt_to_ebitda.toFixed(1)}x` : 'N/A'}</td>
                            <td className="text-right">{d.interest_coverage !== null ? `${d.interest_coverage.toFixed(1)}x` : 'N/A'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {/* Earnings Consistency */}
            <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
              <h4 className="text-sm font-semibold text-base-content/70 mb-4 flex items-center gap-1.5">
                <TrendingUp className="w-4 h-4 text-success" />
                Earnings Consistency
              </h4>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div className="col-span-1 flex flex-col gap-4">
                  <div>
                    <span className="text-xs text-base-content/40 block mb-1">Profitable Years</span>
                    <span className={`text-2xl font-bold ${data.eps_positive_years === data.eps_total_years ? 'text-success' : data.eps_positive_years > 0 ? 'text-warning' : 'text-error'}`}>
                      {data.eps_positive_years}/{data.eps_total_years}
                    </span>
                  </div>
                  <div>
                    <span className="text-xs text-base-content/40 block mb-2">Net Income Growth</span>
                    <div className="flex flex-wrap gap-2">
                      {data.earnings_growth_rates.map((g, i) => (
                        <span key={i} className={`badge badge-sm ${g !== null && g > 0 ? 'badge-success' : 'badge-error'}`}>
                          {g !== null ? fmtPct(g) : 'N/A'}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="col-span-1 md:col-span-2 h-48">
                  <Bar
                    data={epsChart}
                    options={{
                      responsive: true,
                      maintainAspectRatio: false,
                      plugins: {
                        legend: { display: false },
                        tooltip: { mode: 'index', intersect: false }
                      },
                      scales: {
                        y: { grid: { color: 'rgba(128, 128, 128, 0.1)' } },
                        x: { grid: { display: false } }
                      }
                    }}
                  />
                </div>
              </div>
            </div>

            {/* Dividends */}
            {data.universal_dividend_metrics && (
              <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03] mt-6">
                <h4 className="text-sm font-semibold text-base-content/70 mb-4 flex items-center gap-1.5">
                  <DollarSign className="w-4 h-4 text-primary" />
                  Dividends
                </h4>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                  <div className="col-span-1 flex flex-col gap-4">
                    <div className="bg-base-200/30 rounded-xl p-3 border border-white/[0.03]">
                      <div className="text-xs text-base-content/40">Current Yield</div>
                      <div className="text-xl font-bold text-success">{data.universal_dividend_metrics.dividend_yield}%</div>
                    </div>
                    <div className="bg-base-200/30 rounded-xl p-3 border border-white/[0.03]">
                      <div className="text-xs text-base-content/40">Payout Ratio</div>
                      <div className="text-xl font-bold">{data.universal_dividend_metrics.payout_ratio !== null ? `${data.universal_dividend_metrics.payout_ratio}%` : 'N/A'}</div>
                    </div>
                  </div>
                  <div className="col-span-1 md:col-span-2 h-48">
                    {data.universal_dividend_metrics.trailing_payouts.length > 0 ? (
                      <Bar
                        data={{
                          labels: data.universal_dividend_metrics.trailing_payouts.map(p => p.year),
                          datasets: [
                            {
                              label: 'Annual Payout ($)',
                              data: data.universal_dividend_metrics.trailing_payouts.map(p => p.total_payout),
                              backgroundColor: 'rgba(56, 189, 248, 0.7)',
                              borderColor: 'rgb(56, 189, 248)',
                              borderWidth: 1,
                              borderRadius: 4
                            }
                          ]
                        }}
                        options={{
                          responsive: true,
                          maintainAspectRatio: false,
                          plugins: {
                            legend: { display: false },
                            tooltip: { mode: 'index', intersect: false }
                          },
                          scales: {
                            y: {
                              grid: { color: 'rgba(128, 128, 128, 0.1)' },
                              ticks: { callback: (value) => '$' + value }
                            },
                            x: { grid: { display: false } }
                          }
                        }}
                      />
                    ) : (
                      <div className="flex h-full items-center justify-center text-sm text-base-content/50 bg-base-200 rounded-lg">
                        No recent dividend history found
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Industry Specific Metrics */}
            {data.industry_metrics && (
              <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03] mt-6 border-l-4 border-info">
                <h4 className="text-sm font-semibold text-base-content/70 mb-3 flex items-center gap-1.5">
                  <Building2 className="w-4 h-4 text-info" />
                  Industry Specific Fundamentals
                </h4>
                <div className="mb-4">
                  <span className="badge badge-info badge-outline">{data.industry_metrics.type}</span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {data.industry_metrics.metrics.map((metric, i) => {
                    // Try to parse history into numbers for sparkline
                    const hasValidHistory = metric.history && metric.history.length > 1;
                    let sparklineData: number[] = [];
                    let sparklineLabels: string[] = [];
                    if (hasValidHistory && metric.history) {
                      sparklineLabels = metric.history.map(h => h.year);
                      sparklineData = metric.history.map(h => {
                        const clean = h.value.replace(/[^0-9.-]+/g, "");
                        return clean ? parseFloat(clean) : 0;
                      });
                    }

                    return (
                      <div key={i} className="bg-base-200/30 rounded-xl p-3 border border-white/[0.03] flex flex-col justify-between">
                        <div>
                          <div className="text-xs text-base-content/60 font-medium mb-1">{metric.name}</div>
                          <div className="text-xl font-bold text-base-content mb-1">{metric.value}</div>
                          <div className="text-[10px] text-base-content/50 leading-tight mb-2">{metric.desc}</div>
                          {metric.target && (
                            <div className="text-[10px] text-info font-medium mb-2">Target: {metric.target}</div>
                          )}
                        </div>
                        {hasValidHistory && sparklineData.length > 0 && (
                          <div className="h-12 w-full mt-2 opacity-80">
                            <Line
                              data={{
                                labels: sparklineLabels,
                                datasets: [{
                                  data: sparklineData,
                                  borderColor: 'rgb(56, 189, 248)',
                                  borderWidth: 2,
                                  pointRadius: 0,
                                  tension: 0.3
                                }]
                              }}
                              options={{
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                                scales: { x: { display: false }, y: { display: false } }
                              }}
                            />
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

          </div>
        )}
      </div>
    </div>
  );
}

function StatCard({ label, value, good }: { label: string; value: string; good?: boolean }) {
  return (
    <div className="bg-base-200/30 rounded-xl p-2 text-center border border-white/[0.03]">
      <div className="text-[10px] text-base-content/40">{label}</div>
      <div className={`text-sm font-bold ${good === true ? 'text-success' : good === false ? 'text-error' : ''}`}>
        {value}
      </div>
    </div>
  );
}

function pctStr(v: number | null): string {
  return v !== null ? `${v.toFixed(1)}%` : 'N/A';
}

/* ─────────── Trailing Twelve Months snapshot ─────────── */

function TTMStat({ label, value, negative }: { label: string; value: string; negative?: boolean }) {
  return (
    <div className="bg-base-200/30 rounded-xl p-2 text-center border border-white/[0.03]">
      <div className="text-[10px] text-base-content/40 truncate">{label}</div>
      <div className={`text-sm font-bold tabular-nums ${negative ? 'text-error' : ''}`}>{value}</div>
    </div>
  );
}

function TTMSnapshotCard({ ttm }: { ttm: TTMSnapshot }) {
  return (
    <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
      <h4 className="text-sm font-semibold text-base-content/70 mb-3 flex items-center gap-1.5">
        <Clock className="w-4 h-4 text-info" />
        Trailing Twelve Months
        <span className="text-[10px] text-base-content/40 font-normal">
          most recent 4 quarters{ttm.through ? ` · through ${ttm.through}` : ''}
        </span>
      </h4>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        <TTMStat label="Revenue" value={fmt(ttm.revenue)} />
        <TTMStat label="Net Income" value={fmt(ttm.net_income)} negative={(ttm.net_income ?? 0) < 0} />
        <TTMStat label="EPS" value={ttm.eps !== null ? `$${ttm.eps.toFixed(2)}` : 'N/A'} negative={(ttm.eps ?? 0) < 0} />
        <TTMStat label="Free Cash Flow" value={fmt(ttm.free_cash_flow)} negative={(ttm.free_cash_flow ?? 0) < 0} />
        <TTMStat label="Net Margin" value={pctStr(ttm.net_margin)} negative={(ttm.net_margin ?? 0) < 0} />
        <TTMStat label="Op Margin" value={pctStr(ttm.operating_margin)} negative={(ttm.operating_margin ?? 0) < 0} />
      </div>
    </div>
  );
}

/* ─────────── Returns on Capital — ROE / ROA / ROIC history ─────────── */

function retColor(v: number | null, good: number, warn: number): string {
  if (v === null) return 'text-base-content/40';
  if (v >= good) return 'text-success';
  if (v >= warn) return 'text-warning';
  return 'text-error';
}

function ReturnsOnCapitalSection({ roc }: { roc: ReturnsOnCapital }) {
  const rows = [
    { key: 'roe', label: 'ROE', desc: 'Return on Equity — Net Income ÷ Shareholders’ Equity', good: 15, warn: 8 },
    { key: 'roa', label: 'ROA', desc: 'Return on Assets — Net Income ÷ Total Assets', good: 5, warn: 2 },
    { key: 'roic', label: 'ROIC', desc: 'Return on Invested Capital — NOPAT ÷ (Debt + Equity − Cash)', good: 10, warn: 6 },
  ] as const;
  const cols = [
    { key: 'ttm', label: 'TTM', highlight: true },
    { key: 'current', label: 'Latest FY', highlight: false },
    { key: 'y1', label: '1Y ago', highlight: false },
    { key: 'y3', label: '3Y ago', highlight: false },
    { key: 'y5', label: '5Y ago', highlight: false },
  ] as const;

  return (
    <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
      <h4 className="text-sm font-semibold text-base-content/70 mb-3 flex items-center gap-1.5">
        <Landmark className="w-4 h-4 text-success" />
        Returns on Capital
        <span className="text-[10px] text-base-content/40 font-normal">ROE · ROA · ROIC</span>
      </h4>
      <div className="overflow-x-auto">
        <table className="table table-xs table-pro w-full">
          <thead>
            <tr className="text-base-content/40">
              <th>Metric</th>
              {cols.map((c) => (
                <th key={c.key} className={`text-right ${c.highlight ? 'text-primary bg-primary/5' : ''}`}>{c.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td className="font-medium border-b border-dashed border-base-content/20 cursor-help" title={r.desc}>
                  {r.label}
                </td>
                {cols.map((c) => {
                  const v = roc.summary[r.key][c.key];
                  return (
                    <td key={c.key} className={`text-right tabular-nums font-semibold ${c.highlight ? 'bg-primary/5' : ''} ${retColor(v, r.good, r.warn)}`}>
                      {v !== null ? `${v.toFixed(1)}%` : 'N/A'}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-base-content/30 mt-2">
        <span className="text-primary font-semibold">TTM</span> = trailing 4 quarters of income over the most-recent-quarter balance sheet (period-matched, up-to-date).
        Latest&nbsp;FY / 1Y / 3Y / 5Y are point-in-time at each fiscal year-end, from audited annual statements. ROIC uses NOPAT = Operating Income × (1 − effective tax rate).
      </p>
    </div>
  );
}
