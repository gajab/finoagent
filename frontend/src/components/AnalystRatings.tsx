import React from 'react';
import { BarChart3 } from 'lucide-react';
import type { AnalystRating } from '../types';

interface AnalystRatingsProps {
  ratings: AnalystRating[];
}

const PERIOD_LABELS: Record<string, string> = {
  '0m': 'Current',
  '-1m': '1 Month Ago',
  '-2m': '2 Months Ago',
  '-3m': '3 Months Ago',
};

export const AnalystRatings: React.FC<AnalystRatingsProps> = ({ ratings }) => {
  if (ratings.length === 0) {
    return (
      <div className="glass-card p-5">
        <h3 className="font-bold text-sm flex items-center gap-2 mb-3">
          <BarChart3 className="w-4 h-4 text-secondary" /> Analyst Ratings
        </h3>
        <p className="text-sm text-base-content/40">No analyst ratings available.</p>
      </div>
    );
  }

  const current = ratings[0];
  const total = current.strongBuy + current.buy + current.hold + current.sell + current.strongSell;

  const segments = [
    { label: 'Strong Buy', count: current.strongBuy, color: 'bg-success', textColor: 'text-success' },
    { label: 'Buy', count: current.buy, color: 'bg-success/60', textColor: 'text-success/70' },
    { label: 'Hold', count: current.hold, color: 'bg-warning', textColor: 'text-warning' },
    { label: 'Sell', count: current.sell, color: 'bg-error/60', textColor: 'text-error/70' },
    { label: 'Strong Sell', count: current.strongSell, color: 'bg-error', textColor: 'text-error' },
  ];

  const score = total > 0
    ? (current.strongBuy * 5 + current.buy * 4 + current.hold * 3 + current.sell * 2 + current.strongSell * 1) / total
    : 0;
  let consensus = 'Hold';
  if (score >= 4.2) consensus = 'Strong Buy';
  else if (score >= 3.5) consensus = 'Buy';
  else if (score >= 2.5) consensus = 'Hold';
  else if (score >= 1.8) consensus = 'Sell';
  else consensus = 'Strong Sell';

  const consensusBadge = consensus.includes('Buy')
    ? 'bg-success/15 text-success border-success/20'
    : consensus === 'Hold'
    ? 'bg-warning/15 text-warning border-warning/20'
    : 'bg-error/15 text-error border-error/20';

  return (
    <div className="glass-card p-5">
      <h3 className="font-bold text-sm flex items-center gap-2 mb-4">
        <BarChart3 className="w-4 h-4 text-secondary" /> Analyst Ratings
      </h3>

      {/* Consensus summary */}
      <div className="flex items-center gap-3 mb-4">
        <span className={`text-xs font-bold px-3 py-1.5 rounded-lg border ${consensusBadge}`}>{consensus}</span>
        <span className="text-xs text-base-content/40">
          {total} analyst{total !== 1 ? 's' : ''} · Score: {score.toFixed(1)}/5.0
        </span>
      </div>

      {/* Visual bar */}
      <div className="flex w-full h-5 rounded-xl overflow-hidden mb-4 bg-base-300/30">
        {segments.map((seg) =>
          seg.count > 0 ? (
            <div
              key={seg.label}
              className={`${seg.color} flex items-center justify-center text-[10px] font-bold transition-all duration-500`}
              style={{ width: `${(seg.count / total) * 100}%` }}
              title={`${seg.label}: ${seg.count}`}
            >
              {seg.count > 0 && seg.count}
            </div>
          ) : null
        )}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3 mb-4">
        {segments.map((seg) => (
          <div key={seg.label} className="flex items-center gap-1.5 text-xs">
            <span className={`w-2.5 h-2.5 rounded ${seg.color}`}></span>
            <span className="text-base-content/50">{seg.label}: <strong className="text-base-content/80">{seg.count}</strong></span>
          </div>
        ))}
      </div>

      {/* Historical table */}
      <div className="overflow-x-auto">
        <table className="table-pro text-xs">
          <thead>
            <tr>
              <th className="!py-2 !px-3">Period</th>
              <th className="!py-2 !px-3 text-success">Strong Buy</th>
              <th className="!py-2 !px-3 text-success/70">Buy</th>
              <th className="!py-2 !px-3 text-warning">Hold</th>
              <th className="!py-2 !px-3 text-error/70">Sell</th>
              <th className="!py-2 !px-3 text-error">Strong Sell</th>
            </tr>
          </thead>
          <tbody>
            {ratings.map((r, i) => (
              <tr key={i}>
                <td className="font-medium !px-3 !py-2">{PERIOD_LABELS[r.period] || r.period}</td>
                <td className="!px-3 !py-2 tabular-nums">{r.strongBuy}</td>
                <td className="!px-3 !py-2 tabular-nums">{r.buy}</td>
                <td className="!px-3 !py-2 tabular-nums">{r.hold}</td>
                <td className="!px-3 !py-2 tabular-nums">{r.sell}</td>
                <td className="!px-3 !py-2 tabular-nums">{r.strongSell}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
