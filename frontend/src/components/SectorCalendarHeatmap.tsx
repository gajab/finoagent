import React, { useEffect, useState } from 'react';
import { Loader2, AlertCircle, CalendarDays } from 'lucide-react';
import { fetchSectorCalendarReturns } from '../api';
import type { SectorCalendarResponse, SectorCalendarYear } from '../types';

// ---------------------------------------------------------------------------
// Color helpers — intensity-scaled green/red
// ---------------------------------------------------------------------------

function returnColor(ret: number): { bg: string; text: string } {
  if (ret >= 60)  return { bg: '#14532d', text: '#bbf7d0' };
  if (ret >= 40)  return { bg: '#166534', text: '#dcfce7' };
  if (ret >= 20)  return { bg: '#15803d', text: '#f0fdf4' };
  if (ret >= 10)  return { bg: '#16a34a', text: '#fff' };
  if (ret >= 3)   return { bg: '#4ade80', text: '#14532d' };
  if (ret >= 0)   return { bg: '#86efac', text: '#14532d' };
  if (ret >= -3)  return { bg: '#fca5a5', text: '#7f1d1d' };
  if (ret >= -10) return { bg: '#f87171', text: '#fff' };
  if (ret >= -20) return { bg: '#dc2626', text: '#fff' };
  if (ret >= -40) return { bg: '#b91c1c', text: '#fef2f2' };
  return           { bg: '#7f1d1d', text: '#fef2f2' };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function SectorCalendarHeatmap() {
  const [data, setData] = useState<SectorCalendarResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchSectorCalendarReturns()
      .then(setData)
      .catch(e => setError(e?.message || 'Failed to load calendar returns'))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-16 text-base-content/40">
        <Loader2 className="w-5 h-5 animate-spin" />
        <span className="text-sm">Loading calendar returns…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 p-4 rounded-xl bg-error/10 border border-error/20 text-error">
        <AlertCircle className="w-4 h-4 shrink-0" />
        <span className="text-sm">{error}</span>
      </div>
    );
  }

  if (!data || data.years.length === 0) {
    return <p className="text-base-content/40 py-8 text-center text-sm">No data available.</p>;
  }

  const years: SectorCalendarYear[] = data.years;
  const numRows = Math.max(...years.map(y => y.sectors.length));

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center gap-2">
        <CalendarDays className="w-5 h-5 text-primary" />
        <h2 className="text-lg font-semibold">Sector Returns by Calendar Year</h2>
        <span className="ml-auto text-[10px] text-base-content/35 uppercase tracking-wide font-medium">ranked best → worst</span>
      </div>

      {/* Heatmap table */}
      <div className="overflow-x-auto rounded-xl border border-white/[0.07]">
        <table className="border-collapse text-xs" style={{ minWidth: `${years.length * 90 + 52}px` }}>
          <thead>
            <tr>
              {/* Rank header */}
              <th className="sticky left-0 z-10 w-9 px-2 py-2.5 text-center text-[9px] font-bold uppercase tracking-wider text-base-content/30 bg-base-200/90 backdrop-blur-sm border-b border-white/[0.07]">
                #
              </th>
              {years.map(y => (
                <th
                  key={y.year}
                  className="px-2 py-2.5 text-center font-bold text-base-content/70 bg-base-200/90 border-b border-white/[0.07] whitespace-nowrap"
                  style={{ minWidth: 86 }}
                >
                  {y.year}
                  {y.in_progress && <span className="ml-1 text-warning text-[9px]">★</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: numRows }).map((_, rank) => (
              <tr key={rank} className="group">
                {/* Rank number */}
                <td className="sticky left-0 z-10 text-center text-[9px] font-bold text-base-content/25 bg-base-200/70 backdrop-blur-sm border-r border-white/[0.04] group-hover:bg-base-200/90 transition-colors">
                  {rank + 1}
                </td>
                {years.map(y => {
                  const sector = y.sectors[rank];
                  if (!sector) return <td key={y.year} className="p-0.5" />;
                  const { bg, text } = returnColor(sector.return);
                  return (
                    <td key={y.year} className="p-0.5">
                      <div
                        className="flex flex-col items-center justify-center rounded-lg px-1 py-2 leading-tight cursor-default transition-opacity hover:opacity-90"
                        title={`${sector.name}: ${sector.return > 0 ? '+' : ''}${sector.return}%`}
                        style={{ backgroundColor: bg, color: text, minHeight: 46 }}
                      >
                        <span className="font-semibold text-center" style={{ fontSize: 9.5, lineHeight: 1.25 }}>
                          {sector.name}
                        </span>
                        <span className="font-bold mt-0.5" style={{ fontSize: 11 }}>
                          {sector.return > 0 ? '+' : ''}{sector.return}%
                        </span>
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-[10px] text-base-content/30">
        ★ Current year in-progress — returns computed Jan 1 → today.
        Sectors: XLK Tech · XLF Fin · XLV Health · XLE Energy · XLI Industrials ·
        XLY Disc · XLP Staples · XLU Util · XLB Materials · XLRE Real Estate · XLC Comm
      </p>
    </div>
  );
}
