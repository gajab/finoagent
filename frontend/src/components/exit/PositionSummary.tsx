import React from 'react';
import { DollarSign, Calendar, TrendingUp, TrendingDown, Scale, Clock } from 'lucide-react';
import type { ExitPosition } from '../../types';

interface Props {
  position: ExitPosition;
  ticker: string;
}

function fmt(val: number | null | undefined): string {
  if (val === null || val === undefined) return 'N/A';
  const abs = Math.abs(val);
  const sign = val < 0 ? '-' : '';
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(2)}`;
}

export function PositionSummary({ position, ticker }: Props) {
  const gainLoss = position.unrealized_gain_loss;
  const gainPct = position.unrealized_gain_loss_pct;
  const isGain = gainLoss >= 0;

  const cards = [
    {
      label: 'Current Price',
      value: `$${position.current_price.toFixed(2)}`,
      icon: <DollarSign className="w-3.5 h-3.5" />,
      color: 'text-primary',
    },
    {
      label: 'Market Value',
      value: fmt(position.market_value),
      icon: <Scale className="w-3.5 h-3.5" />,
      color: 'text-info',
    },
    {
      label: 'Total Cost',
      value: fmt(position.total_cost),
      icon: <DollarSign className="w-3.5 h-3.5" />,
      color: 'text-base-content/70',
    },
    {
      label: 'Unrealized P&L',
      value: `${isGain ? '+' : ''}${fmt(gainLoss)} (${isGain ? '+' : ''}${gainPct.toFixed(1)}%)`,
      icon: isGain ? <TrendingUp className="w-3.5 h-3.5" /> : <TrendingDown className="w-3.5 h-3.5" />,
      color: isGain ? 'text-success' : 'text-error',
    },
    {
      label: 'Holding Period',
      value: `${position.holding_period_days}d`,
      sub: position.tax_type,
      icon: <Clock className="w-3.5 h-3.5" />,
      color: position.tax_type === 'Long-Term' ? 'text-success' : 'text-warning',
    },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3">
      {cards.map((c, i) => (
        <div key={i} className="metric-card text-left">
          <div className="flex items-center gap-1.5 text-[10px] font-medium text-base-content/40 uppercase tracking-wider mb-1.5">
            {c.icon}
            {c.label}
          </div>
          <span className={`text-lg font-black tabular-nums ${c.color}`}>{c.value}</span>
          {c.sub && <span className="text-[10px] text-base-content/40 mt-0.5 block">{c.sub}</span>}
        </div>
      ))}
    </div>
  );
}
