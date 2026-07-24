import React, { useState } from 'react';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement,
  BarElement, Filler, Tooltip, Legend,
} from 'chart.js';
import { Line, Bar } from 'react-chartjs-2';
import type { TechnicalData } from '../types';

// One chart at a time (Price / Volume / MACD / RSI) — replaces the six stacked
// charts as the underlying's main chart area. Dark-theme only.
ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, BarElement, Filler, Tooltip, Legend);

type View = 'price' | 'volume' | 'macd' | 'rsi';
const VIEWS: { id: View; label: string }[] = [
  { id: 'price', label: 'Price' }, { id: 'volume', label: 'Volume' },
  { id: 'macd', label: 'MACD' }, { id: 'rsi', label: 'RSI' },
];

const GRID = 'rgba(255,255,255,0.06)';
const TICK = 'rgba(255,255,255,0.45)';

const baseOpts = (): any => ({
  responsive: true,
  maintainAspectRatio: false,
  interaction: { mode: 'index', intersect: false },
  plugins: {
    legend: { display: true, position: 'top', align: 'end', labels: { color: TICK, boxWidth: 10, boxHeight: 2, font: { size: 10 }, padding: 10 } },
    tooltip: { enabled: true },
  },
  scales: {
    x: { grid: { color: GRID }, ticks: { color: TICK, maxTicksLimit: 6, autoSkip: true, maxRotation: 0, font: { size: 10 } } },
    y: { grid: { color: GRID }, ticks: { color: TICK, font: { size: 10 } } },
  },
});

const shortDate = (t: string) => { const d = new Date(t); return isNaN(d.getTime()) ? t : `${d.getMonth() + 1}/${d.getDate()}`; };

export function UnderlyingCharts({ technical: t }: { technical: TechnicalData }) {
  const [view, setView] = useState<View>('price');

  const priceLabels = t.timestamps.map(shortDate);
  const rsiLabels = (t.rsiTimestamps?.length ? t.rsiTimestamps : t.timestamps).map(shortDate);
  const macdLabels = (t.macd?.timestamps?.length ? t.macd.timestamps : t.timestamps).map(shortDate);

  let chart: React.ReactNode;
  if (view === 'price') {
    const flat = (v?: number | null) => (v != null && v > 0 ? t.prices.map(() => v) : null);
    const rline = flat(t.resistanceLevel), sline = flat(t.supportLevel);
    const datasets: any[] = [
      { label: 'Price', data: t.prices, borderColor: 'rgb(56,189,248)', backgroundColor: 'rgba(56,189,248,0.12)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
    ];
    if (rline) datasets.push({ label: 'Resistance', data: rline, borderColor: 'rgba(239,68,68,0.55)', borderDash: [5, 4], pointRadius: 0, borderWidth: 1 });
    if (sline) datasets.push({ label: 'Support', data: sline, borderColor: 'rgba(34,197,94,0.55)', borderDash: [5, 4], pointRadius: 0, borderWidth: 1 });
    chart = <Line options={baseOpts()} data={{ labels: priceLabels, datasets } as any} />;
  } else if (view === 'volume') {
    const colors = t.volumes.map((_, i) => (i > 0 && t.prices[i] < t.prices[i - 1] ? 'rgba(239,68,68,0.45)' : 'rgba(34,197,94,0.45)'));
    chart = <Bar options={baseOpts()} data={{ labels: priceLabels, datasets: [{ label: 'Volume', data: t.volumes, backgroundColor: colors, borderWidth: 0 }] } as any} />;
  } else if (view === 'macd') {
    const m = t.macd;
    chart = m ? (
      <Line options={baseOpts()} data={{ labels: macdLabels, datasets: [
        { label: 'MACD', data: m.macdValues, borderColor: 'rgb(56,189,248)', pointRadius: 0, borderWidth: 2, tension: 0.3 },
        { label: 'Signal', data: m.signalValues, borderColor: 'rgb(251,191,36)', pointRadius: 0, borderWidth: 1.5, tension: 0.3 },
      ] } as any} />
    ) : <NoData label="MACD" />;
  } else {
    const guide = (v: number) => t.rsiValues.map(() => v);
    const opts = baseOpts();
    opts.scales.y = { ...opts.scales.y, min: 0, max: 100 };
    chart = t.rsiValues?.length ? (
      <Line options={opts} data={{ labels: rsiLabels, datasets: [
        { label: 'RSI', data: t.rsiValues, borderColor: 'rgb(168,85,247)', backgroundColor: 'rgba(168,85,247,0.1)', fill: false, pointRadius: 0, borderWidth: 2, tension: 0.3 },
        { label: 'Overbought (70)', data: guide(70), borderColor: 'rgba(239,68,68,0.35)', borderDash: [4, 4], pointRadius: 0, borderWidth: 1 },
        { label: 'Oversold (30)', data: guide(30), borderColor: 'rgba(34,197,94,0.35)', borderDash: [4, 4], pointRadius: 0, borderWidth: 1 },
      ] } as any} />
    ) : <NoData label="RSI" />;
  }

  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/20 p-3">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <span className="text-xs font-semibold text-base-content/60 mr-1">Chart</span>
        <div className="inline-flex gap-0.5 p-0.5 bg-base-300/40 rounded-lg">
          {VIEWS.map((v) => (
            <button key={v.id} type="button" onClick={() => setView(v.id)}
              className={`text-xs px-3 py-1 rounded-md transition-colors ${view === v.id ? 'bg-secondary text-secondary-content' : 'text-base-content/60 hover:text-base-content'}`}>
              {v.label}
            </button>
          ))}
        </div>
      </div>
      <div className="h-56">{chart}</div>
    </div>
  );
}

function NoData({ label }: { label: string }) {
  return <div className="h-full flex items-center justify-center text-xs text-base-content/40">No {label} data available for this timeframe.</div>;
}
