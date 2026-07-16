import React from 'react';
import {
  Chart as ChartJS, CategoryScale, LinearScale, BarElement,
  PointElement, LineElement, Title, Tooltip, Legend,
} from 'chart.js';
import { Bar } from 'react-chartjs-2';
import type { ExitStructuralData, ExitStructuralChartData } from '../../types';

ChartJS.register(CategoryScale, LinearScale, BarElement, PointElement, LineElement, Title, Tooltip, Legend);

interface Props {
  data: ExitStructuralData;
  chartData: ExitStructuralChartData;
}

const chartOpts = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: { display: true, position: 'bottom' as const, labels: { boxWidth: 10, font: { size: 10 } } },
    tooltip: { mode: 'index' as const, intersect: false },
  },
  scales: {
    x: { grid: { display: false }, ticks: { font: { size: 10 } } },
    y: { grid: { color: 'rgba(255,255,255,0.06)' }, ticks: { font: { size: 10 } } },
  },
};

function directionBadge(direction: string | null) {
  if (!direction) return <span className="badge badge-xs badge-ghost">N/A</span>;
  if (direction === 'declining') return <span className="badge badge-xs badge-error">Declining</span>;
  if (direction === 'improving') return <span className="badge badge-xs badge-success">Increasing</span>;
  return <span className="badge badge-xs badge-info">Stable</span>;
}

export function PillarStructural({ data, chartData }: Props) {
  const investChart = {
    labels: chartData.years,
    datasets: [
      {
        label: 'R&D Spend',
        data: chartData.rd_spend,
        backgroundColor: '#3abff880',
        borderRadius: 4,
      },
      {
        label: 'CapEx',
        data: chartData.capex,
        backgroundColor: '#f8727280',
        borderRadius: 4,
      },
    ],
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-base-200 rounded-lg p-2.5">
          <div className="text-xs text-base-content/60">R&D / Revenue</div>
          <div className="text-sm font-bold mt-1">
            {data.rd_as_pct_revenue != null ? `${data.rd_as_pct_revenue.toFixed(1)}%` : 'N/A'}
          </div>
          <div className="flex items-center gap-1 mt-0.5">
            <span className="text-xs text-base-content/50">Trend:</span>
            {directionBadge(data.rd_direction)}
          </div>
        </div>
        <div className="bg-base-200 rounded-lg p-2.5">
          <div className="text-xs text-base-content/60">CapEx / Revenue</div>
          <div className="text-sm font-bold mt-1">
            {data.capex_to_revenue_pct.toFixed(1)}%
          </div>
        </div>
        <div className="bg-base-200 rounded-lg p-2.5">
          <div className="text-xs text-base-content/60">3Y Revenue CAGR</div>
          <div className={`text-sm font-bold mt-1 ${data.revenue_growth_3y_cagr < 0 ? 'text-error' : data.revenue_growth_3y_cagr < 5 ? 'text-warning' : 'text-success'}`}>
            {data.revenue_growth_3y_cagr.toFixed(1)}%
          </div>
        </div>
        <div className="bg-base-200 rounded-lg p-2.5">
          <div className="text-xs text-base-content/60">Industry Growth</div>
          <div className="mt-1">
            <span className={`badge badge-sm ${data.industry_growth_signal === 'Declining' ? 'badge-error' : data.industry_growth_signal === 'Mature' ? 'badge-warning' : 'badge-success'}`}>
              {data.industry_growth_signal}
            </span>
          </div>
          <div className="text-xs text-base-content/50 mt-0.5">{data.industry}</div>
        </div>
      </div>

      {/* Investment chart */}
      {(chartData.rd_spend.length > 0 || chartData.capex.length > 0) && (
        <div className="bg-base-200/30 rounded-lg p-3">
          <h5 className="text-xs font-bold mb-2">R&D & CapEx Investment</h5>
          <div className="h-44">
            <Bar data={investChart} options={chartOpts} />
          </div>
        </div>
      )}
    </div>
  );
}
