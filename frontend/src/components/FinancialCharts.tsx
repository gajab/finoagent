import React from 'react';
import { LineChart } from 'lucide-react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend,
} from 'chart.js';
import { Bar } from 'react-chartjs-2';
import type { FinancialData } from '../types';

// Register Chart.js components
ChartJS.register(CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend);

interface FinancialChartsProps {
  financials: FinancialData;
  ticker: string;
}

function formatRevenue(val: number | null): string {
  if (val === null) return 'N/A';
  if (Math.abs(val) >= 1e12) return `$${(val / 1e12).toFixed(1)}T`;
  if (Math.abs(val) >= 1e9) return `$${(val / 1e9).toFixed(1)}B`;
  if (Math.abs(val) >= 1e6) return `$${(val / 1e6).toFixed(1)}M`;
  return `$${val.toLocaleString()}`;
}

function makeChartData(
  labels: string[],
  dataPoints: (number | null)[],
  label: string,
  borderColor: string,
  bgColor: string,
) {
  return {
    labels,
    datasets: [
      {
        label,
        data: dataPoints,
        borderColor,
        backgroundColor: bgColor,
        borderWidth: 2,
        borderRadius: 6,
      },
    ],
  };
}

function makeChartOptions(formatFn: (v: number) => string) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: 'rgba(17, 17, 27, 0.95)',
        titleColor: '#cdd6f4',
        bodyColor: 'rgba(205, 214, 244, 0.7)',
        callbacks: {
          label: (ctx: any) =>
            ctx.parsed.y !== null ? `${ctx.dataset.label}: ${formatFn(ctx.parsed.y)}` : 'N/A',
        },
      },
    },
    scales: {
      x: {
        grid: { display: false },
        ticks: { color: 'rgba(160, 160, 160, 0.8)' },
      },
      y: {
        grid: { color: 'rgba(128, 128, 128, 0.15)' },
        ticks: {
          color: 'rgba(160, 160, 160, 0.8)',
          callback: (value: number | string) => formatFn(Number(value)),
        },
      },
    },
  } as const;
}

export const FinancialCharts: React.FC<FinancialChartsProps> = ({ financials, ticker }) => {
  if (!financials.years.length) {
    return (
      <div className="glass-card">
        <div className="p-5">
          <h3 className="font-bold text-sm flex items-center gap-2">
            <LineChart size={20} /> Financials
          </h3>
          <p className="text-base-content/60">No financial data available.</p>
        </div>
      </div>
    );
  }

  const epsData = makeChartData(
    financials.years,
    financials.eps,
    'EPS',
    'oklch(0.723 0.219 149.579)',
    'oklch(0.723 0.219 149.579 / 0.5)',
  );

  const revenueData = makeChartData(
    financials.years,
    financials.revenue,
    'Revenue',
    'oklch(0.7 0.143 215.221)',
    'oklch(0.7 0.143 215.221 / 0.5)',
  );

  const fcfData = makeChartData(
    financials.years,
    financials.freeCashFlow,
    'Free Cash Flow',
    'oklch(0.75 0.15 60)',
    'oklch(0.75 0.15 60 / 0.5)',
  );

  const epsOptions = makeChartOptions((v) => `$${v.toFixed(2)}`);
  const revenueOptions = makeChartOptions((v) => formatRevenue(v));
  const fcfOptions = makeChartOptions((v) => formatRevenue(v));

  return (
    <div className="glass-card">
      <div className="p-5">
        <h3 className="font-bold text-sm flex items-center gap-2 mb-4">
          <LineChart size={20} /> Financial Overview
        </h3>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* EPS Chart */}
          <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
            <h4 className="text-sm font-semibold text-base-content/70 mb-2">Earnings Per Share (EPS)</h4>
            <div className="h-48">
              <Bar key={`eps-${ticker}`} data={epsData} options={epsOptions as any} />
            </div>
          </div>

          {/* Revenue Chart */}
          <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
            <h4 className="text-sm font-semibold text-base-content/70 mb-2">Revenue</h4>
            <div className="h-48">
              <Bar key={`rev-${ticker}`} data={revenueData} options={revenueOptions as any} />
            </div>
          </div>

          {/* FCF Chart */}
          <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
            <h4 className="text-sm font-semibold text-base-content/70 mb-2">Free Cash Flow</h4>
            <div className="h-48">
              <Bar key={`fcf-${ticker}`} data={fcfData} options={fcfOptions as any} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
