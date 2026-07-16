import React, { useMemo, useState } from 'react';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement,
  BarElement, Filler, Tooltip, Legend,
} from 'chart.js';
import { Line, Bar } from 'react-chartjs-2';
import { Loader2, CalendarClock, Coins } from 'lucide-react';
import type { DebtHistoryResponse, DebtRange } from '../types';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, BarElement, Filler, Tooltip, Legend);

const GREEN = 'rgb(16,185,129)';
const RED = 'rgb(244,63,94)';
const MUTED = 'rgba(148,163,184,0.55)';
const GRID = 'rgba(148,163,184,0.12)';

const RANGE_ORDER = ['1D', '1M', '3M', 'YTD', '1Y', '5Y', '10Y'];

function fmtLabel(t: string, rangeKey: string): string {
  if (rangeKey === '1D') return t;                 // already HH:MM
  const [y, m, d] = t.split('-');
  if (rangeKey === '5Y' || rangeKey === '10Y') return `${m}/${y.slice(2)}`;
  return `${parseInt(m, 10)}/${parseInt(d, 10)}`;
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  const dt = new Date(iso + (iso.length === 10 ? 'T00:00:00' : ''));
  return dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

const DebtPriceHistory: React.FC<{ data: DebtHistoryResponse | null; loading: boolean }> = ({ data, loading }) => {
  const ranges = data?.ranges ?? [];
  const available = useMemo(
    () => RANGE_ORDER.filter((k) => ranges.some((r) => r.key === k)),
    [ranges],
  );
  const [sel, setSel] = useState<string>('1Y');
  const active: DebtRange | undefined =
    ranges.find((r) => r.key === sel) ?? ranges.find((r) => r.key === '1Y') ?? ranges[0];

  const up = (active?.change_pct ?? 0) >= 0;
  const lineColor = up ? GREEN : RED;

  const chart = useMemo(() => {
    if (!active) return null;
    const labels = active.series.map((p) => fmtLabel(p.t, active.key));
    return {
      data: {
        labels,
        datasets: [{
          data: active.series.map((p) => p.c),
          borderColor: lineColor,
          backgroundColor: up ? 'rgba(16,185,129,0.10)' : 'rgba(244,63,94,0.10)',
          borderWidth: 1.75, fill: true, pointRadius: 0, tension: 0.25,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index' as const, intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (it: any) => active.series[it[0].dataIndex]?.t ?? '',
              label: (it: any) => ` ${data?.currency ?? '$'} ${Number(it.raw).toFixed(2)}`,
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: MUTED, maxTicksLimit: 6, font: { size: 10 } } },
          y: { position: 'right' as const, grid: { color: GRID }, ticks: { color: MUTED, font: { size: 10 }, callback: (v: any) => Number(v).toFixed(0) } },
        },
      },
    };
  }, [active, up, lineColor, data?.currency]);

  const div = data?.dividends;
  const divChart = useMemo(() => {
    if (!div?.history?.length) return null;
    return {
      data: {
        labels: div.history.map((h) => fmtLabel(h.date, '5Y')),
        datasets: [{ data: div.history.map((h) => h.amount), backgroundColor: 'rgba(99,102,241,0.55)', borderRadius: 2 }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { title: (it: any) => div.history[it[0].dataIndex]?.date ?? '', label: (it: any) => ` ${Number(it.raw).toFixed(4)} / sh` } },
        },
        scales: {
          x: { grid: { display: false }, ticks: { color: MUTED, maxTicksLimit: 6, font: { size: 9 } } },
          y: { position: 'right' as const, grid: { color: GRID }, ticks: { color: MUTED, font: { size: 9 }, maxTicksLimit: 4 } },
        },
      },
    };
  }, [div]);

  return (
    <div className="glass-card">
      <div className="card-body">
        {/* Header: price + range change + toggles */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-3">
          <div className="flex items-baseline gap-3">
            <h3 className="text-sm font-semibold text-base-content/70">Price & dividends</h3>
            {data?.current_price != null && (
              <span className="text-2xl font-bold text-base-content">
                {data.currency === 'USD' ? '$' : ''}{data.current_price.toFixed(2)}
              </span>
            )}
            {active && (
              <span className={`text-sm font-semibold ${up ? 'text-emerald-400' : 'text-rose-400'}`}>
                {up ? '▲' : '▼'} {active.change_pct != null ? `${active.change_pct > 0 ? '+' : ''}${active.change_pct.toFixed(2)}%` : '—'}
                <span className="text-base-content/40 font-normal ml-1">{active.key}</span>
              </span>
            )}
          </div>
          <div className="flex gap-1 flex-wrap">
            {RANGE_ORDER.map((k) => {
              const enabled = available.includes(k);
              return (
                <button key={k} disabled={!enabled} onClick={() => setSel(k)}
                  className={`text-xs px-2.5 py-1 rounded-lg border transition-colors ${
                    active?.key === k
                      ? 'bg-secondary/15 text-secondary border-secondary/40 font-semibold'
                      : enabled
                        ? 'bg-base-200 border-base-300 hover:border-secondary/40 text-base-content/70'
                        : 'bg-base-200/40 border-base-300/40 text-base-content/25 cursor-not-allowed'
                  }`}>
                  {k}
                </button>
              );
            })}
          </div>
        </div>

        {/* Price chart */}
        <div className="h-60 relative">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center text-base-content/40">
              <Loader2 className="w-5 h-5 animate-spin mr-2" /> Loading chart…
            </div>
          )}
          {!loading && chart && <Line data={chart.data} options={chart.options as any} />}
          {!loading && !chart && (
            <div className="absolute inset-0 flex items-center justify-center text-sm text-base-content/40">No price history available.</div>
          )}
        </div>
        {active?.partial && (
          <p className="text-[11px] text-base-content/40 mt-1">
            {active.key} shows all available history since inception ({fmtDate(active.start_date ?? null)}).
          </p>
        )}

        {/* Dividends */}
        <div className="border-t border-base-300/50 mt-4 pt-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div>
            <div className="flex items-center gap-2 mb-3">
              <Coins className="w-4 h-4 text-secondary" />
              <span className="text-sm font-semibold">Dividends</span>
              {div?.frequency && <span className="text-xs px-2 py-0.5 rounded-full bg-base-200 border border-base-300">{div.frequency}</span>}
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-xl bg-secondary/10 border border-secondary/25 px-3 py-2.5 col-span-2">
                <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-secondary/80">
                  <CalendarClock className="w-3.5 h-3.5" /> Next ex-dividend {div?.next_estimated && <span className="opacity-60">(est.)</span>}
                </div>
                <div className="text-base font-bold text-base-content">{fmtDate(div?.next_ex_date ?? null)}</div>
              </div>
              <div className="rounded-xl bg-base-200/40 border border-base-300/50 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wide text-base-content/40">TTM distributions</div>
                <div className="text-sm font-bold">{div?.ttm_total != null ? `$${div.ttm_total.toFixed(2)}` : '—'}</div>
                <div className="text-[10px] text-base-content/40">{div?.ttm_yield_pct != null ? `${div.ttm_yield_pct.toFixed(2)}% yield` : ''}</div>
              </div>
              <div className="rounded-xl bg-base-200/40 border border-base-300/50 px-3 py-2">
                <div className="text-[10px] uppercase tracking-wide text-base-content/40">Last paid</div>
                <div className="text-sm font-bold">{div?.last_amount != null ? `$${div.last_amount.toFixed(3)}` : '—'}</div>
                <div className="text-[10px] text-base-content/40">{fmtDate(div?.last_date ?? null)}</div>
              </div>
            </div>
          </div>
          <div>
            <div className="text-xs text-base-content/50 mb-2">Distribution history</div>
            <div className="h-32">
              {divChart ? <Bar data={divChart.data} options={divChart.options as any} />
                : <div className="h-full flex items-center justify-center text-xs text-base-content/40">No dividend history.</div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DebtPriceHistory;
