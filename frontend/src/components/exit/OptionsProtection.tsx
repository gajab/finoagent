import React from 'react';
import { Shield, DollarSign, Lock, Unlock } from 'lucide-react';
import type { ExitOptionsProtection } from '../../types';

interface Props {
  data: ExitOptionsProtection;
}

function MetricRow({ label, value, valueClass = '' }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="flex justify-between items-center py-1.5">
      <span className="text-[11px] text-base-content/40">{label}</span>
      <span className={`text-xs font-semibold tabular-nums ${valueClass}`}>{value}</span>
    </div>
  );
}

export function OptionsProtection({ data }: Props) {
  if (!data.available) {
    return (
      <div className="glass-card p-5 text-center">
        <p className="text-sm text-base-content/40">Options data not available for this ticker.</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs text-base-content/40">
        <Shield className="w-3.5 h-3.5" />
        Contracts needed: <span className="font-semibold text-base-content/60">{data.contracts_needed}</span>
        &middot; Current: <span className="font-semibold text-base-content/60">${data.current_price?.toFixed(2)}</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {/* Protective Put */}
        {data.protective_put && (
          <div className="glass-card p-4 !border-error/15 hover:!border-error/25 transition-colors">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-7 h-7 rounded-lg bg-error/15 flex items-center justify-center">
                <Shield className="w-3.5 h-3.5 text-error" />
              </div>
              <span className="font-bold text-sm">Protective Put</span>
            </div>
            <div className="divide-y divide-white/[0.03]">
              <MetricRow label="Strike" value={`$${data.protective_put.strike.toFixed(2)}`} />
              <MetricRow label="Expiration" value={`${data.protective_put.expiration} (${data.protective_put.dte}d)`} />
              <MetricRow label="Cost / contract" value={`$${data.protective_put.mid_price.toFixed(2)}`} />
              <MetricRow label="Total Cost" value={`$${data.protective_put.total_cost.toFixed(0)}`} valueClass="text-error" />
              <MetricRow label="Protection Floor" value={`$${data.protective_put.protection_floor.toFixed(2)}`} valueClass="text-success" />
              <MetricRow label="Max Loss/Share" value={`$${data.protective_put.max_loss_per_share.toFixed(2)}`} />
              <MetricRow label="Ann. Cost" value={`${data.protective_put.annualized_cost_pct.toFixed(1)}%`} />
            </div>
          </div>
        )}

        {/* Zero-Cost Collar */}
        {data.zero_cost_collar && (
          <div className="glass-card p-4 !border-info/15 hover:!border-info/25 transition-colors">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-7 h-7 rounded-lg bg-info/15 flex items-center justify-center">
                <Lock className="w-3.5 h-3.5 text-info" />
              </div>
              <span className="font-bold text-sm">Zero-Cost Collar</span>
            </div>
            <div className="divide-y divide-white/[0.03]">
              <MetricRow label="Put Strike" value={`$${data.zero_cost_collar.put_strike.toFixed(2)}`} />
              <MetricRow label="Call Strike" value={`$${data.zero_cost_collar.call_strike.toFixed(2)}`} />
              <MetricRow label="Expiration" value={`${data.zero_cost_collar.expiration} (${data.zero_cost_collar.dte}d)`} />
              <MetricRow label="Net Credit/Debit" value={`$${data.zero_cost_collar.net_credit_debit.toFixed(2)}`} valueClass={data.zero_cost_collar.net_credit_debit >= 0 ? 'text-success' : 'text-error'} />
              <MetricRow label="Floor" value={`$${data.zero_cost_collar.protection_floor.toFixed(2)}`} valueClass="text-success" />
              <MetricRow label="Cap" value={`$${data.zero_cost_collar.upside_cap.toFixed(2)}`} valueClass="text-warning" />
            </div>
          </div>
        )}

        {/* Covered Call */}
        {data.covered_call && (
          <div className="glass-card p-4 !border-success/15 hover:!border-success/25 transition-colors">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-7 h-7 rounded-lg bg-success/15 flex items-center justify-center">
                <DollarSign className="w-3.5 h-3.5 text-success" />
              </div>
              <span className="font-bold text-sm">Covered Call</span>
            </div>
            <div className="divide-y divide-white/[0.03]">
              <MetricRow label="Strike" value={`$${data.covered_call.strike.toFixed(2)}`} />
              <MetricRow label="Expiration" value={`${data.covered_call.expiration} (${data.covered_call.dte}d)`} />
              <MetricRow label="Premium" value={`$${data.covered_call.mid_price.toFixed(2)}`} />
              <MetricRow label="Total Premium" value={`$${data.covered_call.total_premium.toFixed(0)}`} valueClass="text-success" />
              <MetricRow label="Ann. Return" value={`${data.covered_call.annualized_return_pct.toFixed(1)}%`} valueClass="text-success" />
              <MetricRow label="Upside Cap" value={`${data.covered_call.upside_cap_pct.toFixed(1)}% above`} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
