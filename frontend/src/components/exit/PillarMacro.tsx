import React from 'react';
import type { ExitMacroData } from '../../types';

interface Props {
  data: ExitMacroData;
}

function cyclBadge(cyc: string) {
  if (cyc === 'High') return 'badge-error';
  if (cyc === 'Medium') return 'badge-warning';
  return 'badge-success';
}

function volBadge(regime: string) {
  if (regime === 'High') return 'badge-error';
  if (regime === 'Elevated') return 'badge-warning';
  return 'badge-success';
}

export function PillarMacro({ data }: Props) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {/* Beta */}
        <div className="bg-base-200 rounded-lg p-2.5">
          <div className="text-xs text-base-content/60">Beta</div>
          <div className={`text-lg font-bold mt-1 ${data.beta != null && data.beta > 1.5 ? 'text-error' : data.beta != null && data.beta > 1 ? 'text-warning' : 'text-success'}`}>
            {data.beta != null ? data.beta.toFixed(2) : 'N/A'}
          </div>
          <div className="text-xs text-base-content/50 mt-0.5">{data.beta_interpretation}</div>
        </div>

        {/* Sector Cyclicality */}
        <div className="bg-base-200 rounded-lg p-2.5">
          <div className="text-xs text-base-content/60">Sector Cyclicality</div>
          <div className="mt-1">
            <span className={`badge badge-sm ${cyclBadge(data.sector_cyclicality)}`}>
              {data.sector_cyclicality}
            </span>
          </div>
          <div className="text-xs text-base-content/50 mt-0.5">{data.sector}</div>
        </div>

        {/* Volatility Regime */}
        <div className="bg-base-200 rounded-lg p-2.5">
          <div className="text-xs text-base-content/60">Volatility Regime</div>
          <div className="mt-1">
            <span className={`badge badge-sm ${volBadge(data.vol_regime)}`}>
              {data.vol_regime}
            </span>
          </div>
          <div className="text-xs text-base-content/50 mt-0.5">
            HV30: {data.hv30 != null ? `${data.hv30.toFixed(1)}%` : 'N/A'} · HV60: {data.hv60 != null ? `${data.hv60.toFixed(1)}%` : 'N/A'}
          </div>
        </div>
      </div>

      {/* Dividend vs Rates */}
      <div className="bg-base-200 rounded-lg p-2.5 flex items-center justify-between">
        <div>
          <div className="text-xs text-base-content/60">Dividend Yield</div>
          <div className="text-sm font-bold mt-1">
            {data.dividend_yield.toFixed(2)}%
          </div>
        </div>
        <div className="text-right">
          <div className="text-xs text-base-content/60">Div Yield vs Risk-Free Rate</div>
          <div className={`text-sm font-bold mt-1 ${data.dividend_yield_vs_rates === 'Below' ? 'text-warning' : 'text-success'}`}>
            {data.dividend_yield_vs_rates}
          </div>
        </div>
      </div>
    </div>
  );
}
