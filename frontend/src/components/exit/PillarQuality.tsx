import React from 'react';
import { Landmark, Coins, Percent, RefreshCcw } from 'lucide-react';
import type { ExitQualityData } from '../../types';

interface Props {
  data: ExitQualityData;
}

function tone(v: number | null, good: number, warn: number): string {
  if (v === null) return '';
  if (v >= good) return 'text-success';
  if (v >= warn) return 'text-warning';
  return 'text-error';
}

export function PillarQuality({ data }: Props) {
  const buyback = data.shares_trend === 'buyback';
  const dilution = data.shares_trend === 'dilution';

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h4 className="text-sm font-semibold flex items-center gap-2">
          <Landmark size={16} className="text-primary" /> Returns, Cash Conversion & Capital Allocation
        </h4>
        {data.shares_trend && (
          <span className={`badge gap-1 ${buyback ? 'badge-success' : dilution ? 'badge-error' : 'badge-ghost'}`}>
            <RefreshCcw size={11} />
            {buyback ? 'Net Buybacks' : dilution ? 'Dilution' : 'Stable Share Count'}
            {data.buyback_yield_pct != null && buyback ? ` · ${data.buyback_yield_pct.toFixed(1)}%/yr` : ''}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {/* ROIC — the headline quality metric */}
        <div className="bg-base-200 rounded-lg p-3 ring-1 ring-primary/15">
          <div className="text-xs text-base-content/60 flex items-center gap-1.5 mb-1"><Landmark size={12} /> ROIC</div>
          <div className={`text-lg font-bold ${tone(data.roic, 12, 6)}`}>{data.roic != null ? `${data.roic.toFixed(1)}%` : 'N/A'}</div>
        </div>

        {/* ROE */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 flex items-center gap-1.5 mb-1"><Percent size={12} /> ROE</div>
          <div className={`text-lg font-bold ${tone(data.roe, 15, 8)}`}>{data.roe != null ? `${data.roe.toFixed(1)}%` : 'N/A'}</div>
        </div>

        {/* ROA */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 flex items-center gap-1.5 mb-1"><Percent size={12} /> ROA</div>
          <div className={`text-lg font-bold ${tone(data.roa, 5, 2)}`}>{data.roa != null ? `${data.roa.toFixed(1)}%` : 'N/A'}</div>
        </div>

        {/* FCF conversion */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 flex items-center gap-1.5 mb-1"><Coins size={12} /> FCF Conversion</div>
          <div className={`text-lg font-bold ${tone(data.fcf_conversion != null ? data.fcf_conversion * 100 : null, 70, 40)}`}>
            {data.fcf_conversion != null ? `${(data.fcf_conversion * 100).toFixed(0)}%` : 'N/A'}
          </div>
          <div className="text-[10px] text-base-content/50 mt-1">FCF ÷ net income</div>
        </div>

        {/* Gross margin */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 flex items-center gap-1.5 mb-1"><Percent size={12} /> Gross Margin</div>
          <div className={`text-lg font-bold ${tone(data.gross_margin, 50, 20)}`}>{data.gross_margin != null ? `${data.gross_margin.toFixed(1)}%` : 'N/A'}</div>
        </div>
      </div>
    </div>
  );
}
