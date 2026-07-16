import React, { useMemo } from 'react';
import { Activity, BarChart3, TrendingUp, TrendingDown, ArrowRightLeft, Calendar, Zap } from 'lucide-react';
import { Line, Bar } from 'react-chartjs-2';
import type { ExitSwingSignals, ExitLongtermSignals, ExitPriceChart, ExitTrendData } from '../../types';

interface Props {
  swing: ExitSwingSignals;
  longterm: ExitLongtermSignals;
  priceChart?: ExitPriceChart;
  costBasis?: number;
}

function scoreColor(score: number): string {
  if (score >= 60) return 'text-error';
  if (score >= 40) return 'text-warning';
  if (score >= 25) return 'text-info';
  return 'text-success';
}

function scoreBadge(score: number): string {
  if (score >= 60) return 'bg-error/15 text-error border-error/20';
  if (score >= 40) return 'bg-warning/15 text-warning border-warning/20';
  if (score >= 25) return 'bg-info/15 text-info border-info/20';
  return 'bg-success/15 text-success border-success/20';
}

function trendBadge(direction: string): string {
  if (direction === 'Uptrend') return 'bg-success/15 text-success border-success/20';
  if (direction === 'Downtrend') return 'bg-error/15 text-error border-error/20';
  return 'bg-base-200/50 text-base-content/50 border-white/[0.06]';
}

function SignalRow({ label, score, detail }: { label: string; score: number; detail: string }) {
  return (
    <tr className="hover:bg-base-200/30 transition-colors">
      <td className="text-xs font-medium py-2.5 px-3">{label}</td>
      <td className="py-2.5 px-3">
        <div className="flex items-center gap-2">
          <div className="w-14 bg-base-300/50 rounded-full h-1.5 overflow-hidden">
            <div
              className={`h-1.5 rounded-full transition-all duration-700 ${score >= 70 ? 'bg-error' : score >= 50 ? 'bg-warning' : score >= 30 ? 'bg-info' : 'bg-success'}`}
              style={{ width: `${score}%` }}
            />
          </div>
          <span className={`text-xs font-bold tabular-nums ${scoreColor(score)}`}>{score}</span>
        </div>
      </td>
      <td className="text-[11px] text-base-content/50 py-2.5 px-3">{detail}</td>
    </tr>
  );
}

function TrendCard({ label, trend }: { label: string; trend: ExitTrendData }) {
  const isUp = trend.change_pct >= 0;
  return (
    <div className="metric-card text-left">
      <div className="flex items-center justify-between mb-1">
        <span className="text-[10px] text-base-content/40 font-medium uppercase tracking-wider">{label}</span>
        <span className={`text-[10px] font-bold px-2 py-0.5 rounded-md border ${trendBadge(trend.direction)}`}>
          {trend.direction}
        </span>
      </div>
      <div className={`text-xl font-black tabular-nums ${isUp ? 'text-success' : 'text-error'}`}>
        {isUp ? '+' : ''}{trend.change_pct}%
      </div>
      <div className="text-[10px] text-base-content/30 mt-0.5">
        H: ${trend.high.toLocaleString()} &middot; L: ${trend.low.toLocaleString()}
        {trend.volatility != null && <> &middot; Vol: {trend.volatility}%</>}
      </div>
    </div>
  );
}

function eventColor(type: string): string {
  if (type === 'earnings') return 'rgba(99, 102, 241, 0.8)';
  if (type === '52w_high') return 'rgba(34, 197, 94, 0.8)';
  if (type === '52w_low') return 'rgba(239, 68, 68, 0.8)';
  if (type === 'golden_cross') return 'rgba(34, 197, 94, 0.9)';
  if (type === 'death_cross') return 'rgba(239, 68, 68, 0.9)';
  if (type === 'volume_spike') return 'rgba(245, 158, 11, 0.7)';
  return 'rgba(255, 255, 255, 0.6)';
}

function eventIcon(type: string): string {
  if (type === 'earnings') return '\uD83D\uDCCA';
  if (type === '52w_high') return '\uD83D\uDD3A';
  if (type === '52w_low') return '\uD83D\uDD3B';
  if (type === 'golden_cross') return '\u2728';
  if (type === 'death_cross') return '\uD83D\uDC80';
  if (type === 'volume_spike') return '\uD83D\uDCC8';
  if (type === 'big_move') return '\u26A1';
  return '\uD83D\uDCCC';
}

export function TechnicalSignals({ swing, longterm, priceChart, costBasis }: Props) {
  const chartData = useMemo(() => {
    if (!priceChart || !priceChart.timestamps.length) return null;

    const labels = priceChart.timestamps.map(d => {
      const dt = new Date(d);
      return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' });
    });

    const eventPoints: (number | null)[] = priceChart.timestamps.map(() => null);
    const eventLabels: string[] = priceChart.timestamps.map(() => '');
    const eventColors: string[] = priceChart.timestamps.map(() => 'transparent');

    if (priceChart.events) {
      for (const ev of priceChart.events) {
        const idx = priceChart.timestamps.indexOf(ev.date);
        if (idx !== -1 && ev.price != null) {
          eventPoints[idx] = ev.price;
          eventLabels[idx] = ev.label;
          eventColors[idx] = eventColor(ev.type);
        }
      }
    }

    const datasets: any[] = [
      {
        label: 'Price',
        data: priceChart.prices,
        borderColor: 'rgb(99, 102, 241)',
        backgroundColor: 'rgba(99, 102, 241, 0.05)',
        fill: true,
        tension: 0.2,
        pointRadius: 0,
        borderWidth: 2,
        yAxisID: 'y',
        order: 2,
      },
    ];

    if (priceChart.sma50?.some(v => v != null)) {
      datasets.push({
        label: 'SMA 50',
        data: priceChart.sma50,
        borderColor: 'rgba(245, 158, 11, 0.6)',
        borderWidth: 1,
        borderDash: [4, 2],
        pointRadius: 0,
        fill: false,
        tension: 0.2,
        yAxisID: 'y',
        order: 3,
      });
    }
    if (priceChart.sma200?.some(v => v != null)) {
      datasets.push({
        label: 'SMA 200',
        data: priceChart.sma200,
        borderColor: 'rgba(239, 68, 68, 0.5)',
        borderWidth: 1,
        borderDash: [6, 3],
        pointRadius: 0,
        fill: false,
        tension: 0.2,
        yAxisID: 'y',
        order: 4,
      });
    }

    if (costBasis && costBasis > 0) {
      datasets.push({
        label: `Cost Basis ($${costBasis.toFixed(2)})`,
        data: Array(priceChart.prices.length).fill(costBasis),
        borderColor: 'rgba(168, 85, 247, 0.5)',
        borderWidth: 1.5,
        borderDash: [8, 4],
        pointRadius: 0,
        fill: false,
        yAxisID: 'y',
        order: 5,
      });
    }

    if (eventPoints.some(p => p !== null)) {
      datasets.push({
        label: 'Events',
        data: eventPoints,
        borderColor: 'transparent',
        backgroundColor: eventColors,
        pointRadius: eventPoints.map(p => p !== null ? 6 : 0),
        pointStyle: 'triangle',
        pointBorderColor: eventColors,
        pointBorderWidth: 2,
        showLine: false,
        yAxisID: 'y',
        order: 1,
      });
    }

    return { labels, datasets };
  }, [priceChart, costBasis]);

  const volumeData = useMemo(() => {
    if (!priceChart || !priceChart.volumes.length) return null;
    const labels = priceChart.timestamps.map(d => {
      const dt = new Date(d);
      return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    });
    const colors = priceChart.prices.map((p, i) =>
      i > 0 && p >= priceChart.prices[i - 1] ? 'rgba(166, 227, 161, 0.4)' : 'rgba(243, 139, 168, 0.3)'
    );
    return {
      labels,
      datasets: [{
        label: 'Volume',
        data: priceChart.volumes,
        backgroundColor: colors,
        borderWidth: 0,
        barPercentage: 0.9,
        categoryPercentage: 0.9,
      }],
    };
  }, [priceChart]);

  const chartOpts: any = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: true, position: 'top' as const, labels: { boxWidth: 10, font: { size: 10, family: 'Inter' }, padding: 12, color: 'rgba(205, 214, 244, 0.5)' } },
      tooltip: {
        mode: 'index' as const,
        intersect: false,
        backgroundColor: 'rgba(30, 30, 46, 0.95)',
        titleFont: { family: 'Inter', size: 11 },
        bodyFont: { family: 'Inter', size: 11 },
        borderColor: 'rgba(255,255,255,0.06)',
        borderWidth: 1,
        cornerRadius: 12,
        padding: 10,
        callbacks: {
          label: (ctx: any) => {
            if (ctx.dataset.label === 'Events') {
              const idx = ctx.dataIndex;
              const ev = priceChart?.events?.find(e => priceChart.timestamps.indexOf(e.date) === idx);
              return ev ? `${ev.label}: ${ev.description}` : '';
            }
            return `${ctx.dataset.label}: $${ctx.parsed.y?.toFixed(2) || ''}`;
          },
        },
      },
    },
    scales: {
      x: { display: true, ticks: { maxTicksLimit: 12, font: { size: 9, family: 'Inter' }, color: 'rgba(205, 214, 244, 0.3)' }, grid: { display: false } },
      y: { display: true, position: 'right' as const, ticks: { font: { size: 10, family: 'Inter' }, color: 'rgba(205, 214, 244, 0.3)' }, grid: { color: 'rgba(255,255,255,0.03)' } },
    },
  };

  const volOpts: any = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { enabled: false } },
    scales: { x: { display: false }, y: { display: false } },
  };

  const significantEvents = useMemo(() => {
    if (!priceChart?.events) return [];
    return priceChart.events
      .filter(e => ['earnings', '52w_high', '52w_low', 'golden_cross', 'death_cross', 'big_move'].includes(e.type))
      .slice(0, 12);
  }, [priceChart]);

  return (
    <div className="space-y-4">
      {/* Price Chart with Events */}
      {chartData && (
        <div className="glass-card p-4 sm:p-5">
          <h4 className="font-bold text-sm flex items-center gap-2 mb-3">
            <TrendingUp className="w-4 h-4 text-primary" />
            Price & Volume — Holding Period
          </h4>

          {/* 30/60/90 Day Trends */}
          {priceChart && (priceChart.trend_30d || priceChart.trend_60d || priceChart.trend_90d) && (
            <div className="grid grid-cols-3 gap-3 mb-4">
              {priceChart.trend_30d && <TrendCard label="30 Days" trend={priceChart.trend_30d} />}
              {priceChart.trend_60d && <TrendCard label="60 Days" trend={priceChart.trend_60d} />}
              {priceChart.trend_90d && <TrendCard label="90 Days" trend={priceChart.trend_90d} />}
            </div>
          )}

          <div style={{ height: '300px' }}>
            <Line data={chartData} options={chartOpts} />
          </div>

          {volumeData && (
            <div style={{ height: '60px', marginTop: '-8px' }}>
              <Bar data={volumeData} options={volOpts} />
            </div>
          )}
        </div>
      )}

      {/* Key Events */}
      {significantEvents.length > 0 && (
        <div className="glass-card p-4 sm:p-5">
          <h4 className="font-bold text-sm flex items-center gap-2 mb-3">
            <Zap className="w-4 h-4 text-warning" />
            Key Events
          </h4>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5 max-h-48 overflow-y-auto">
            {significantEvents.map((ev, i) => (
              <div key={i} className="flex items-center gap-2.5 text-xs bg-base-200/30 rounded-xl px-3 py-2 border border-white/[0.03] hover:border-white/[0.06] transition-colors">
                <span className="text-sm flex-shrink-0">{eventIcon(ev.type)}</span>
                <div className="flex-1 min-w-0">
                  <span className="font-semibold text-base-content/80">{ev.label}</span>
                  {ev.price != null && <span className="text-base-content/40 ml-1.5">${ev.price}</span>}
                </div>
                <span className="text-[10px] text-base-content/30 flex-shrink-0 tabular-nums">{ev.date}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Technical Signal Tables */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Swing Trader */}
        <div className="glass-card p-4 sm:p-5">
          <div className="flex items-center justify-between mb-3">
            <h4 className="font-bold text-sm flex items-center gap-2">
              <Activity className="w-4 h-4 text-warning" />
              Swing Exit Signals
            </h4>
            <span className={`text-xs font-bold px-2.5 py-1 rounded-lg border tabular-nums ${scoreBadge(swing.composite_score)}`}>
              {swing.composite_score}/100
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="table-pro text-xs">
              <thead>
                <tr>
                  <th className="!py-2 !px-3">Indicator</th>
                  <th className="!py-2 !px-3">Score</th>
                  <th className="!py-2 !px-3">Detail</th>
                </tr>
              </thead>
              <tbody>
                <SignalRow label="RSI" score={swing.rsi_score} detail={`${swing.rsi_value.toFixed(1)} — ${swing.rsi_signal}`} />
                <SignalRow label="MACD" score={swing.macd_score} detail={`${swing.macd_signal} (hist: ${swing.macd_histogram.toFixed(3)})`} />
                <SignalRow label="Bollinger %B" score={swing.bollinger_score} detail={`${swing.bollinger_pct_b.toFixed(2)} — ${swing.bollinger_position}`} />
                <SignalRow label="EMA Cross" score={swing.ema_crossover_score} detail={swing.ema_signal} />
                <SignalRow label="Volume" score={swing.volume_divergence_score} detail={swing.volume_phase} />
              </tbody>
            </table>
          </div>
        </div>

        {/* Long-Term */}
        <div className="glass-card p-4 sm:p-5">
          <div className="flex items-center justify-between mb-3">
            <h4 className="font-bold text-sm flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-info" />
              Long-Term Exit Signals
            </h4>
            <span className={`text-xs font-bold px-2.5 py-1 rounded-lg border tabular-nums ${scoreBadge(longterm.composite_score)}`}>
              {longterm.composite_score}/100
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="table-pro text-xs">
              <thead>
                <tr>
                  <th className="!py-2 !px-3">Indicator</th>
                  <th className="!py-2 !px-3">Score</th>
                  <th className="!py-2 !px-3">Detail</th>
                </tr>
              </thead>
              <tbody>
                <SignalRow label="SMA Cross" score={longterm.sma_cross_score} detail={`${longterm.sma_cross_label}${longterm.sma50 != null && longterm.sma200 != null ? ` (50d: ${longterm.sma50.toFixed(0)}, 200d: ${longterm.sma200.toFixed(0)})` : ''}`} />
                <SignalRow label="Fundamental" score={longterm.fundamental_health_score} detail={longterm.fundamental_health_score >= 60 ? 'Deteriorating' : 'Healthy'} />
                <SignalRow label="Valuation" score={longterm.valuation_score} detail={longterm.valuation_score >= 60 ? 'Overvalued' : 'Fair/Undervalued'} />
                <SignalRow label="Analyst View" score={longterm.analyst_consensus_score} detail={longterm.analyst_recommendation || 'N/A'} />
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
