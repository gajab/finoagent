import React, { useState } from 'react';
import {
  Layers, Box, ArrowLeftRight, ChevronDown, ChevronUp,
} from 'lucide-react';
import { BoxStrategy } from './BoxStrategy';
import { LongShortStrategy } from './LongShortStrategy';

interface Props {
  ticker?: string;
}

type Strategy = 'box' | 'longshort';

const STRATEGIES: { id: Strategy; label: string; icon: React.ReactNode; description: string }[] = [
  {
    id: 'box',
    label: 'Box Spread',
    icon: <Box className="w-4 h-4" />,
    description: 'Synthetic lending/borrowing via 4-leg options strategy. Earn or pay interest with defined risk.',
  },
  {
    id: 'longshort',
    label: 'Long/Short Equity',
    icon: <ArrowLeftRight className="w-4 h-4" />,
    description: 'Hedge market risk by pairing long positions with sector ETF shorts or direct pair trades.',
  },
];

export function Strategies({ ticker }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [activeStrategy, setActiveStrategy] = useState<Strategy | null>(null);

  return (
    <div className="glass-card">
      <div className="p-5">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h2 className="font-bold text-sm flex items-center gap-2">
            <Layers className="w-5 h-5 text-secondary" />
            Advanced Strategies
          </h2>
          <button
            className="btn btn-ghost btn-xs gap-1"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? 'Collapse' : 'Explore'}
            {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>
        </div>

        {/* Collapsed: Strategy Cards Preview */}
        {!expanded && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
            {STRATEGIES.map((s) => (
              <button
                key={s.id}
                className="bg-base-200/40 hover:bg-base-200/60 rounded-xl p-4 text-left transition-all group border border-white/[0.03]"
                onClick={() => { setExpanded(true); setActiveStrategy(s.id); }}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-secondary">{s.icon}</span>
                  <span className="font-semibold text-sm group-hover:text-secondary transition-colors">
                    {s.label}
                  </span>
                </div>
                <p className="text-xs text-base-content/50">{s.description}</p>
              </button>
            ))}
          </div>
        )}

        {/* Expanded: Active Strategy */}
        {expanded && (
          <div className="mt-2 space-y-3">
            {/* Strategy Tabs */}
            <div className="tabs tabs-boxed bg-base-200/40 p-1 w-fit border border-white/[0.03] rounded-xl">
              {STRATEGIES.map((s) => (
                <button
                  key={s.id}
                  className={`tab tab-sm gap-1 ${activeStrategy === s.id ? 'tab-active' : ''}`}
                  onClick={() => setActiveStrategy(s.id)}
                >
                  {s.icon}
                  {s.label}
                </button>
              ))}
            </div>

            {/* Strategy Content */}
            {activeStrategy === 'box' && <BoxStrategy />}
            {activeStrategy === 'longshort' && <LongShortStrategy ticker={ticker} />}

            {!activeStrategy && (
              <div className="text-center py-8 text-base-content/50 text-sm">
                Select a strategy above to get started.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
