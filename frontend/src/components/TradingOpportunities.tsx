import React, { useState, useMemo } from 'react';
import {
  DollarSign,
  Shield,
  TrendingUp,
  ArrowUpDown,
  ChevronDown,
  ChevronUp,
  Info,
} from 'lucide-react';
import { OptionsData, OptionTrade } from '../types';

interface Props {
  options: OptionsData;
  currentPrice: number;
}

type SortKey = 'annualizedReturn' | 'probOTM' | 'strike' | 'dte' | 'mid' | 'otmPct';
type SortDir = 'asc' | 'desc';

const SortHeader: React.FC<{
  label: string;
  sortKey: SortKey;
  currentSort: SortKey;
  dir: SortDir;
  onSort: (key: SortKey) => void;
}> = ({ label, sortKey, currentSort, dir, onSort }) => (
  <th
    className="cursor-pointer hover:bg-base-100 select-none whitespace-nowrap"
    onClick={() => onSort(sortKey)}
  >
    <div className="flex items-center gap-1">
      {label}
      {currentSort === sortKey ? (
        dir === 'desc' ? (
          <ChevronDown size={12} />
        ) : (
          <ChevronUp size={12} />
        )
      ) : (
        <ArrowUpDown size={10} className="opacity-30" />
      )}
    </div>
  </th>
);

const TradeTable: React.FC<{
  trades: OptionTrade[];
  type: 'csp' | 'cc';
  price: number;
}> = ({ trades, type, price }) => {
  const [sortKey, setSortKey] = useState<SortKey>('annualizedReturn');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const sorted = useMemo(() => {
    const s = [...trades];
    s.sort((a, b) => {
      const va = (a as any)[sortKey] ?? 0;
      const vb = (b as any)[sortKey] ?? 0;
      return sortDir === 'desc' ? vb - va : va - vb;
    });
    return s;
  }, [trades, sortKey, sortDir]);

  if (trades.length === 0) {
    return (
      <div className="alert alert-warning py-3">
        <Info size={14} />
        <span>
          No {type === 'csp' ? 'cash secured put' : 'covered call'} opportunities found meeting the
          criteria (≥88% prob OTM, ≥4% annualized).
        </span>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto max-h-[400px]">
      <table className="table table-xs table-pro table-pin-rows">
        <thead>
          <tr className="text-xs">
            <SortHeader
              label="Exp"
              sortKey="dte"
              currentSort={sortKey}
              dir={sortDir}
              onSort={handleSort}
            />
            <th>DTE</th>
            <SortHeader
              label="Strike"
              sortKey="strike"
              currentSort={sortKey}
              dir={sortDir}
              onSort={handleSort}
            />
            <SortHeader
              label="OTM %"
              sortKey="otmPct"
              currentSort={sortKey}
              dir={sortDir}
              onSort={handleSort}
            />
            <SortHeader
              label="Mid"
              sortKey="mid"
              currentSort={sortKey}
              dir={sortDir}
              onSort={handleSort}
            />
            <th>Bid / Ask</th>
            <SortHeader
              label="Ann. %"
              sortKey="annualizedReturn"
              currentSort={sortKey}
              dir={sortDir}
              onSort={handleSort}
            />
            {type === 'cc' && <th>Total If Called</th>}
            <SortHeader
              label="Prob OTM"
              sortKey="probOTM"
              currentSort={sortKey}
              dir={sortDir}
              onSort={handleSort}
            />
            <th>Delta</th>
            <th>IV</th>
            <th>OI</th>
            <th>Vol</th>
            <th>{type === 'csp' ? 'Capital Req.' : 'Premium/100'}</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((t, i) => {
            const returnColor =
              t.annualizedReturn >= 8
                ? 'text-success font-bold'
                : t.annualizedReturn >= 6
                  ? 'text-success'
                  : t.annualizedReturn >= 4
                    ? 'text-warning'
                    : '';
            const probColor =
              t.probOTM >= 95
                ? 'text-success font-bold'
                : t.probOTM >= 92
                  ? 'text-success'
                  : t.probOTM >= 88
                    ? 'text-warning'
                    : '';

            return (
              <tr key={`${t.expiration}-${t.strike}-${i}`} className="hover">
                <td className="whitespace-nowrap">{t.expiration}</td>
                <td>{t.dte}d</td>
                <td className="font-semibold">${t.strike.toFixed(1)}</td>
                <td>{t.otmPct}%</td>
                <td className="font-semibold">${t.mid.toFixed(2)}</td>
                <td className="text-xs">
                  ${t.bid.toFixed(2)} / ${t.ask.toFixed(2)}
                </td>
                <td className={returnColor}>{t.annualizedReturn.toFixed(1)}%</td>
                {type === 'cc' && (
                  <td className="text-success">{t.totalReturnIfCalled?.toFixed(1)}%</td>
                )}
                <td className={probColor}>{t.probOTM.toFixed(1)}%</td>
                <td>{Math.abs(t.delta).toFixed(3)}</td>
                <td>{t.iv.toFixed(1)}%</td>
                <td>{t.openInterest.toLocaleString()}</td>
                <td>{t.volume.toLocaleString()}</td>
                <td>
                  ${(type === 'csp' ? t.capitalRequired : t.premiumPer100)?.toLocaleString()}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

export const TradingOpportunities: React.FC<Props> = ({ options, currentPrice }) => {
  const [activeTab, setActiveTab] = useState<'csp' | 'cc'>('csp');

  if (!options.available) return null;

  const { cashSecuredPuts, coveredCalls, criteria } = options;

  return (
    <div className="glass-card">
      <div className="p-5">
        <h3 className="font-bold text-sm flex items-center gap-2">
          <DollarSign size={20} /> Trading Opportunities @ ${currentPrice}
        </h3>

        {/* Criteria banner */}
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="badge badge-primary badge-sm">≥{criteria.minProbOTM}% Prob OTM</span>
          <span className="badge badge-primary badge-sm">
            ≥{criteria.minAnnualizedReturn}% Annualized
          </span>
          <span className="badge badge-ghost badge-sm">
            {criteria.minDTE}–{criteria.maxDTE} DTE
          </span>
        </div>

        {/* Tabs */}
        <div role="tablist" className="tabs tabs-bordered mt-2">
          <button
            role="tab"
            className={`tab gap-1 ${activeTab === 'csp' ? 'tab-active' : ''}`}
            onClick={() => setActiveTab('csp')}
          >
            <Shield size={14} />
            Cash Secured Puts ({cashSecuredPuts.length})
          </button>
          <button
            role="tab"
            className={`tab gap-1 ${activeTab === 'cc' ? 'tab-active' : ''}`}
            onClick={() => setActiveTab('cc')}
          >
            <TrendingUp size={14} />
            Covered Calls ({coveredCalls.length})
          </button>
        </div>

        {/* Description */}
        {activeTab === 'csp' && (
          <div className="text-xs text-base-content/60 mt-1">
            <strong>Cash Secured Puts:</strong> Sell OTM puts to collect premium. If expired
            worthless, you keep the premium as income. Capital required = strike × 100. Annualized
            return shown assumes the option expires worthless.
          </div>
        )}
        {activeTab === 'cc' && (
          <div className="text-xs text-base-content/60 mt-1">
            <strong>Covered Calls:</strong> Sell OTM calls against shares you own to collect
            premium. If expired worthless, you keep shares + premium. "Total If Called" includes
            premium + upside to strike, annualized. Annualized return shown is premium-only if
            expired worthless.
          </div>
        )}

        {/* Table */}
        <div className="mt-2">
          {activeTab === 'csp' ? (
            <TradeTable trades={cashSecuredPuts} type="csp" price={currentPrice} />
          ) : (
            <TradeTable trades={coveredCalls} type="cc" price={currentPrice} />
          )}
        </div>
      </div>
    </div>
  );
};
