import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { TrendingUp, TrendingDown, Loader2, AlertTriangle, Activity, BarChart3, Target, Gauge } from 'lucide-react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Filler,
  Tooltip,
  Legend,
  type ChartOptions,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import { fetchPricePrediction } from '../api';
import type { PricePredictionData } from '../types';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip, Legend);

interface PricePredictionProps {
  ticker: string;
}

function signalColor(signal: string): string {
  switch (signal) {
    case 'bullish':
    case 'strong_bullish':
      return 'text-success';
    case 'bearish':
    case 'strong_bearish':
      return 'text-error';
    case 'oversold':
    case 'strongly_oversold':
      return 'text-info';
    case 'overbought':
    case 'strongly_overbought':
      return 'text-warning';
    default:
      return 'text-warning';
  }
}

function signalBadge(signal: string): string {
  switch (signal) {
    case 'bullish':
      return 'badge-success';
    case 'bearish':
      return 'badge-error';
    default:
      return 'badge-warning';
  }
}

function formatSignal(s: string | null | undefined): string {
  return s ? s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) : '—';
}

// Null-safe number formatters — prediction fields can be null for thin/limited-history names.
const nf = (n: number | null | undefined, d = 2): string => (n == null || Number.isNaN(n)) ? '—' : n.toFixed(d);
const mf = (n: number | null | undefined, d = 2): string => (n == null || Number.isNaN(n)) ? '—' : `$${n.toFixed(d)}`;

export function PricePrediction({ ticker }: PricePredictionProps) {
  const [data, setData] = useState<PricePredictionData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [horizon, setHorizon] = useState(30);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await fetchPricePrediction(ticker, horizon);
      if (result.error) {
        setError(result.error);
        setData(null);
      } else {
        setData(result);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load prediction');
    } finally {
      setLoading(false);
    }
  }, [ticker, horizon]);

  useEffect(() => {
    load();
  }, [load]);

  // Build projection chart data
  const projectionChartData = useMemo(() => {
    if (!data) return null;

    const { current_price, linear_trend, volatility } = data;
    const projected = linear_trend.projected_price;
    const rangeLow = volatility.projected_95_range.low;
    const rangeHigh = volatility.projected_95_range.high;
    const sma50 = data.mean_reversion.sma50;
    const dailyVol = volatility.daily_volatility_pct / 100;

    // Generate intermediate points for a smooth chart
    const steps = 6;
    const labels: string[] = [];
    const projectedLine: number[] = [];
    const upperBand: number[] = [];
    const lowerBand: number[] = [];
    const smaLine: number[] = [];

    for (let i = 0; i <= steps; i++) {
      const dayFraction = i / steps;
      const day = Math.round(dayFraction * horizon);
      labels.push(day === 0 ? 'Today' : `Day ${day}`);

      // Linear interpolation for projected price
      const projPrice = current_price + (projected - current_price) * dayFraction;
      projectedLine.push(Math.round(projPrice * 100) / 100);

      // Confidence band expands with sqrt(time) — 95% interval
      const bandWidth = current_price * dailyVol * Math.sqrt(day || 0.5) * 1.96;
      upperBand.push(Math.round((projPrice + bandWidth) * 100) / 100);
      lowerBand.push(Math.round((projPrice - bandWidth) * 100) / 100);

      // SMA50 is a constant level
      smaLine.push(sma50);
    }

    return {
      labels,
      datasets: [
        {
          label: '95% Upper',
          data: upperBand,
          borderColor: 'rgba(239, 68, 68, 0.3)',
          backgroundColor: 'rgba(239, 68, 68, 0.05)',
          fill: '+1',
          borderWidth: 1,
          borderDash: [4, 4],
          pointRadius: 0,
          tension: 0.3,
        },
        {
          label: 'Projected',
          data: projectedLine,
          borderColor: 'rgb(56, 189, 248)',
          backgroundColor: 'rgba(56, 189, 248, 0.1)',
          fill: false,
          borderWidth: 2.5,
          pointRadius: (ctx: any) => (ctx.dataIndex === 0 || ctx.dataIndex === steps ? 5 : 0),
          pointBackgroundColor: 'rgb(56, 189, 248)',
          tension: 0.3,
        },
        {
          label: '95% Lower',
          data: lowerBand,
          borderColor: 'rgba(34, 197, 94, 0.3)',
          backgroundColor: 'rgba(34, 197, 94, 0.05)',
          fill: '-1',
          borderWidth: 1,
          borderDash: [4, 4],
          pointRadius: 0,
          tension: 0.3,
        },
        {
          label: 'SMA 50',
          data: smaLine,
          borderColor: 'rgba(251, 191, 36, 0.5)',
          borderWidth: 1.5,
          borderDash: [6, 3],
          pointRadius: 0,
          fill: false,
        },
      ],
    };
  }, [data, horizon]);

  const projectionChartOptions: ChartOptions<'line'> = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        position: 'top',
        labels: { color: '#999', boxWidth: 12, usePointStyle: true, padding: 15 },
      },
      tooltip: {
        callbacks: {
          label: (ctx) => `${ctx.dataset.label}: $${ctx.parsed.y?.toFixed(2) || 'N/A'}`,
        },
      },
    },
    scales: {
      x: {
        ticks: { color: '#999' },
        grid: { display: false },
      },
      y: {
        ticks: {
          color: '#999',
          callback: (v) => `$${Number(v).toFixed(0)}`,
        },
        grid: { color: 'rgba(128,128,128,0.15)' },
      },
    },
  };

  // Build indicator score chart data
  const indicatorChartData = useMemo(() => {
    if (!data) return null;

    // Map signals to numeric scores for visualization (-2 to +2)
    function signalToScore(signal: string): number {
      switch (signal) {
        case 'strong_bullish': return 2;
        case 'bullish': return 1;
        case 'oversold': return 1;
        case 'strongly_oversold': return 1.5;
        case 'neutral': return 0;
        case 'overbought': return -1;
        case 'strongly_overbought': return -1.5;
        case 'bearish': return -1;
        case 'strong_bearish': return -2;
        default: return 0;
      }
    }

    const trendScore = data.linear_trend.trend_direction === 'upward'
      ? (data.linear_trend.trend_strength === 'strong' ? 2 : data.linear_trend.trend_strength === 'moderate' ? 1 : 0.5)
      : (data.linear_trend.trend_strength === 'strong' ? -2 : data.linear_trend.trend_strength === 'moderate' ? -1 : -0.5);

    const scores = [
      trendScore,
      signalToScore(data.mean_reversion.signal),
      signalToScore(data.momentum.signal),
      data.overall_signal.score,
    ];

    const colors = scores.map((s) =>
      s > 0 ? 'rgba(34, 197, 94, 0.7)' : s < 0 ? 'rgba(239, 68, 68, 0.7)' : 'rgba(251, 191, 36, 0.7)'
    );

    return {
      labels: ['Trend', 'Reversion', 'Momentum', 'Overall'],
      datasets: [
        {
          label: 'Signal Score',
          data: scores,
          borderColor: colors,
          backgroundColor: colors.map((c) => c.replace('0.7', '0.2')),
          borderWidth: 2,
          pointRadius: 6,
          pointBackgroundColor: colors,
          fill: true,
          tension: 0,
        },
      ],
    };
  }, [data]);

  const indicatorChartOptions: ChartOptions<'line'> = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (ctx) => {
            const val = ctx.parsed.y ?? 0;
            const label = val > 0 ? 'Bullish' : val < 0 ? 'Bearish' : 'Neutral';
            return `${ctx.label}: ${val > 0 ? '+' : ''}${val.toFixed(1)} (${label})`;
          },
        },
      },
    },
    scales: {
      x: {
        ticks: { color: '#999' },
        grid: { display: false },
      },
      y: {
        min: -3,
        max: 3,
        ticks: {
          color: '#999',
          stepSize: 1,
          callback: (v) => {
            const n = Number(v);
            if (n === 2) return 'Strong Bull';
            if (n === 1) return 'Bullish';
            if (n === 0) return 'Neutral';
            if (n === -1) return 'Bearish';
            if (n === -2) return 'Strong Bear';
            return '';
          },
        },
        grid: {
          color: (ctx) => ctx.tick.value === 0 ? 'rgba(128,128,128,0.4)' : 'rgba(128,128,128,0.1)',
        },
      },
    },
  };

  return (
    <div className="glass-card">
      <div className="p-5">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <h2 className="font-bold text-sm flex items-center gap-2">
            <Target className="w-5 h-5 text-primary" />
            Price Prediction
          </h2>
          <div className="flex items-center gap-2">
            <span className="text-xs text-base-content/50">Horizon:</span>
            <select
              className="select select-bordered select-xs"
              value={horizon}
              onChange={(e) => setHorizon(Number(e.target.value))}
            >
              {[7, 14, 30, 60, 90].map((d) => (
                <option key={d} value={d}>
                  {d} days
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Loading */}
        {loading && (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="w-8 h-8 animate-spin text-primary" />
            <span className="ml-3 text-base-content/60">Running prediction models...</span>
          </div>
        )}

        {/* Error */}
        {error && !loading && (
          <div className="alert alert-error">
            <AlertTriangle className="w-5 h-5" />
            <span>{error}</span>
          </div>
        )}

        {/* Data */}
        {data && !loading && (
          <div className="space-y-4 mt-2">
            {/* Overall Signal */}
            <div className="flex items-center gap-4">
              <div className={`badge ${signalBadge(data.overall_signal.signal)} badge-lg gap-1 text-base font-bold px-4 py-3`}>
                {data.overall_signal.signal === 'bullish' ? (
                  <TrendingUp className="w-4 h-4" />
                ) : data.overall_signal.signal === 'bearish' ? (
                  <TrendingDown className="w-4 h-4" />
                ) : (
                  <Activity className="w-4 h-4" />
                )}
                {data.overall_signal.signal.toUpperCase()}
              </div>
              <div className="text-sm text-base-content/60">
                Confidence: <span className="font-bold text-base-content">{data.overall_signal.confidence_pct}%</span>
                <span className="mx-2">|</span>
                Score: <span className="font-mono">{data.overall_signal.score}</span>
              </div>
            </div>

            {/* Price Projection Chart */}
            <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
              <h4 className="text-sm font-semibold text-base-content/70 mb-2">
                Price Projection — {horizon} Day Outlook
              </h4>
              <div className="h-56">
                {projectionChartData && (
                  <Line data={projectionChartData} options={projectionChartOptions} />
                )}
              </div>
              <div className="flex justify-between text-xs text-base-content/40 mt-2 px-1">
                <span>Current: {mf(data.current_price, 2)}</span>
                <span>Projected: {mf(data.linear_trend?.projected_price, 2)} ({(data.linear_trend?.projected_change_pct ?? 0) >= 0 ? '+' : ''}{nf(data.linear_trend?.projected_change_pct, 1)}%)</span>
                <span>95% Range: {mf(data.volatility?.projected_95_range?.low, 0)} — {mf(data.volatility?.projected_95_range?.high, 0)}</span>
              </div>
            </div>

            {/* Indicator Signals Chart */}
            <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
              <h4 className="text-sm font-semibold text-base-content/70 mb-2">
                Indicator Signals
              </h4>
              <div className="h-40">
                {indicatorChartData && (
                  <Line data={indicatorChartData} options={indicatorChartOptions} />
                )}
              </div>
            </div>

            {/* Stats Grid */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              {/* Projected Price */}
              <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
                <div className="text-xs text-base-content/50 mb-1 flex items-center gap-1">
                  <BarChart3 className="w-3 h-3" />
                  Projected Price
                </div>
                <div className="text-lg font-bold">{mf(data.linear_trend?.projected_price, 2)}</div>
                <div className={`text-sm font-medium ${(data.linear_trend?.projected_change_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                  {(data.linear_trend?.projected_change_pct ?? 0) >= 0 ? '+' : ''}{nf(data.linear_trend?.projected_change_pct, 2)}%
                </div>
                <div className="text-xs text-base-content/40 mt-0.5">
                  {formatSignal(data.linear_trend?.trend_direction)} ({data.linear_trend?.trend_strength ?? '—'})
                </div>
              </div>

              {/* Mean Reversion */}
              <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
                <div className="text-xs text-base-content/50 mb-1 flex items-center gap-1">
                  <Activity className="w-3 h-3" />
                  Mean Reversion
                </div>
                <div className="text-lg font-bold">{nf(data.mean_reversion?.z_score, 2)}</div>
                <div className={`text-sm font-medium ${signalColor(data.mean_reversion?.signal)}`}>
                  {formatSignal(data.mean_reversion?.signal)}
                </div>
                <div className="text-xs text-base-content/40 mt-0.5">
                  SMA50: {mf(data.mean_reversion?.sma50, 2)} ({(data.mean_reversion?.deviation_pct ?? 0) >= 0 ? '+' : ''}{nf(data.mean_reversion?.deviation_pct, 1)}%)
                </div>
              </div>

              {/* Momentum */}
              <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
                <div className="text-xs text-base-content/50 mb-1 flex items-center gap-1">
                  <TrendingUp className="w-3 h-3" />
                  Momentum
                </div>
                <div className={`text-lg font-bold ${(data.momentum?.roc_14d_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                  {(data.momentum?.roc_14d_pct ?? 0) >= 0 ? '+' : ''}{nf(data.momentum?.roc_14d_pct, 2)}%
                </div>
                <div className={`text-sm font-medium ${signalColor(data.momentum?.signal)}`}>
                  {formatSignal(data.momentum?.signal)}
                </div>
                <div className="text-xs text-base-content/40 mt-0.5">
                  ROC-14d{data.momentum?.roc_30d_pct != null ? ` | 30d: ${nf(data.momentum.roc_30d_pct, 1)}%` : ''}
                </div>
              </div>

              {/* Volatility */}
              <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
                <div className="text-xs text-base-content/50 mb-1 flex items-center gap-1">
                  <Gauge className="w-3 h-3" />
                  Volatility
                </div>
                <div className="text-lg font-bold">{nf(data.volatility?.annualized_volatility_pct, 1)}%</div>
                <div className="text-sm text-base-content/60">Annual</div>
                <div className="text-xs text-base-content/40 mt-0.5">
                  95% Range: {mf(data.volatility?.projected_95_range?.low, 0)} - {mf(data.volatility?.projected_95_range?.high, 0)}
                </div>
              </div>
            </div>

            {/* Details */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
              <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
                <h4 className="font-semibold text-base-content/70 mb-2">Linear Trend</h4>
                <div className="space-y-1 text-base-content/60">
                  <div>R-squared: <span className="font-mono text-base-content">{nf(data.linear_trend?.r_squared, 4)}</span></div>
                  <div>Daily slope: <span className="font-mono text-base-content">{mf(data.linear_trend?.slope_per_day, 4)}</span></div>
                  <div>Daily change: <span className="font-mono text-base-content">{nf(data.linear_trend?.daily_change_pct, 4)}%</span></div>
                </div>
              </div>
              <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
                <h4 className="font-semibold text-base-content/70 mb-2">Volatility Detail</h4>
                <div className="space-y-1 text-base-content/60">
                  <div>Daily vol: <span className="font-mono text-base-content">{nf(data.volatility?.daily_volatility_pct, 4)}%</span></div>
                  <div>Annual vol: <span className="font-mono text-base-content">{nf(data.volatility?.annualized_volatility_pct, 2)}%</span></div>
                  <div>{horizon}d range: <span className="font-mono text-base-content">{mf(data.volatility?.projected_95_range?.low, 2)} — {mf(data.volatility?.projected_95_range?.high, 2)}</span></div>
                </div>
              </div>
            </div>

            {/* Disclaimer */}
            <p className="text-xs text-base-content/30 mt-2">
              {data.disclaimer}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
