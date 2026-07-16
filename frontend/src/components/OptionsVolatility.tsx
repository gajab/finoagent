import React from 'react';
import { Activity, BarChart3, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { OptionsData } from '../types';

interface Props {
  options: OptionsData;
}

export const OptionsVolatility: React.FC<Props> = ({ options }) => {
  if (!options.available) {
    return (
      <div className="glass-card">
        <div className="p-5">
          <h3 className="font-bold text-sm flex items-center gap-2">
            <Activity size={20} /> Options &amp; Volatility
          </h3>
          <p className="text-base-content/60">{options.error || 'No options data available.'}</p>
        </div>
      </div>
    );
  }

  const { iv, hv30, hv60, putCallRatio } = options;
  const pcr = putCallRatio.openInterest;

  // Interpret P/C ratio
  let pcrSentiment: string;
  let pcrColor: string;
  let PcrIcon: typeof TrendingUp;
  if (pcr > 1.2) {
    pcrSentiment = 'Bearish — high put activity relative to calls, suggesting hedging or bearish bets';
    pcrColor = 'text-error';
    PcrIcon = TrendingDown;
  } else if (pcr > 0.9) {
    pcrSentiment = 'Neutral — balanced put/call activity';
    pcrColor = 'text-warning';
    PcrIcon = Minus;
  } else if (pcr > 0.5) {
    pcrSentiment = 'Bullish — more call activity than puts, suggesting optimism';
    pcrColor = 'text-success';
    PcrIcon = TrendingUp;
  } else {
    pcrSentiment = 'Very Bullish — significantly more call activity, strong bullish sentiment';
    pcrColor = 'text-success';
    PcrIcon = TrendingUp;
  }

  // IV vs HV comparison
  let ivHvComparison = '';
  if (iv.current && hv30) {
    const diff = iv.current - hv30;
    if (diff > 5) {
      ivHvComparison = `IV is ${diff.toFixed(1)}% above HV30 — options are relatively expensive (good for sellers)`;
    } else if (diff < -5) {
      ivHvComparison = `IV is ${Math.abs(diff).toFixed(1)}% below HV30 — options are relatively cheap (good for buyers)`;
    } else {
      ivHvComparison = 'IV and HV30 are roughly aligned — options are fairly priced';
    }
  }

  const fmtNum = (n: number) => n.toLocaleString();

  return (
    <div className="glass-card">
      <div className="p-5">
        <h3 className="font-bold text-sm flex items-center gap-2">
          <Activity size={20} /> Options &amp; Volatility
        </h3>

        {/* Volatility stats */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
          <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03] text-center">
            <div className="text-xs text-base-content/50 uppercase tracking-wide">Implied Vol</div>
            <div className="text-xl font-bold text-primary mt-1">
              {iv.current != null ? `${iv.current}%` : 'N/A'}
            </div>
          </div>
          <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03] text-center">
            <div className="text-xs text-base-content/50 uppercase tracking-wide">HV 30-Day</div>
            <div className="text-xl font-bold mt-1">
              {hv30 != null ? `${hv30}%` : 'N/A'}
            </div>
          </div>
          <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03] text-center">
            <div className="text-xs text-base-content/50 uppercase tracking-wide">HV 60-Day</div>
            <div className="text-xl font-bold mt-1">
              {hv60 != null ? `${hv60}%` : 'N/A'}
            </div>
          </div>
          <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03] text-center">
            <div className="text-xs text-base-content/50 uppercase tracking-wide">IV Range</div>
            <div className="text-sm font-bold mt-1">
              {iv.low != null ? `${iv.low}%` : '?'} — {iv.high != null ? `${iv.high}%` : '?'}
            </div>
          </div>
        </div>

        {ivHvComparison && (
          <div className="alert alert-info py-2 mt-2">
            <Activity size={14} />
            <span className="text-sm">{ivHvComparison}</span>
          </div>
        )}

        {/* Put/Call Ratio */}
        <div className="mt-4">
          <h4 className="font-semibold text-sm text-base-content/70 mb-2 flex items-center gap-1">
            <BarChart3 size={16} /> Put/Call Ratio
          </h4>
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
              <div className="text-xs text-base-content/50">By Open Interest</div>
              <div className="text-2xl font-bold mt-1">{pcr}</div>
              <div className="text-xs text-base-content/50 mt-1">
                Puts: {fmtNum(putCallRatio.totalPutOI)} | Calls: {fmtNum(putCallRatio.totalCallOI)}
              </div>
            </div>
            <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
              <div className="text-xs text-base-content/50">By Volume</div>
              <div className="text-2xl font-bold mt-1">{putCallRatio.volume}</div>
              <div className="text-xs text-base-content/50 mt-1">
                Puts: {fmtNum(putCallRatio.totalPutVol)} | Calls:{' '}
                {fmtNum(putCallRatio.totalCallVol)}
              </div>
            </div>
          </div>
          <div className={`flex items-center gap-1 mt-2 text-sm ${pcrColor}`}>
            <PcrIcon size={14} />
            {pcrSentiment}
          </div>
        </div>

        {/* Quick stats footer */}
        <div className="flex flex-wrap gap-2 mt-3 text-xs text-base-content/50">
          <span className="badge badge-ghost badge-sm">
            {options.expirationCount} expirations available
          </span>
          <span className="badge badge-ghost badge-sm">Nearest: {options.nearestExpiration}</span>
          <span className="badge badge-ghost badge-sm">Farthest: {options.farthestExpiration}</span>
        </div>
      </div>
    </div>
  );
};
