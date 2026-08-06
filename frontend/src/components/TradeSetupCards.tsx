import React, { useState } from 'react';
import { Target, Shield, Crosshair, Sparkles, ChevronDown, ChevronUp, Layers3, CalendarClock } from 'lucide-react';
import type { TradeSetup, TradeSetupsData } from '../types';
import { DirectionBadge, ConfidencePill, InfoTip } from './taUi';
import IndicatorAIConsole from './IndicatorAIConsole';
import { analyzeTa } from '../api';

const money = (n?: number | null) => (n == null ? '—' : `$${n.toFixed(2)}`);

const TYPE_LABEL: Record<string, string> = {
  trend_continuation: 'Trend Pullback',
  mean_reversion_fade: 'Mean-Reversion Fade',
  range_income: 'Range Income',
  range_bracket: 'Range Rotation',
  breakout: 'Breakout',
};

// ── the entry / stop / target "plan" visual ──
function PriceLadder({ setup, spot }: { setup: TradeSetup; spot: number | null }) {
  const entry = setup.entry.level ?? (setup.entry.low != null && setup.entry.high != null ? (setup.entry.low + setup.entry.high) / 2 : null);
  const stop = setup.stop.level;
  const target = setup.targets?.[0]?.level ?? null;
  const pts = [entry, setup.entry.low, setup.entry.high, stop, target, spot].filter((x): x is number => x != null && isFinite(x));
  if (pts.length < 2 || entry == null) return null;
  const min = Math.min(...pts), max = Math.max(...pts);
  const pad = (max - min) * 0.16 || max * 0.01 || 1;
  const lo = min - pad, hi = max + pad;
  const y = (p: number) => (1 - (p - lo) / (hi - lo)) * 100;

  const seg = (a: number | null, b: number | null) => (a == null || b == null) ? null
    : { top: Math.min(y(a), y(b)), height: Math.abs(y(a) - y(b)) };
  const reward = seg(entry, target);
  const risk = seg(entry, stop);

  const Row = ({ price, label, tone, Icon, strong }: { price: number | null; label: string; tone: string; Icon: any; strong?: boolean }) => {
    if (price == null) return null;
    return (
      <div className="absolute left-0 right-0 flex items-center" style={{ top: `${y(price)}%`, transform: 'translateY(-50%)' }}>
        <span className={`w-16 text-right text-[10px] tabular-nums pr-1.5 font-semibold ${tone}`}>{money(price)}</span>
        <span className={`flex-1 border-t ${strong ? 'border-solid' : 'border-dashed'} ${tone} opacity-40`} />
        <span className={`inline-flex items-center gap-1 text-[9px] pl-1.5 whitespace-nowrap ${tone}`}><Icon className="w-2.5 h-2.5" />{label}</span>
      </div>
    );
  };

  return (
    <div className="relative h-[190px] w-full">
      {/* reward / risk rail */}
      <div className="absolute" style={{ left: '68px', width: '3px', top: 0, bottom: 0 }}>
        <div className="absolute w-full rounded-full bg-base-content/10" style={{ top: 0, bottom: 0 }} />
        {reward && <div className="absolute w-full rounded-full bg-success/70" style={{ top: `${reward.top}%`, height: `${reward.height}%` }} />}
        {risk && <div className="absolute w-full rounded-full bg-error/70" style={{ top: `${risk.top}%`, height: `${risk.height}%` }} />}
      </div>
      <Row price={target} label="Target" tone="text-success" Icon={Target} strong />
      <Row price={setup.entry.high !== setup.entry.low ? setup.entry.high : null} label="" tone="text-warning" Icon={Crosshair} />
      <Row price={entry} label="Entry" tone="text-warning" Icon={Crosshair} strong />
      <Row price={setup.entry.high !== setup.entry.low ? setup.entry.low : null} label="" tone="text-warning" Icon={Crosshair} />
      <Row price={stop} label="Stop" tone="text-error" Icon={Shield} strong />
      {spot != null && (
        <div className="absolute right-0 flex items-center gap-1" style={{ top: `${y(spot)}%`, transform: 'translateY(-50%)' }}>
          <span className="text-[9px] text-primary font-bold">now →</span>
        </div>
      )}
    </div>
  );
}

function SetupCard({ setup, ticker, spot }: { setup: TradeSetup; ticker: string; spot: number | null }) {
  const [showWhy, setShowWhy] = useState(false);
  const [showAI, setShowAI] = useState(false);
  const rr = setup.risk_reward;
  const rrTone = rr == null ? 'text-base-content/50' : rr >= 2 ? 'text-success' : rr >= 1 ? 'text-warning' : 'text-error';
  const fitWarn = setup.regime_fit === 'counter_regime';

  return (
    <div className="glass-card p-4">
      <div className="flex items-start justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] font-bold text-base-content/40">#{setup.rank}</span>
          <DirectionBadge direction={setup.direction} />
          <span className="text-sm font-bold text-base-content/85">{TYPE_LABEL[setup.type] || setup.type}</span>
          <ConfidencePill level={setup.confidence} />
          {fitWarn && <span className="badge badge-xs badge-warning gap-1">counter-trend</span>}
        </div>
        <div className="text-right">
          <div className={`text-lg font-bold tabular-nums leading-none ${rrTone}`}>{rr != null ? `${rr}R` : '—'}</div>
          <div className="text-[9px] text-base-content/40 flex items-center gap-0.5 justify-end">reward : risk <InfoTip text="How many dollars you aim to make for every dollar risked. Above 2R is a strong setup." /></div>
        </div>
      </div>

      <p className="text-sm text-base-content/75 leading-snug mt-2.5">{setup.thesis}</p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
        <PriceLadder setup={setup} spot={spot} />
        <div className="space-y-2">
          <div className="rounded-lg border border-white/[0.06] bg-base-200/30 p-2.5">
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <div className="text-[10px] uppercase tracking-wider text-base-content/45 flex items-center gap-1"><Layers3 className="w-3 h-3" /> The option play</div>
              {setup.options.expiry?.date && (
                <span className="inline-flex items-center gap-1 rounded-md bg-primary/10 border border-primary/20 text-primary px-1.5 py-0.5 text-[10px] font-semibold">
                  <CalendarClock className="w-3 h-3" /> Exp {setup.options.expiry.date}{setup.options.expiry.dte != null ? ` · ${setup.options.expiry.dte}DTE` : ''}
                </span>
              )}
            </div>
            <div className="text-xs font-bold text-base-content/85 mt-0.5">{setup.options.structure}</div>
            <div className="text-[11px] text-base-content/60 leading-snug mt-0.5">{setup.options.detail}</div>
          </div>
          <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2.5 text-[11px] text-base-content/60 leading-snug">
            <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-base-content/45 mb-0.5">Sizing &amp; risk <InfoTip text="Risk per share is entry minus stop. Keep total dollar risk to a small, fixed % of your account per trade." /></div>
            {setup.sizing.risk_per_share != null && <div>Risk ≈ <b className="text-base-content/80">${setup.sizing.risk_per_share}</b>/share.</div>}
            {setup.sizing.note}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 mt-3 flex-wrap">
        <button className="btn btn-ghost btn-xs gap-1" onClick={() => setShowWhy(w => !w)}>
          {showWhy ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />} Why this setup
        </button>
        <button className="btn btn-primary btn-xs gap-1" onClick={() => setShowAI(s => !s)}>
          <Sparkles className="w-3.5 h-3.5" /> Ask AI about this setup
        </button>
      </div>

      {showWhy && (
        <div className="flex flex-wrap gap-1 mt-2">
          {setup.evidence.filter(Boolean).map((e, i) => (
            <span key={i} className="badge badge-sm bg-base-100/60 border-base-content/10 text-[10px]">{e}</span>
          ))}
        </div>
      )}

      {showAI && (
        <div className="mt-2">
          <IndicatorAIConsole
            selectionJson={{ ticker, spot, setup }}
            chips={[{ key: 'setup', label: `${TYPE_LABEL[setup.type] || setup.type} (${setup.direction})`, tone: 'text-base-content/70' }]}
            analyzeFn={(sel, msgs) => analyzeTa(ticker, sel, msgs)}
          />
        </div>
      )}
    </div>
  );
}

export default function TradeSetupCards({ data, ticker }: { data: TradeSetupsData; ticker: string }) {
  const setups = data.setups || [];
  if (!setups.length) {
    return (
      <div className="glass-card p-6 text-center">
        <Crosshair className="w-6 h-6 mx-auto text-base-content/30 mb-2" />
        <p className="text-sm font-semibold text-base-content/70">No high-conviction setup right now</p>
        <p className="text-xs text-base-content/50 mt-1">The signals don't line up into a clean trade yet. Check the key levels in <b>Advanced</b>, or wait for structure to develop.</p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {setups.map((s, i) => <SetupCard key={i} setup={s} ticker={ticker} spot={data.price} />)}
    </div>
  );
}
