import React from 'react';
import { TrendingUp, TrendingDown, BarChart3, Calendar, Percent } from 'lucide-react';
import type { StockData } from '../types';

interface StockHeaderProps {
  data: StockData;
}

export const StockHeader: React.FC<StockHeaderProps> = ({ data }) => {
  const isPositive = data.change >= 0;

  return (
    <div className="glass-card p-5 sm:p-6 animate-fade-in-up">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        {/* Left: Company info + Price */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-6">
          {/* Company Name */}
          <div>
            <h2 className="text-xl sm:text-2xl font-bold tracking-tight text-base-content">
              {data.companyName}
            </h2>
            <span className="text-sm font-medium text-base-content/40">{data.ticker}</span>
          </div>

          {/* Divider */}
          <div className="hidden sm:block w-px h-10 bg-white/[0.06]" />

          {/* Price Block */}
          <div className="flex items-baseline gap-3">
            <span className="text-3xl sm:text-4xl font-black tracking-tighter text-base-content">
              ${data.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
            <span className={`flex items-center gap-1 text-sm font-bold px-2.5 py-1 rounded-lg ${
              isPositive
                ? 'bg-success/15 text-success'
                : 'bg-error/15 text-error'
            }`}>
              {isPositive ? <TrendingUp className="w-3.5 h-3.5" /> : <TrendingDown className="w-3.5 h-3.5" />}
              {isPositive ? '+' : ''}{data.change.toFixed(2)} ({isPositive ? '+' : ''}{data.changePercent.toFixed(2)}%)
            </span>
          </div>
        </div>

        {/* Right: Key Stats */}
        <div className="flex flex-wrap gap-2">
          {data.marketCap && (
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-base-200/60 border border-white/[0.04] text-xs">
              <BarChart3 className="w-3 h-3 text-primary/60" />
              <span className="text-base-content/50">MCap</span>
              <span className="font-semibold">{data.marketCap}</span>
            </div>
          )}
          {data.fiftyTwoWeekLow !== null && data.fiftyTwoWeekHigh !== null && (
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-base-200/60 border border-white/[0.04] text-xs">
              <Calendar className="w-3 h-3 text-info/60" />
              <span className="text-base-content/50">52W</span>
              <span className="font-semibold">${data.fiftyTwoWeekLow.toFixed(0)} – ${data.fiftyTwoWeekHigh.toFixed(0)}</span>
            </div>
          )}
          {(data.dividendYield || 0) > 0 && (
            <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-base-200/60 border border-white/[0.04] text-xs">
              <Percent className="w-3 h-3 text-success/60" />
              <span className="text-base-content/50">Yield</span>
              <span className="font-semibold text-success">{data.dividendYield.toFixed(2)}%</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
