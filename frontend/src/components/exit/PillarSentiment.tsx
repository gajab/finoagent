import React from 'react';
import { Users, Target, Activity, MessageCircle } from 'lucide-react';
import type { ExitSentimentData } from '../../types';

interface Props {
  data: ExitSentimentData;
}

function fmtPct(val: number | null | undefined): string {
  if (val === null || val === undefined) return 'N/A';
  return `${val.toFixed(1)}%`;
}

function getProgressColor(val: number | null, thresholdLow: number, thresholdHigh: number, invert = false): string {
  if (val === null) return 'bg-base-content/20';
  if (invert) {
    if (val < thresholdLow) return 'bg-success';
    if (val < thresholdHigh) return 'bg-warning';
    return 'bg-error';
  } else {
    if (val < thresholdLow) return 'bg-error';
    if (val < thresholdHigh) return 'bg-warning';
    return 'bg-success';
  }
}

export function PillarSentiment({ data }: Props) {
  // Label colors
  let labelColor = 'badge-ghost';
  if (data.sentiment_label.includes('Bullish')) labelColor = 'badge-success';
  else if (data.sentiment_label.includes('Bearish')) labelColor = 'badge-error';
  else if (data.sentiment_label === 'Neutral') labelColor = 'badge-warning';

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold flex items-center gap-2">
          <Activity size={16} className="text-primary" /> Market Sentiment
        </h4>
        <span className={`badge ${labelColor}`}>{data.sentiment_label}</span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        {/* Short Interest */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 flex items-center gap-1.5 mb-1">
            <Activity size={12} /> Short % of Float
          </div>
          <div className="text-lg font-bold">
            {fmtPct(data.short_pct_of_float)}
          </div>
          <div className="h-1.5 w-full bg-base-100 rounded-full mt-2 overflow-hidden">
            <div 
              className={`h-full ${getProgressColor(data.short_pct_of_float, 5, 15, true)}`}
              style={{ width: `${Math.min(100, (data.short_pct_of_float || 0) * 3)}%` }}
            />
          </div>
          {data.short_ratio != null && (
            <div className="text-[10px] text-base-content/50 mt-1">
              Short Ratio: {data.short_ratio.toFixed(1)} days to cover
            </div>
          )}
        </div>

        {/* Institutional Ownership */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 flex items-center gap-1.5 mb-1">
            <Users size={12} /> Institutional Own.
          </div>
          <div className="text-lg font-bold">
            {fmtPct(data.institutional_ownership_pct)}
          </div>
          <div className="h-1.5 w-full bg-base-100 rounded-full mt-2 overflow-hidden">
            <div 
              className={`h-full ${getProgressColor(data.institutional_ownership_pct, 40, 70, false)}`}
              style={{ width: `${Math.min(100, data.institutional_ownership_pct || 0)}%` }}
            />
          </div>
        </div>

        {/* Insider Ownership */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 flex items-center gap-1.5 mb-1">
            <Users size={12} /> Insider Own.
          </div>
          <div className="text-lg font-bold">
            {fmtPct(data.insider_ownership_pct)}
          </div>
          <div className="h-1.5 w-full bg-base-100 rounded-full mt-2 overflow-hidden">
            <div 
              className={`h-full ${getProgressColor(data.insider_ownership_pct, 2, 5, false)}`}
              style={{ width: `${Math.min(100, (data.insider_ownership_pct || 0) * 10)}%` }}
            />
          </div>
        </div>

        {/* Analyst Consensus */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 flex items-center gap-1.5 mb-1">
            <MessageCircle size={12} /> Analyst Consensus
          </div>
          <div className="text-sm font-bold truncate">
            {data.analyst_consensus || 'N/A'}
          </div>
          {data.target_upside_pct != null && (
            <div className="flex items-center gap-1 mt-2">
              <Target size={12} className="text-base-content/50" />
              <span className={`text-[11px] font-semibold ${data.target_upside_pct >= 0 ? 'text-success' : 'text-error'}`}>
                {data.target_upside_pct >= 0 ? '+' : ''}{data.target_upside_pct.toFixed(1)}% to target
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
