import React from 'react';
import { Activity, BarChart3 } from 'lucide-react';
import type { ExitSentimentData } from '../../types';

interface Props {
  data: ExitSentimentData;
}

export function PillarSentiment({ data }: Props) {
  const l = data.sentiment_label || '';
  const labelColor =
    l.includes('Strong') || l.includes('Constructive') ? 'badge-success' :
    l.includes('Weak') || l.includes('Soft') ? 'badge-error' : 'badge-warning';

  const rangePos = data.range_position_pct;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold flex items-center gap-2">
          <Activity size={16} className="text-primary" /> Price Momentum & Positioning
        </h4>
        <span className={`badge ${labelColor}`}>{data.sentiment_label}</span>
      </div>

      {/* 52-week range position */}
      {rangePos != null && (
        <div className="bg-base-200 rounded-lg p-3">
          <div className="flex justify-between text-xs text-base-content/60 mb-1.5">
            <span>52-Week Range Position</span>
            <span className="font-bold">{rangePos.toFixed(0)}%</span>
          </div>
          <div className="relative h-2 bg-base-100 rounded-full overflow-hidden">
            <div className="absolute inset-0 flex">
              <div className="h-full bg-error/20" style={{ width: '33%' }} />
              <div className="h-full bg-warning/20" style={{ width: '34%' }} />
              <div className="h-full bg-success/20" style={{ width: '33%' }} />
            </div>
            <div className="absolute top-0 h-full w-1 bg-base-content rounded-full transition-all duration-500"
              style={{ left: `${Math.min(100, Math.max(0, rangePos))}%` }} />
          </div>
          <div className="flex justify-between text-[10px] text-base-content/30 mt-0.5"><span>52W Low</span><span>52W High</span></div>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 mb-1">From 52W High</div>
          <div className={`text-lg font-bold ${data.pct_from_52w_high != null ? (data.pct_from_52w_high >= -5 ? 'text-success' : data.pct_from_52w_high <= -25 ? 'text-error' : '') : ''}`}>
            {data.pct_from_52w_high != null ? `${data.pct_from_52w_high.toFixed(1)}%` : 'N/A'}
          </div>
        </div>

        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 mb-1">Trend</div>
          <div className="flex flex-col gap-0.5 text-[11px] font-semibold">
            <span className={data.above_50dma == null ? 'text-base-content/40' : data.above_50dma ? 'text-success' : 'text-error'}>
              {data.above_50dma == null ? '— 50DMA' : data.above_50dma ? '↑ Above 50DMA' : '↓ Below 50DMA'}
            </span>
            <span className={data.above_200dma == null ? 'text-base-content/40' : data.above_200dma ? 'text-success' : 'text-error'}>
              {data.above_200dma == null ? '— 200DMA' : data.above_200dma ? '↑ Above 200DMA' : '↓ Below 200DMA'}
            </span>
          </div>
        </div>

        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 mb-1">1Y Return</div>
          <div className={`text-lg font-bold ${data.one_year_return_pct != null ? (data.one_year_return_pct >= 0 ? 'text-success' : 'text-error') : ''}`}>
            {data.one_year_return_pct != null ? `${data.one_year_return_pct >= 0 ? '+' : ''}${data.one_year_return_pct.toFixed(0)}%` : 'N/A'}
          </div>
        </div>

        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 flex items-center gap-1 mb-1"><BarChart3 size={12} /> vs S&amp;P 500</div>
          <div className={`text-lg font-bold ${data.relative_to_sp_pct != null ? (data.relative_to_sp_pct >= 0 ? 'text-success' : 'text-error') : ''}`}>
            {data.relative_to_sp_pct != null ? `${data.relative_to_sp_pct >= 0 ? '+' : ''}${data.relative_to_sp_pct.toFixed(0)}%` : 'N/A'}
          </div>
          <div className="text-[10px] text-base-content/40 mt-1">Relative strength</div>
        </div>
      </div>
    </div>
  );
}
