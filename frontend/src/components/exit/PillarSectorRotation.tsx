import React, { useMemo } from 'react';
import { TrendingUp, TrendingDown, ArrowRightLeft, BarChart3, Activity } from 'lucide-react';
import { Line } from 'react-chartjs-2';
import type { ExitSectorRotationData } from '../../types';

interface Props {
  data: ExitSectorRotationData;
}

function signalBadge(signal: string) {
  if (signal.includes('Outflow') || signal.includes('Leaving'))
    return 'badge-error';
  if (signal.includes('Inflow') || signal.includes('Strengthening') || signal.includes('Strong Inflows'))
    return 'badge-success';
  if (signal.includes('Weakening'))
    return 'badge-warning';
  return 'badge-ghost';
}

function volBadge(trend: string) {
  if (trend === 'Surging' || trend === 'Increasing') return 'badge-success';
  if (trend === 'Declining' || trend === 'Decreasing') return 'badge-error';
  return 'badge-ghost';
}

function relPerfColor(val: number | null) {
  if (val === null) return 'text-base-content/50';
  if (val > 2) return 'text-success';
  if (val > 0) return 'text-success/70';
  if (val < -2) return 'text-error';
  if (val < 0) return 'text-error/70';
  return 'text-base-content';
}

function relPerfIcon(val: number | null) {
  if (val === null) return null;
  if (val > 0) return <TrendingUp className="w-3 h-3 text-success inline ml-1" />;
  if (val < 0) return <TrendingDown className="w-3 h-3 text-error inline ml-1" />;
  return <ArrowRightLeft className="w-3 h-3 text-base-content/50 inline ml-1" />;
}

export function PillarSectorRotation({ data }: Props) {
  const rsChartData = useMemo(() => {
    if (!data.sector_rs_series.length || !data.price_timestamps.length) return null;
    const labels = data.price_timestamps.map(d => {
      const dt = new Date(d);
      return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    });
    // Make sure labels match the series length
    const seriesLen = data.sector_rs_series.length;
    const displayLabels = labels.slice(-seriesLen);

    return {
      labels: displayLabels,
      datasets: [
        {
          label: `${data.sector_etf}/SPY Relative Strength`,
          data: data.sector_rs_series,
          borderColor: 'rgb(99, 102, 241)',
          backgroundColor: 'rgba(99, 102, 241, 0.1)',
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          borderWidth: 2,
        },
      ],
    };
  }, [data]);

  const rsChartOpts: any = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: { mode: 'index' as const, intersect: false },
    },
    scales: {
      x: { display: true, ticks: { maxTicksLimit: 8, font: { size: 10 } }, grid: { display: false } },
      y: {
        display: true,
        ticks: { font: { size: 10 } },
        grid: { color: 'rgba(255,255,255,0.05)' },
      },
    },
  };

  // Reference line at 100 (neutral RS)
  if (rsChartData && data.sector_rs_series.length > 0) {
    rsChartData.datasets.push({
      label: 'Baseline (100)',
      data: Array(data.sector_rs_series.length).fill(100),
      borderColor: 'rgba(255, 255, 255, 0.25)',
      backgroundColor: 'transparent',
      fill: false,
      tension: 0,
      pointRadius: 0,
      borderWidth: 1,
    } as any);
  }

  return (
    <div className="space-y-4">
      {/* Top metrics row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-base-200 rounded-lg p-2.5">
          <div className="text-xs text-base-content/60">Rotation Signal</div>
          <div className="mt-1">
            <span className={`badge badge-sm ${signalBadge(data.rotation_signal)}`}>
              {data.rotation_signal}
            </span>
          </div>
        </div>
        <div className="bg-base-200 rounded-lg p-2.5">
          <div className="text-xs text-base-content/60">Sector ETF</div>
          <div className="mt-1 font-bold text-sm">{data.sector_etf}</div>
          <div className="text-xs text-base-content/50">{data.sector}</div>
        </div>
        <div className="bg-base-200 rounded-lg p-2.5">
          <div className="text-xs text-base-content/60">Volume Trend</div>
          <div className="mt-1">
            <span className={`badge badge-sm ${volBadge(data.sector_volume_trend)}`}>
              {data.sector_volume_trend}
            </span>
          </div>
        </div>
        <div className="bg-base-200 rounded-lg p-2.5">
          <div className="text-xs text-base-content/60">Relative Strength</div>
          <div className="mt-1">
            <span className={`badge badge-sm ${signalBadge(data.relative_strength_signal)}`}>
              {data.relative_strength_signal}
            </span>
          </div>
        </div>
      </div>

      {/* Sector vs SPY performance comparison */}
      <div>
        <h5 className="text-xs font-bold mb-2 flex items-center gap-1.5">
          <BarChart3 className="w-3.5 h-3.5" />
          Sector vs. Market Performance
        </h5>
        <div className="overflow-x-auto">
          <table className="table table-xs">
            <thead>
              <tr>
                <th>Period</th>
                <th>{data.sector_etf}</th>
                <th>SPY</th>
                <th>Relative</th>
              </tr>
            </thead>
            <tbody>
              {[
                { label: '1 Month', sector: data.sector_return_1m, spy: data.spy_return_1m, vs: data.sector_vs_spy_1m },
                { label: '3 Months', sector: data.sector_return_3m, spy: data.spy_return_3m, vs: data.sector_vs_spy_3m },
                { label: '6 Months', sector: data.sector_return_6m, spy: data.spy_return_6m, vs: data.sector_vs_spy_6m },
              ].map((row) => (
                <tr key={row.label} className="hover:bg-base-200/50">
                  <td className="font-medium text-xs">{row.label}</td>
                  <td className={`text-xs font-bold ${(row.sector ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                    {row.sector !== null ? `${row.sector > 0 ? '+' : ''}${row.sector}%` : '—'}
                  </td>
                  <td className={`text-xs font-bold ${(row.spy ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                    {row.spy !== null ? `${row.spy > 0 ? '+' : ''}${row.spy}%` : '—'}
                  </td>
                  <td className={`text-xs font-bold ${relPerfColor(row.vs)}`}>
                    {row.vs !== null ? (
                      <>
                        {row.vs > 0 ? '+' : ''}{row.vs}%
                        {relPerfIcon(row.vs)}
                      </>
                    ) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Relative Strength Chart */}
      {rsChartData && (
        <div>
          <h5 className="text-xs font-bold mb-2 flex items-center gap-1.5">
            <Activity className="w-3.5 h-3.5" />
            Sector Relative Strength vs. SPY (1Y)
          </h5>
          <div className="bg-base-200 rounded-lg p-3" style={{ height: '200px' }}>
            <Line data={rsChartData} options={rsChartOpts} />
          </div>
          <p className="text-xs text-base-content/40 mt-1">
            Above 100 = sector outperforming SPY. Below 100 = underperforming. Rising = funds flowing in.
          </p>
        </div>
      )}
    </div>
  );
}
