import React from 'react';
import type { ExitValuationData } from '../../types';

interface Props {
  data: ExitValuationData;
  currentPrice: number;
}

function fmtPct(val: number | null | undefined): string {
  if (val === null || val === undefined) return 'N/A';
  return `${val > 0 ? '+' : ''}${val.toFixed(1)}%`;
}

function MetricBox({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-base-200 rounded-lg p-2.5">
      <div className="text-xs text-base-content/60">{label}</div>
      <div className={`text-sm font-bold mt-1 ${color || ''}`}>{value}</div>
    </div>
  );
}

export function PillarValuation({ data, currentPrice }: Props) {
  const peColor = data.trailing_pe != null
    ? data.trailing_pe > 40 ? 'text-error' : data.trailing_pe > 25 ? 'text-warning' : 'text-success'
    : '';

  const pegColor = data.peg_ratio != null
    ? data.peg_ratio > 2 ? 'text-error' : data.peg_ratio > 1.5 ? 'text-warning' : 'text-success'
    : '';

  const fiftyTwoRange = data.fifty_two_week_high != null && data.fifty_two_week_low != null;
  const rangePct = fiftyTwoRange ? data.price_vs_52w_pct ?? 0 : 0;

  return (
    <div className="space-y-3">
      {/* Valuation Multiples */}
      <div className="grid grid-cols-3 md:grid-cols-6 gap-3">
        <MetricBox label="Trailing P/E" value={data.trailing_pe != null ? data.trailing_pe.toFixed(1) : 'N/A'} color={peColor} />
        <MetricBox label="Forward P/E" value={data.forward_pe != null ? data.forward_pe.toFixed(1) : 'N/A'} />
        <MetricBox
          label="PE Expansion"
          value={data.pe_expansion != null ? fmtPct(data.pe_expansion) : 'N/A'}
          color={data.pe_expansion != null && data.pe_expansion > 20 ? 'text-error' : ''}
        />
        <MetricBox label="PEG Ratio" value={data.peg_ratio != null ? data.peg_ratio.toFixed(2) : 'N/A'} color={pegColor} />
        <MetricBox label="EV/EBITDA" value={data.ev_to_ebitda != null ? data.ev_to_ebitda.toFixed(1) : 'N/A'} />
        <MetricBox label="P/B" value={data.price_to_book != null ? data.price_to_book.toFixed(2) : 'N/A'} />
      </div>

      {/* 52-Week Range Bar */}
      {fiftyTwoRange && (
        <div className="bg-base-200 rounded-lg p-3">
          <div className="flex justify-between text-xs text-base-content/60 mb-1">
            <span>52W Low: ${data.fifty_two_week_low!.toFixed(2)}</span>
            <span>52W High: ${data.fifty_two_week_high!.toFixed(2)}</span>
          </div>
          <div className="relative w-full h-3 bg-base-300 rounded-full">
            {/* Gradient bar */}
            <div className="absolute inset-0 rounded-full overflow-hidden">
              <div className="w-full h-full bg-gradient-to-r from-success via-warning to-error" />
            </div>
            {/* Position indicator */}
            <div
              className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full border-2 border-primary shadow"
              style={{ left: `${Math.min(Math.max(rangePct, 2), 98)}%` }}
            />
          </div>
          <div className="text-center text-xs mt-1">
            <span className={`font-bold ${rangePct > 90 ? 'text-error' : rangePct > 70 ? 'text-warning' : 'text-success'}`}>
              {rangePct.toFixed(0)}% of 52W range
            </span>
          </div>
        </div>
      )}

      {/* Analyst Targets */}
      {data.num_analysts != null && data.num_analysts > 0 && (
        <div className="bg-base-200 rounded-lg p-3">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-bold">Analyst Consensus ({data.num_analysts} analysts)</span>
            <span className={`badge badge-sm ${data.recommendation === 'buy' || data.recommendation === 'strongBuy' ? 'badge-success' : data.recommendation === 'sell' || data.recommendation === 'strongSell' ? 'badge-error' : 'badge-warning'}`}>
              {data.recommendation || 'N/A'}
            </span>
          </div>
          <div className="grid grid-cols-4 gap-2 text-center">
            <div>
              <div className="text-xs text-base-content/60">Low</div>
              <div className="text-sm font-bold text-error">${data.analyst_target_low?.toFixed(0) || 'N/A'}</div>
            </div>
            <div>
              <div className="text-xs text-base-content/60">Mean</div>
              <div className="text-sm font-bold text-info">${data.analyst_target_mean?.toFixed(0) || 'N/A'}</div>
            </div>
            <div>
              <div className="text-xs text-base-content/60">High</div>
              <div className="text-sm font-bold text-success">${data.analyst_target_high?.toFixed(0) || 'N/A'}</div>
            </div>
            <div>
              <div className="text-xs text-base-content/60">Upside</div>
              <div className={`text-sm font-bold ${(data.target_upside_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                {fmtPct(data.target_upside_pct)}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
