import React from 'react';
import { Compass } from 'lucide-react';
import type { SetupContext } from '../types';
import { DirectionBadge, StatTile, InfoTip } from './taUi';

const regimeText = (r?: string | null) =>
  r === 'trending' ? 'Trending' : r === 'mean_reverting' ? 'Mean-Reverting' : 'Transitional';

const trendIco = (t?: string | null) => t === 'up' ? '▲' : t === 'down' ? '▼' : '◦';
const trendTone = (t?: string | null) => t === 'up' ? 'text-success' : t === 'down' ? 'text-error' : 'text-base-content/40';

function readout(dir: string, regime: string | null | undefined, ticker: string): string {
  const t = ticker || 'This stock';
  if (regime === 'trending') {
    if (dir === 'bullish') return `${t} is trending up. The higher-probability play is buying pullbacks into support — not chasing green candles.`;
    if (dir === 'bearish') return `${t} is trending down. Selling bounces into resistance is favored over trying to catch the bottom.`;
    return `${t} is trending, but the direction is mixed across timeframes — wait for a clean break before committing.`;
  }
  if (regime === 'mean_reverting') return `${t} is range-bound. Fade the extremes back toward the mean and sell premium — don't chase breakouts here.`;
  if (dir === 'bullish') return `${t} has a bullish lean but no strong regime yet — favor buying into support and keep size modest.`;
  if (dir === 'bearish') return `${t} has a bearish lean but no strong regime yet — favor selling into resistance and keep size modest.`;
  return `${t} has no clear edge right now — trade the range or stand aside until structure resolves.`;
}

export default function MarketContextHero({ context, spot, ticker }: { context: SetupContext; spot: number | null; ticker: string }) {
  const bias = context?.bias ?? { direction: 'neutral', strength: 'weak', score: 0, rationale: '', confirmations: [], regime: '' };
  const regime = context?.regime ?? { label: null, confidence: null, hurst: null, note: null, favored: [] };
  const em = context?.expected_move;
  const dealer = context?.dealer;
  const ta = context?.trend_alignment;
  const long = dealer?.gamma === 'long';

  // expected-move meter geometry (lower — spot — upper)
  let spotPct = 50;
  if (em && em.lower != null && em.upper != null && spot && em.upper > em.lower) {
    spotPct = Math.max(4, Math.min(96, ((spot - em.lower) / (em.upper - em.lower)) * 100));
  }

  return (
    <div className="glass-card p-4 sm:p-5">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <DirectionBadge direction={bias.direction} strength={bias.strength} size="lg" />
          <div>
            <div className="text-[11px] uppercase tracking-wider text-base-content/45 flex items-center gap-1">
              <Compass className="w-3 h-3" /> Market context
            </div>
            <div className="text-sm font-semibold text-base-content/80">{regimeText(regime.label)} · {bias.strength} bias</div>
          </div>
        </div>
        <div className="text-[10px] text-base-content/40 text-right">
          {spot != null && <div className="text-base font-bold text-base-content tabular-nums">${spot.toFixed(2)}</div>}
          <div>live read</div>
        </div>
      </div>

      <p className="text-sm text-base-content/70 leading-snug mt-3">{readout(bias.direction, regime.label, ticker)}</p>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 mt-3">
        <StatTile
          label="Regime" tip="How the stock is moving: a Trend keeps going one way (trade with it); Mean-Reverting bounces around a middle (fade the edges)."
          value={regimeText(regime.label)}
          tone={regime.label === 'trending' ? 'success' : regime.label === 'mean_reverting' ? 'warning' : undefined}
          sub={regime.hurst != null ? `Hurst ${regime.hurst} · ${regime.confidence ?? ''}` : regime.confidence ?? undefined}
        />
        <StatTile
          label="Expected move" tip="How far the options market expects the stock to move over the next ~30 days (±1 standard deviation). Good for sizing targets and stops."
          value={em?.pct_30d != null ? `±${em.pct_30d}%` : '—'}
          sub={em && em.lower != null && em.upper != null ? (
            <span className="block mt-1">
              <span className="relative block h-1.5 rounded-full bg-base-content/10">
                <span className="absolute top-1/2 -translate-y-1/2 w-2 h-2 rounded-full bg-primary ring-2 ring-base-100" style={{ left: `${spotPct}%`, transform: 'translate(-50%,-50%)' }} />
              </span>
              <span className="flex justify-between text-[9px] text-base-content/40 mt-0.5 tabular-nums"><span>${em.lower}</span><span>${em.upper}</span></span>
            </span>
          ) : (em?.iv != null ? `IV ${em.iv}%` : undefined)}
        />
        <StatTile
          label="Dealer flow" tip="Options dealers' hedging. 'Dampened' = they push against moves (calmer, range-y). 'Amplified' = they chase moves (bigger swings, trendier)."
          value={dealer?.gamma ? (long ? 'Dampened' : 'Amplified') : '—'}
          tone={dealer?.gamma ? (long ? 'success' : 'error') : undefined}
          sub={dealer?.flip != null ? `flip $${dealer.flip}` : undefined}
        />
        <StatTile
          label="Timeframes" tip="Trend direction on the Daily, 4-hour and 1-hour charts. When all three agree, the move is higher-conviction."
          value={ta ? (
            <span className="flex items-center gap-2">
              {(['daily', 'h4', 'h1'] as const).map(k => (
                <span key={k} className="flex flex-col items-center leading-none">
                  <span className={`text-base ${trendTone(ta[k])}`}>{trendIco(ta[k])}</span>
                  <span className="text-[9px] text-base-content/40 mt-0.5">{k === 'daily' ? 'D' : k === 'h4' ? '4H' : '1H'}</span>
                </span>
              ))}
            </span>
          ) : '—'}
        />
      </div>
    </div>
  );
}
