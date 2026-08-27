import React from 'react';
import { Target, Calendar, TrendingUp, TrendingDown, Users, Gauge } from 'lucide-react';
import type { ExitCatalystData } from '../../types';

interface Props {
  data: ExitCatalystData;
}

function recLabel(mean: number | null, key: string | null): { text: string; cls: string } {
  if (key) {
    const k = key.toLowerCase();
    if (k.includes('strong_buy')) return { text: 'Strong Buy', cls: 'badge-success' };
    if (k.includes('buy')) return { text: 'Buy', cls: 'badge-success badge-outline' };
    if (k.includes('hold') || k.includes('neutral')) return { text: 'Hold', cls: 'badge-warning' };
    if (k.includes('sell') || k.includes('under')) return { text: 'Sell', cls: 'badge-error' };
  }
  if (mean == null) return { text: 'N/A', cls: 'badge-ghost' };
  if (mean <= 1.5) return { text: 'Strong Buy', cls: 'badge-success' };
  if (mean <= 2.5) return { text: 'Buy', cls: 'badge-success badge-outline' };
  if (mean <= 3.5) return { text: 'Hold', cls: 'badge-warning' };
  return { text: 'Sell', cls: 'badge-error' };
}

function revisionBadge(trend: string | null): { text: string; cls: string; up: boolean } | null {
  if (!trend) return null;
  const raising = trend.includes('raising');
  const cutting = trend.includes('cutting');
  if (raising) return { text: trend === 'raising' ? 'Estimates Raising' : 'Estimates Ticking Up', cls: 'badge-success', up: true };
  if (cutting) return { text: trend === 'cutting' ? 'Estimates Cutting' : 'Estimates Ticking Down', cls: 'badge-error', up: false };
  return { text: 'Estimates Flat', cls: 'badge-ghost', up: true };
}

export function PillarCatalyst({ data }: Props) {
  const rec = recLabel(data.recommendation_mean, data.recommendation_key);
  const rev = revisionBadge(data.eps_revision_trend);
  const upside = data.target_upside_pct;
  const nearEarnings = data.days_to_earnings != null && data.days_to_earnings <= 14;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h4 className="text-sm font-semibold flex items-center gap-2">
          <Gauge size={16} className="text-primary" /> Analyst Consensus & Revision Momentum
        </h4>
        <div className="flex items-center gap-2">
          <span className={`badge ${rec.cls}`}>{rec.text}</span>
          {rev && (
            <span className={`badge ${rev.cls} gap-1`}>
              {rev.up ? <TrendingUp size={11} /> : <TrendingDown size={11} />}{rev.text}
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {/* Consensus score */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 mb-1">Consensus (1=Buy · 5=Sell)</div>
          <div className="text-lg font-bold">{data.recommendation_mean != null ? data.recommendation_mean.toFixed(2) : 'N/A'}</div>
          {data.num_analysts != null && (
            <div className="text-[10px] text-base-content/50 mt-2 flex items-center gap-1"><Users size={10} /> {data.num_analysts} analysts</div>
          )}
        </div>

        {/* Target upside */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 flex items-center gap-1.5 mb-1"><Target size={12} /> Mean Target Upside</div>
          <div className={`text-lg font-bold ${upside != null ? (upside >= 0 ? 'text-success' : 'text-error') : ''}`}>
            {upside != null ? `${upside >= 0 ? '+' : ''}${upside.toFixed(1)}%` : 'N/A'}
          </div>
        </div>

        {/* EPS revision net */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 mb-1">EPS Revisions (30d net)</div>
          <div className={`text-lg font-bold ${data.eps_revision_net != null ? (data.eps_revision_net > 0 ? 'text-success' : data.eps_revision_net < 0 ? 'text-error' : '') : ''}`}>
            {data.eps_revision_net != null ? `${data.eps_revision_net > 0 ? '+' : ''}${data.eps_revision_net}` : 'N/A'}
          </div>
          <div className="text-[10px] text-base-content/50 mt-2">Up minus down revisions</div>
        </div>

        {/* Earnings growth */}
        <div className="bg-base-200 rounded-lg p-3">
          <div className="text-xs text-base-content/60 mb-1">Earnings Growth (YoY qtr)</div>
          <div className={`text-lg font-bold ${data.earnings_qtr_growth_pct != null ? (data.earnings_qtr_growth_pct >= 0 ? 'text-success' : 'text-error') : ''}`}>
            {data.earnings_qtr_growth_pct != null ? `${data.earnings_qtr_growth_pct >= 0 ? '+' : ''}${data.earnings_qtr_growth_pct.toFixed(1)}%` : 'N/A'}
          </div>
        </div>

        {/* Days to earnings */}
        <div className={`rounded-lg p-3 ${nearEarnings ? 'bg-warning/10 border border-warning/20' : 'bg-base-200'}`}>
          <div className="text-xs text-base-content/60 flex items-center gap-1.5 mb-1"><Calendar size={12} /> Next Earnings</div>
          <div className={`text-lg font-bold ${nearEarnings ? 'text-warning' : ''}`}>
            {data.days_to_earnings != null ? `${data.days_to_earnings}d` : 'N/A'}
          </div>
          {nearEarnings && <div className="text-[10px] text-warning/80 mt-2">Event risk imminent</div>}
        </div>
      </div>
    </div>
  );
}
