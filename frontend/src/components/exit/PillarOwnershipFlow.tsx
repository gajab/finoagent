import React from 'react';
import { Users, TrendingDown, TrendingUp, Activity, ArrowUpRight, ArrowDownRight } from 'lucide-react';
import type { ExitOwnershipFlowData } from '../../types';

interface Props {
  data: ExitOwnershipFlowData;
}

function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return 'N/A';
  return `${v.toFixed(digits)}%`;
}

export function PillarOwnershipFlow({ data }: Props) {
  const shortHot = (data.short_pct_float ?? 0) >= 10;
  const shortRising = (data.short_change_pct ?? 0) > 2;
  const insiderBuying = data.insider_signal?.toLowerCase().includes('buy');
  const insiderSelling = data.insider_signal?.toLowerCase().includes('sell');

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold flex items-center gap-2">
          <Activity size={16} className="text-primary" /> Short Interest & Insider Flow
        </h4>
        {data.insider_signal && data.insider_signal !== 'N/A' && (
          <span className={`badge ${insiderBuying ? 'badge-success' : insiderSelling ? 'badge-error' : 'badge-ghost'}`}>
            Insider: {data.insider_signal}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {/* Short % of float */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 mb-1">Short % of Float</div>
          <div className={`text-lg font-bold ${shortHot ? 'text-error' : ''}`}>{fmtPct(data.short_pct_float)}</div>
          <div className="h-1.5 w-full bg-base-100 rounded-full mt-2 overflow-hidden">
            <div className={`h-full ${shortHot ? 'bg-error' : (data.short_pct_float ?? 0) >= 5 ? 'bg-warning' : 'bg-success'}`}
              style={{ width: `${Math.min(100, (data.short_pct_float ?? 0) * 4)}%` }} />
          </div>
        </div>

        {/* Days to cover */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 mb-1">Days to Cover</div>
          <div className="text-lg font-bold">{data.days_to_cover != null ? `${data.days_to_cover.toFixed(1)}d` : 'N/A'}</div>
          <div className="text-[10px] text-base-content/50 mt-2">Short interest ÷ avg volume</div>
        </div>

        {/* Short change MoM */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 mb-1">Short Δ (MoM)</div>
          <div className={`text-lg font-bold flex items-center gap-1 ${shortRising ? 'text-error' : (data.short_change_pct ?? 0) < -2 ? 'text-success' : ''}`}>
            {data.short_change_pct != null && (data.short_change_pct >= 0 ? <ArrowUpRight size={16} /> : <ArrowDownRight size={16} />)}
            {data.short_change_pct != null ? `${data.short_change_pct >= 0 ? '+' : ''}${data.short_change_pct.toFixed(1)}%` : 'N/A'}
          </div>
          <div className="text-[10px] text-base-content/50 mt-2">{shortRising ? 'Bears adding' : (data.short_change_pct ?? 0) < -2 ? 'Shorts covering' : 'Stable'}</div>
        </div>

        {/* Institutional ownership */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 flex items-center gap-1.5 mb-1"><Users size={12} /> Institutional</div>
          <div className="text-lg font-bold">{fmtPct(data.institution_pct)}</div>
          <div className="h-1.5 w-full bg-base-100 rounded-full mt-2 overflow-hidden">
            <div className="h-full bg-info" style={{ width: `${Math.min(100, data.institution_pct ?? 0)}%` }} />
          </div>
        </div>

        {/* Insider ownership */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 flex items-center gap-1.5 mb-1"><Users size={12} /> Insider Held</div>
          <div className="text-lg font-bold">{fmtPct(data.insider_pct)}</div>
        </div>

        {/* Insider net activity */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 mb-1">Insider Net (6m)</div>
          <div className={`text-lg font-bold flex items-center gap-1 ${insiderBuying ? 'text-success' : insiderSelling ? 'text-error' : ''}`}>
            {data.insider_net_pct != null && (insiderBuying ? <TrendingUp size={16} /> : insiderSelling ? <TrendingDown size={16} /> : null)}
            {data.insider_net_pct != null ? `${data.insider_net_pct >= 0 ? '+' : ''}${data.insider_net_pct.toFixed(2)}%` : (data.insider_signal || 'N/A')}
          </div>
          <div className="text-[10px] text-base-content/50 mt-2">Net insider share change</div>
        </div>
      </div>
    </div>
  );
}
