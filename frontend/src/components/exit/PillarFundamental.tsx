import React from 'react';
import {
  Chart as ChartJS, CategoryScale, LinearScale, BarElement,
  PointElement, LineElement, Title, Tooltip, Legend,
} from 'chart.js';
import { Bar, Line } from 'react-chartjs-2';
import type { ExitFundamentalData, ExitPillarChartData } from '../../types';

ChartJS.register(CategoryScale, LinearScale, BarElement, PointElement, LineElement, Title, Tooltip, Legend);

interface Props {
  data: ExitFundamentalData;
  chartData: ExitPillarChartData;
}

function fmtPct(val: number | null | undefined): string {
  if (val === null || val === undefined) return 'N/A';
  return `${val > 0 ? '+' : ''}${val.toFixed(1)}%`;
}

function fmtRatio(val: number | null | undefined): string {
  if (val === null || val === undefined) return 'N/A';
  return val.toFixed(2);
}

function directionBadge(direction: string) {
  if (direction === 'declining') return <span className="badge badge-xs badge-error">Declining</span>;
  if (direction === 'improving') return <span className="badge badge-xs badge-success">Improving</span>;
  return <span className="badge badge-xs badge-info">Stable</span>;
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

export function PillarFundamental({ data, chartData }: Props) {
  const marginChart = {
    labels: chartData.years,
    datasets: [
      { label: 'Gross Margin %', data: chartData.gross_margin, borderColor: '#36d399', backgroundColor: '#36d39930', tension: 0.3, fill: false, pointRadius: 3 },
      { label: 'Op. Margin %', data: chartData.operating_margin, borderColor: '#3abff8', backgroundColor: '#3abff830', tension: 0.3, fill: false, pointRadius: 3 },
      { label: 'Net Margin %', data: chartData.net_margin, borderColor: '#f87272', backgroundColor: '#f8727230', tension: 0.3, fill: false, pointRadius: 3 },
    ],
  };

  const fcfChart = {
    labels: chartData.years,
    datasets: [
      {
        label: 'Free Cash Flow',
        data: chartData.fcf,
        backgroundColor: chartData.fcf.map(v => v >= 0 ? '#36d39980' : '#f8727280'),
        borderRadius: 4,
      },
    ],
  };

  const revenueGrowthChart = {
    labels: chartData.years.slice(1),
    datasets: [
      {
        label: 'Revenue Growth %',
        data: data.revenue_growth_rates,
        backgroundColor: data.revenue_growth_rates.map(v => (v ?? 0) >= 0 ? '#3abff880' : '#f8727280'),
        borderRadius: 4,
      },
    ],
  };

  return (
    <div className="space-y-4">
      {/* Key Metrics Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-base-200 rounded-lg p-2.5 group relative">
          <div className="text-xs text-base-content/60 flex items-center gap-1">
            Revenue Trend
            <span className="opacity-0 group-hover:opacity-100 absolute -top-8 left-0 bg-neutral text-neutral-content text-[10px] px-2 py-1 rounded z-50 whitespace-nowrap transition-opacity">Compares recent vs older revenue halves to detect direction</span>
          </div>
          <div className="flex items-center gap-1.5 mt-1">
            {directionBadge(data.revenue_direction)}
          </div>
        </div>
        <div className="bg-base-200 rounded-lg p-2.5 group relative">
          <div className="text-xs text-base-content/60 flex items-center gap-1">
            Margin Trend
            <span className="opacity-0 group-hover:opacity-100 absolute -top-8 left-0 bg-neutral text-neutral-content text-[10px] px-2 py-1 rounded z-50 whitespace-nowrap transition-opacity">Operating margin trend over recent fiscal years</span>
          </div>
          <div className="flex items-center gap-1.5 mt-1">
            {directionBadge(data.margin_direction)}
          </div>
        </div>
        <div className="bg-base-200 rounded-lg p-2.5 group relative">
          <div className="text-xs text-base-content/60 flex items-center gap-1">
            Debt / Equity
            <span className="opacity-0 group-hover:opacity-100 absolute -top-8 left-0 bg-neutral text-neutral-content text-[10px] px-2 py-1 rounded z-50 whitespace-nowrap transition-opacity">Total debt relative to equity. &lt;1.0 is healthy, &gt;2.0 is concerning</span>
          </div>
          <div className={`text-sm font-bold mt-1 ${data.debt_to_equity != null && data.debt_to_equity > 2 ? 'text-error' : data.debt_to_equity != null && data.debt_to_equity > 1 ? 'text-warning' : 'text-success'}`}>
            {fmtRatio(data.debt_to_equity)}
          </div>
        </div>
        <div className="bg-base-200 rounded-lg p-2.5 group relative">
          <div className="text-xs text-base-content/60 flex items-center gap-1">
            Interest Coverage
            <span className="opacity-0 group-hover:opacity-100 absolute -top-8 left-0 bg-neutral text-neutral-content text-[10px] px-2 py-1 rounded z-50 whitespace-nowrap transition-opacity">EBIT / Interest Expense. &gt;5x is healthy, &lt;2x is risky</span>
          </div>
          <div className={`text-sm font-bold mt-1 ${data.interest_coverage != null && data.interest_coverage < 3 ? 'text-error' : data.interest_coverage != null && data.interest_coverage < 6 ? 'text-warning' : 'text-success'}`}>
            {data.interest_coverage != null ? `${data.interest_coverage.toFixed(1)}x` : 'N/A'}
          </div>
        </div>
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="bg-base-200/30 rounded-lg p-3">
          <h5 className="text-xs font-bold mb-2">Revenue Growth Rates</h5>
          <div className="h-40">
            <Bar data={revenueGrowthChart} options={chartOpts} />
          </div>
        </div>
        <div className="bg-base-200/30 rounded-lg p-3">
          <h5 className="text-xs font-bold mb-2">Margin Trends</h5>
          <div className="h-40">
            <Line data={marginChart} options={chartOpts} />
          </div>
        </div>
        <div className="bg-base-200/30 rounded-lg p-3">
          <h5 className="text-xs font-bold mb-2">Free Cash Flow</h5>
          <div className="h-40">
            <Bar data={fcfChart} options={chartOpts} />
          </div>
        </div>
      </div>

      {/* Earnings Surprises */}
      {data.earnings_surprises.length > 0 && (
        <div>
          <h5 className="text-xs font-bold mb-2">Recent Earnings Surprises</h5>
          <div className="flex gap-2 flex-wrap">
            {data.earnings_surprises.map((es, i) => (
              <div key={i} className={`badge badge-sm gap-1 ${es.surprise_pct >= 0 ? 'badge-success' : 'badge-error'}`}>
                {es.quarter}: {fmtPct(es.surprise_pct)}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Bottom row: Current Ratio & EPS consistency */}
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-base-200 rounded-lg p-2.5 group relative">
          <div className="text-xs text-base-content/60 flex items-center gap-1">
            Current Ratio
            <span className="opacity-0 group-hover:opacity-100 absolute -top-8 left-0 bg-neutral text-neutral-content text-[10px] px-2 py-1 rounded z-50 whitespace-nowrap transition-opacity">Current assets / current liabilities. &gt;1.5 is healthy, &lt;1.0 is risky</span>
          </div>
          <div className={`text-sm font-bold mt-1 ${data.current_ratio != null && data.current_ratio < 1 ? 'text-error' : data.current_ratio != null && data.current_ratio < 1.5 ? 'text-warning' : 'text-success'}`}>
            {fmtRatio(data.current_ratio)}
          </div>
        </div>
        <div className="bg-base-200 rounded-lg p-2.5 group relative">
          <div className="text-xs text-base-content/60 flex items-center gap-1">
            EPS Consistency
            <span className="opacity-0 group-hover:opacity-100 absolute -top-8 left-0 bg-neutral text-neutral-content text-[10px] px-2 py-1 rounded z-50 whitespace-nowrap transition-opacity">% of quarters with positive EPS. &gt;80% is consistent</span>
          </div>
          <div className={`text-sm font-bold mt-1 ${data.eps_consistency < 50 ? 'text-error' : data.eps_consistency < 80 ? 'text-warning' : 'text-success'}`}>
            {data.eps_consistency.toFixed(0)}%
          </div>
        </div>
      </div>
    </div>
  );
}
